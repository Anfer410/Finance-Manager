"""
services/family_service.py

DB operations for family management.
All functions require explicit family_id / user_id — no session coupling here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from data.db import get_engine, get_schema

_ENGINE = get_engine()
_SCHEMA = get_schema()


@dataclass
class FamilyMember:
    user_id:      int
    display_name: str
    username:     str
    person_name:  str
    family_role:  str          # 'member' | 'head'
    is_active:    bool
    is_instance_admin: bool
    joined_at:    object        # datetime


@dataclass
class Family:
    id:           int
    name:         str
    created_at:   object
    member_count: int


# ── Family queries ─────────────────────────────────────────────────────────────

def get_all_families() -> list[Family]:
    with _ENGINE.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT f.id, f.name, f.created_at,
                   COUNT(fm.id) FILTER (WHERE fm.left_at IS NULL) AS member_count
            FROM {_SCHEMA}.families f
            LEFT JOIN {_SCHEMA}.family_memberships fm ON fm.family_id = f.id
            GROUP BY f.id
            ORDER BY f.id
        """)).fetchall()
    return [Family(id=r[0], name=r[1], created_at=r[2], member_count=r[3]) for r in rows]


def get_family(family_id: int) -> Optional[Family]:
    with _ENGINE.connect() as conn:
        row = conn.execute(text(f"""
            SELECT f.id, f.name, f.created_at,
                   COUNT(fm.id) FILTER (WHERE fm.left_at IS NULL) AS member_count
            FROM {_SCHEMA}.families f
            LEFT JOIN {_SCHEMA}.family_memberships fm ON fm.family_id = f.id
            WHERE f.id = :fid
            GROUP BY f.id
        """), {"fid": family_id}).fetchone()
    if not row:
        return None
    return Family(id=row[0], name=row[1], created_at=row[2], member_count=row[3])


def create_family(name: str, created_by: int) -> int:
    """Create a new family and seed its config from family_id=1 defaults. Returns new family_id."""
    from services.config_repo import (
        load_bank_rules, save_bank_rules,
        load_banks, save_banks,
        load_categories, save_categories,
        load_transaction_cfg, save_transaction_cfg,
    )
    with _ENGINE.begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {_SCHEMA}.families (name, created_by)
            VALUES (:name, :uid)
            RETURNING id
        """), {"name": name, "uid": created_by}).fetchone()
        new_fid = row[0]

    # Seed config from family 1 defaults
    try:
        save_bank_rules(load_bank_rules(1), new_fid)
        save_banks(load_banks(1), new_fid)
        save_categories(load_categories(1), new_fid)
        save_transaction_cfg(load_transaction_cfg(1), new_fid)
    except Exception as e:
        print(f"[family_service] config seed warning for family {new_fid}: {e}")

    return new_fid


def rename_family(family_id: int, name: str) -> None:
    with _ENGINE.begin() as conn:
        conn.execute(text(f"""
            UPDATE {_SCHEMA}.families SET name = :name WHERE id = :fid
        """), {"name": name, "fid": family_id})


# ── Member queries ─────────────────────────────────────────────────────────────

def get_family_members(family_id: int) -> list[FamilyMember]:
    with _ENGINE.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT u.id, u.display_name, u.username, u.person_name,
                   fm.family_role, u.is_active, u.is_instance_admin,
                   fm.joined_at
            FROM {_SCHEMA}.family_memberships fm
            JOIN {_SCHEMA}.app_users u ON u.id = fm.user_id
            WHERE fm.family_id = :fid AND fm.left_at IS NULL
            ORDER BY fm.family_role DESC, u.display_name  -- heads first
        """), {"fid": family_id}).fetchall()
    return [
        FamilyMember(
            user_id=r[0], display_name=r[1], username=r[2], person_name=r[3],
            family_role=r[4], is_active=r[5], is_instance_admin=r[6], joined_at=r[7],
        )
        for r in rows
    ]


def get_users_without_family() -> list[dict]:
    """Return users that have no active family membership — for the 'add to family' flow."""
    with _ENGINE.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT u.id, u.display_name, u.username
            FROM {_SCHEMA}.app_users u
            WHERE NOT EXISTS (
                SELECT 1 FROM {_SCHEMA}.family_memberships fm
                WHERE fm.user_id = u.id AND fm.left_at IS NULL
            )
            ORDER BY u.display_name
        """)).fetchall()
    return [{"id": r[0], "display_name": r[1], "username": r[2]} for r in rows]


# ── Member mutations ───────────────────────────────────────────────────────────

def update_member_role(user_id: int, family_id: int, new_role: str) -> None:
    with _ENGINE.begin() as conn:
        conn.execute(text(f"""
            UPDATE {_SCHEMA}.family_memberships
            SET family_role = :role
            WHERE user_id = :uid AND family_id = :fid AND left_at IS NULL
        """), {"role": new_role, "uid": user_id, "fid": family_id})


def remove_member(user_id: int, family_id: int) -> None:
    """
    Set left_at on the active membership row.
    Also cleans up bank permissions for that user in this family.
    Does NOT remove/modify bank rules (person_override cleanup is a manual step
    that the Family Head should do separately via Settings).
    """
    with _ENGINE.begin() as conn:
        conn.execute(text(f"""
            UPDATE {_SCHEMA}.family_memberships
            SET left_at = NOW()
            WHERE user_id = :uid AND family_id = :fid AND left_at IS NULL
        """), {"uid": user_id, "fid": family_id})

        conn.execute(text(f"""
            DELETE FROM {_SCHEMA}.user_bank_permissions
            WHERE user_id = :uid AND family_id = :fid
        """), {"uid": user_id, "fid": family_id})


def add_user_to_family(user_id: int, family_id: int, role: str = "member") -> None:
    """
    Add an existing user to a family.
    Raises if the user already has an active membership (unique index enforces one family at a time).
    """
    with _ENGINE.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO {_SCHEMA}.family_memberships (family_id, user_id, family_role)
            VALUES (:fid, :uid, :role)
        """), {"fid": family_id, "uid": user_id, "role": role})

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
    """Returns only active (non-archived) families."""
    with _ENGINE.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT f.id, f.name, f.created_at,
                   COUNT(fm.id) FILTER (WHERE fm.left_at IS NULL) AS member_count
            FROM {_SCHEMA}.families f
            LEFT JOIN {_SCHEMA}.family_memberships fm ON fm.family_id = f.id
            WHERE f.archived_at IS NULL
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
    """Create a new family and seed generic config (categories, transaction cfg) from family 1.
    Bank rules and bank presets are intentionally NOT copied — they are account-specific and must
    be configured by the new family's head via the bank wizard.
    Returns new family_id.
    """
    from services.config_repo import (
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

    # Seed generic config from family 1 (categories + transaction config only)
    try:
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


# ── User deletion ──────────────────────────────────────────────────────────────

def get_user_deletion_info(user_id: int) -> dict:
    """
    Return what kind of deletion applies to this user.
    action='hard' → no transaction data, safe to remove completely.
    action='soft' → has transaction data, can only soft-delete.
    """
    with _ENGINE.connect() as conn:
        fam = conn.execute(text(f"""
            SELECT family_id FROM {_SCHEMA}.family_memberships
            WHERE user_id = :uid AND left_at IS NULL
        """), {"uid": user_id}).fetchone()

        tx_count = conn.execute(text(f"""
            SELECT COUNT(*) FROM (
                SELECT id FROM {_SCHEMA}.transactions_debit  WHERE :uid = ANY(person)
                UNION ALL
                SELECT id FROM {_SCHEMA}.transactions_credit WHERE :uid = ANY(person)
            ) t
        """), {"uid": user_id}).scalar() or 0

    return {
        "has_family":    fam is not None,
        "family_id":     fam[0] if fam else None,
        "tx_count":      int(tx_count),
        "action":        "soft" if tx_count > 0 else "hard",
    }


def delete_user(user_id: int) -> str:
    """
    Conditionally delete a user.
    Hard delete (no transactions): scrubs person[] arrays, removes account entirely.
    Soft delete (has transactions): sets deleted_at + is_active=False, ends membership.
    Returns 'hard' or 'soft'.
    """
    info = get_user_deletion_info(user_id)

    with _ENGINE.begin() as conn:
        if info["action"] == "hard":
            conn.execute(text(f"""
                DELETE FROM {_SCHEMA}.family_memberships WHERE user_id = :uid
            """), {"uid": user_id})
            for tbl in ("transactions_debit", "transactions_credit"):
                conn.execute(text(f"""
                    UPDATE {_SCHEMA}.{tbl} SET uploaded_by = NULL WHERE uploaded_by = :uid
                """), {"uid": user_id})
                conn.execute(text(f"""
                    UPDATE {_SCHEMA}.{tbl}
                    SET person = array_remove(person, :uid)
                    WHERE :uid = ANY(person)
                """), {"uid": user_id})
            conn.execute(text(f"DELETE FROM {_SCHEMA}.password_reset_tokens WHERE user_id = :uid"), {"uid": user_id})
            conn.execute(text(f"DELETE FROM {_SCHEMA}.app_user_prefs        WHERE user_id = :uid"), {"uid": user_id})
            conn.execute(text(f"DELETE FROM {_SCHEMA}.app_custom_charts      WHERE user_id = :uid"), {"uid": user_id})
            # app_dashboards ON DELETE CASCADE handles widgets/shares/subscriptions
            conn.execute(text(f"DELETE FROM {_SCHEMA}.app_dashboards         WHERE user_id = :uid"), {"uid": user_id})
            conn.execute(text(f"DELETE FROM {_SCHEMA}.user_bank_permissions  WHERE user_id = :uid"), {"uid": user_id})
            conn.execute(text(f"DELETE FROM {_SCHEMA}.app_users              WHERE id      = :uid"), {"uid": user_id})
        else:  # soft
            conn.execute(text(f"""
                UPDATE {_SCHEMA}.family_memberships SET left_at = NOW()
                WHERE user_id = :uid AND left_at IS NULL
            """), {"uid": user_id})
            conn.execute(text(f"""
                UPDATE {_SCHEMA}.app_users
                SET deleted_at = NOW(), is_active = FALSE
                WHERE id = :uid
            """), {"uid": user_id})

    return info["action"]


# ── Family deletion / archival ─────────────────────────────────────────────────

def get_family_deletion_info(family_id: int) -> dict:
    """
    Return what kind of deletion applies to this family.
    action='hard'             → no members, no transactions → full wipe
    action='hard_with_orphan' → has members, no transactions → wipe + free members
    action='archive'          → has transactions → soft archive
    """
    with _ENGINE.connect() as conn:
        member_count = conn.execute(text(f"""
            SELECT COUNT(*) FROM {_SCHEMA}.family_memberships fm
            JOIN {_SCHEMA}.app_users u ON u.id = fm.user_id
            WHERE fm.family_id = :fid AND fm.left_at IS NULL AND u.deleted_at IS NULL
        """), {"fid": family_id}).scalar() or 0

        tx_count = conn.execute(text(f"""
            SELECT COUNT(*) FROM (
                SELECT id FROM {_SCHEMA}.transactions_debit  WHERE family_id = :fid
                UNION ALL
                SELECT id FROM {_SCHEMA}.transactions_credit WHERE family_id = :fid
            ) t
        """), {"fid": family_id}).scalar() or 0

    if tx_count > 0:
        action = "archive"
    elif member_count > 0:
        action = "hard_with_orphan"
    else:
        action = "hard"

    return {
        "member_count": int(member_count),
        "tx_count":     int(tx_count),
        "action":       action,
    }


def delete_family(family_id: int, archived_by: int) -> str:
    """
    Conditionally delete or archive a family.
    Returns the action taken: 'hard', 'hard_with_orphan', or 'archive'.
    """
    info   = get_family_deletion_info(family_id)
    action = info["action"]

    _CONFIG_TABLES = (
        "app_config_bank_rules", "app_config_banks",
        "app_config_categories", "app_config_transaction",
        "app_config_archive", "app_loans",
    )

    with _ENGINE.begin() as conn:
        if action in ("hard", "hard_with_orphan"):
            conn.execute(text(f"DELETE FROM {_SCHEMA}.transaction_flags WHERE family_id = :fid"), {"fid": family_id})
            for tbl in _CONFIG_TABLES:
                conn.execute(text(f"DELETE FROM {_SCHEMA}.{tbl} WHERE family_id = :fid"), {"fid": family_id})
            conn.execute(text(f"DELETE FROM {_SCHEMA}.invitations WHERE family_id = :fid"), {"fid": family_id})
            # End memberships (free/orphan users)
            conn.execute(text(f"""
                UPDATE {_SCHEMA}.family_memberships SET left_at = NOW()
                WHERE family_id = :fid AND left_at IS NULL
            """), {"fid": family_id})
            conn.execute(text(f"DELETE FROM {_SCHEMA}.families WHERE id = :fid"), {"fid": family_id})
        else:  # archive — keep all data + memberships intact for clean restore
            conn.execute(text(f"""
                UPDATE {_SCHEMA}.families
                SET archived_at = NOW(), archived_by = :by
                WHERE id = :fid
            """), {"fid": family_id, "by": archived_by})

    return action


def is_family_archived(family_id: int) -> bool:
    with _ENGINE.connect() as conn:
        row = conn.execute(text(f"""
            SELECT archived_at FROM {_SCHEMA}.families WHERE id = :fid
        """), {"fid": family_id}).fetchone()
    return row is not None and row[0] is not None


def get_archived_families() -> list[dict]:
    with _ENGINE.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT f.id, f.name, f.archived_at, u.display_name AS archived_by_name,
                   (SELECT COUNT(*) FROM {_SCHEMA}.transactions_debit  WHERE family_id = f.id) +
                   (SELECT COUNT(*) FROM {_SCHEMA}.transactions_credit WHERE family_id = f.id) AS tx_count,
                   (SELECT COUNT(*) FROM {_SCHEMA}.family_memberships  WHERE family_id = f.id) AS member_count
            FROM {_SCHEMA}.families f
            LEFT JOIN {_SCHEMA}.app_users u ON u.id = f.archived_by
            WHERE f.archived_at IS NOT NULL
            ORDER BY f.archived_at DESC
        """)).fetchall()
    return [
        {
            "id": r[0], "name": r[1], "archived_at": r[2],
            "archived_by_name": r[3], "tx_count": int(r[4]), "member_count": int(r[5]),
        }
        for r in rows
    ]


def purge_archived_family(family_id: int) -> None:
    """Hard-delete an archived family and ALL its data. Raw tables are NOT touched."""
    _CONFIG_TABLES = (
        "app_config_bank_rules", "app_config_banks",
        "app_config_categories", "app_config_transaction",
        "app_config_archive", "app_loans",
    )
    with _ENGINE.begin() as conn:
        conn.execute(text(f"DELETE FROM {_SCHEMA}.transactions_debit  WHERE family_id = :fid"), {"fid": family_id})
        conn.execute(text(f"DELETE FROM {_SCHEMA}.transactions_credit WHERE family_id = :fid"), {"fid": family_id})
        conn.execute(text(f"DELETE FROM {_SCHEMA}.transaction_flags   WHERE family_id = :fid"), {"fid": family_id})
        for tbl in _CONFIG_TABLES:
            conn.execute(text(f"DELETE FROM {_SCHEMA}.{tbl} WHERE family_id = :fid"), {"fid": family_id})
        conn.execute(text(f"DELETE FROM {_SCHEMA}.invitations         WHERE family_id = :fid"), {"fid": family_id})
        conn.execute(text(f"DELETE FROM {_SCHEMA}.family_memberships  WHERE family_id = :fid"), {"fid": family_id})
        conn.execute(text(f"DELETE FROM {_SCHEMA}.families            WHERE id        = :fid"), {"fid": family_id})


def restore_archived_family(family_id: int) -> None:
    """Restore an archived family — clears archived_at so members regain access."""
    with _ENGINE.begin() as conn:
        conn.execute(text(f"""
            UPDATE {_SCHEMA}.families SET archived_at = NULL, archived_by = NULL WHERE id = :fid
        """), {"fid": family_id})

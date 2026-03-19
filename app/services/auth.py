"""
services/auth.py

Authentication helpers:
  - Password hashing (bcrypt)
  - User lookup from DB
  - Session read/write via NiceGUI app.storage.user
  - Route guard decorators

Session keys stored in app.storage.user:
  auth_user_id            int
  auth_username           str
  auth_display_name       str
  auth_person_name        str
  auth_is_instance_admin  bool
  auth_family_id          int | None   (active family; None if no membership)
  auth_family_role        str | None   'member' | 'head' | None
  auth_selected_persons   list[int]    (instance admins only — [] means all)
"""

from __future__ import annotations
import functools
from dataclasses import dataclass, field
from typing import Optional

import bcrypt
from sqlalchemy import text
from nicegui import app, ui

from data.db import get_engine, get_schema

_ENGINE = get_engine()
_SCHEMA = get_schema()


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plaintext.encode(), hashed.encode())
    except Exception:
        return False


# ── User dataclass ────────────────────────────────────────────────────────────

@dataclass
class AuthUser:
    id:                int
    username:          str
    display_name:      str
    person_name:       str
    is_active:         bool
    is_instance_admin: bool
    family_id:         Optional[int]    # active family; None if no membership
    family_role:       Optional[str]    # 'member' | 'head' | None
    selected_persons:  list[int] = field(default_factory=list)


# ── DB queries ─────────────────────────────────────────────────────────────────

_USER_SELECT = """
    SELECT u.id, u.username, u.display_name, u.person_name,
           u.is_active, u.is_instance_admin,
           fm.family_id, fm.family_role,
           COALESCE(p.selected_persons, CAST('[]' AS jsonb)) AS selected_persons
    FROM   {schema}.app_users u
    LEFT JOIN {schema}.app_user_prefs p ON p.user_id = u.id
    LEFT JOIN {schema}.family_memberships fm
           ON fm.user_id = u.id AND fm.left_at IS NULL
"""


def _row_to_user(row) -> AuthUser:
    return AuthUser(
        id=row[0], username=row[1], display_name=row[2],
        person_name=row[3], is_active=row[4], is_instance_admin=row[5],
        family_id=row[6], family_role=row[7],
        selected_persons=row[8] if isinstance(row[8], list) else list(row[8]),
    )


def get_user_by_username(username: str) -> Optional[AuthUser]:
    with _ENGINE.connect() as conn:
        row = conn.execute(text(
            _USER_SELECT.format(schema=_SCHEMA) + " WHERE u.username = :username"
        ), {"username": username}).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_id(user_id: int) -> Optional[AuthUser]:
    with _ENGINE.connect() as conn:
        row = conn.execute(text(
            _USER_SELECT.format(schema=_SCHEMA) + " WHERE u.id = :uid"
        ), {"uid": user_id}).fetchone()
    return _row_to_user(row) if row else None


def get_all_users() -> list[AuthUser]:
    with _ENGINE.connect() as conn:
        rows = conn.execute(text(
            _USER_SELECT.format(schema=_SCHEMA) + " ORDER BY u.id"
        )).fetchall()
    return [_row_to_user(r) for r in rows]


def create_user(
    username:     str,
    password:     str,
    display_name: str,
    person_name:  str | None = None,   # auto-derived from display_name if omitted
    family_id:    int | None = None,   # None = no family membership created
    family_role:  str  = "member",
) -> AuthUser:
    if person_name is None:
        person_name = display_name.lower()
    with _ENGINE.begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {_SCHEMA}.app_users
                (username, password_hash, display_name, person_name)
            VALUES (:u, :ph, :dn, :pn)
            RETURNING id
        """), {
            "u": username, "ph": hash_password(password),
            "dn": display_name, "pn": person_name,
        }).fetchone()
        user_id = row[0]

        conn.execute(text(f"""
            INSERT INTO {_SCHEMA}.app_user_prefs (user_id, selected_persons)
            VALUES (:uid, '[]')
        """), {"uid": user_id})

        if family_id is not None:
            conn.execute(text(f"""
                INSERT INTO {_SCHEMA}.family_memberships (family_id, user_id, family_role)
                VALUES (:fid, :uid, :role)
            """), {"fid": family_id, "uid": user_id, "role": family_role})

    return get_user_by_id(user_id)


def update_user(
    user_id:              int,
    *,
    display_name:         str | None  = None,
    person_name:          str | None  = None,
    is_active:            bool | None = None,
    is_instance_admin:    bool | None = None,
    email:                str | None  = None,
    must_change_password: bool | None = None,
    password:             str | None  = None,
) -> None:
    fields, params = [], {"uid": user_id}
    if display_name is not None:
        fields.append("display_name = :display_name")
        params["display_name"] = display_name
    if person_name is not None:
        fields.append("person_name = :person_name")
        params["person_name"] = person_name
    if is_active is not None:
        fields.append("is_active = :is_active")
        params["is_active"] = is_active
    if is_instance_admin is not None:
        fields.append("is_instance_admin = :is_instance_admin")
        params["is_instance_admin"] = is_instance_admin
    if email is not None:
        fields.append("email = :email")
        params["email"] = email
    if must_change_password is not None:
        fields.append("must_change_password = :must_change_password")
        params["must_change_password"] = must_change_password
    if password is not None:
        fields.append("password_hash = :password_hash")
        params["password_hash"] = hash_password(password)
    if not fields:
        return
    with _ENGINE.begin() as conn:
        conn.execute(text(
            f"UPDATE {_SCHEMA}.app_users SET {', '.join(fields)} WHERE id = :uid"
        ), params)


def save_selected_persons(user_id: int, persons: list[int]) -> None:
    import json as _json
    with _ENGINE.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO {_SCHEMA}.app_user_prefs (user_id, selected_persons, updated_at)
            VALUES (:uid, CAST(:sp AS jsonb), NOW())
            ON CONFLICT (user_id) DO UPDATE
                SET selected_persons = CAST(:sp AS jsonb), updated_at = NOW()
        """), {"uid": user_id, "sp": _json.dumps(persons)})


# ── Session helpers ───────────────────────────────────────────────────────────

def login(user: AuthUser) -> None:
    app.storage.user.update({
        "auth_user_id":           user.id,
        "auth_username":          user.username,
        "auth_display_name":      user.display_name,
        "auth_person_name":       user.person_name,
        "auth_is_instance_admin": user.is_instance_admin,
        "auth_family_id":         user.family_id,
        "auth_family_role":       user.family_role,
        "auth_selected_persons":  user.selected_persons,
    })


def logout() -> None:
    for key in list(app.storage.user.keys()):
        if key.startswith("auth_"):
            del app.storage.user[key]


def current_user_id() -> int | None:
    return app.storage.user.get("auth_user_id")


def is_authenticated() -> bool:
    return current_user_id() is not None


def is_instance_admin() -> bool:
    return bool(app.storage.user.get("auth_is_instance_admin"))


def is_family_head() -> bool:
    return (app.storage.user.get("auth_family_role") == "head"
            or bool(app.storage.user.get("auth_is_instance_admin")))


def current_family_id() -> int | None:
    return app.storage.user.get("auth_family_id")


def current_family_role() -> str | None:
    return app.storage.user.get("auth_family_role")


def current_person_name() -> str | None:
    return app.storage.user.get("auth_person_name")


def current_display_name() -> str | None:
    return app.storage.user.get("auth_display_name")


def current_selected_persons() -> list[int]:
    """Instance admin: returns saved preference ([] = all). Others: [own user id]."""
    if is_instance_admin():
        return app.storage.user.get("auth_selected_persons", [])
    uid = current_user_id()
    return [uid] if uid else []


def update_session_selected_persons(persons: list[int]) -> None:
    app.storage.user["auth_selected_persons"] = persons
    uid = current_user_id()
    if uid:
        save_selected_persons(uid, persons)


# ── Login attempt ─────────────────────────────────────────────────────────────

def attempt_login(username: str, password: str) -> tuple[bool, str]:
    user = get_user_by_username(username.strip())
    if not user:
        return False, "Invalid username or password."
    if not user.is_active:
        return False, "Account is disabled."
    with _ENGINE.connect() as conn:
        row = conn.execute(text(
            f"SELECT password_hash FROM {_SCHEMA}.app_users WHERE id = :uid"
        ), {"uid": user.id}).fetchone()
    if not row or not verify_password(password, row[0]):
        return False, "Invalid username or password."
    login(user)
    return True, ""


# ── Route guards ──────────────────────────────────────────────────────────────

def require_auth(handler):
    """Redirect to /login if not authenticated."""
    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            ui.navigate.to("/login")
            return
        return handler(*args, **kwargs)
    return wrapper


def require_instance_admin(handler):
    """Redirect to / if not an instance admin."""
    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            ui.navigate.to("/login")
            return
        if not is_instance_admin():
            ui.navigate.to("/")
            return
        return handler(*args, **kwargs)
    return wrapper


def require_family_head(handler):
    """Redirect to / if not a family head (or instance admin)."""
    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            ui.navigate.to("/login")
            return
        if not (is_family_head() or is_instance_admin()):
            ui.navigate.to("/")
            return
        return handler(*args, **kwargs)
    return wrapper

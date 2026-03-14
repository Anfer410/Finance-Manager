"""
services/auth.py

Authentication helpers:
  - Password hashing (bcrypt)
  - User lookup from DB
  - Session read/write via NiceGUI app.storage.user
  - Route guard decorator

Session keys stored in app.storage.user:
  auth_user_id       int
  auth_username      str
  auth_display_name  str
  auth_person_name   str
  auth_role          'admin' | 'user'
  auth_selected_persons  list[str]   (admin only — [] means all)
"""

from __future__ import annotations
import functools
from dataclasses import dataclass
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
    id:               int
    username:         str
    display_name:     str
    person_name:      str
    role:             str          # 'admin' | 'user'
    is_active:        bool
    selected_persons: list[str]    # [] = all (admin); ignored for users


# ── DB lookups ────────────────────────────────────────────────────────────────

def get_user_by_username(username: str) -> Optional[AuthUser]:
    with _ENGINE.connect() as conn:
        row = conn.execute(text(f"""
            SELECT u.id, u.username, u.display_name, u.person_name,
                   u.role, u.is_active,
                   COALESCE(p.selected_persons, CAST('[]' AS jsonb)) AS selected_persons
            FROM   {_SCHEMA}.app_users u
            LEFT JOIN {_SCHEMA}.app_user_prefs p ON p.user_id = u.id
            WHERE  u.username = :username
        """), {"username": username}).fetchone()
    if not row:
        return None
    return AuthUser(
        id=row[0], username=row[1], display_name=row[2],
        person_name=row[3], role=row[4], is_active=row[5],
        selected_persons=row[6] if isinstance(row[6], list) else list(row[6]),
    )


def get_user_by_id(user_id: int) -> Optional[AuthUser]:
    with _ENGINE.connect() as conn:
        row = conn.execute(text(f"""
            SELECT u.id, u.username, u.display_name, u.person_name,
                   u.role, u.is_active,
                   COALESCE(p.selected_persons, CAST('[]' AS jsonb)) AS selected_persons
            FROM   {_SCHEMA}.app_users u
            LEFT JOIN {_SCHEMA}.app_user_prefs p ON p.user_id = u.id
            WHERE  u.id = :uid
        """), {"uid": user_id}).fetchone()
    if not row:
        return None
    return AuthUser(
        id=row[0], username=row[1], display_name=row[2],
        person_name=row[3], role=row[4], is_active=row[5],
        selected_persons=row[6] if isinstance(row[6], list) else list(row[6]),
    )


def get_all_users() -> list[AuthUser]:
    with _ENGINE.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT u.id, u.username, u.display_name, u.person_name,
                   u.role, u.is_active,
                   COALESCE(p.selected_persons, CAST('[]' AS jsonb)) AS selected_persons
            FROM   {_SCHEMA}.app_users u
            LEFT JOIN {_SCHEMA}.app_user_prefs p ON p.user_id = u.id
            ORDER BY u.id
        """)).fetchall()
    return [AuthUser(
        id=r[0], username=r[1], display_name=r[2],
        person_name=r[3], role=r[4], is_active=r[5],
        selected_persons=r[6] if isinstance(r[6], list) else list(r[6]),
    ) for r in rows]


def create_user(username: str, password: str, display_name: str,
                person_name: str, role: str = "user") -> AuthUser:
    with _ENGINE.begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {_SCHEMA}.app_users
                (username, password_hash, display_name, person_name, role)
            VALUES (:u, :ph, :dn, :pn, :role)
            RETURNING id
        """), {
            "u": username, "ph": hash_password(password),
            "dn": display_name, "pn": person_name, "role": role,
        }).fetchone()
        user_id = row[0]
        conn.execute(text(f"""
            INSERT INTO {_SCHEMA}.app_user_prefs (user_id, selected_persons)
            VALUES (:uid, '[]')
        """), {"uid": user_id})
    return get_user_by_id(user_id)


def update_user(user_id: int, *, display_name: str | None = None,
                person_name: str | None = None, role: str | None = None,
                is_active: bool | None = None,
                password: str | None = None) -> None:
    fields, params = [], {"uid": user_id}
    if display_name is not None:
        fields.append("display_name = :display_name")
        params["display_name"] = display_name
    if person_name is not None:
        fields.append("person_name = :person_name")
        params["person_name"] = person_name
    if role is not None:
        fields.append("role = :role")
        params["role"] = role
    if is_active is not None:
        fields.append("is_active = :is_active")
        params["is_active"] = is_active
    if password is not None:
        fields.append("password_hash = :password_hash")
        params["password_hash"] = hash_password(password)
    if not fields:
        return
    with _ENGINE.begin() as conn:
        conn.execute(text(
            f"UPDATE {_SCHEMA}.app_users SET {', '.join(fields)} WHERE id = :uid"
        ), params)


def save_selected_persons(user_id: int, persons: list[str]) -> None:
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
    """Write user into NiceGUI session storage."""
    app.storage.user.update({
        "auth_user_id":          user.id,
        "auth_username":         user.username,
        "auth_display_name":     user.display_name,
        "auth_person_name":      user.person_name,
        "auth_role":             user.role,
        "auth_selected_persons": user.selected_persons,
    })


def logout() -> None:
    for key in list(app.storage.user.keys()):
        if key.startswith("auth_"):
            del app.storage.user[key]


def current_user_id() -> int | None:
    return app.storage.user.get("auth_user_id")


def current_role() -> str | None:
    return app.storage.user.get("auth_role")


def is_admin() -> bool:
    return current_role() == "admin"


def is_authenticated() -> bool:
    return current_user_id() is not None


def current_person_name() -> str | None:
    return app.storage.user.get("auth_person_name")


def current_display_name() -> str | None:
    return app.storage.user.get("auth_display_name")


def current_selected_persons() -> list[str]:
    """Admin: returns saved preference ([] = all). User: returns [own person]."""
    if is_admin():
        return app.storage.user.get("auth_selected_persons", [])
    pn = current_person_name()
    return [pn] if pn else []


def update_session_selected_persons(persons: list[str]) -> None:
    """Update in-session preference and persist to DB."""
    app.storage.user["auth_selected_persons"] = persons
    uid = current_user_id()
    if uid:
        save_selected_persons(uid, persons)


# ── Login attempt ─────────────────────────────────────────────────────────────

def attempt_login(username: str, password: str) -> tuple[bool, str]:
    """
    Returns (success, error_message).
    On success, writes session — caller should redirect.
    """
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


# ── Route guard ───────────────────────────────────────────────────────────────

def require_auth(handler):
    """Decorator — redirects to /login if not authenticated."""
    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            ui.navigate.to("/login")
            return
        return handler(*args, **kwargs)
    return wrapper


def require_admin(handler):
    """Decorator — redirects to / if not admin."""
    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            ui.navigate.to("/login")
            return
        if not is_admin():
            ui.navigate.to("/")
            return
        return handler(*args, **kwargs)
    return wrapper
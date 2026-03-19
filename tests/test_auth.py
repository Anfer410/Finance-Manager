"""
tests/test_auth.py

Tests for services/auth.py — Phase 3 auth layer.

Unit tests (no DB): session helpers, role checks, password hashing.
Integration tests (db_conn): DB lookups, create_user, update_user, login data.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Stub nicegui before importing auth (auth does module-level get_engine/get_schema)
for _mod in ("nicegui", "nicegui.app"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


# ── Unit tests — password helpers ─────────────────────────────────────────────

class TestPasswordHelpers:
    def setup_method(self):
        from services.auth import hash_password, verify_password
        self.hash_password  = hash_password
        self.verify_password = verify_password

    def test_hash_is_not_plaintext(self):
        h = self.hash_password("secret")
        assert h != "secret"

    def test_verify_correct_password(self):
        h = self.hash_password("mysecret")
        assert self.verify_password("mysecret", h) is True

    def test_verify_wrong_password(self):
        h = self.hash_password("mysecret")
        assert self.verify_password("wrong", h) is False

    def test_verify_bad_hash_returns_false(self):
        assert self.verify_password("any", "not-a-hash") is False


# ── Unit tests — session helpers ──────────────────────────────────────────────

class TestSessionHelpers:
    """Patch app.storage.user so we don't need NiceGUI running."""

    def _make_storage(self, **kwargs):
        return dict(kwargs)

    def test_is_authenticated_true(self):
        from services import auth
        with patch.object(auth.app.storage, "user", {"auth_user_id": 1}):
            assert auth.is_authenticated() is True

    def test_is_authenticated_false(self):
        from services import auth
        with patch.object(auth.app.storage, "user", {}):
            assert auth.is_authenticated() is False

    def test_is_instance_admin_true(self):
        from services import auth
        with patch.object(auth.app.storage, "user", {"auth_is_instance_admin": True}):
            assert auth.is_instance_admin() is True

    def test_is_instance_admin_false_when_missing(self):
        from services import auth
        with patch.object(auth.app.storage, "user", {}):
            assert auth.is_instance_admin() is False

    def test_is_family_head_true(self):
        from services import auth
        with patch.object(auth.app.storage, "user", {"auth_family_role": "head"}):
            assert auth.is_family_head() is True

    def test_is_family_head_false_for_member(self):
        from services import auth
        with patch.object(auth.app.storage, "user", {"auth_family_role": "member"}):
            assert auth.is_family_head() is False

    def test_current_family_id(self):
        from services import auth
        with patch.object(auth.app.storage, "user", {"auth_family_id": 42}):
            assert auth.current_family_id() == 42

    def test_current_family_id_none_when_missing(self):
        from services import auth
        with patch.object(auth.app.storage, "user", {}):
            assert auth.current_family_id() is None

    def test_current_selected_persons_instance_admin(self):
        from services import auth
        storage = {"auth_user_id": 1, "auth_is_instance_admin": True,
                   "auth_selected_persons": [2, 3]}
        with patch.object(auth.app.storage, "user", storage):
            assert auth.current_selected_persons() == [2, 3]

    def test_current_selected_persons_member_returns_own_id(self):
        from services import auth
        storage = {"auth_user_id": 5, "auth_is_instance_admin": False}
        with patch.object(auth.app.storage, "user", storage):
            assert auth.current_selected_persons() == [5]

    def test_current_selected_persons_admin_empty_means_all(self):
        from services import auth
        storage = {"auth_user_id": 1, "auth_is_instance_admin": True,
                   "auth_selected_persons": []}
        with patch.object(auth.app.storage, "user", storage):
            assert auth.current_selected_persons() == []


# ── Integration tests — DB lookups ────────────────────────────────────────────

@pytest.fixture
def seeded_user(pg_engine, schema):
    """
    Insert a user + family membership in a COMMITTED transaction so that auth
    functions (which open their own connections) can see the data.
    Cleanup is performed after the test.
    """
    with pg_engine.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO {schema}.families (id, name)
            VALUES (10, 'Test Family')
            ON CONFLICT (id) DO NOTHING
        """))
        row = conn.execute(text(f"""
            INSERT INTO {schema}.app_users
                (username, password_hash, display_name, person_name, is_instance_admin)
            VALUES ('auth_test_user', 'hash', 'Auth Test', 'authtest', FALSE)
            RETURNING id
        """)).fetchone()
        user_id = row[0]
        conn.execute(text(f"""
            INSERT INTO {schema}.family_memberships (family_id, user_id, family_role)
            VALUES (10, :uid, 'head')
        """), {"uid": user_id})

    yield user_id

    with pg_engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {schema}.family_memberships WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text(f"DELETE FROM {schema}.app_user_prefs WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text(f"DELETE FROM {schema}.app_users WHERE id = :uid"), {"uid": user_id})


class TestGetUser:
    def test_get_by_username_returns_user(self, db_conn, schema, seeded_user, pg_engine):
        from services import auth as _auth
        # Point auth module at the test engine
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        user = _auth.get_user_by_username("auth_test_user")
        assert user is not None
        assert user.username == "auth_test_user"

    def test_get_by_username_includes_family(self, db_conn, schema, seeded_user, pg_engine):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        user = _auth.get_user_by_username("auth_test_user")
        assert user.family_id == 10
        assert user.family_role == "head"

    def test_get_by_username_is_not_instance_admin(self, db_conn, schema, seeded_user, pg_engine):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        user = _auth.get_user_by_username("auth_test_user")
        assert user.is_instance_admin is False

    def test_get_nonexistent_returns_none(self, db_conn, schema, pg_engine):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        assert _auth.get_user_by_username("nobody_xyz") is None

    def test_get_by_id_matches_get_by_username(self, db_conn, schema, seeded_user, pg_engine):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        by_name = _auth.get_user_by_username("auth_test_user")
        by_id   = _auth.get_user_by_id(by_name.id)
        assert by_id.username == by_name.username
        assert by_id.family_id == by_name.family_id

    def test_user_without_family_has_none_family_id(self, pg_engine, schema):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        with pg_engine.begin() as conn:
            conn.execute(text(f"""
                INSERT INTO {schema}.app_users
                    (username, password_hash, display_name, person_name)
                VALUES ('no_family_user', 'x', 'No Family', 'nofamily')
            """))
        try:
            user = _auth.get_user_by_username("no_family_user")
            assert user.family_id is None
            assert user.family_role is None
        finally:
            with pg_engine.begin() as conn:
                conn.execute(text(f"DELETE FROM {schema}.app_users WHERE username = 'no_family_user'"))


class TestCreateUser:
    def _cleanup(self, pg_engine, schema, username):
        with pg_engine.begin() as conn:
            row = conn.execute(text(
                f"SELECT id FROM {schema}.app_users WHERE username = :u"
            ), {"u": username}).fetchone()
            if row:
                uid = row[0]
                conn.execute(text(f"DELETE FROM {schema}.family_memberships WHERE user_id = :uid"), {"uid": uid})
                conn.execute(text(f"DELETE FROM {schema}.app_user_prefs WHERE user_id = :uid"), {"uid": uid})
                conn.execute(text(f"DELETE FROM {schema}.app_users WHERE id = :uid"), {"uid": uid})

    def test_create_inserts_membership(self, pg_engine, schema):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        with pg_engine.begin() as conn:
            conn.execute(text(f"""
                INSERT INTO {schema}.families (id, name) VALUES (20, 'Create Test Family')
                ON CONFLICT (id) DO NOTHING
            """))
        try:
            user = _auth.create_user("new_member", "pass1234", "New Member", "newmember",
                                     family_id=20, family_role="member")
            assert user.family_id == 20
            assert user.family_role == "member"
        finally:
            self._cleanup(pg_engine, schema, "new_member")

    def test_create_with_no_family_has_no_membership(self, pg_engine, schema):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        try:
            user = _auth.create_user("default_fam_user", "pass1234", "Default Fam", "defaultfam")
            assert user.family_id is None
            assert user.family_role is None
        finally:
            self._cleanup(pg_engine, schema, "default_fam_user")

    def test_create_password_is_hashed(self, pg_engine, schema):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        try:
            _auth.create_user("hashed_pw_user", "plaintext", "H", "h")
            with pg_engine.connect() as conn:
                row = conn.execute(text(
                    f"SELECT password_hash FROM {schema}.app_users WHERE username = 'hashed_pw_user'"
                )).fetchone()
            assert row[0] != "plaintext"
            assert _auth.verify_password("plaintext", row[0])
        finally:
            self._cleanup(pg_engine, schema, "hashed_pw_user")


class TestUpdateUser:
    def test_update_display_name(self, db_conn, schema, seeded_user, pg_engine):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        _auth.update_user(seeded_user, display_name="Updated Name")
        user = _auth.get_user_by_id(seeded_user)
        assert user.display_name == "Updated Name"

    def test_update_is_instance_admin(self, db_conn, schema, seeded_user, pg_engine):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        _auth.update_user(seeded_user, is_instance_admin=True)
        user = _auth.get_user_by_id(seeded_user)
        assert user.is_instance_admin is True

    def test_update_no_fields_is_noop(self, db_conn, schema, seeded_user, pg_engine):
        from services import auth as _auth
        _auth._ENGINE = pg_engine
        _auth._SCHEMA = schema

        # Should not raise
        _auth.update_user(seeded_user)

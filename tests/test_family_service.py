"""
tests/test_family_service.py

Integration tests for services/family_service.py.
All tests use committed transactions (pg_engine) because family_service opens
its own connections internally.  Each test is responsible for its own cleanup.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

for _mod in ("nicegui", "nicegui.app"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_user(pg_engine, schema: str, username: str) -> int:
    with pg_engine.begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {schema}.app_users (username, password_hash, display_name, person_name)
            VALUES (:u, 'x', :u, :u) RETURNING id
        """), {"u": username}).fetchone()
    return row[0]


def _cleanup_user(pg_engine, schema: str, user_id: int) -> None:
    with pg_engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {schema}.family_memberships WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text(f"DELETE FROM {schema}.app_user_prefs  WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text(f"DELETE FROM {schema}.app_users       WHERE id      = :uid"), {"uid": user_id})


def _cleanup_family(pg_engine, schema: str, family_id: int) -> None:
    with pg_engine.begin() as conn:
        # Delete all FK-referencing rows before removing the family row
        conn.execute(text(f"DELETE FROM {schema}.family_memberships         WHERE family_id = :fid"), {"fid": family_id})
        for cfg in ("bank_rules", "banks", "categories", "transaction", "archive"):
            conn.execute(text(f"DELETE FROM {schema}.app_config_{cfg} WHERE family_id = :fid"), {"fid": family_id})
        conn.execute(text(f"DELETE FROM {schema}.families                   WHERE id        = :fid"), {"fid": family_id})


def _svc(pg_engine, schema: str):
    from services import family_service as svc
    svc._ENGINE = pg_engine
    svc._SCHEMA = schema
    return svc


# ── Family CRUD ───────────────────────────────────────────────────────────────

class TestFamilyQueries:
    def test_create_family_returns_int(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_create_creator")
        fid = None
        try:
            fid = svc.create_family("CreateTestFamily", uid)
            assert isinstance(fid, int) and fid > 0
        finally:
            if fid:
                _cleanup_family(pg_engine, schema, fid)
            _cleanup_user(pg_engine, schema, uid)

    def test_get_all_families_includes_new(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_getall_creator")
        fid = svc.create_family("GetAllTestFamily", uid)
        try:
            ids = [f.id for f in svc.get_all_families()]
            assert fid in ids
        finally:
            _cleanup_family(pg_engine, schema, fid)
            _cleanup_user(pg_engine, schema, uid)

    def test_get_family_returns_correct_data(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_getbyid_creator")
        fid = svc.create_family("GetByIdFamily", uid)
        try:
            f = svc.get_family(fid)
            assert f is not None
            assert f.id == fid
            assert f.name == "GetByIdFamily"
        finally:
            _cleanup_family(pg_engine, schema, fid)
            _cleanup_user(pg_engine, schema, uid)

    def test_get_nonexistent_family_returns_none(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        assert svc.get_family(999_999) is None

    def test_rename_family(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_rename_creator")
        fid = svc.create_family("OldName", uid)
        try:
            svc.rename_family(fid, "NewName")
            assert svc.get_family(fid).name == "NewName"
        finally:
            _cleanup_family(pg_engine, schema, fid)
            _cleanup_user(pg_engine, schema, uid)

    def test_member_count_increments_on_add(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_mcount_creator")
        fid = svc.create_family("MCountFamily", uid)
        try:
            svc.add_user_to_family(uid, fid, role="head")
            assert svc.get_family(fid).member_count == 1
        finally:
            _cleanup_family(pg_engine, schema, fid)
            _cleanup_user(pg_engine, schema, uid)


# ── Member CRUD ───────────────────────────────────────────────────────────────

class TestMemberMutations:
    """Uses pre-seeded families 2, 7, 42 (from conftest) to avoid family teardown."""

    def test_get_family_members_empty_for_unseeded_family(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        # family 42 has no members seeded
        members = svc.get_family_members(42)
        assert isinstance(members, list)

    def test_add_user_appears_in_members(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_addmem_user")
        try:
            svc.add_user_to_family(uid, 2, role="member")
            ids = [m.user_id for m in svc.get_family_members(2)]
            assert uid in ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_add_user_role_is_stored(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_role_stored_user")
        try:
            svc.add_user_to_family(uid, 7, role="head")
            m = next(m for m in svc.get_family_members(7) if m.user_id == uid)
            assert m.family_role == "head"
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_update_member_role(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_updrole_user")
        try:
            svc.add_user_to_family(uid, 2, role="member")
            svc.update_member_role(uid, 2, "head")
            m = next(m for m in svc.get_family_members(2) if m.user_id == uid)
            assert m.family_role == "head"
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_remove_member_hides_from_active_list(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_remove_user")
        try:
            svc.add_user_to_family(uid, 2, role="member")
            assert any(m.user_id == uid for m in svc.get_family_members(2))
            svc.remove_member(uid, 2)
            assert not any(m.user_id == uid for m in svc.get_family_members(2))
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_remove_member_sets_left_at_in_db(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_leftat_user")
        try:
            svc.add_user_to_family(uid, 7, role="member")
            svc.remove_member(uid, 7)
            with pg_engine.connect() as conn:
                row = conn.execute(text(f"""
                    SELECT left_at FROM {schema}.family_memberships
                    WHERE user_id = :uid AND family_id = 7
                """), {"uid": uid}).fetchone()
            assert row is not None and row[0] is not None
        finally:
            _cleanup_user(pg_engine, schema, uid)


# ── Users without family ──────────────────────────────────────────────────────

class TestUsersWithoutFamily:
    def test_new_user_appears_in_without_family(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_nofam_user")
        try:
            ids = [u["id"] for u in svc.get_users_without_family()]
            assert uid in ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_added_user_absent_from_without_family(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_nofam2_user")
        try:
            svc.add_user_to_family(uid, 2, role="member")
            ids = [u["id"] for u in svc.get_users_without_family()]
            assert uid not in ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_removed_user_reappears_in_without_family(self, pg_engine, schema):
        svc = _svc(pg_engine, schema)
        uid = _make_user(pg_engine, schema, "fs_nofam3_user")
        try:
            svc.add_user_to_family(uid, 2, role="member")
            svc.remove_member(uid, 2)
            ids = [u["id"] for u in svc.get_users_without_family()]
            assert uid in ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

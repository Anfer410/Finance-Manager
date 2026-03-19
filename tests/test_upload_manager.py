"""
tests/test_upload_manager.py

Tests for services/upload_manager.py.

Unit tests: _sanitize, _raw_join_clause (pure logic).
Integration tests: get_upload_batches, reassign_persons, delete_batch.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

APP_DIR = Path(__file__).parent.parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# ── module loader ─────────────────────────────────────────────────────────────

def _load(pg_engine):
    path = APP_DIR / "services" / "upload_manager.py"
    spec = importlib.util.spec_from_file_location("services.upload_manager", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── helpers ───────────────────────────────────────────────────────────────────

_FAMILY_ID   = 2   # pre-seeded
_SOURCE_FILE = "um_test_batch.csv"
_ACCOUNT_KEY = "um_test_chk"


def _seed_debit_rows(pg_engine, schema: str, n: int = 3,
                     source_file: str = _SOURCE_FILE,
                     account_key: str = _ACCOUNT_KEY,
                     family_id: int   = _FAMILY_ID,
                     person_ids: list | None = None) -> None:
    person_arr = "{1}" if not person_ids else "{" + ",".join(str(i) for i in person_ids) + "}"
    with pg_engine.begin() as conn:
        # Ensure 2024 partition exists
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {schema}.transactions_debit_2024
            PARTITION OF {schema}.transactions_debit
            FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')
        """))
        for i in range(n):
            conn.execute(text(f"""
                INSERT INTO {schema}.transactions_debit
                    (account_key, transaction_date, description, amount, person,
                     source_file, family_id)
                VALUES (:ak, :dt, :desc, :amt, CAST(:p AS integer[]), :sf, :fid)
                ON CONFLICT DO NOTHING
            """), {
                "ak":   account_key,
                "dt":   date(2024, 1, i + 1),
                "desc": f"UM Txn {i}",
                "amt":  float(10 * (i + 1)),
                "p":    person_arr,
                "sf":   source_file,
                "fid":  family_id,
            })


def _cleanup_debit(pg_engine, schema: str,
                   source_file: str = _SOURCE_FILE,
                   account_key: str = _ACCOUNT_KEY,
                   family_id: int   = _FAMILY_ID) -> None:
    with pg_engine.begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {schema}.transactions_debit
            WHERE source_file = :sf AND account_key = :ak AND family_id = :fid
        """), {"sf": source_file, "ak": account_key, "fid": family_id})


# ── Unit: _sanitize ───────────────────────────────────────────────────────────

class TestSanitize:
    def _svc(self):
        import importlib.util
        path = APP_DIR / "services" / "upload_manager.py"
        spec = importlib.util.spec_from_file_location("_um_unit", path)
        mod  = importlib.util.module_from_spec(spec)
        # Need data.db stubbed for module-level import
        import sys
        from unittest.mock import MagicMock
        sys.modules.setdefault("data.db", MagicMock())
        spec.loader.exec_module(mod)
        return mod

    def test_lowercase(self):
        svc = self._svc()
        assert svc._sanitize("WF_Checking") == "wf_checking"

    def test_spaces_to_underscore(self):
        svc = self._svc()
        assert svc._sanitize("my account") == "my_account"

    def test_hyphens_to_underscore(self):
        svc = self._svc()
        assert svc._sanitize("cap-one") == "cap_one"

    def test_strips_special_chars(self):
        svc = self._svc()
        assert svc._sanitize("acc@123!") == "acc123"


# ── Unit: _raw_join_clause ────────────────────────────────────────────────────

class TestRawJoinClause:
    def _svc(self):
        import sys
        from unittest.mock import MagicMock
        sys.modules.setdefault("data.db", MagicMock())
        path = APP_DIR / "services" / "upload_manager.py"
        spec = importlib.util.spec_from_file_location("_um_join", path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_empty_dedup_returns_empty_string(self):
        svc = self._svc()
        assert svc._raw_join_clause([], "debit") == ""

    def test_date_column_maps_to_transaction_date(self):
        svc = self._svc()
        result = svc._raw_join_clause([("trans_date", "date")], "debit")
        assert "transaction_date" in result
        assert "trans_date" in result

    def test_text_column_maps_to_description(self):
        svc = self._svc()
        result = svc._raw_join_clause([("memo", "text")], "debit")
        assert "description" in result
        assert "memo" in result

    def test_numeric_debit_maps_to_amount(self):
        svc = self._svc()
        result = svc._raw_join_clause([("amt", "numeric")], "debit")
        assert "t.amount" in result

    def test_numeric_credit_with_credit_in_name(self):
        svc = self._svc()
        result = svc._raw_join_clause([("credit_amount", "numeric")], "credit")
        assert "t.credit" in result

    def test_numeric_credit_with_debit_in_name(self):
        svc = self._svc()
        result = svc._raw_join_clause([("debit_amount", "numeric")], "credit")
        assert "t.debit" in result

    def test_multiple_columns_joined_with_and(self):
        svc = self._svc()
        result = svc._raw_join_clause(
            [("trans_date", "date"), ("memo", "text")], "debit"
        )
        assert " AND " in result


# ── Integration: get_upload_batches ──────────────────────────────────────────

class TestGetUploadBatches:
    def test_returns_list(self, pg_engine, schema):
        svc = _load(pg_engine)
        result = svc.get_upload_batches(_FAMILY_ID)
        assert isinstance(result, list)

    def test_seeded_batch_appears(self, pg_engine, schema):
        svc = _load(pg_engine)
        _seed_debit_rows(pg_engine, schema, n=2)
        try:
            batches = svc.get_upload_batches(_FAMILY_ID)
            sf_list = [b["source_file"] for b in batches]
            assert _SOURCE_FILE in sf_list
        finally:
            _cleanup_debit(pg_engine, schema)

    def test_batch_dict_has_expected_keys(self, pg_engine, schema):
        svc = _load(pg_engine)
        _seed_debit_rows(pg_engine, schema)
        try:
            batches = svc.get_upload_batches(_FAMILY_ID)
            b = next(b for b in batches if b["source_file"] == _SOURCE_FILE)
            assert {"source_file", "account_key", "table_type",
                    "row_count", "date_from", "date_to",
                    "persons", "uploaded_at"} <= b.keys()
        finally:
            _cleanup_debit(pg_engine, schema)

    def test_row_count_correct(self, pg_engine, schema):
        svc = _load(pg_engine)
        _seed_debit_rows(pg_engine, schema, n=3)
        try:
            batches = svc.get_upload_batches(_FAMILY_ID)
            b = next(b for b in batches if b["source_file"] == _SOURCE_FILE)
            assert b["row_count"] == 3
        finally:
            _cleanup_debit(pg_engine, schema)

    def test_family_isolation(self, pg_engine, schema):
        """Batches from family 2 should not appear in family 7 results."""
        svc = _load(pg_engine)
        _seed_debit_rows(pg_engine, schema, family_id=2)
        try:
            batches = svc.get_upload_batches(7)
            sf_list = [b["source_file"] for b in batches]
            assert _SOURCE_FILE not in sf_list
        finally:
            _cleanup_debit(pg_engine, schema)


# ── Integration: reassign_persons ────────────────────────────────────────────

class TestReassignPersons:
    def test_reassign_updates_person_array(self, pg_engine, schema):
        svc = _load(pg_engine)
        _seed_debit_rows(pg_engine, schema, n=2, person_ids=[1])
        try:
            svc.reassign_persons(
                source_file=_SOURCE_FILE,
                account_key=_ACCOUNT_KEY,
                table_type="debit",
                new_person_ids=[2, 3],
                family_id=_FAMILY_ID,
            )
            with pg_engine.connect() as conn:
                rows = conn.execute(text(f"""
                    SELECT DISTINCT person FROM {schema}.transactions_debit
                    WHERE source_file = :sf AND account_key = :ak AND family_id = :fid
                """), {"sf": _SOURCE_FILE, "ak": _ACCOUNT_KEY, "fid": _FAMILY_ID}).fetchall()
            # All rows should now have person = {2,3}
            for row in rows:
                assert sorted(row[0]) == [2, 3]
        finally:
            _cleanup_debit(pg_engine, schema)


# ── Integration: delete_batch ────────────────────────────────────────────────

class TestDeleteBatch:
    def test_delete_returns_row_count(self, pg_engine, schema):
        svc = _load(pg_engine)
        _seed_debit_rows(pg_engine, schema, n=3)
        try:
            deleted = svc.delete_batch(
                source_file=_SOURCE_FILE,
                account_key=_ACCOUNT_KEY,
                table_type="debit",
                family_id=_FAMILY_ID,
            )
            assert deleted == 3
        finally:
            _cleanup_debit(pg_engine, schema)

    def test_delete_removes_rows_from_db(self, pg_engine, schema):
        svc = _load(pg_engine)
        _seed_debit_rows(pg_engine, schema, n=2)
        svc.delete_batch(_SOURCE_FILE, _ACCOUNT_KEY, "debit", _FAMILY_ID)
        with pg_engine.connect() as conn:
            count = conn.execute(text(f"""
                SELECT COUNT(*) FROM {schema}.transactions_debit
                WHERE source_file = :sf AND account_key = :ak AND family_id = :fid
            """), {"sf": _SOURCE_FILE, "ak": _ACCOUNT_KEY, "fid": _FAMILY_ID}).scalar()
        assert count == 0

    def test_delete_nonexistent_batch_returns_zero(self, pg_engine, schema):
        svc = _load(pg_engine)
        deleted = svc.delete_batch("no_such_file.csv", "no_key", "debit", _FAMILY_ID)
        assert deleted == 0

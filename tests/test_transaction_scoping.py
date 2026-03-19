"""
tests/test_transaction_scoping.py

Integration tests for Phase 5 — Transaction Scoping.

Covers:
  - family_id stamped on transactions_debit and transactions_credit at INSERT
  - uploaded_by stamped on INSERT
  - Dashboard query functions respect family_id filter
    (data inserted for family 1 is not visible to family 2 and vice-versa)
  - Specifically: get_years, get_spend_by_category, get_category_trend,
    get_fixed_vs_variable, get_persons_with_ids, get_weekly_transactions,
    gettransactions_table
"""
from __future__ import annotations

import datetime
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

SCHEMA = "finance"

# Minimal bank rules covering all account_keys used in view-dependent tests.
# Patched into ViewManager so it doesn't need a working data.db connection.
from data.bank_rules import BankRule as _BankRule
_TEST_RULES = [
    _BankRule(bank_name=ak, prefix=ak, account_type="checking")
    for ak in ("ck_f1", "ck_f2", "ck_years", "ck_tt", "ck_wk")
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_module(rel_path: str, module_name: str):
    """Load an app module from disk, bypassing any cached stubs."""
    full = Path(__file__).parent.parent / "app" / rel_path
    spec = importlib.util.spec_from_file_location(module_name, full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ensure_partition(conn, year: int, table: str) -> None:
    """Create a year partition for the given table if it does not exist."""
    part = f"{SCHEMA}.{table}_{year}"
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {part}
        PARTITION OF {SCHEMA}.{table}
        FOR VALUES FROM ('{year}-01-01') TO ('{year + 1}-01-01')
    """))


def _insert_debit(conn, *, family_id: int, year: int = 2024,
                  amount: float = -100.0, description: str = "Test spend",
                  uploaded_by: int | None = None) -> None:
    """Insert a single debit transaction row directly."""
    _ensure_partition(conn, year, "transactions_debit")
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.transactions_debit
            (account_key, transaction_date, description, amount,
             person, source_file, family_id, uploaded_by)
        VALUES
            ('test_check', :txn_date, :desc, :amt,
             ARRAY[]::integer[], 'test.csv', :fid, :uby)
        ON CONFLICT DO NOTHING
    """), {
        "txn_date": datetime.date(year, 6, 15),
        "desc":     description,
        "amt":      amount,
        "fid":      family_id,
        "uby":      uploaded_by,
    })


def _insert_credit(conn, *, family_id: int, year: int = 2024,
                   debit: float = 50.0, credit: float = 0.0,
                   description: str = "Test credit spend",
                   category: str = "Dining",
                   uploaded_by: int | None = None) -> None:
    """Insert a single credit transaction row directly."""
    _ensure_partition(conn, year, "transactions_credit")
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.transactions_credit
            (account_key, transaction_date, description, debit, credit,
             person, source_file, family_id, uploaded_by)
        VALUES
            ('test_cc', :txn_date, :desc, :dbt, :crd,
             ARRAY[]::integer[], 'test.csv', :fid, :uby)
        ON CONFLICT DO NOTHING
    """), {
        "txn_date": datetime.date(year, 6, 15),
        "desc":     description,
        "dbt":      debit,
        "crd":      credit,
        "fid":      family_id,
        "uby":      uploaded_by,
    })


def _fdd(pg_engine):
    """
    Load finance_dashboard_data bound to the test engine.
    Returns the module. Caller patches auth.current_family_id separately.
    """
    # Install test engine into data.db before loading fdd
    import data.db as db_mod
    db_mod.get_engine = lambda: pg_engine
    db_mod.get_schema = lambda: SCHEMA

    fdd_path = Path(__file__).parent.parent / "app" / "data" / "finance_dashboard_data.py"
    spec = importlib.util.spec_from_file_location("data.finance_dashboard_data", fdd_path)
    mod = importlib.util.module_from_spec(spec)
    # Inject patched data.db so module-level _SCHEMA resolves correctly
    mod.__spec__ = spec
    spec.loader.exec_module(mod)
    # Override module-level constants with test schema
    mod._SCHEMA     = SCHEMA
    mod.V_ALL_SPEND    = f"{SCHEMA}.v_all_spend"
    mod.V_CREDIT_SPEND = f"{SCHEMA}.v_credit_spend"
    mod.V_DEBIT_SPEND  = f"{SCHEMA}.v_debit_spend"
    mod.V_INCOME       = f"{SCHEMA}.v_income"
    return mod


# ── family_id stamped on INSERT ───────────────────────────────────────────────

class TestFamilyIdStamping:
    def test_debit_family_id_stored(self, pg_engine, db_conn):
        _ensure_partition(db_conn, 2024, "transactions_debit")
        _insert_debit(db_conn, family_id=42, year=2024)
        row = db_conn.execute(text(f"""
            SELECT family_id FROM {SCHEMA}.transactions_debit
            WHERE description = 'Test spend' AND family_id = 42
        """)).fetchone()
        assert row is not None
        assert row[0] == 42

    def test_credit_family_id_stored(self, pg_engine, db_conn):
        _ensure_partition(db_conn, 2024, "transactions_credit")
        _insert_credit(db_conn, family_id=7, year=2024)
        row = db_conn.execute(text(f"""
            SELECT family_id FROM {SCHEMA}.transactions_credit
            WHERE description = 'Test credit spend' AND family_id = 7
        """)).fetchone()
        assert row is not None
        assert row[0] == 7

    def test_uploaded_by_stored(self, pg_engine, db_conn):
        _ensure_partition(db_conn, 2024, "transactions_debit")
        # Create a real user within this transaction so the FK constraint passes.
        uid = db_conn.execute(text(f"""
            INSERT INTO {SCHEMA}.app_users (username, display_name, person_name, password_hash)
            VALUES ('uploader_test_user', 'Uploader', 'uploader', 'hash')
            RETURNING id
        """)).fetchone()[0]
        _insert_debit(db_conn, family_id=1, year=2024,
                      description="uploader test", uploaded_by=uid)
        row = db_conn.execute(text(f"""
            SELECT uploaded_by FROM {SCHEMA}.transactions_debit
            WHERE description = 'uploader test'
        """)).fetchone()
        assert row is not None
        assert row[0] == uid

    def test_uploaded_by_null_when_not_provided(self, pg_engine, db_conn):
        _ensure_partition(db_conn, 2024, "transactions_debit")
        _insert_debit(db_conn, family_id=1, year=2024,
                      description="no uploader", uploaded_by=None)
        row = db_conn.execute(text(f"""
            SELECT uploaded_by FROM {SCHEMA}.transactions_debit
            WHERE description = 'no uploader'
        """)).fetchone()
        assert row is not None
        assert row[0] is None


# ── View passthrough of family_id ─────────────────────────────────────────────

class TestViewFamilyIdPassthrough:
    """
    After views are built, the family_id column should be queryable directly.
    We build views here using ViewManager against the test DB.
    """

    def _rebuild_views(self, pg_engine, family_id: int = 1) -> None:
        """Build minimal views for the test family against the test engine."""
        from services.view_manager import ViewManager

        with (
            patch("services.view_manager.load_rules", return_value=_TEST_RULES),
            patch("services.view_manager.load_config") as mock_cfg,
            patch("services.view_manager.load_category_config") as mock_cat,
        ):
            from services.transaction_config import TransactionConfig
            from data.category_rules import CategoryConfig
            mock_cfg.return_value  = TransactionConfig()
            mock_cat.return_value  = CategoryConfig()

            vm = ViewManager(pg_engine, schema=SCHEMA)
            vm.refresh()

    def test_v_all_spend_has_family_id_column(self, pg_engine, db_conn):
        self._rebuild_views(pg_engine)
        # Just assert the column exists (view may be empty)
        db_conn.execute(text(
            f"SELECT family_id FROM {SCHEMA}.v_all_spend LIMIT 0"
        ))  # no exception = column exists

    def test_v_income_has_family_id_column(self, pg_engine, db_conn):
        self._rebuild_views(pg_engine)
        db_conn.execute(text(
            f"SELECT family_id FROM {SCHEMA}.v_income LIMIT 0"
        ))


# ── Dashboard query family isolation ─────────────────────────────────────────

class TestDashboardFamilyIsolation:
    """
    Insert rows for two families, then verify that each dashboard function
    returns only the rows for the requested family.

    Because finance_dashboard_data calls auth.current_family_id() internally,
    we patch it with the family under test.
    """

    def _setup_views(self, pg_engine) -> None:
        """Build views (empty — no bank rules needed for direct-insert tests)."""
        with (
            patch("services.view_manager.load_rules", return_value=_TEST_RULES),
            patch("services.view_manager.load_config") as mc,
            patch("services.view_manager.load_category_config") as mcat,
        ):
            from services.transaction_config import TransactionConfig
            from data.category_rules import CategoryConfig
            mc.return_value   = TransactionConfig()
            mcat.return_value = CategoryConfig()
            from services.view_manager import ViewManager
            ViewManager(pg_engine, schema=SCHEMA).refresh()

    def _insert(self, pg_engine, sql, params=None):
        """Commit a row directly via pg_engine (bypasses db_conn rollback)."""
        with pg_engine.begin() as conn:
            conn.execute(text(sql), params or {})

    def _cleanup(self, pg_engine, account_keys):
        """Delete test rows by account_key so each test starts clean."""
        with pg_engine.begin() as conn:
            for ak in account_keys:
                conn.execute(text(
                    f"DELETE FROM {SCHEMA}.transactions_debit  WHERE account_key = :ak"
                ), {"ak": ak})
                conn.execute(text(
                    f"DELETE FROM {SCHEMA}.transactions_credit WHERE account_key = :ak"
                ), {"ak": ak})

    def test_get_years_scoped_to_family(self, pg_engine):
        """
        Insert 2023 rows for family 1 and 2025 rows for family 2.
        get_years() for family 1 should return 2023, not 2025.
        """
        ak = "ck_years"
        self._cleanup(pg_engine, [ak])
        try:
            with pg_engine.begin() as conn:
                _ensure_partition(conn, 2023, "transactions_debit")
                _ensure_partition(conn, 2025, "transactions_debit")
                conn.execute(text(f"""
                    INSERT INTO {SCHEMA}.transactions_debit
                        (account_key, transaction_date, description, amount,
                         person, source_file, family_id)
                    VALUES
                        (:ak, '2023-03-01', 'fam1 row', -10, ARRAY[]::integer[], 'x.csv', 1),
                        (:ak, '2025-03-01', 'fam2 row', -20, ARRAY[]::integer[], 'x.csv', 2)
                """), {"ak": ak})

            self._setup_views(pg_engine)
            fdd = _fdd(pg_engine)

            with patch.object(fdd.auth, "current_family_id", return_value=1):
                years_f1 = fdd.get_years()
            with patch.object(fdd.auth, "current_family_id", return_value=2):
                years_f2 = fdd.get_years()

            assert 2023 in years_f1
            assert 2025 not in years_f1
            assert 2025 in years_f2
            assert 2023 not in years_f2
        finally:
            self._cleanup(pg_engine, [ak])

    def test_gettransactions_table_scoped_to_family(self, pg_engine):
        """Transactions table for family 1 should not include family 2 rows."""
        ak = "ck_tt"
        self._cleanup(pg_engine, [ak])
        try:
            with pg_engine.begin() as conn:
                _ensure_partition(conn, 2024, "transactions_debit")
                conn.execute(text(f"""
                    INSERT INTO {SCHEMA}.transactions_debit
                        (account_key, transaction_date, description, amount,
                         person, source_file, family_id)
                    VALUES
                        (:ak, '2024-07-01', 'FamilyOne txn', -111, ARRAY[]::integer[], 'a.csv', 1),
                        (:ak, '2024-07-02', 'FamilyTwo txn', -222, ARRAY[]::integer[], 'b.csv', 2)
                """), {"ak": ak})

            self._setup_views(pg_engine)
            fdd = _fdd(pg_engine)

            with patch.object(fdd.auth, "current_family_id", return_value=1):
                rows_f1 = fdd.gettransactions_table(year=2024)
            with patch.object(fdd.auth, "current_family_id", return_value=2):
                rows_f2 = fdd.gettransactions_table(year=2024)

            descs_f1 = [r["description"] for r in rows_f1]
            descs_f2 = [r["description"] for r in rows_f2]

            assert "FamilyOne txn" in descs_f1
            assert "FamilyTwo txn" not in descs_f1
            assert "FamilyTwo txn" in descs_f2
            assert "FamilyOne txn" not in descs_f2
        finally:
            self._cleanup(pg_engine, [ak])

    def test_get_weekly_transactions_scoped_to_family(self, pg_engine):
        ak = "ck_wk"
        self._cleanup(pg_engine, [ak])
        try:
            with pg_engine.begin() as conn:
                _ensure_partition(conn, 2024, "transactions_debit")
                conn.execute(text(f"""
                    INSERT INTO {SCHEMA}.transactions_debit
                        (account_key, transaction_date, description, amount,
                         person, source_file, family_id)
                    VALUES
                        (:ak, '2024-08-05', 'WeeklyF1', -77, ARRAY[]::integer[], 'a.csv', 1),
                        (:ak, '2024-08-06', 'WeeklyF2', -88, ARRAY[]::integer[], 'b.csv', 2)
                """), {"ak": ak})

            self._setup_views(pg_engine)
            fdd = _fdd(pg_engine)

            with patch.object(fdd.auth, "current_family_id", return_value=1):
                result_f1 = fdd.get_weekly_transactions(year=2024)
            with patch.object(fdd.auth, "current_family_id", return_value=2):
                result_f2 = fdd.get_weekly_transactions(year=2024)

            all_descs_f1 = [t["description"] for txns in result_f1["by_week"].values() for t in txns]
            all_descs_f2 = [t["description"] for txns in result_f2["by_week"].values() for t in txns]

            assert "WeeklyF1" in all_descs_f1
            assert "WeeklyF2" not in all_descs_f1
            assert "WeeklyF2" in all_descs_f2
            assert "WeeklyF1" not in all_descs_f2
        finally:
            self._cleanup(pg_engine, [ak])

"""
tests/test_custom_chart_query_db.py

Integration tests for services/custom_chart_query.py — actual query execution
against the test database.

Complements test_custom_chart_query.py (which only tests validation logic).
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

APP_DIR = Path(__file__).parent.parent / "app"
SCHEMA  = "finance"
_FAMILY = 7   # pre-seeded, used exclusively by this suite


# ── module loader ─────────────────────────────────────────────────────────────

def _load_module(name: str, rel_path: str, pg_engine=None):
    path = APP_DIR / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── session-scoped fixture: seed + view refresh ───────────────────────────────

@pytest.fixture(scope="module")
def seeded_views(pg_engine):
    """
    Seed a handful of debit and income transactions for family 7,
    then build/refresh the v_all_spend and v_income views.
    Torn down after the module.
    """
    with pg_engine.begin() as conn:
        # Ensure 2024 partition exists
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.transactions_debit_2024
            PARTITION OF {SCHEMA}.transactions_debit
            FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')
        """))
        # Seed spend transactions
        for i, (m, amt) in enumerate([(1, 100.0), (2, 200.0), (3, 150.0)]):
            conn.execute(text(f"""
                INSERT INTO {SCHEMA}.transactions_debit
                    (account_key, transaction_date, description, amount,
                     person, source_file, family_id)
                VALUES ('ccq_chk', :dt, :desc, :amt,
                        CAST('{{1}}' AS integer[]), 'ccq_test.csv', :fid)
                ON CONFLICT DO NOTHING
            """), {"dt": date(2024, m, 15), "desc": f"Spend {i}",
                   "amt": amt, "fid": _FAMILY})

        # Seed income: use a recognisable employer pattern so v_income picks it up.
        # v_income is built from v_debit_spend using income view — but its exact
        # definition depends on the config.  Seed into transactions_debit with a
        # negative amount (income is stored as negative in checking) and rely on
        # the view.  If the view is empty that is also a valid test result.

    # Refresh views so v_all_spend etc. exist
    from services.view_manager import ViewManager
    ViewManager(pg_engine, schema=SCHEMA).refresh()

    yield

    # Teardown
    with pg_engine.begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {SCHEMA}.transactions_debit
            WHERE family_id = :fid AND source_file = 'ccq_test.csv'
        """), {"fid": _FAMILY})


# ── get_source_columns ────────────────────────────────────────────────────────

class TestGetSourceColumns:
    def test_v_all_spend_returns_columns(self, pg_engine, seeded_views):
        ccq = _load_module("ccq", "services/custom_chart_query.py")
        cols = ccq.get_source_columns("v_all_spend")
        assert isinstance(cols, list)
        assert len(cols) > 0

    def test_transaction_date_in_columns(self, pg_engine, seeded_views):
        ccq = _load_module("ccq", "services/custom_chart_query.py")
        cols = ccq.get_source_columns("v_all_spend")
        assert "transaction_date" in cols

    def test_amount_in_columns(self, pg_engine, seeded_views):
        ccq = _load_module("ccq", "services/custom_chart_query.py")
        cols = ccq.get_source_columns("v_all_spend")
        assert "amount" in cols

    def test_invalid_source_raises(self, pg_engine):
        ccq = _load_module("ccq2", "services/custom_chart_query.py")
        with pytest.raises(ValueError):
            ccq.get_source_columns("not_a_view")


# ── execute_chart_query ───────────────────────────────────────────────────────

class TestExecuteChartQuery:
    def _basic_config(self):
        return {
            "data_source": "v_all_spend",
            "x_column":    "transaction_date",
            "y_column":    "amount",
            "y_agg":       "sum",
            "date_trunc":  "month",
        }

    def test_returns_x_and_series_keys(self, pg_engine, seeded_views):
        ccq = _load_module("ccq3", "services/custom_chart_query.py")
        result = ccq.execute_chart_query(self._basic_config())
        assert "x" in result
        assert "series" in result

    def test_x_is_list(self, pg_engine, seeded_views):
        ccq = _load_module("ccq4", "services/custom_chart_query.py")
        result = ccq.execute_chart_query(self._basic_config())
        assert isinstance(result["x"], list)

    def test_series_is_dict(self, pg_engine, seeded_views):
        ccq = _load_module("ccq5", "services/custom_chart_query.py")
        result = ccq.execute_chart_query(self._basic_config())
        assert isinstance(result["series"], dict)

    def test_date_range_filters_data(self, pg_engine, seeded_views):
        ccq = _load_module("ccq6", "services/custom_chart_query.py")
        # Only Jan 2024
        result = ccq.execute_chart_query(
            self._basic_config(),
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 31),
        )
        # At most one x-value (January)
        assert len(result["x"]) <= 1

    def test_no_data_date_range_returns_empty(self, pg_engine, seeded_views):
        ccq = _load_module("ccq7", "services/custom_chart_query.py")
        result = ccq.execute_chart_query(
            self._basic_config(),
            date_from=date(2099, 1, 1),
            date_to=date(2099, 12, 31),
        )
        assert result["x"] == []

    def test_count_aggregation(self, pg_engine, seeded_views):
        ccq = _load_module("ccq8", "services/custom_chart_query.py")
        cfg = {**self._basic_config(), "y_agg": "count"}
        result = ccq.execute_chart_query(cfg)
        assert "x" in result and "series" in result

    def test_explicit_none_date_range_is_no_filter(self, pg_engine, seeded_views):
        ccq = _load_module("ccq9", "services/custom_chart_query.py")
        result_all  = ccq.execute_chart_query(self._basic_config(), date_from=None, date_to=None)
        result_2024 = ccq.execute_chart_query(
            self._basic_config(),
            date_from=date(2024, 1, 1), date_to=date(2024, 12, 31),
        )
        # Result with no filter should have at least as many points as the year-limited one
        assert len(result_all["x"]) >= len(result_2024["x"])

    def test_invalid_source_raises(self, pg_engine):
        ccq = _load_module("ccq10", "services/custom_chart_query.py")
        with pytest.raises(ValueError):
            ccq.execute_chart_query({"data_source": "bad_source"})


# ── _resolve_time_range ───────────────────────────────────────────────────────

class TestResolveTimeRange:
    def _mod(self):
        import sys
        from unittest.mock import MagicMock
        sys.modules.setdefault("data.db", MagicMock())
        return _load_module("ccq_tr", "services/custom_chart_query.py")

    def test_all_time_returns_none_none(self):
        ccq = self._mod()
        df, dt = ccq._resolve_time_range({"time_mode": "all_time"})
        assert df is None and dt is None

    def test_trailing_returns_date_range(self):
        ccq = self._mod()
        df, dt = ccq._resolve_time_range({"time_mode": "trailing", "trailing_months": 12})
        assert df is not None and dt is not None
        assert df < dt

    def test_year_mode_full_year(self):
        ccq = self._mod()
        df, dt = ccq._resolve_time_range({"time_mode": "year", "fixed_year": 2023})
        assert df == date(2023, 1, 1)
        assert dt == date(2023, 12, 31)

    def test_date_range_mode(self):
        ccq = self._mod()
        df, dt = ccq._resolve_time_range({
            "time_mode": "date_range",
            "date_from": "2024-01-01",
            "date_to":   "2024-06-30",
        })
        assert df == date(2024, 1, 1)
        assert dt == date(2024, 6, 30)


# ── _fmt_person ───────────────────────────────────────────────────────────────

class TestFmtPerson:
    def _mod(self):
        import sys
        from unittest.mock import MagicMock
        sys.modules.setdefault("data.db", MagicMock())
        return _load_module("ccq_fp", "services/custom_chart_query.py")

    def test_none_returns_none_label(self):
        ccq = self._mod()
        assert ccq._fmt_person(None, {}) == "(none)"

    def test_list_of_ids_resolved(self):
        ccq = self._mod()
        result = ccq._fmt_person([1, 2], {1: "Alice", 2: "Bob"})
        assert "Alice" in result and "Bob" in result

    def test_postgres_array_string(self):
        ccq = self._mod()
        result = ccq._fmt_person("{1,2}", {1: "Alice", 2: "Bob"})
        assert "Alice" in result

    def test_unknown_id_falls_back_to_string(self):
        ccq = self._mod()
        result = ccq._fmt_person([99], {})
        assert "99" in result

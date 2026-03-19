"""
tests/test_finance_dashboard_data_db.py

Integration tests for data/finance_dashboard_data.py — query functions that
hit the actual database.

Complements test_finance_dashboard_data.py (which only tests pure logic with
mocked _q).  All functions here use `_family_filter()` → `auth.current_family_id()`,
so that is patched to return our test family_id.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

APP_DIR = Path(__file__).parent.parent / "app"
SCHEMA  = "finance"
_FAMILY = 42   # pre-seeded, exclusive to this test suite


# ── module loader ─────────────────────────────────────────────────────────────

def _load_fdd(pg_engine):
    """Load finance_dashboard_data fresh, bound to the test engine."""
    path = APP_DIR / "data" / "finance_dashboard_data.py"
    spec = importlib.util.spec_from_file_location("data.finance_dashboard_data", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── session-scoped fixture: seed + view refresh ───────────────────────────────

@pytest.fixture(scope="module")
def seeded_dashboard_data(pg_engine):
    """
    Seed spend transactions and a bank rule for family 42, then refresh views.
    Torn down after the module.
    """
    import json

    with pg_engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.transactions_debit_2023
            PARTITION OF {SCHEMA}.transactions_debit
            FOR VALUES FROM ('2023-01-01') TO ('2024-01-01')
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.transactions_debit_2024
            PARTITION OF {SCHEMA}.transactions_debit
            FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')
        """))

        # Seed a bank rule so ViewManager includes fdd_chk in the views
        bank_rule = {
            "bank_name": "FDD Test Bank",
            "prefix": "fdd_chk",
            "account_type": "checking",
            "match_field": "filename",
            "match_type": "contains",
            "match_value": "fdd_seed",
        }
        conn.execute(text(f"""
            INSERT INTO {SCHEMA}.app_config_bank_rules (family_id, data, updated_at)
            VALUES (:fid, CAST(:data AS jsonb), NOW())
            ON CONFLICT (family_id) DO UPDATE
                SET data = CAST(:data AS jsonb), updated_at = NOW()
        """), {"fid": _FAMILY, "data": json.dumps({"rules": [bank_rule]})})

        # Seed spend rows: Jan–Mar 2024 — negative amounts = outflows (v_debit_spend)
        for m, amt in [(1, -300.0), (2, -250.0), (3, -400.0)]:
            conn.execute(text(f"""
                INSERT INTO {SCHEMA}.transactions_debit
                    (account_key, transaction_date, description, amount,
                     person, source_file, family_id)
                VALUES ('fdd_chk', :dt, :desc, :amt,
                        CAST('{{1}}' AS integer[]), 'fdd_seed.csv', :fid)
                ON CONFLICT DO NOTHING
            """), {"dt": date(2024, m, 10), "desc": f"Spend {m}",
                   "amt": amt, "fid": _FAMILY})

        # Seed one 2023 row so get_years returns multiple years
        conn.execute(text(f"""
            INSERT INTO {SCHEMA}.transactions_debit
                (account_key, transaction_date, description, amount,
                 person, source_file, family_id)
            VALUES ('fdd_chk', '2023-06-15', 'Old Spend', -100.0,
                    CAST('{{1}}' AS integer[]), 'fdd_seed_2023.csv', :fid)
            ON CONFLICT DO NOTHING
        """), {"fid": _FAMILY})

    from services.view_manager import ViewManager
    ViewManager(pg_engine, schema=SCHEMA).refresh()

    yield

    with pg_engine.begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {SCHEMA}.transactions_debit
            WHERE family_id = :fid AND source_file IN ('fdd_seed.csv','fdd_seed_2023.csv')
        """), {"fid": _FAMILY})
        conn.execute(text(f"""
            DELETE FROM {SCHEMA}.app_config_bank_rules WHERE family_id = :fid
        """), {"fid": _FAMILY})


# ── context manager: patch _family_filter on the fdd module ──────────────────

def _family_ctx(fdd):
    """Patch _family_filter on the loaded fdd module to return _FAMILY."""
    return patch.object(
        fdd, "_family_filter",
        return_value=("AND family_id = :_fid", {"_fid": _FAMILY}),
    )


# ── get_years ─────────────────────────────────────────────────────────────────

class TestGetYears:
    def test_returns_list_of_ints(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            years = fdd.get_years()
        assert isinstance(years, list)
        assert all(isinstance(y, int) for y in years)

    def test_includes_seeded_years(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            years = fdd.get_years()
        assert 2024 in years
        assert 2023 in years

    def test_ordered_descending(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            years = fdd.get_years()
        assert years == sorted(years, reverse=True)

    def test_empty_family_returns_current_year(self, pg_engine, seeded_dashboard_data):
        """Family with no transactions falls back to current year."""
        from datetime import datetime
        fdd = _load_fdd(pg_engine)
        with patch.object(fdd, "_family_filter",
                          return_value=("AND family_id = :_fid", {"_fid": 999_999})):
            years = fdd.get_years()
        assert years == [datetime.now().year]


# ── get_yearly_kpi ────────────────────────────────────────────────────────────

class TestGetYearlyKpi:
    def test_returns_spend_income_net(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            kpi = fdd.get_yearly_kpi(2024)
        assert {"spend", "income", "net"} <= kpi.keys()

    def test_spend_matches_seeded_total(self, pg_engine, seeded_dashboard_data):
        """Seeded spend for 2024: 300 + 250 + 400 = 950."""
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            kpi = fdd.get_yearly_kpi(2024)
        assert kpi["spend"] == pytest.approx(950.0, abs=0.01)

    def test_empty_year_returns_zeros(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            kpi = fdd.get_yearly_kpi(1999)
        assert kpi["spend"] == 0.0
        assert kpi["income"] == 0.0

    def test_net_equals_income_minus_spend(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            kpi = fdd.get_yearly_kpi(2024)
        assert kpi["net"] == pytest.approx(kpi["income"] - kpi["spend"], abs=0.01)


# ── get_monthly_spend_series ──────────────────────────────────────────────────

class TestGetMonthlySpendSeries:
    def test_returns_expected_keys(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            result = fdd.get_monthly_spend_series(2024)
        assert {"months", "spend", "income", "budget"} <= result.keys()

    def test_months_is_12_labels(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            result = fdd.get_monthly_spend_series(2024)
        assert len(result["months"]) == 12
        assert result["months"][0] == "Jan"

    def test_spend_is_12_values(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            result = fdd.get_monthly_spend_series(2024)
        assert len(result["spend"]) == 12

    def test_jan_spend_correct(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            result = fdd.get_monthly_spend_series(2024)
        # January (index 0) should have 300.0 seeded
        assert result["spend"][0] == pytest.approx(300.0, abs=0.01)

    def test_unseeded_months_are_zero(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            result = fdd.get_monthly_spend_series(2024)
        # April–December (indices 3-11) should be 0 (no data seeded)
        for i in range(3, 12):
            assert result["spend"][i] == 0.0

    def test_budget_is_none_for_empty_months(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            result = fdd.get_monthly_spend_series(2024)
        # Months with no spend AND no income should have None budget
        for i in range(3, 12):
            assert result["budget"][i] is None


# ── get_persons ───────────────────────────────────────────────────────────────

class TestGetPersons:
    def test_returns_list(self, pg_engine, seeded_dashboard_data):
        fdd = _load_fdd(pg_engine)
        with _family_ctx(fdd):
            persons = fdd.get_persons()
        assert isinstance(persons, list)

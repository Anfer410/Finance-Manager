"""
tests/test_loan_service.py

Tests for services/loan_service.py.

Unit tests (no DB): amortization math, stats, payoff, calculate_loan, helpers.
Integration tests (pg_engine): save_loan, load_loans, delete_loan.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

for _mod in ("nicegui", "nicegui.app"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from services.loan_service import (
    LoanRecord,
    _add_months,
    _months_between,
    compute_amortization,
    compute_stats,
    payoff_with_extra,
    calculate_loan,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _simple_loan(**overrides) -> LoanRecord:
    defaults = dict(
        name="Test Loan",
        loan_type="personal",
        rate_type="fixed",
        interest_rate=6.0,
        original_principal=10_000.0,
        term_months=24,
        start_date=date(2023, 1, 1),
        monthly_payment=443.21,
        current_balance=8_000.0,
        balance_as_of=date(2024, 1, 1),
    )
    defaults.update(overrides)
    return LoanRecord(**defaults)


# ── _add_months ────────────────────────────────────────────────────────────────

class TestAddMonths:
    def test_add_zero(self):
        assert _add_months(date(2024, 3, 15), 0) == date(2024, 3, 15)

    def test_add_crosses_year(self):
        assert _add_months(date(2024, 11, 1), 2) == date(2025, 1, 1)

    def test_add_clamps_day_to_month_end(self):
        # Jan 31 + 1 month → Feb 29 (2024 is leap year)
        assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)

    def test_add_12_months_same_day(self):
        assert _add_months(date(2023, 6, 15), 12) == date(2024, 6, 15)


# ── _months_between ───────────────────────────────────────────────────────────

class TestMonthsBetween:
    def test_same_date_is_zero(self):
        d = date(2024, 6, 1)
        assert _months_between(d, d) == 0

    def test_one_year(self):
        assert _months_between(date(2023, 1, 1), date(2024, 1, 1)) == 12

    def test_partial_year(self):
        assert _months_between(date(2024, 1, 1), date(2024, 7, 1)) == 6

    def test_negative_clamps_to_zero(self):
        assert _months_between(date(2024, 6, 1), date(2023, 1, 1)) == 0


# ── compute_amortization ──────────────────────────────────────────────────────

class TestComputeAmortization:
    def test_returns_rows(self):
        rows = compute_amortization(_simple_loan())
        assert len(rows) > 0

    def test_first_row_month_num_is_one(self):
        rows = compute_amortization(_simple_loan())
        assert rows[0].month_num == 1

    def test_balance_decreases(self):
        rows = compute_amortization(_simple_loan())
        assert rows[-1].balance < rows[0].balance

    def test_final_balance_near_zero(self):
        rows = compute_amortization(_simple_loan())
        assert rows[-1].balance <= 0.01

    def test_zero_rate_loan(self):
        loan = _simple_loan(interest_rate=0.0, monthly_payment=400.0, current_balance=4_000.0)
        rows = compute_amortization(loan)
        # All interest rows should be 0
        assert all(r.interest == 0.0 for r in rows)
        assert rows[-1].balance <= 0.01

    def test_each_row_has_positive_principal(self):
        rows = compute_amortization(_simple_loan())
        assert all(r.principal >= 0 for r in rows)

    def test_interest_plus_principal_equals_payment(self):
        rows = compute_amortization(_simple_loan())
        for r in rows:
            assert abs((r.principal + r.interest) - r.payment) < 0.01

    def test_already_paid_off_loan_returns_empty(self):
        loan = _simple_loan(current_balance=0.0)
        rows = compute_amortization(loan)
        assert rows == []

    def test_insurance_excluded_from_pi_calculation(self):
        """monthly_insurance should reduce P&I payment used in amortization."""
        loan_no_ins = _simple_loan(monthly_payment=500.0, monthly_insurance=0.0)
        loan_with_ins = _simple_loan(monthly_payment=500.0, monthly_insurance=100.0)
        rows_no_ins = compute_amortization(loan_no_ins)
        rows_with_ins = compute_amortization(loan_with_ins)
        # With insurance, P&I portion is smaller → takes longer to pay off
        assert len(rows_with_ins) >= len(rows_no_ins)


# ── compute_stats ──────────────────────────────────────────────────────────────

class TestComputeStats:
    def test_returns_stats(self):
        stats = compute_stats(_simple_loan())
        assert stats is not None

    def test_payoff_date_is_future(self):
        stats = compute_stats(_simple_loan())
        assert stats.payoff_date > date(2024, 1, 1)

    def test_months_remaining_positive(self):
        stats = compute_stats(_simple_loan())
        assert stats.months_remaining > 0

    def test_equity_pct_in_range(self):
        stats = compute_stats(_simple_loan())
        assert 0.0 <= stats.equity_pct <= 100.0

    def test_daily_interest_positive(self):
        stats = compute_stats(_simple_loan())
        assert stats.daily_interest > 0

    def test_principal_paid(self):
        loan = _simple_loan(original_principal=10_000.0, current_balance=8_000.0)
        stats = compute_stats(loan)
        assert stats.principal_paid == 2_000.0

    def test_amortization_embedded(self):
        stats = compute_stats(_simple_loan())
        assert len(stats.amortization) > 0


# ── payoff_with_extra ─────────────────────────────────────────────────────────

class TestPayoffWithExtra:
    def test_extra_payment_reduces_months(self):
        loan = _simple_loan()
        _, _, months_saved = payoff_with_extra(loan, extra=100.0)
        assert months_saved > 0

    def test_extra_payment_saves_interest(self):
        loan = _simple_loan()
        _, interest_saved, _ = payoff_with_extra(loan, extra=100.0)
        assert interest_saved > 0

    def test_zero_extra_no_savings(self):
        loan = _simple_loan()
        _, interest_saved, months_saved = payoff_with_extra(loan, extra=0.0)
        assert interest_saved == 0.0
        assert months_saved == 0

    def test_new_payoff_date_earlier(self):
        loan = _simple_loan()
        base_stats = compute_stats(loan)
        new_payoff, _, _ = payoff_with_extra(loan, extra=200.0)
        assert new_payoff < base_stats.payoff_date


# ── calculate_loan ────────────────────────────────────────────────────────────

class TestCalculateLoan:
    def test_returns_expected_keys(self):
        result = calculate_loan(100_000, 5.0, 360)
        assert {"monthly_payment", "total_interest", "total_cost", "payoff_date"} <= result.keys()

    def test_monthly_payment_positive(self):
        result = calculate_loan(100_000, 5.0, 360)
        assert result["monthly_payment"] > 0

    def test_total_cost_equals_principal_plus_interest(self):
        result = calculate_loan(50_000, 4.0, 120)
        assert abs(result["total_cost"] - (50_000 + result["total_interest"])) < 0.02

    def test_zero_rate_divides_evenly(self):
        result = calculate_loan(12_000, 0.0, 12)
        assert result["monthly_payment"] == 1_000.0
        assert result["total_interest"] == 0.0

    def test_zero_term_handled(self):
        # Should not raise
        result = calculate_loan(10_000, 5.0, 0)
        assert result["monthly_payment"] >= 0


# ── DB: save / load / delete ──────────────────────────────────────────────────

def _cleanup_loans(pg_engine, schema: str, family_id: int) -> None:
    with pg_engine.begin() as conn:
        conn.execute(text(
            f"DELETE FROM {schema}.app_loans WHERE family_id = :fid"
        ), {"fid": family_id})


class TestLoanDB:
    def test_save_and_load_loan(self, pg_engine, schema):
        from services import loan_service as svc
        loan = _simple_loan(name="DB Test Loan")
        fid = 2  # pre-seeded family
        try:
            loan_id = svc.save_loan(loan, family_id=fid)
            assert isinstance(loan_id, int)
            loans = svc.load_loans(fid)
            names = [l.name for l in loans]
            assert "DB Test Loan" in names
        finally:
            _cleanup_loans(pg_engine, schema, fid)

    def test_save_new_loan_has_no_id(self, pg_engine, schema):
        from services import loan_service as svc
        loan = _simple_loan()
        assert loan.id is None
        fid = 2
        try:
            returned_id = svc.save_loan(loan, family_id=fid)
            assert returned_id > 0
        finally:
            _cleanup_loans(pg_engine, schema, fid)

    def test_update_existing_loan(self, pg_engine, schema):
        from services import loan_service as svc
        fid = 2
        try:
            loan = _simple_loan(name="Before Update")
            loan_id = svc.save_loan(loan, family_id=fid)
            from dataclasses import replace
            updated = replace(loan, id=loan_id, name="After Update")
            svc.save_loan(updated, family_id=fid)
            loans = svc.load_loans(fid)
            names = [l.name for l in loans]
            assert "After Update" in names
            assert "Before Update" not in names
        finally:
            _cleanup_loans(pg_engine, schema, fid)

    def test_delete_loan_removes_from_active(self, pg_engine, schema):
        from services import loan_service as svc
        fid = 7  # different pre-seeded family
        try:
            loan_id = svc.save_loan(_simple_loan(name="ToDelete"), family_id=fid)
            svc.delete_loan(loan_id, family_id=fid)
            loans = svc.load_loans(fid)
            assert not any(l.id == loan_id for l in loans)
        finally:
            _cleanup_loans(pg_engine, schema, fid)

    def test_loans_are_family_scoped(self, pg_engine, schema):
        """A loan in family 2 must not appear when querying family 7."""
        from services import loan_service as svc
        try:
            svc.save_loan(_simple_loan(name="FamIsolation"), family_id=2)
            loans_fam7 = svc.load_loans(7)
            assert not any(l.name == "FamIsolation" for l in loans_fam7)
        finally:
            _cleanup_loans(pg_engine, schema, 2)

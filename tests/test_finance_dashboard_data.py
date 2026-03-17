"""
tests/test_finance_dashboard_data.py

Unit tests for the pure-logic helpers in data/finance_dashboard_data.py
and components/widgets/base.py (RenderContext.build).

These tests do NOT hit the database — _q() is patched with fixture data.
"""

from __future__ import annotations

import sys
import os
from datetime import date
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure app/ is on the path and DB modules are stubbed before any app import
# ---------------------------------------------------------------------------

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Stub data.db at the package level so module-level code in
# finance_dashboard_data (e.g. _SCHEMA = get_schema()) doesn't hit a real DB.
_mock_db = MagicMock()
_mock_db.get_engine.return_value = MagicMock()
_mock_db.get_schema.return_value = "finance"
sys.modules.setdefault("data.db", _mock_db)

import data.finance_dashboard_data as fdd  # noqa: E402  (import after sys.path setup)


# ===========================================================================
# _persons_filter
# ===========================================================================

class TestPersonsFilter:
    def test_none_returns_empty(self):
        clause, params = fdd._persons_filter(None)
        assert clause == ""
        assert params == {}

    def test_empty_list_returns_empty(self):
        clause, params = fdd._persons_filter([])
        assert clause == ""
        assert params == {}

    def test_single_person(self):
        clause, params = fdd._persons_filter([1])
        assert "CAST(:_persons AS integer[])" in clause
        assert params["_persons"] == "{1}"

    def test_multiple_persons(self):
        clause, params = fdd._persons_filter([1, 2, 3])
        assert "CAST(:_persons AS integer[])" in clause
        assert params["_persons"] == "{1,2,3}"

    def test_no_double_colon_in_clause(self):
        """Regression: SQLAlchemy breaks on :name::type syntax."""
        clause, _ = fdd._persons_filter([1])
        assert "::" not in clause, "double-colon cast breaks SQLAlchemy bind params"

    def test_string_ids_are_cast_to_int(self):
        clause, params = fdd._persons_filter(["2", "5"])
        assert params["_persons"] == "{2,5}"


# ===========================================================================
# get_spend_by_person_monthly — full year mode
# ===========================================================================

class TestGetSpendByPersonMonthlyYear:
    _ROWS = [
        ("Alice", 1, 100.0),
        ("Alice", 3, 200.0),
        ("Bob",   1,  50.0),
        ("Bob",   2,  75.0),
    ]

    def test_returns_12_month_labels(self):
        with patch.object(fdd, "_q", return_value=self._ROWS):
            result = fdd.get_spend_by_person_monthly(2025)
        assert result["months"] == fdd.MONTH_LABELS
        assert len(result["months"]) == 12

    def test_alice_values_correct(self):
        with patch.object(fdd, "_q", return_value=self._ROWS):
            result = fdd.get_spend_by_person_monthly(2025)
        alice = result["persons"]["Alice"]
        assert alice[0] == 100.0   # Jan
        assert alice[2] == 200.0   # Mar
        assert alice[1] == 0.0     # Feb — no row

    def test_bob_values_correct(self):
        with patch.object(fdd, "_q", return_value=self._ROWS):
            result = fdd.get_spend_by_person_monthly(2025)
        bob = result["persons"]["Bob"]
        assert bob[0] == 50.0
        assert bob[1] == 75.0
        assert bob[2] == 0.0

    def test_empty_returns_empty_persons(self):
        with patch.object(fdd, "_q", return_value=[]):
            result = fdd.get_spend_by_person_monthly(2025)
        assert result["persons"] == {}
        assert result["months"] == fdd.MONTH_LABELS


# ===========================================================================
# get_spend_by_person_monthly — date range (trailing months) mode
# ===========================================================================

class TestGetSpendByPersonMonthlyDateRange:
    # row layout: (person_name, label, month_start, spend)
    _ROWS = [
        ("Alice", "Jan 25", date(2025, 1, 1), 300.0),
        ("Alice", "Feb 25", date(2025, 2, 1), 400.0),
        ("Bob",   "Jan 25", date(2025, 1, 1), 150.0),
    ]

    def test_month_labels_ordered(self):
        with patch.object(fdd, "_q", return_value=self._ROWS):
            result = fdd.get_spend_by_person_monthly(
                2025,
                date_from=date(2025, 1, 1),
                date_to=date(2025, 3, 1),
            )
        assert result["months"] == ["Jan 25", "Feb 25"]

    def test_alice_values(self):
        with patch.object(fdd, "_q", return_value=self._ROWS):
            result = fdd.get_spend_by_person_monthly(
                2025,
                date_from=date(2025, 1, 1),
                date_to=date(2025, 3, 1),
            )
        assert result["persons"]["Alice"] == [300.0, 400.0]

    def test_bob_missing_month_filled_with_zero(self):
        with patch.object(fdd, "_q", return_value=self._ROWS):
            result = fdd.get_spend_by_person_monthly(
                2025,
                date_from=date(2025, 1, 1),
                date_to=date(2025, 3, 1),
            )
        assert result["persons"]["Bob"] == [150.0, 0.0]

    def test_empty_rows_returns_empty(self):
        with patch.object(fdd, "_q", return_value=[]):
            result = fdd.get_spend_by_person_monthly(
                2025,
                date_from=date(2025, 1, 1),
                date_to=date(2025, 3, 1),
            )
        assert result["months"] == []
        assert result["persons"] == {}


# ===========================================================================
# RenderContext.build
# ===========================================================================

class TestRenderContextBuild:
    """Pure logic — no DB required."""

    @pytest.fixture(autouse=True)
    def _imports(self):
        from components.widgets.base import RenderContext, TimeMode
        self.RenderContext = RenderContext
        self.TimeMode = TimeMode

    def test_default_page_year(self):
        ctx = self.RenderContext.build(2025, None, {}, {})
        assert ctx.year == 2025
        assert ctx.time_mode == self.TimeMode.PAGE_YEAR
        assert ctx.persons is None

    def test_specific_year_override(self):
        ctx = self.RenderContext.build(2025, None, {"time_mode": "year", "year": 2023}, {})
        assert ctx.year == 2023
        assert ctx.time_mode == self.TimeMode.YEAR

    def test_trailing_sets_date_range(self):
        ctx = self.RenderContext.build(
            2025, None, {"time_mode": "trailing", "trailing_months": 6}, {}
        )
        assert ctx.time_mode == self.TimeMode.TRAILING
        assert ctx.trailing_months == 6
        assert ctx.date_from is not None
        assert ctx.date_to is not None
        assert ctx.date_from < ctx.date_to

    def test_all_time(self):
        ctx = self.RenderContext.build(2025, None, {"time_mode": "all_time"}, {})
        assert ctx.time_mode == self.TimeMode.ALL_TIME

    def test_person_override_takes_priority_over_page(self):
        ctx = self.RenderContext.build(2025, [1, 2], {"persons": [3]}, {})
        assert ctx.persons == [3]

    def test_page_persons_used_when_no_widget_override(self):
        ctx = self.RenderContext.build(2025, [1, 2], {}, {})
        assert ctx.persons == [1, 2]

    def test_invalid_time_mode_falls_back_to_page_year(self):
        ctx = self.RenderContext.build(2025, None, {"time_mode": "bogus_value"}, {})
        assert ctx.time_mode == self.TimeMode.PAGE_YEAR

    def test_loan_id_resolved_from_string(self):
        ctx = self.RenderContext.build(2025, None, {"loan_id": "7"}, {})
        assert ctx.loan_id == 7

    def test_loan_id_none_when_absent(self):
        ctx = self.RenderContext.build(2025, None, {}, {})
        assert ctx.loan_id is None

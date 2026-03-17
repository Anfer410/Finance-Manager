"""
tests/test_upload_pipeline.py

Unit tests for services/upload_pipeline.py — pure logic only, no DB.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure app/ is on the path and DB/view modules are stubbed
# ---------------------------------------------------------------------------

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Stub modules that would hit the DB or filesystem at import time
for _mod in ("data.db", "services.raw_table_manager", "services.view_manager", "db_migration"):
    sys.modules.setdefault(_mod, MagicMock())

import pandas as pd
from services.upload_pipeline import (
    _resolve_person,
    suggest_mapping,
    sniff,
    SniffResult,
    ColumnMapping,
)


# ===========================================================================
# _resolve_person
# ===========================================================================

class _FakeRule:
    """Minimal stand-in for BankRule."""
    def __init__(self, member_aliases=None):
        self.member_aliases = member_aliases


class TestResolvePerson:
    def _row(self, **kwargs) -> pd.Series:
        return pd.Series(kwargs)

    def test_no_member_col_returns_default(self):
        rule = _FakeRule(member_aliases={"ALICE": 1})
        result = _resolve_person(self._row(description="foo"), rule, member_col=None, default_person_ids=[7])
        assert result == [7]

    def test_no_aliases_on_rule_returns_default(self):
        rule = _FakeRule(member_aliases=None)
        result = _resolve_person(self._row(member_name="ALICE"), rule, member_col="member_name", default_person_ids=[3])
        assert result == [3]

    def test_no_rule_returns_default(self):
        result = _resolve_person(self._row(member_name="ALICE"), rule=None, member_col="member_name", default_person_ids=[5])
        assert result == [5]

    def test_exact_alias_match(self):
        rule = _FakeRule(member_aliases={"ALICE": 42})
        result = _resolve_person(self._row(member_name="ALICE"), rule, member_col="member_name", default_person_ids=[1])
        assert result == [42]

    def test_alias_match_is_case_insensitive(self):
        rule = _FakeRule(member_aliases={"ALICE": 42})
        result = _resolve_person(self._row(member_name="alice"), rule, member_col="member_name", default_person_ids=[1])
        assert result == [42]

    def test_partial_match_alias_in_value(self):
        # alias "ALICE" found inside raw value "ALICE SMITH"
        rule = _FakeRule(member_aliases={"ALICE": 9})
        result = _resolve_person(self._row(member_name="ALICE SMITH"), rule, member_col="member_name", default_person_ids=[1])
        assert result == [9]

    def test_partial_match_value_in_alias(self):
        # raw value "ALI" found inside alias "ALICE"
        rule = _FakeRule(member_aliases={"ALICE": 9})
        result = _resolve_person(self._row(member_name="ALI"), rule, member_col="member_name", default_person_ids=[1])
        assert result == [9]

    def test_no_match_returns_default(self):
        rule = _FakeRule(member_aliases={"ALICE": 9})
        result = _resolve_person(self._row(member_name="BOB"), rule, member_col="member_name", default_person_ids=[2])
        assert result == [2]

    def test_member_col_not_in_row_returns_default(self):
        rule = _FakeRule(member_aliases={"ALICE": 9})
        result = _resolve_person(self._row(description="foo"), rule, member_col="member_name", default_person_ids=[6])
        assert result == [6]

    def test_result_is_list_of_int(self):
        rule = _FakeRule(member_aliases={"ALICE": "42"})  # alias stored as string
        result = _resolve_person(self._row(member_name="ALICE"), rule, member_col="member_name", default_person_ids=[1])
        assert result == [42]
        assert isinstance(result[0], int)

    def test_empty_default_returned_when_no_match(self):
        rule = _FakeRule(member_aliases={"ALICE": 9})
        result = _resolve_person(self._row(member_name="BOB"), rule, member_col="member_name", default_person_ids=[])
        assert result == []


# ===========================================================================
# Person type normalisation (mirrors UploadPipeline.run logic)
# ===========================================================================

def _normalise_person(person):
    """Extracted inline from UploadPipeline.run for unit-testing."""
    if isinstance(person, int):
        return [person]
    elif not isinstance(person, list):
        return []
    return person


class TestPersonNormalisation:
    """
    Guards the fix for the radio-button bug where e.args returns a string.
    After the fix, the UI casts e.args to int before storing in person_ref,
    but this ensures the pipeline is also robust.
    """

    def test_int_becomes_single_element_list(self):
        assert _normalise_person(3) == [3]

    def test_list_passes_through(self):
        assert _normalise_person([1, 2]) == [1, 2]

    def test_string_becomes_empty_list(self):
        # String "2" should NOT resolve to user 2 — callers must cast to int first.
        assert _normalise_person("2") == []

    def test_none_becomes_empty_list(self):
        assert _normalise_person(None) == []

    def test_empty_list_passes_through(self):
        assert _normalise_person([]) == []


# ===========================================================================
# sniff
# ===========================================================================

class TestSniff:
    def _csv(self, text: str) -> bytes:
        return text.strip().encode()

    def test_detects_header(self):
        csv = self._csv("date,description,amount\n2024-01-01,Coffee,5.00")
        result = sniff(csv)
        assert result.has_header is True
        assert "date" in result.norm_columns

    def test_row_count(self):
        csv = self._csv("date,description,amount\n2024-01-01,A,1\n2024-01-02,B,2")
        result = sniff(csv)
        assert result.row_count == 2

    def test_normalises_column_names(self):
        csv = self._csv("Transaction Date,Description,Amount\n2024-01-01,Coffee,5.00")
        result = sniff(csv)
        assert "transaction_date" in result.norm_columns
        assert "description" in result.norm_columns

    def test_sample_rows(self):
        csv = self._csv("date,amount\n2024-01-01,5.00\n2024-01-02,10.00")
        result = sniff(csv)
        assert len(result.sample_rows) == 2
        assert result.sample_rows[0][0] == "2024-01-01"

    def test_no_header_fallback(self):
        # All columns look like data (dates/numbers)
        csv = self._csv("2024-01-01,5.00,Coffee")
        result = sniff(csv)
        # Should still parse without crashing
        assert result.row_count >= 1


# ===========================================================================
# suggest_mapping
# ===========================================================================

class TestSuggestMapping:
    def _sniff(self, cols: list[str]) -> SniffResult:
        return SniffResult(
            raw_columns=cols,
            norm_columns=cols,
            has_header=True,
            sample_rows=[],
            row_count=1,
        )

    def test_checking_standard_cols(self):
        m = suggest_mapping(self._sniff(["date", "description", "amount"]), "checking")
        assert m.date == "date"
        assert m.description == "description"
        assert m.amount == "amount"
        assert m.debit is None
        assert m.credit is None

    def test_credit_standard_cols(self):
        m = suggest_mapping(self._sniff(["date", "description", "debit", "credit"]), "credit")
        assert m.date == "date"
        assert m.debit == "debit"
        assert m.credit == "credit"
        assert m.amount is None

    def test_fuzzy_date_match(self):
        m = suggest_mapping(self._sniff(["transaction_date", "memo", "amount"]), "checking")
        assert m.date == "transaction_date"

    def test_missing_role_returns_none(self):
        m = suggest_mapping(self._sniff(["date", "description"]), "checking")
        assert m.amount is None

    def test_member_name_detected(self):
        m = suggest_mapping(self._sniff(["date", "description", "amount", "cardholder"]), "checking")
        assert m.member_name == "cardholder"

    def test_missing_required_checking(self):
        m = suggest_mapping(self._sniff(["date", "description"]), "checking")
        missing = m.missing_required("checking")
        assert "amount" in missing

    def test_missing_required_credit(self):
        m = suggest_mapping(self._sniff(["date", "description", "debit"]), "credit")
        missing = m.missing_required("credit")
        assert "credit" in missing

    def test_no_missing_when_all_present_checking(self):
        m = suggest_mapping(self._sniff(["date", "description", "amount"]), "checking")
        assert m.missing_required("checking") == []

    def test_no_missing_when_all_present_credit(self):
        m = suggest_mapping(self._sniff(["date", "description", "debit", "credit"]), "credit")
        assert m.missing_required("credit") == []


# ===========================================================================
# ColumnMapping.dedup_columns
# ===========================================================================

class TestColumnMappingDedupColumns:
    def test_checking_dedup(self):
        m = ColumnMapping(date="date", description="description", amount="amount")
        cols = m.dedup_columns("checking")
        assert cols == ["date", "description", "amount"]

    def test_credit_dedup(self):
        m = ColumnMapping(date="date", description="description", debit="debit", credit="credit")
        cols = m.dedup_columns("credit")
        assert cols == ["date", "debit", "credit", "description"]

    def test_falls_back_to_description_when_all_none(self):
        m = ColumnMapping()
        cols = m.dedup_columns("checking")
        assert cols == ["description"]

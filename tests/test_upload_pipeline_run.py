"""
tests/test_upload_pipeline_run.py

Integration tests for UploadPipeline.run() — the main upload orchestration.

Strategy:
  - Provide a BankRule directly (bypasses filename matching).
  - Mock parse_csv to return a controlled DataFrame.
  - Mock load_archive_enabled → False to skip raw-archive logic.
  - Mock default_view_manager to skip view refresh.
  - Let write_to_consolidated hit the real test DB so we verify actual inserts.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import text

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Stub NiceGUI (not needed at all here, but transitive imports may pull it)
for _mod in ("nicegui", "nicegui.app"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from data.bank_rules import BankRule  # noqa: E402
from services.upload_pipeline import UploadPipeline, ColumnMapping  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

_FAMILY_ID = 2   # pre-seeded in conftest
_SOURCE = "pipeline_run_test.csv"

_DEBIT_RULE = BankRule(
    bank_name="Test Checking",
    prefix="test_chk",
    account_type="checking",
    column_map={"date": "date", "description": "description", "amount": "amount"},
)

_CREDIT_RULE = BankRule(
    bank_name="Test Credit",
    prefix="test_cc",
    account_type="credit",
    column_map={"date": "date", "description": "description", "debit": "debit", "credit": "credit"},
)


def _debit_df(n: int = 2) -> pd.DataFrame:
    return pd.DataFrame({
        "date":        [date(2024, 3, i + 1) for i in range(n)],
        "description": [f"Txn {i}" for i in range(n)],
        "amount":      [float(10 * (i + 1)) for i in range(n)],
    })


def _credit_df(n: int = 2) -> pd.DataFrame:
    return pd.DataFrame({
        "date":        [date(2024, 3, i + 1) for i in range(n)],
        "description": [f"CC Txn {i}" for i in range(n)],
        "debit":       [float(5 * (i + 1)) for i in range(n)],
        "credit":      [0.0] * n,
    })


def _cleanup(pg_engine, schema: str, account_key: str) -> None:
    for tbl in ("transactions_debit", "transactions_credit"):
        with pg_engine.begin() as conn:
            conn.execute(text(
                f"DELETE FROM {schema}.{tbl} "
                f"WHERE family_id = :fid AND source_file = :sf AND account_key = :ak"
            ), {"fid": _FAMILY_ID, "sf": _SOURCE, "ak": account_key})


# ── tests ─────────────────────────────────────────────────────────────────────

class TestUploadPipelineRun:
    def _run(self, rule: BankRule, df: pd.DataFrame, **kwargs) -> object:
        pipeline = UploadPipeline()
        with patch("services.raw_table_manager.parse_csv", return_value=df.copy()), \
             patch("services.config_repo.load_archive_enabled", return_value=False):
            return pipeline.run(
                raw=b"placeholder",
                filename=_SOURCE,
                person=1,
                family_id=_FAMILY_ID,
                uploaded_by=0,
                bank_rule=rule,
                **kwargs,
            )

    # ── happy path ────────────────────────────────────────────────────────────

    def test_run_debit_returns_no_error(self, pg_engine, schema):
        try:
            result = self._run(_DEBIT_RULE, _debit_df())
            assert result.error is None
        finally:
            _cleanup(pg_engine, schema, "test_chk")

    def test_run_debit_inserted_count(self, pg_engine, schema):
        try:
            result = self._run(_DEBIT_RULE, _debit_df(3))
            assert result.inserted == 3
            assert result.total == 3
            assert result.skipped == 0
        finally:
            _cleanup(pg_engine, schema, "test_chk")

    def test_run_debit_rows_in_db(self, pg_engine, schema):
        try:
            self._run(_DEBIT_RULE, _debit_df(2))
            with pg_engine.connect() as conn:
                count = conn.execute(text(
                    f"SELECT COUNT(*) FROM {schema}.transactions_debit "
                    f"WHERE family_id = :fid AND source_file = :sf AND account_key = 'test_chk'"
                ), {"fid": _FAMILY_ID, "sf": _SOURCE}).scalar()
            assert count == 2
        finally:
            _cleanup(pg_engine, schema, "test_chk")

    def test_run_debit_family_id_stamped(self, pg_engine, schema):
        try:
            self._run(_DEBIT_RULE, _debit_df(1))
            with pg_engine.connect() as conn:
                row = conn.execute(text(
                    f"SELECT family_id FROM {schema}.transactions_debit "
                    f"WHERE source_file = :sf AND account_key = 'test_chk' LIMIT 1"
                ), {"sf": _SOURCE}).fetchone()
            assert row is not None and row[0] == _FAMILY_ID
        finally:
            _cleanup(pg_engine, schema, "test_chk")

    def test_run_credit_returns_no_error(self, pg_engine, schema):
        try:
            result = self._run(_CREDIT_RULE, _credit_df())
            assert result.error is None
        finally:
            _cleanup(pg_engine, schema, "test_cc")

    def test_run_credit_rows_in_db(self, pg_engine, schema):
        try:
            self._run(_CREDIT_RULE, _credit_df(2))
            with pg_engine.connect() as conn:
                count = conn.execute(text(
                    f"SELECT COUNT(*) FROM {schema}.transactions_credit "
                    f"WHERE family_id = :fid AND source_file = :sf AND account_key = 'test_cc'"
                ), {"fid": _FAMILY_ID, "sf": _SOURCE}).scalar()
            assert count == 2
        finally:
            _cleanup(pg_engine, schema, "test_cc")

    # ── dedup: second run inserts nothing ─────────────────────────────────────

    def test_run_debit_dedup_on_second_upload(self, pg_engine, schema):
        try:
            df = _debit_df(2)
            self._run(_DEBIT_RULE, df)
            result2 = self._run(_DEBIT_RULE, df)
            # ON CONFLICT DO NOTHING — second run inserts 0
            assert result2.inserted == 0
        finally:
            _cleanup(pg_engine, schema, "test_chk")

    # ── error paths ───────────────────────────────────────────────────────────

    def test_run_no_matching_rule_returns_error(self):
        pipeline = UploadPipeline()
        with patch("data.bank_rules.load_rules", return_value=[]):
            result = pipeline.run(
                raw=b"placeholder",
                filename="unknown_bank_file.csv",
                person=1,
                family_id=_FAMILY_ID,
                uploaded_by=0,
            )
        assert result.error is not None
        assert result.inserted == 0

    def test_run_empty_df_inserts_nothing(self, pg_engine, schema):
        empty_df = pd.DataFrame(columns=["date", "description", "amount"])
        try:
            result = self._run(_DEBIT_RULE, empty_df)
            assert result.inserted == 0
            assert result.total == 0
        finally:
            _cleanup(pg_engine, schema, "test_chk")

    def test_run_bad_csv_parse_returns_error(self):
        pipeline = UploadPipeline()
        with patch("services.raw_table_manager.parse_csv", side_effect=ValueError("bad csv")), \
             patch("services.config_repo.load_archive_enabled", return_value=False):
            result = pipeline.run(
                raw=b"garbage",
                filename=_SOURCE,
                person=1,
                family_id=_FAMILY_ID,
                uploaded_by=0,
                bank_rule=_DEBIT_RULE,
            )
        assert result.error is not None

    # ── explicit col_mapping override ─────────────────────────────────────────

    def test_run_with_explicit_col_mapping(self, pg_engine, schema):
        """Explicit col_mapping arg bypasses bank_rule.column_map."""
        rule_no_map = BankRule(
            bank_name="No Map Bank", prefix="test_nomap", account_type="checking",
        )
        mapping = ColumnMapping(date="date", description="description", amount="amount")
        try:
            result = self._run(rule_no_map, _debit_df(1), col_mapping=mapping)
            assert result.error is None
            assert result.inserted == 1
        finally:
            _cleanup(pg_engine, schema, "test_nomap")

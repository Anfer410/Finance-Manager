"""
tests/test_transfer_detection.py

Integration tests for Phase 1 and Phase 2 of automated transfer detection.

Phase 1 — internal_transfer:
  - Matching pair (checking outflow + savings inflow, same amount, within 7 days)
    → both sides flagged in transaction_flags
  - No match when only one side exists
  - No false positive when amounts match across different families
  - No match when date gap exceeds 7 days
  - No match when both rows are in the same account_key
  - user_kept=TRUE rows are not re-flagged after re-running detection
  - Idempotent: running detection twice does not create duplicate flags

Phase 2 — credit_payment:
  - Matching debit outflow against credit card payment received (credit > 0)
    → debit side flagged; only one flag per debit row
  - No match when amounts differ
  - No match when date gap exceeds 7 days
  - No match across families
  - No match against credit purchases (debit > 0) — only credit > 0 rows match
  - user_kept=TRUE not re-flagged
  - Idempotent second run

Note: detection functions open their own engine connections, so test data must
be committed before detection runs.  Tests use pg_engine directly and clean up
in try/finally blocks (same pattern as test_upload_pipeline_run.py).
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

# Stub NiceGUI — not needed here but transitive imports may pull it
for _mod in ("nicegui", "nicegui.app"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from services.transfer_detection_service import (  # noqa: E402
    detect_internal_transfers,
    detect_credit_payments,
)

SCHEMA    = "finance"
FAMILY_ID = 2   # pre-seeded in conftest


# ── helpers ───────────────────────────────────────────────────────────────────

def _ensure_partition(engine, year: int) -> None:
    for tbl in ("transactions_debit", "transactions_credit"):
        part = f"{SCHEMA}.{tbl}_{year}"
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {part}
                PARTITION OF {SCHEMA}.{tbl}
                FOR VALUES FROM ('{year}-01-01') TO ('{year + 1}-01-01')
            """))


def _insert_debit(engine, *, account_key: str, amount: float,
                  txn_date: date, description: str = "Transfer",
                  family_id: int = FAMILY_ID) -> int:
    """Insert a committed row and return its id."""
    _ensure_partition(engine, txn_date.year)
    with engine.begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {SCHEMA}.transactions_debit
                (account_key, transaction_date, description, amount,
                 person, source_file, family_id)
            VALUES (:ak, :dt, :desc, :amt, ARRAY[]::integer[], 'detect_test.csv', :fid)
            RETURNING id
        """), {"ak": account_key, "dt": txn_date, "desc": description,
               "amt": amount, "fid": family_id}).fetchone()
    return row[0]


def _insert_credit(engine, *, account_key: str, debit: float = 0.0, credit: float,
                   txn_date: date, description: str = "Payment",
                   family_id: int = FAMILY_ID) -> int:
    """Insert a committed credit row and return its id."""
    _ensure_partition(engine, txn_date.year)
    with engine.begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {SCHEMA}.transactions_credit
                (account_key, transaction_date, description, debit, credit,
                 person, source_file, family_id)
            VALUES (:ak, :dt, :desc, :dbt, :crd, ARRAY[]::integer[], 'detect_test.csv', :fid)
            RETURNING id
        """), {"ak": account_key, "dt": txn_date, "desc": description,
               "dbt": debit, "crd": credit, "fid": family_id}).fetchone()
    return row[0]


def _cleanup(engine, family_id: int = FAMILY_ID) -> None:
    """Remove all test rows inserted by these tests."""
    with engine.begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {SCHEMA}.transaction_flags WHERE family_id = :fid
        """), {"fid": family_id})
        conn.execute(text(f"""
            DELETE FROM {SCHEMA}.transactions_debit
            WHERE family_id = :fid AND source_file = 'detect_test.csv'
        """), {"fid": family_id})
        conn.execute(text(f"""
            DELETE FROM {SCHEMA}.transactions_credit
            WHERE family_id = :fid AND source_file = 'detect_test.csv'
        """), {"fid": family_id})


def _get_flags(engine, family_id: int = FAMILY_ID) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT flag_type, tx_table, tx_id, matched_id, amount, user_kept
            FROM {SCHEMA}.transaction_flags
            WHERE family_id = :fid
            ORDER BY tx_id
        """), {"fid": family_id}).fetchall()
    return [{"flag_type": r[0], "tx_table": r[1], "tx_id": r[2],
             "matched_id": r[3], "amount": r[4], "user_kept": r[5]}
            for r in rows]


# ── tests ─────────────────────────────────────────────────────────────────────

def test_matching_pair_flags_both_sides(pg_engine, schema):
    """A matching outflow/inflow pair on different accounts → both sides flagged."""
    try:
        d = date(2024, 5, 10)
        out_id = _insert_debit(pg_engine, account_key="checking", amount=-500.00, txn_date=d)
        in_id  = _insert_debit(pg_engine, account_key="savings",  amount=+500.00, txn_date=d)

        count = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 2
        flags = _get_flags(pg_engine)
        flagged_ids = {f["tx_id"] for f in flags}
        assert out_id in flagged_ids
        assert in_id  in flagged_ids
        for f in flags:
            assert f["flag_type"] == "internal_transfer"
            assert f["tx_table"]  == "debit"
            assert f["amount"]    == 500.00
            assert f["user_kept"] is False
    finally:
        _cleanup(pg_engine)


def test_only_outflow_no_match(pg_engine, schema):
    """An outflow with no corresponding inflow → nothing flagged."""
    try:
        _insert_debit(pg_engine, account_key="checking", amount=-300.00,
                      txn_date=date(2024, 5, 10))

        count = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
        assert _get_flags(pg_engine) == []
    finally:
        _cleanup(pg_engine)


def test_date_gap_too_large_no_match(pg_engine, schema):
    """Matching amounts but 8-day gap → not flagged (window is 7 days)."""
    try:
        _insert_debit(pg_engine, account_key="checking", amount=-200.00,
                      txn_date=date(2024, 5, 1))
        _insert_debit(pg_engine, account_key="savings",  amount=+200.00,
                      txn_date=date(2024, 5, 9))   # 8 days later

        count = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
    finally:
        _cleanup(pg_engine)


def test_same_account_key_no_match(pg_engine, schema):
    """Matching amounts on the SAME account_key → not a transfer, not flagged."""
    try:
        _insert_debit(pg_engine, account_key="checking", amount=-150.00,
                      txn_date=date(2024, 5, 5))
        _insert_debit(pg_engine, account_key="checking", amount=+150.00,
                      txn_date=date(2024, 5, 5))

        count = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
    finally:
        _cleanup(pg_engine)


def test_cross_family_no_match(pg_engine, schema):
    """Outflow in family 2, inflow in family 7 → not flagged (different families)."""
    try:
        _insert_debit(pg_engine, account_key="checking", amount=-400.00,
                      txn_date=date(2024, 5, 10), family_id=2)
        _insert_debit(pg_engine, account_key="savings",  amount=+400.00,
                      txn_date=date(2024, 5, 10), family_id=7)

        count = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
    finally:
        _cleanup(pg_engine, family_id=2)
        _cleanup(pg_engine, family_id=7)


def test_user_kept_not_reflagged(pg_engine, schema):
    """
    A transaction already flagged with user_kept=TRUE must not be matched again
    when detection re-runs.
    """
    try:
        d = date(2024, 6, 1)
        out_id = _insert_debit(pg_engine, account_key="checking", amount=-250.00, txn_date=d)
        in_id  = _insert_debit(pg_engine, account_key="savings",  amount=+250.00, txn_date=d)

        # Manually insert the outflow flag with user_kept=TRUE
        with pg_engine.begin() as conn:
            conn.execute(text(f"""
                INSERT INTO {SCHEMA}.transaction_flags
                    (family_id, flag_type, tx_table, tx_id,
                     matched_table, matched_id, amount, user_kept)
                VALUES (:fid, 'internal_transfer', 'debit', :txid,
                        'debit', :mid, 250.00, TRUE)
            """), {"fid": FAMILY_ID, "txid": out_id, "mid": in_id})

        count = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)

        # outflow is user_kept, so the pair is excluded from matching entirely
        assert count == 0
    finally:
        _cleanup(pg_engine)


def test_idempotent_second_run(pg_engine, schema):
    """Running detection twice on the same data inserts flags only once."""
    try:
        d = date(2024, 7, 15)
        _insert_debit(pg_engine, account_key="checking", amount=-600.00, txn_date=d)
        _insert_debit(pg_engine, account_key="savings",  amount=+600.00, txn_date=d)

        first  = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)
        second = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)

        assert first  == 2
        assert second == 0   # ON CONFLICT DO NOTHING — no duplicates
        assert len(_get_flags(pg_engine)) == 2
    finally:
        _cleanup(pg_engine)


def test_within_7_day_boundary(pg_engine, schema):
    """Exactly 7-day gap → should be flagged (boundary is inclusive)."""
    try:
        _insert_debit(pg_engine, account_key="checking", amount=-100.00,
                      txn_date=date(2024, 8, 1))
        _insert_debit(pg_engine, account_key="savings",  amount=+100.00,
                      txn_date=date(2024, 8, 8))   # exactly 7 days

        count = detect_internal_transfers(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 2
    finally:
        _cleanup(pg_engine)


# ── Phase 2: credit payment detection ─────────────────────────────────────────

def test_credit_payment_flags_debit_side(pg_engine, schema):
    """Checking outflow matching a credit card payment received → debit side flagged."""
    try:
        d = date(2024, 9, 15)
        debit_id  = _insert_debit(pg_engine,  account_key="checking", amount=-450.00, txn_date=d)
        credit_id = _insert_credit(pg_engine, account_key="visa",     credit=450.00,  txn_date=d)

        count = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 1
        flags = _get_flags(pg_engine)
        assert len(flags) == 1
        f = flags[0]
        assert f["flag_type"]  == "credit_payment"
        assert f["tx_table"]   == "debit"
        assert f["tx_id"]      == debit_id
        assert f["matched_id"] == credit_id
        assert f["amount"]     == 450.00
        assert f["user_kept"]  is False
    finally:
        _cleanup(pg_engine)


def test_credit_payment_amount_mismatch_no_match(pg_engine, schema):
    """Debit amount doesn't match credit amount → not flagged."""
    try:
        _insert_debit(pg_engine,  account_key="checking", amount=-300.00, txn_date=date(2024, 9, 1))
        _insert_credit(pg_engine, account_key="visa",     credit=301.00,  txn_date=date(2024, 9, 1))

        count = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
    finally:
        _cleanup(pg_engine)


def test_credit_payment_date_gap_too_large(pg_engine, schema):
    """Matching amounts but 8-day gap → not flagged."""
    try:
        _insert_debit(pg_engine,  account_key="checking", amount=-200.00, txn_date=date(2024, 9, 1))
        _insert_credit(pg_engine, account_key="visa",     credit=200.00,  txn_date=date(2024, 9, 9))

        count = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
    finally:
        _cleanup(pg_engine)


def test_credit_payment_cross_family_no_match(pg_engine, schema):
    """Debit in family 2, credit payment in family 7 → not flagged."""
    try:
        _insert_debit(pg_engine,  account_key="checking", amount=-500.00,
                      txn_date=date(2024, 9, 10), family_id=2)
        _insert_credit(pg_engine, account_key="visa",     credit=500.00,
                       txn_date=date(2024, 9, 10), family_id=7)

        count = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
    finally:
        _cleanup(pg_engine, family_id=2)
        _cleanup(pg_engine, family_id=7)


def test_credit_payment_purchase_not_matched(pg_engine, schema):
    """Credit card purchase (debit > 0) must NOT be matched — only credit > 0 rows match."""
    try:
        _insert_debit(pg_engine,  account_key="checking", amount=-75.00, txn_date=date(2024, 9, 5))
        # This is a purchase on the card (debit > 0), NOT a payment received
        _insert_credit(pg_engine, account_key="visa", debit=75.00, credit=0.00,
                       txn_date=date(2024, 9, 5))

        count = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
    finally:
        _cleanup(pg_engine)


def test_credit_payment_user_kept_not_reflagged(pg_engine, schema):
    """Debit flagged with user_kept=TRUE is not matched on subsequent detection run."""
    try:
        d = date(2024, 10, 1)
        debit_id  = _insert_debit(pg_engine,  account_key="checking", amount=-350.00, txn_date=d)
        credit_id = _insert_credit(pg_engine, account_key="visa",     credit=350.00,  txn_date=d)

        with pg_engine.begin() as conn:
            conn.execute(text(f"""
                INSERT INTO {SCHEMA}.transaction_flags
                    (family_id, flag_type, tx_table, tx_id,
                     matched_table, matched_id, amount, user_kept)
                VALUES (:fid, 'credit_payment', 'debit', :txid,
                        'credit', :mid, 350.00, TRUE)
            """), {"fid": FAMILY_ID, "txid": debit_id, "mid": credit_id})

        count = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 0
    finally:
        _cleanup(pg_engine)


def test_credit_payment_idempotent(pg_engine, schema):
    """Running detection twice produces no duplicate flags."""
    try:
        d = date(2024, 10, 15)
        _insert_debit(pg_engine,  account_key="checking", amount=-600.00, txn_date=d)
        _insert_credit(pg_engine, account_key="visa",     credit=600.00,  txn_date=d)

        first  = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)
        second = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)

        assert first  == 1
        assert second == 0
        assert len(_get_flags(pg_engine)) == 1
    finally:
        _cleanup(pg_engine)


def test_credit_payment_within_7_day_boundary(pg_engine, schema):
    """Exactly 7-day gap between debit and credit payment → flagged."""
    try:
        _insert_debit(pg_engine,  account_key="checking", amount=-125.00,
                      txn_date=date(2024, 10, 1))
        _insert_credit(pg_engine, account_key="visa",     credit=125.00,
                       txn_date=date(2024, 10, 8))  # exactly 7 days

        count = detect_credit_payments(FAMILY_ID, pg_engine, SCHEMA)

        assert count == 1
    finally:
        _cleanup(pg_engine)

"""
services/upload_manager.py

Manages uploaded transaction batches — query, reassign person, delete.

A "batch" is one logical upload: source_file + account_key in transactions_debit
or transactions_credit.  Raw archive tables (raw_<account_key>) are kept in sync
via the dedup key because they have no source_file column.

Public API
──────────
    get_upload_batches(family_id)
        → list[dict]  (one entry per source_file × account_key × table_type)

    reassign_persons(source_file, account_key, table_type, new_person_ids, family_id)
        → None  (updates consolidated + raw archive)

    delete_batch(source_file, account_key, table_type, family_id)
        → int   (rows deleted from consolidated table)

Batch dict shape
────────────────
    {
        "source_file":  str,
        "account_key":  str,
        "bank_name":    str,          # friendly name from BankRule, or account_key
        "table_type":   "debit"|"credit",
        "row_count":    int,
        "date_from":    date,
        "date_to":      date,
        "persons":      list[str],    # display_names of current person[] values
        "uploaded_at":  datetime,
    }
"""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import text

from data.db import get_engine, get_schema


def _engine():
    return get_engine()


def _schema():
    return get_schema()


# ── Public API ────────────────────────────────────────────────────────────────

def get_upload_batches(family_id: int) -> list[dict]:
    """
    Return all upload batches for a family, sorted newest first.
    Enriches each batch with a friendly bank_name and person display names.
    """
    schema = _schema()

    debit_sql = text(f"""
        SELECT  source_file,
                account_key,
                'debit'                 AS table_type,
                COUNT(*)                AS row_count,
                MIN(transaction_date)   AS date_from,
                MAX(transaction_date)   AS date_to,
                array_agg(DISTINCT pid) FILTER (WHERE pid IS NOT NULL) AS person_ids,
                MAX(inserted_at)        AS uploaded_at
        FROM    {schema}.transactions_debit,
                LATERAL unnest(person) AS pid
        WHERE   family_id = :fid
          AND   source_file != ''
        GROUP   BY source_file, account_key
    """)

    credit_sql = text(f"""
        SELECT  source_file,
                account_key,
                'credit'                AS table_type,
                COUNT(*)                AS row_count,
                MIN(transaction_date)   AS date_from,
                MAX(transaction_date)   AS date_to,
                array_agg(DISTINCT pid) FILTER (WHERE pid IS NOT NULL) AS person_ids,
                MAX(inserted_at)        AS uploaded_at
        FROM    {schema}.transactions_credit,
                LATERAL unnest(person) AS pid
        WHERE   family_id = :fid
          AND   source_file != ''
        GROUP   BY source_file, account_key
    """)

    params = {"fid": family_id}

    with _engine().connect() as conn:
        debit_rows  = conn.execute(debit_sql,  params).fetchall()
        credit_rows = conn.execute(credit_sql, params).fetchall()

    # Build person_id → display_name lookup
    person_map = _person_display_map()
    # Build account_key → bank_name lookup
    bank_map = _bank_name_map(family_id)

    batches = []
    for row in debit_rows + credit_rows:
        person_ids = row[6] or []
        batches.append({
            "source_file": row[0],
            "account_key": row[1],
            "bank_name":   bank_map.get(row[1], row[1]),
            "table_type":  row[2],
            "row_count":   row[3],
            "date_from":   row[4],
            "date_to":     row[5],
            "persons":     [person_map.get(pid, f"user#{pid}") for pid in person_ids],
            "uploaded_at": row[7],
        })

    batches.sort(key=lambda b: b["uploaded_at"] or datetime.min, reverse=True)
    return batches


def reassign_persons(
    source_file: str,
    account_key: str,
    table_type: str,
    new_person_ids: list[int],
    family_id: int,
) -> None:
    """
    Update person[] on all rows of a batch in the consolidated table,
    then sync the same change to the raw archive table.
    """
    schema      = _schema()
    tbl         = f"{schema}.transactions_{'debit' if table_type == 'debit' else 'credit'}"
    raw_tbl     = f"{schema}.raw_{_sanitize(account_key)}"
    raw_tbl_bare = f"raw_{_sanitize(account_key)}"
    ids_arr     = "{" + ",".join(str(i) for i in new_person_ids) + "}"

    with _engine().begin() as conn:
        # 1. Update consolidated table
        conn.execute(text(f"""
            UPDATE {tbl}
            SET    person = :ids
            WHERE  source_file = :sf
              AND  account_key = :ak
              AND  family_id   = :fid
        """), {"ids": ids_arr, "sf": source_file, "ak": account_key, "fid": family_id})

        # 2. Sync raw archive via dynamic join on dedup constraint columns
        if _raw_table_exists(conn, schema, raw_tbl_bare):
            dedup_info = _raw_dedup_columns(conn, schema, raw_tbl_bare)
            join_clause = _raw_join_clause(dedup_info, table_type)
            if join_clause:
                conn.execute(text(f"""
                    UPDATE {raw_tbl} r
                    SET    person = :pj
                    FROM   {tbl} t
                    WHERE  t.source_file = :sf
                      AND  t.account_key = :ak
                      AND  t.family_id   = :fid
                      AND  {join_clause}
                """), {"pj": json.dumps(new_person_ids),
                       "sf": source_file, "ak": account_key, "fid": family_id})


def delete_batch(
    source_file: str,
    account_key: str,
    table_type: str,
    family_id: int,
) -> int:
    """
    Delete all rows of a batch from the consolidated table and the raw archive.
    Returns the number of rows deleted from the consolidated table.
    """
    schema       = _schema()
    tbl          = f"{schema}.transactions_{'debit' if table_type == 'debit' else 'credit'}"
    raw_tbl      = f"{schema}.raw_{_sanitize(account_key)}"
    raw_tbl_bare = f"raw_{_sanitize(account_key)}"

    with _engine().begin() as conn:
        # 1. Delete matching rows from raw archive BEFORE deleting from consolidated
        #    (we need the consolidated rows alive for the JOIN)
        if _raw_table_exists(conn, schema, raw_tbl_bare):
            dedup_info  = _raw_dedup_columns(conn, schema, raw_tbl_bare)
            join_clause = _raw_join_clause(dedup_info, table_type)
            if join_clause:
                conn.execute(text(f"""
                    DELETE FROM {raw_tbl} r
                    USING  {tbl} t
                    WHERE  t.source_file = :sf
                      AND  t.account_key = :ak
                      AND  t.family_id   = :fid
                      AND  {join_clause}
                """), {"sf": source_file, "ak": account_key, "fid": family_id})

        # 2. Delete from consolidated table
        result = conn.execute(text(f"""
            DELETE FROM {tbl}
            WHERE  source_file = :sf AND account_key = :ak AND family_id = :fid
        """), {"sf": source_file, "ak": account_key, "fid": family_id})

    return result.rowcount


def backfill_currency(account_key: str, currency: str, family_id: int) -> int:
    """
    Set the currency column on all existing transactions for the given account_key.
    Updates both transactions_debit and transactions_credit (only the table that
    actually has rows for this account_key will be affected).
    Returns the total number of rows updated.
    """
    schema = _schema()
    currency = currency.strip().upper()
    total = 0
    with _engine().begin() as conn:
        for tbl in (f"{schema}.transactions_debit", f"{schema}.transactions_credit"):
            result = conn.execute(text(f"""
                UPDATE {tbl}
                SET    currency   = :cur
                WHERE  account_key = :ak
                  AND  family_id   = :fid
            """), {"cur": currency, "ak": account_key, "fid": family_id})
            total += result.rowcount
    return total


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_").replace("-", "_"))


def _raw_table_exists(conn, schema: str, table_name: str) -> bool:
    row = conn.execute(text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = :s AND table_name = :t
    """), {"s": schema, "t": table_name}).fetchone()
    return row is not None


def _raw_dedup_columns(conn, schema: str, raw_table_name: str) -> list[tuple[str, str]]:
    """
    Return [(col_name, data_type)] for the raw table's dedup unique constraint.
    The constraint is named uq_<table_name>_dedup.
    Returns [] if no such constraint exists.
    """
    rows = conn.execute(text("""
        SELECT kcu.column_name, c.data_type
        FROM   information_schema.table_constraints tc
        JOIN   information_schema.key_column_usage kcu
               ON  kcu.constraint_name   = tc.constraint_name
               AND kcu.constraint_schema = tc.constraint_schema
        JOIN   information_schema.columns c
               ON  c.table_schema  = kcu.constraint_schema
               AND c.table_name    = kcu.table_name
               AND c.column_name   = kcu.column_name
        WHERE  tc.constraint_type   = 'UNIQUE'
          AND  tc.table_schema      = :schema
          AND  tc.table_name        = :table
          AND  tc.constraint_name   = :cname
        ORDER  BY kcu.ordinal_position
    """), {
        "schema": schema,
        "table":  raw_table_name,
        "cname":  f"uq_{raw_table_name}_dedup",
    }).fetchall()
    return [(r[0], r[1]) for r in rows]


_DATE_TYPES    = {"date", "timestamp without time zone", "timestamp with time zone"}
_NUMERIC_TYPES = {"numeric", "double precision", "real", "integer", "bigint", "smallint"}
_TEXT_TYPES    = {"text", "character varying", "character"}


def _raw_join_clause(dedup_info: list[tuple[str, str]], table_type: str) -> str:
    """
    Build a SQL join condition fragment mapping raw table dedup columns
    to their corresponding consolidated table columns, based on data type.

    Column role inference:
      date/timestamp  → transaction_date
      text            → description
      numeric         → amount  (debit: single amount;
                                 credit: "debit" in name → t.debit,
                                         "credit" in name → t.credit,
                                         otherwise → t.debit)
    Returns empty string if dedup_info is empty (caller should skip raw sync).
    """
    if not dedup_info:
        return ""

    parts = []
    for col, dtype in dedup_info:
        q = f'"{col}"'
        if dtype in _DATE_TYPES:
            parts.append(f"r.{q}::date = t.transaction_date")
        elif dtype in _TEXT_TYPES:
            parts.append(f"r.{q} = t.description")
        elif dtype in _NUMERIC_TYPES:
            if table_type == "debit":
                parts.append(f"r.{q}::NUMERIC = t.amount")
            else:
                col_lower = col.lower()
                if "credit" in col_lower:
                    parts.append(f"r.{q}::NUMERIC = t.credit")
                else:
                    parts.append(f"r.{q}::NUMERIC = t.debit")

    return " AND ".join(parts)


def _person_display_map() -> dict[int, str]:
    """Return {user_id: display_name} for all users."""
    schema = _schema()
    with _engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT id, display_name FROM {schema}.app_users"
        )).fetchall()
    return {r[0]: r[1] for r in rows}


def _bank_name_map(family_id: int) -> dict[str, str]:
    """Return {account_key: bank_name} from BankRule config."""
    try:
        from data.bank_rules import load_rules
        return {r.prefix: r.bank_name for r in load_rules(family_id)}
    except Exception:
        return {}

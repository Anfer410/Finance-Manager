"""
services/upload_pipeline.py

Pure upload pipeline — no NiceGUI, fully testable.

Flow:
    1. sniff(raw_bytes)                     → SniffResult
    2. suggest_mapping(sniff, account_type) → ColumnMapping
    3. UploadPipeline.run(...)              → UploadResult

On each upload the pipeline:
  - Matches the filename against BankRules
  - Parses the CSV
  - Remaps columns to standard roles
  - Writes normalised rows into transactions_debit or transactions_credit
    (partitioned consolidated tables) using INSERT … ON CONFLICT DO NOTHING
  - Upserts into raw_* tables (serve as the archive / export source)
  - Auto-creates year partitions if the data spans a new year
  - Refreshes views
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Literal

import pandas as pd


# ── Column role constants ──────────────────────────────────────────────────────

ColRole = Literal["date", "description", "amount", "debit", "credit", "member_name", "ignore"]

ROLE_CANDIDATES: dict[ColRole, list[str]] = {
    "date":        ["transaction_date", "date", "trans_date", "posted_date",
                    "posting_date", "transaction date", "trans date"],
    "description": ["description", "memo", "transaction_description", "desc",
                    "narrative", "details", "payee", "merchant"],
    "amount":      ["amount", "transaction_amount", "trans_amount"],
    "debit":       ["debit", "debit_amount", "withdrawal", "charge", "charges"],
    "credit":      ["credit", "credit_amount", "deposit", "payment", "payments"],
    "member_name": ["member_name", "cardholder", "cardholder_name", "name"],
}

REQUIRED_ROLES: dict[str, list[ColRole]] = {
    "checking": ["date", "description", "amount"],
    "credit":   ["date", "description", "debit", "credit"],
}

DEDUP_ROLES: dict[str, list[ColRole]] = {
    "checking": ["date", "description", "amount"],
    "credit":   ["date", "debit", "credit", "description"],
}


# ── Sniff result ──────────────────────────────────────────────────────────────

@dataclass
class SniffResult:
    raw_columns:  list[str]
    norm_columns: list[str]
    has_header:   bool
    sample_rows:  list[list[str]]
    row_count:    int


def _normalize_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def sniff(raw: bytes) -> SniffResult:
    text = raw.decode("utf-8", errors="replace")
    try:
        dialect = pd.io.parsers.readers.csv.Sniffer().sniff(text[:4096])
        sep = dialect.delimiter
    except Exception:
        sep = ","

    try:
        df_h = pd.read_csv(io.BytesIO(raw), sep=sep, nrows=6, dtype=str)
        first_row = df_h.columns.tolist()
        looks_like_data = sum(
            1 for v in first_row
            if re.match(r"^[\d\-/\.]+$", str(v).strip())
        )
        has_header = looks_like_data < (len(first_row) // 2)
    except Exception:
        has_header = True

    if has_header:
        df = pd.read_csv(io.BytesIO(raw), sep=sep, dtype=str)
        raw_cols = list(df.columns)
    else:
        df = pd.read_csv(io.BytesIO(raw), sep=sep, header=None, dtype=str)
        raw_cols = [f"col_{i}" for i in range(len(df.columns))]
        df.columns = raw_cols

    norm_cols = [_normalize_col(c) for c in raw_cols]
    sample = [list(map(str, row.values)) for _, row in df.head(5).iterrows()]

    return SniffResult(
        raw_columns  = raw_cols,
        norm_columns = norm_cols,
        has_header   = has_header,
        sample_rows  = sample,
        row_count    = len(df),
    )


# ── Column mapping ─────────────────────────────────────────────────────────────

@dataclass
class ColumnMapping:
    date:        str | None = None
    description: str | None = None
    amount:      str | None = None   # checking only
    debit:       str | None = None   # credit only
    credit:      str | None = None   # credit only
    member_name: str | None = None

    def for_account_type(self, account_type: str) -> dict[ColRole, str | None]:
        if account_type == "credit":
            return {
                "date":        self.date,
                "description": self.description,
                "debit":       self.debit,
                "credit":      self.credit,
                "member_name": self.member_name,
            }
        return {
            "date":        self.date,
            "description": self.description,
            "amount":      self.amount,
            "member_name": self.member_name,
        }

    def missing_required(self, account_type: str) -> list[ColRole]:
        mapped = self.for_account_type(account_type)
        return [r for r in REQUIRED_ROLES[account_type] if not mapped.get(r)]

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @staticmethod
    def from_dict(d: dict) -> "ColumnMapping":
        known = set(ColumnMapping.__dataclass_fields__)
        return ColumnMapping(**{k: v for k, v in d.items() if k in known})

    def dedup_columns(self, account_type: str) -> list[str]:
        cols = []
        for role in DEDUP_ROLES.get(account_type, ["date", "description", "amount"]):
            val = getattr(self, role, None)
            if val:
                cols.append(val)
        return cols or ["description"]


def suggest_mapping(sniff_result: SniffResult, account_type: str = "checking") -> ColumnMapping:
    norm = sniff_result.norm_columns

    def best(candidates: list[str]) -> str | None:
        for c in candidates:
            if c in norm:
                return c
        for c in candidates:
            for col in norm:
                if c in col or col in c:
                    return col
        return None

    return ColumnMapping(
        date        = best(ROLE_CANDIDATES["date"]),
        description = best(ROLE_CANDIDATES["description"]),
        amount      = best(ROLE_CANDIDATES["amount"])      if account_type == "checking" else None,
        debit       = best(ROLE_CANDIDATES["debit"])       if account_type == "credit"   else None,
        credit      = best(ROLE_CANDIDATES["credit"])      if account_type == "credit"   else None,
        member_name = best(ROLE_CANDIDATES["member_name"]),
    )


# ── Upload result ─────────────────────────────────────────────────────────────

@dataclass
class UploadResult:
    bank_name: str
    inserted:  int
    skipped:   int
    total:     int
    error:     str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


# ── Consolidated table writer ─────────────────────────────────────────────────

def _resolve_person(row: pd.Series, rule, member_col: str | None, default_person_ids: list[int]) -> list[int]:
    """
    Resolve the person for a single row using rule.member_aliases.
    Returns a list of user IDs.
    Falls back to default_person_ids (already resolved IDs from person_ref or person_override).
    """
    if member_col and member_col in row.index and rule and rule.member_aliases:
        raw_val = str(row[member_col]).strip().upper()
        for alias_raw, user_id in rule.member_aliases.items():
            if alias_raw.upper() in raw_val or raw_val in alias_raw.upper():
                return [int(user_id)]
    return default_person_ids


def write_to_consolidated(
    df: pd.DataFrame,
    rule,
    mapping: "ColumnMapping",
    person: list[int],
    source_file: str,
) -> int:
    """
    Write normalised rows from df into transactions_debit or transactions_credit.
    Uses INSERT … ON CONFLICT DO NOTHING for dedup.
    Auto-creates year partitions if needed.
    Returns number of rows inserted.
    """
    from sqlalchemy import text
    from data.db import get_engine, get_schema
    from db_migration import ensure_partition_for_year

    engine      = get_engine()
    schema      = get_schema()
    account_key = rule.prefix
    is_credit   = rule.account_type == "credit"

    # Resolve column names — use what mapping resolved, no silent fallbacks
    date_col   = mapping.date
    desc_col   = mapping.description or ""
    member_col = mapping.member_name

    df_cols = set(df.columns)

    if not date_col or date_col not in df_cols:
        print(f"[write_to_consolidated] ERROR: date col '{date_col}' not in df {list(df_cols)}")
        return 0
    if desc_col not in df_cols:
        desc_col = ""  # description is optional — will store empty string

    if is_credit:
        debit_col  = mapping.debit  or ""
        credit_col = mapping.credit or ""
        tbl        = f"{schema}.transactions_credit"
        if not debit_col or debit_col not in df_cols:
            print(f"[write_to_consolidated] ERROR: debit col '{debit_col}' not in df {list(df_cols)}")
            return 0
        if not credit_col or credit_col not in df_cols:
            print(f"[write_to_consolidated] ERROR: credit col '{credit_col}' not in df {list(df_cols)}")
            return 0
    else:
        amount_col = mapping.amount or ""
        tbl        = f"{schema}.transactions_debit"
        if not amount_col or amount_col not in df_cols:
            print(f"[write_to_consolidated] ERROR: amount col '{amount_col}' not in df {list(df_cols)}")
            return 0

    print(f"[write_to_consolidated] account_key={account_key} is_credit={is_credit} "
          f"date_col={date_col} desc_col={desc_col} "
          f"{'debit_col=' + debit_col + ' credit_col=' + credit_col if is_credit else 'amount_col=' + amount_col} "
          f"rows={len(df)} df_cols={list(df.columns)}")

    inserted = 0
    skipped_date = 0
    rows_by_year: dict[int, list[dict]] = {}

    for _, row in df.iterrows():
        # Parse date
        raw_date = row.get(date_col)
        if pd.isna(raw_date) or str(raw_date).strip() in ("", "NaN", "nan"):
            skipped_date += 1
            continue
        try:
            txn_date = pd.to_datetime(str(raw_date), dayfirst=False).date()
        except Exception:
            skipped_date += 1
            continue

        year = txn_date.year
        rows_by_year.setdefault(year, [])

        desc = str(row.get(desc_col, "")).strip()
        p    = _resolve_person(row, rule, member_col, person)

        if is_credit:
            try:
                dbt = float(str(row.get(debit_col, 0) or 0).replace(",", ""))
                if pd.isna(dbt): dbt = 0.0
            except ValueError:
                dbt = 0.0
            try:
                crd = float(str(row.get(credit_col, 0) or 0).replace(",", ""))
                if pd.isna(crd): crd = 0.0
            except ValueError:
                crd = 0.0
            rows_by_year[year].append({
                "account_key":      account_key,
                "transaction_date": txn_date,
                "description":      desc,
                "debit":            abs(dbt),
                "credit":           abs(crd),
                "person":           p,
                "source_file":      source_file,
            })
        else:
            try:
                amt = float(str(row.get(amount_col, 0) or 0).replace(",", ""))
            except ValueError:
                amt = 0.0
            rows_by_year[year].append({
                "account_key":      account_key,
                "transaction_date": txn_date,
                "description":      desc,
                "amount":           amt,
                "person":           p,
                "source_file":      source_file,
            })

    if skipped_date:
        print(f"[write_to_consolidated] skipped {skipped_date} rows (unparseable date in col '{date_col}')")
    if not rows_by_year:
        print(f"[write_to_consolidated] no valid rows to insert — check date_col '{date_col}' exists in df")
        return 0

    with engine.begin() as conn:
        # Auto-create any missing year partitions
        for year in rows_by_year:
            ensure_partition_for_year(conn, schema, year)

        for year, batch in rows_by_year.items():
            if not batch:
                continue

            if is_credit:
                sql = text(f"""
                    INSERT INTO {tbl}
                        (account_key, transaction_date, description,
                         debit, credit, person, source_file)
                    VALUES
                        (:account_key, :transaction_date, :description,
                         :debit, :credit, :person, :source_file)
                    ON CONFLICT DO NOTHING
                """)
            else:
                sql = text(f"""
                    INSERT INTO {tbl}
                        (account_key, transaction_date, description,
                         amount, person, source_file)
                    VALUES
                        (:account_key, :transaction_date, :description,
                         :amount, :person, :source_file)
                    ON CONFLICT DO NOTHING
                """)

            result = conn.execute(sql, batch)
            inserted += result.rowcount

    return inserted


# ── Pipeline ──────────────────────────────────────────────────────────────────

class UploadPipeline:
    def run(
        self,
        raw:         bytes,
        filename:    str,
        person:      str,
        bank_rule=None,
        col_mapping: ColumnMapping | None = None,
    ) -> UploadResult:
        from data.bank_rules import _matcher, load_rules
        from services.raw_table_manager import parse_csv, default_manager
        from services.view_manager import default_view_manager

        # ── 1. Match rule ─────────────────────────────────────────────────────
        if bank_rule is None:
            result = _matcher.match(filename, person)
            if result is None:
                return UploadResult(
                    bank_name="unknown", inserted=0, skipped=0, total=0,
                    error=f"No rule matched filename: {filename}",
                )
            bank_name, output_name, person = result
            rules     = {r.bank_name: r for r in load_rules()}
            bank_rule = rules.get(bank_name)
        else:
            bank_name = bank_rule.bank_name
            if bank_rule.person_override is not None:
                person = bank_rule.person_override

        # Normalise person to list[int] — person_override is already list[int];
        # a bare int comes from the upload UI (single user selected).
        if isinstance(person, int):
            person = [person]
        elif not isinstance(person, list):
            person = []

        prefix = bank_rule.prefix if bank_rule else "unknown"

        # ── 2. Parse CSV ──────────────────────────────────────────────────────
        print(f"[UploadPipeline] bank_rule.prefix={getattr(bank_rule,'prefix',None)} column_map={getattr(bank_rule,'column_map',None)}")
        col_map_dict = getattr(bank_rule, "column_map", None) or None
        try:
            import inspect as _inspect
            if "column_map" in _inspect.signature(parse_csv).parameters:
                df = parse_csv(raw, prefix=prefix, column_map=col_map_dict)
            else:
                df = parse_csv(raw, prefix=prefix)
        except Exception as ex:
            return UploadResult(
                bank_name=bank_name, inserted=0, skipped=0, total=len(raw),
                error=f"Could not parse CSV: {ex}",
            )

        total = len(df)

        # ── 3. Resolve column mapping ─────────────────────────────────────────
        # Priority: explicit col_mapping arg > bank_rule.column_map > suggest from df
        mapping = col_mapping
        if mapping is None and bank_rule and getattr(bank_rule, "column_map", None):
            mapping = ColumnMapping.from_dict(bank_rule.column_map)

        account_type = getattr(bank_rule, "account_type", "checking") if bank_rule else "checking"

        if mapping is not None:
            # Rename df columns from actual names → role names
            # e.g. "col_0" → "date", "trans_date" → "date"
            rename = {
                actual: role
                for role, actual in mapping.to_dict().items()
                if actual and actual in df.columns and actual != role
            }
            if rename:
                df = df.rename(columns=rename)

            # mapping_for_write points at what's now in df (role names after rename)
            df_cols = set(df.columns)
            mapping_for_write = ColumnMapping(
                date        = "date"        if "date"        in df_cols else None,
                description = "description" if "description" in df_cols else None,
                amount      = "amount"      if "amount"      in df_cols else None,
                debit       = "debit"       if "debit"       in df_cols else None,
                credit      = "credit"      if "credit"      in df_cols else None,
                member_name = "member_name" if "member_name" in df_cols else None,
            )
        else:
            # No column_map — suggest from whatever columns df already has
            fake_sniff = SniffResult(
                raw_columns  = list(df.columns),
                norm_columns = list(df.columns),
                has_header   = True,
                sample_rows  = [],
                row_count    = len(df),
            )
            mapping_for_write = suggest_mapping(fake_sniff, account_type)

        print(f"[UploadPipeline] df cols: {list(df.columns)}")
        print(f"[UploadPipeline] mapping_for_write: {mapping_for_write.to_dict()}")

        # ── 4. Write to consolidated tables ───────────────────────────────────
        account_type = getattr(bank_rule, "account_type", "checking") if bank_rule else "checking"
        consolidated_inserted = 0
        try:
            consolidated_inserted = write_to_consolidated(
                df          = df,
                rule        = bank_rule,
                mapping     = mapping_for_write,
                person      = person,
                source_file = filename,
            )
            print(f"[UploadPipeline] {consolidated_inserted} rows → transactions_{account_type}")
        except Exception as ex:
            print(f"[UploadPipeline] consolidated write failed: {ex}")
            # Fall through — still write raw table

        # ── 5. Upsert raw table (archive) ─────────────────────────────────────
        if mapping is not None:
            dedup_cols = mapping_for_write.dedup_columns(account_type)
        elif bank_rule and getattr(bank_rule, "dedup_columns", None):
            dedup_cols = bank_rule.dedup_columns
        else:
            dedup_cols = [
                c for c in ["transaction_date", "date", "amount", "debit", "credit", "description"]
                if c in df.columns
            ][:3] or ["description"]

        raw_inserted = 0
        try:
            mgr = default_manager()
            raw_inserted = mgr.upsert(
                df, bank_name=bank_name, dedup_columns=dedup_cols, person=person
            )
        except Exception as ex:
            print(f"[UploadPipeline] raw table upsert failed: {ex}")

        inserted = consolidated_inserted or raw_inserted

        # ── 6. Refresh views ──────────────────────────────────────────────────
        try:
            vm = default_view_manager()
            vm.refresh()
        except Exception as ex:
            print(f"[UploadPipeline] view refresh failed: {ex}")

        return UploadResult(
            bank_name = bank_name,
            inserted  = inserted,
            skipped   = total - inserted,
            total     = total,
        )


# ── Module-level singleton ─────────────────────────────────────────────────────
pipeline = UploadPipeline()
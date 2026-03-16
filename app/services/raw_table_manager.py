"""
raw_table_manager.py

Manages dynamic raw_<account_key> archive tables in Postgres.
- One table per account (keyed by rule.prefix, e.g. raw_wf_checking)
- Auto-creates table from DataFrame schema on first upload
- Deduplicates via UNIQUE constraint + INSERT ON CONFLICT DO NOTHING
- person column is stored for per-person filtering but excluded from CSV export
- No Alembic involvement (these are archive tables, not schema tables)
"""

import json
import re
import uuid
import pandas as pd
import psycopg
from sqlalchemy import text, inspect
from data.db import get_engine, get_schema, get_psycopg_dsn


# ── Type mapping: pandas dtype → Postgres column type ────────────────────────

DTYPE_MAP = {
    "int64":          "BIGINT",
    "int32":          "INTEGER",
    "float64":        "NUMERIC",
    "float32":        "NUMERIC",
    "bool":           "BOOLEAN",
    "datetime64[ns]": "TIMESTAMP",
    "object":         "TEXT",
}

def _pg_type(dtype) -> str:
    return DTYPE_MAP.get(str(dtype), "TEXT")

def _quote(name: str) -> str:
    """Double-quote a Postgres identifier, escaping any internal quotes."""
    return '"' + name.replace('"', '""') + '"'


# Convenience: build a manager using the app-wide DB config
def default_manager() -> "RawTableManager":
    return RawTableManager()


# ── CSV pre-processors ────────────────────────────────────────────────────────

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lowercase column names, replace spaces/special chars with underscores.
    Empty or whitespace-only column names become col_0, col_1, ...
    Duplicate names are suffixed _2, _3 ...
    """
    df = df.copy()
    seen: dict[str, int] = {}
    new_cols = []
    for i, c in enumerate(df.columns):
        name = re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")
        if not name:
            name = f"col_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        new_cols.append(name)
    df.columns = new_cols
    return df


BANK_CSV_PARSERS: dict[str, callable] = {}

def register_parser(prefix: str):
    """Decorator to register a bank-specific CSV parser."""
    def decorator(fn):
        BANK_CSV_PARSERS[prefix] = fn
        return fn
    return decorator


@register_parser("wf")
def _parse_wells_fargo(raw: bytes) -> pd.DataFrame:
    """
    Wells Fargo CSVs have NO header row.
    Standard layout: Date, Amount, *, Check Number, Description (5 cols)
    Some exports have a 6th trailing column — handled gracefully.
    """
    import io
    WF_COLS = ["transaction_date", "amount", "flag", "check_number", "description"]
    df = pd.read_csv(io.BytesIO(raw), header=None, dtype=str)
    n = len(df.columns)
    if n <= len(WF_COLS):
        df.columns = WF_COLS[:n]
    else:
        # Extra columns beyond the standard 5 get generic names
        df.columns = WF_COLS + [f"col_{i}" for i in range(n - len(WF_COLS))]
    return _normalize_columns(df)


@register_parser("cap1")
def _parse_capital_one(raw: bytes) -> pd.DataFrame:
    import io
    df = pd.read_csv(io.BytesIO(raw))
    return _normalize_columns(df)


@register_parser("citi")
def _parse_citi(raw: bytes) -> pd.DataFrame:
    import io
    df = pd.read_csv(io.BytesIO(raw))
    return _normalize_columns(df)


def parse_csv(raw: bytes, prefix: str, column_map: dict | None = None) -> pd.DataFrame:
    """
    Parse raw CSV bytes into a DataFrame with normalised column names.

    When column_map is provided (set by the wizard on BankRule.column_map):
      - It maps role → actual_col_name as the wizard recorded from the sample file
      - For headered files:  actual_col_name is the real header text, e.g. "Trans Date"
      - For headerless files: actual_col_name is "col_0", "col_1" ... (sniff() output)
      - We detect which case we're in by checking if any actual_col_name appears in
        the normalised first row.  If yes → has header.  If no → headerless, read with
        header=None and assign col_0, col_1 ... so the names match column_map keys.

    Without column_map (legacy banks configured before the wizard):
      - Try registered bank-specific parsers keyed by prefix
      - Fall back to generic read_csv with header
    """
    import io

    if column_map:
        actual_names = {v for v in column_map.values() if v}

        # Peek at first row with header to see if column names match what wizard recorded
        df_peek = pd.read_csv(io.BytesIO(raw), dtype=str, nrows=0)
        norm_headers = {
            re.sub(r"[^a-z0-9]+", "_", c.strip().lower()).strip("_")
            for c in df_peek.columns
        }
        has_header = bool(actual_names & norm_headers)  # any overlap → has header

        if has_header:
            df = pd.read_csv(io.BytesIO(raw), dtype=str)
            return _normalize_columns(df)
        else:
            # Headerless — read without header, assign col_0, col_1 ...
            # These match what the wizard stored in column_map
            df = pd.read_csv(io.BytesIO(raw), header=None, dtype=str)
            df.columns = [f"col_{i}" for i in range(len(df.columns))]
            return df  # already normalised — col_N names are clean

    # Legacy path (banks added before the wizard had column mapping)
    parser = BANK_CSV_PARSERS.get(prefix)
    if parser is None:
        for reg_prefix, reg_parser in BANK_CSV_PARSERS.items():
            if prefix.startswith(reg_prefix):
                parser = reg_parser
                break

    if parser:
        return parser(raw)

    # Generic fallback — read with header
    df = pd.read_csv(io.BytesIO(raw), dtype=str)
    return _normalize_columns(df)


# ── Core manager ─────────────────────────────────────────────────────────────

class RawTableManager:
    """
    Manages raw_<bank> tables.

    Usage:
        mgr = RawTableManager()
        inserted = mgr.upsert(df, bank_name="wells_fargo_checking",
                              dedup_columns=["transaction_date","amount","description"])
    """

    def __init__(self):
        self.schema = get_schema()
        self.engine = get_engine()
        self._ensure_schema()

    # ── Public API ────────────────────────────────────────────────────────────

    def upsert(self, df: pd.DataFrame, account_key: str, dedup_columns: list[str], person: str = "") -> int:
        """
        Ensure the raw archive table exists, then insert only rows not already present.
        Table is named raw_<account_key> (e.g. raw_wf_checking).
        Returns the number of newly inserted rows.
        """
        table_name = f"raw_{self._sanitize(account_key)}"
        df = self._coerce_types(df)

        # Final safety net: re-normalize columns to guarantee no empty names
        df = _normalize_columns(df)

        # Inject person as the first column so it's part of the schema from creation.
        # Raw tables are TEXT-based archives, so serialize the ID list to a JSON string.
        person_str = json.dumps(person) if isinstance(person, list) else str(person)
        df = df.copy()
        df.insert(0, "person", person_str)

        if not self._table_exists(table_name):
            self._create_table(df, table_name, dedup_columns)
        else:
            self._ensure_columns(df, table_name)

        return self._insert_unique(df, table_name, dedup_columns)

    def table_exists(self, account_key: str) -> bool:
        return self._table_exists(f"raw_{self._sanitize(account_key)}")

    def list_accounts(self) -> list[str]:
        """Returns account keys (raw_ prefix stripped) for all raw_* tables in the schema."""
        names = inspect(self.engine).get_table_names(schema=self.schema)
        return [n[4:] for n in sorted(names) if n.startswith("raw_")]

    def export_csv(self, account_key: str, person_id: int | None = None) -> str:
        """
        Returns rows from raw_<account_key> as a CSV string.
        Excludes _id and person columns — these are internal archive metadata.
        When person_id is given, only rows whose person JSON array contains that ID are returned.
        """
        import csv
        import io as _io
        SKIP_COLS = {"_id", "person"}
        table = f"{self.schema}.raw_{self._sanitize(account_key)}"
        if person_id is not None:
            where  = "WHERE person::jsonb @> to_jsonb(:pid::int)"
            params = {"pid": person_id}
        else:
            where  = ""
            params = {}
        with self.engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM {table} {where} ORDER BY _id"), params)
            all_cols = list(result.keys())
            rows = result.fetchall()
        export_cols = [c for c in all_cols if c not in SKIP_COLS]
        col_indices = [all_cols.index(c) for c in export_cols]
        buf = _io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(export_cols)
        writer.writerows([row[i] for i in col_indices] for row in rows)
        return buf.getvalue()

    # ── Internals ─────────────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        """Create the schema if it doesn't already exist."""
        with self.engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self.schema}"))

    @staticmethod
    def _sanitize(name: str) -> str:
        return re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_").replace("-", "_"))

    def _table_exists(self, table_name: str) -> bool:
        return inspect(self.engine).has_table(table_name, schema=self.schema)

    def _coerce_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """Try to parse object columns that look like dates. Replace NaN/NaT with None (→ NULL in Postgres)."""
        df = df.copy()
        # Replace 'NaN' strings with actual NaN so they become NULL in Postgres
        df.replace("NaN", pd.NA, inplace=True)
        for col in df.select_dtypes(include="object").columns:
            try:
                parsed = pd.to_datetime(df[col], infer_datetime_format=True)
                df[col] = parsed
            except (ValueError, TypeError):
                pass
        # Ensure all remaining NaN/NA become None (NULL) not the string 'NaN'
        df = df.where(df.notna(), other=None)
        return df

    def _create_table(self, df: pd.DataFrame, table_name: str, dedup_columns: list[str]) -> None:
        col_defs = ",\n    ".join(
            f"{_quote(c)} {_pg_type(df[c].dtype)}"
            for c in df.columns
        )

        valid_dedup     = [c for c in dedup_columns if c in df.columns]
        constraint_name = f"uq_{table_name}_dedup"

        if valid_dedup:
            unique_part = f",\n                CONSTRAINT {constraint_name} UNIQUE ({', '.join(_quote(c) for c in valid_dedup)})"
            if set(valid_dedup) != set(dedup_columns):
                missing = set(dedup_columns) - set(valid_dedup)
                print(f"[RawTableManager] WARNING: dedup columns {missing} not found — "
                      f"constraint will use {valid_dedup} only")
        else:
            unique_part = ""
            print(
                f"[RawTableManager] WARNING: none of {dedup_columns} found in "
                f"{list(df.columns)}\n"
                f"  → Table created WITHOUT a unique constraint. "
                f"Duplicates will NOT be filtered."
            )

        ddl = (
            f"CREATE TABLE IF NOT EXISTS {self.schema}.{table_name} (\n"
            f"    _id BIGSERIAL PRIMARY KEY,\n"
            f"    {col_defs}"
            f"{unique_part}\n"
            f")"
        )
        with self.engine.begin() as conn:
            conn.execute(text(ddl))
        print(f"[RawTableManager] Created table {self.schema}.{table_name}")

    def _ensure_columns(self, df: pd.DataFrame, table_name: str) -> None:
        existing     = {c["name"] for c in inspect(self.engine).get_columns(table_name, schema=self.schema)}
        missing_cols = [c for c in df.columns if c not in existing]
        if not missing_cols:
            return
        with self.engine.begin() as conn:
            for col in missing_cols:
                conn.execute(text(
                    f"ALTER TABLE {self.schema}.{table_name} "
                    f"ADD COLUMN IF NOT EXISTS {_quote(col)} {_pg_type(df[col].dtype)}"
                ))
                print(f"[RawTableManager] Added column '{col}' to {table_name}")

    def _insert_unique(self, df: pd.DataFrame, table_name: str, dedup_columns: list[str]) -> int:
        tmp             = f"tmp_upload_{uuid.uuid4().hex[:12]}"
        constraint_name = f"uq_{table_name}_dedup"
        valid_dedup     = [c for c in dedup_columns if c in df.columns]
        cols            = list(df.columns)
        col_list        = ", ".join(_quote(c) for c in cols)

        with psycopg.connect(get_psycopg_dsn()) as pg_conn:
            with pg_conn.cursor() as cur:
                # Session-scoped temp table — no constraints so duplicate rows
                # in the source CSV are accepted here and deduplicated on insert
                cur.execute(
                    f"CREATE TEMP TABLE {tmp} "
                    f"(LIKE {self.schema}.{table_name} INCLUDING DEFAULTS) ON COMMIT DROP"
                )

                # Bulk load into temp table via COPY
                with cur.copy(f"COPY {tmp} ({col_list}) FROM STDIN") as copy:
                    for row in df.itertuples(index=False, name=None):
                        copy.write_row(row)

                # Insert unique rows only
                if valid_dedup:
                    conflict_clause = f"ON CONFLICT ON CONSTRAINT {constraint_name} DO NOTHING"
                else:
                    conflict_clause = ""   # no constraint — insert everything

                cur.execute(
                    f"INSERT INTO {self.schema}.{table_name} ({col_list}) "
                    f"SELECT {col_list} FROM {tmp} "
                    f"{conflict_clause}"
                )
                inserted = cur.rowcount

            pg_conn.commit()

        print(f"[RawTableManager] Inserted {inserted} new rows into {self.schema}.{table_name}")
        return inserted
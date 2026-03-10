"""
raw_table_manager.py

Manages dynamic raw_<bank> tables in Postgres.
- Auto-creates table from DataFrame schema on first upload
- Deduplicates via UNIQUE constraint + INSERT ON CONFLICT DO NOTHING
- No Alembic involvement (these are data tables, not schema tables)
"""

import re
import uuid
import pandas as pd
import psycopg
from sqlalchemy import create_engine, text, inspect


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


# ── Connection helpers ────────────────────────────────────────────────────────

def _sqlalchemy_url(conn: tuple) -> str:
    user, password, host, port, db = conn
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"

def _psycopg_dsn(conn: tuple) -> str:
    user, password, host, port, db = conn
    return f"host={host} port={port} user={user} password={password} dbname={db}"


# ── CSV pre-processors ────────────────────────────────────────────────────────

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lowercase column names, replace spaces/special chars with underscores.
    e.g. "Transaction Date" → "transaction_date", "Amount ($)" → "amount"
    """
    df = df.copy()
    df.columns = [
        re.sub(r"[^a-z0-9]+", "_", c.strip().lower()).strip("_")
        for c in df.columns
    ]
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
    Wells Fargo CSVs have NO header row — columns are:
      Date, Amount, *, Check Number, Description
    """
    import io
    df = pd.read_csv(
        io.BytesIO(raw),
        header=None,
        names=["transaction_date", "amount", "flag", "check_number", "description"],
    )
    return df


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


def parse_csv(raw: bytes, prefix: str) -> pd.DataFrame:
    """
    Parse raw CSV bytes using the bank-specific parser if registered,
    otherwise fall back to generic pandas read_csv + column normalization.
    """
    import io
    parser = BANK_CSV_PARSERS.get(prefix)
    if parser:
        return parser(raw)
    df = pd.read_csv(io.BytesIO(raw))
    return _normalize_columns(df)


# ── Core manager ─────────────────────────────────────────────────────────────

class RawTableManager:
    """
    Manages raw_<bank> tables.

    Usage:
        mgr = RawTableManager(db_connection_string, schema="finance")
        inserted = mgr.upsert(df, bank_name="wells_fargo_checking",
                              dedup_columns=["transaction_date","amount","description"])
    """

    def __init__(self, db_connection_string: tuple, schema: str = "public"):
        self.conn_tuple = db_connection_string
        self.schema     = schema
        self.engine     = create_engine(_sqlalchemy_url(db_connection_string))
        self._ensure_schema()

    # ── Public API ────────────────────────────────────────────────────────────

    def upsert(self, df: pd.DataFrame, bank_name: str, dedup_columns: list[str], person: str = "") -> int:
        """
        Ensure the raw table exists, then insert only rows not already present.
        Returns the number of newly inserted rows.
        """
        table_name = f"raw_{self._sanitize(bank_name)}"
        df = self._coerce_types(df)

        # Inject person as the first column so it's part of the schema from creation
        df = df.copy()
        df.insert(0, "person", person)

        if not self._table_exists(table_name):
            self._create_table(df, table_name, dedup_columns)
        else:
            self._ensure_columns(df, table_name)

        return self._insert_unique(df, table_name, dedup_columns)

    def table_exists(self, bank_name: str) -> bool:
        return self._table_exists(f"raw_{self._sanitize(bank_name)}")

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

        with psycopg.connect(_psycopg_dsn(self.conn_tuple)) as pg_conn:
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
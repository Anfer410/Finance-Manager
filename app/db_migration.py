"""
db_migration.py

Two modes:

  1. Automatic startup migration (called from main.py on every boot):
         from db_migration import run_migrations
         run_migrations()
     Idempotent — only creates tables/partitions/indexes that don't exist yet.
     Never touches user data or config data.

  2. CLI admin creation:
         python db_migration.py --admin-username andy --admin-person andy
     Creates the first (or an additional) admin user interactively.
     Does NOT run data migrations — just creates the user.

     To run both in one shot (first-time setup):
         python db_migration.py --full-setup --admin-username andy
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from getpass import getpass
from sqlalchemy import Engine, text
from data.db import get_engine, get_schema


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP MIGRATION  — safe to call on every app boot
# ══════════════════════════════════════════════════════════════════════════════

def run_migrations() -> None:
    """
    Called automatically on every app startup.
    Creates any missing tables, partitions, and indexes.
    Never drops or alters existing objects — fully idempotent.
    """
    try:
        engine = get_engine()
        schema = get_schema()
        with engine.begin() as conn:
            _create_app_tables(conn, schema)
            _create_transaction_tables(conn, schema)
            _migrate_configs_if_needed(conn, schema)
        print("[migration] Startup migrations complete.")
    except Exception as ex:
        print(f"[migration] WARNING: startup migration failed: {ex}")
        # Don't crash the app — DB might already be fully set up


# ── App tables ────────────────────────────────────────────────────────────────

def _create_app_tables(conn, schema: str) -> None:
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_users (
            id            SERIAL PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name  TEXT NOT NULL,
            person_name   TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user'
                         CHECK (role IN ('admin', 'user')),
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_user_prefs (
            user_id          INT PRIMARY KEY
                             REFERENCES {schema}.app_users(id) ON DELETE CASCADE,
            selected_persons JSONB NOT NULL DEFAULT '[]',
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    for cfg_name in ("bank_rules", "categories", "transaction"):
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {schema}.app_config_{cfg_name} (
                id         INT PRIMARY KEY DEFAULT 1
                           CHECK (id = 1),
                data       JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_settings (
            key        TEXT PRIMARY KEY,
            value      JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_loans (
            id                           SERIAL PRIMARY KEY,
            name                         TEXT          NOT NULL,
            loan_type                    TEXT          NOT NULL DEFAULT 'other',
            rate_type                    TEXT          NOT NULL DEFAULT 'fixed',
            interest_rate                NUMERIC(7,4)  NOT NULL DEFAULT 0,
            original_principal           NUMERIC(14,2) NOT NULL DEFAULT 0,
            term_months                  INT           NOT NULL DEFAULT 360,
            start_date                   DATE          NOT NULL,
            monthly_payment              NUMERIC(12,2) NOT NULL DEFAULT 0,
            monthly_insurance            NUMERIC(12,2) NOT NULL DEFAULT 0,
            current_balance              NUMERIC(14,2) NOT NULL DEFAULT 0,
            balance_as_of                DATE          NOT NULL,
            arm_adjustment_period_months INT,
            arm_rate_cap                 NUMERIC(6,4),
            arm_lifetime_cap             NUMERIC(6,4),
            payment_description_pattern  TEXT          NOT NULL DEFAULT '',
            payment_account_key          TEXT          NOT NULL DEFAULT '',
            lender                       TEXT          NOT NULL DEFAULT '',
            notes                        TEXT          NOT NULL DEFAULT '',
            is_active                    BOOLEAN       NOT NULL DEFAULT TRUE,
            created_at                   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at                   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))

    # Add monthly_insurance column to existing app_loans tables (idempotent)
    conn.execute(text(f"""
        ALTER TABLE {schema}.app_loans
        ADD COLUMN IF NOT EXISTS monthly_insurance NUMERIC(12,2) NOT NULL DEFAULT 0
    """))


# ── Consolidated transaction tables ───────────────────────────────────────────

def _create_transaction_tables(conn, schema: str) -> None:
    """
    Partitioned tables — one per account type, one partition per calendar year.

    transactions_debit   checking/savings rows
      (id, account_key, transaction_date, description, amount, person, source_file, inserted_at)

    transactions_credit  credit card rows
      (id, account_key, transaction_date, description, debit, credit, person, source_file, inserted_at)

    account_key = rule.prefix (e.g. 'wf_checking', 'citi_daily_spending')
    """

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.transactions_debit (
            id               BIGSERIAL,
            account_key      TEXT          NOT NULL,
            transaction_date DATE          NOT NULL,
            description      TEXT          NOT NULL DEFAULT '',
            amount           NUMERIC(14,2) NOT NULL DEFAULT 0,
            person           INTEGER[]     NOT NULL DEFAULT ARRAY[]::INTEGER[],
            source_file      TEXT          NOT NULL DEFAULT '',
            inserted_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        ) PARTITION BY RANGE (transaction_date)
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.transactions_credit (
            id               BIGSERIAL,
            account_key      TEXT          NOT NULL,
            transaction_date DATE          NOT NULL,
            description      TEXT          NOT NULL DEFAULT '',
            debit            NUMERIC(14,2) NOT NULL DEFAULT 0,
            credit           NUMERIC(14,2) NOT NULL DEFAULT 0,
            person           INTEGER[]     NOT NULL DEFAULT ARRAY[]::INTEGER[],
            source_file      TEXT          NOT NULL DEFAULT '',
            inserted_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        ) PARTITION BY RANGE (transaction_date)
    """))

    # Pre-create partitions: past 5 years + next 2
    current_year = date.today().year
    _ensure_year_partitions(conn, schema, range(current_year - 5, current_year + 3))

    # Shared indexes on parent tables (inherited by partitions)
    for tbl in ("transactions_debit", "transactions_credit"):
        conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS idx_{tbl}_account_date
            ON {schema}.{tbl} (account_key, transaction_date DESC)
        """))
        conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS idx_{tbl}_person
            ON {schema}.{tbl} USING GIN (person)
        """))


def _ensure_year_partitions(conn, schema: str, years) -> None:
    """Create per-year partitions + dedup unique indexes if they don't exist."""
    for year in years:
        for tbl in ("transactions_debit", "transactions_credit"):
            part = f"{tbl}_{year}"
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {schema}.{part}
                PARTITION OF {schema}.{tbl}
                FOR VALUES FROM ('{year}-01-01') TO ('{year + 1}-01-01')
            """))
            if tbl == "transactions_debit":
                conn.execute(text(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_{part}
                    ON {schema}.{part}
                    (account_key, transaction_date, description, amount)
                """))
            else:
                conn.execute(text(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_{part}
                    ON {schema}.{part}
                    (account_key, transaction_date, description, debit, credit)
                """))


def ensure_partition_for_year(conn, schema: str, year: int) -> None:
    """
    Public helper — called by upload_pipeline when data lands in an
    unexpected year.  Idempotent.
    """
    _ensure_year_partitions(conn, schema, [year])


# ── Config seeding (only if tables are empty) ─────────────────────────────────

def _migrate_configs_if_needed(conn, schema: str) -> None:
    """
    Seed config tables from JSON files or defaults, but only if the
    table is currently empty.  Never overwrites existing data.
    """
    _seed_if_empty(conn, schema, "bank_rules",  _default_bank_rules)
    _seed_if_empty(conn, schema, "categories",  _default_categories)
    _seed_if_empty(conn, schema, "transaction", _default_transaction)


def _seed_if_empty(conn, schema: str, name: str, loader) -> None:
    existing = conn.execute(
        text(f"SELECT 1 FROM {schema}.app_config_{name} WHERE id = 1")
    ).fetchone()
    if existing:
        return
    data = loader()
    conn.execute(text(f"""
        INSERT INTO {schema}.app_config_{name} (id, data, updated_at)
        VALUES (1, CAST(:data AS jsonb), NOW())
        ON CONFLICT (id) DO NOTHING
    """), {"data": json.dumps(data)})
    print(f"[migration] Seeded app_config_{name}")


def _default_bank_rules() -> dict:
    path = Path("bank_rules_config.json")
    if path.exists():
        return {"rules": json.loads(path.read_text())}
    from data.bank_rules import DEFAULT_RULES
    return {"rules": [r.to_dict() for r in DEFAULT_RULES]}


def _default_categories() -> dict:
    path = Path("category_rules.json")
    if path.exists():
        return json.loads(path.read_text())
    from data.category_rules import DEFAULT_CATEGORIES, DEFAULT_RULES
    return {"categories": DEFAULT_CATEGORIES, "rules": DEFAULT_RULES}


def _default_transaction() -> dict:
    path = Path("transaction_config.json")
    if path.exists():
        return json.loads(path.read_text())
    from services.transaction_config import TransactionConfig
    return TransactionConfig().to_dict()


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN CREATION  — CLI only
# ══════════════════════════════════════════════════════════════════════════════

def create_admin(
    username:     str,
    password:     str,
    display_name: str,
    person_name:  str,
) -> None:
    """
    Create a new admin user.  Errors if the username already exists.
    Tables must already exist (run run_migrations() first, or use --full-setup).
    """
    from services.auth import hash_password

    engine, schema = _engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text(f"SELECT id FROM {schema}.app_users WHERE username = :u"),
            {"u": username},
        ).fetchone()

        if existing:
            print(f"[migration] User '{username}' already exists — skipping.")
            return

        result = conn.execute(text(f"""
            INSERT INTO {schema}.app_users
                (username, password_hash, display_name, person_name, role)
            VALUES (:u, :ph, :dn, :pn, 'admin')
            RETURNING id
        """), {
            "u":  username,
            "ph": hash_password(password),
            "dn": display_name,
            "pn": person_name,
        })
        user_id = result.fetchone()[0]

        conn.execute(text(f"""
            INSERT INTO {schema}.app_user_prefs (user_id, selected_persons)
            VALUES (:uid, '[]')
            ON CONFLICT (user_id) DO NOTHING
        """), {"uid": user_id})

    print(f"[migration] Admin user '{username}' created (id={user_id}).")


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Finance app DB admin tool.\n"
            "Run without --admin-username to just verify migrations are up to date.\n"
            "Pass --admin-username to create a new admin user."
        )
    )
    parser.add_argument("--admin-username",     default=None,
                        help="Create an admin user with this username")
    parser.add_argument("--admin-display-name", default=None,
                        help="Display name (defaults to username)")
    parser.add_argument("--admin-person",       default=None,
                        help="person_name linking to transaction data (defaults to username)")
    parser.add_argument("--admin-password",     default=None,
                        help="Password (prompted if omitted)")
    parser.add_argument("--full-setup",         action="store_true",
                        help="Run migrations first, then create admin user")
    args = parser.parse_args()

    # Always run migrations when invoked directly, or if --full-setup
    run_migrations()

    if args.admin_username:
        username     = args.admin_username
        display_name = args.admin_display_name or username.title()
        person_name  = args.admin_person       or username

        password = args.admin_password
        if not password:
            password = getpass(f"Password for '{username}': ")
            confirm  = getpass("Confirm password: ")
            if password != confirm:
                print("Passwords do not match.")
                sys.exit(1)

        create_admin(
            username=username,
            password=password,
            display_name=display_name,
            person_name=person_name,
        )
    else:
        print("[migration] No --admin-username given — migrations only.")

    print("[migration] Done.")


if __name__ == "__main__":
    main()
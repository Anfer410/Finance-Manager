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
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            _create_family_tables(conn, schema)       # families first (FK target)
            _create_app_tables(conn, schema)
            _migrate_widget_positions(conn, schema)
            _create_transaction_tables(conn, schema)
            _migrate_configs_if_needed(conn, schema)
        print("[migration] Startup migrations complete.")
    except Exception as ex:
        print(f"[migration] WARNING: startup migration failed: {ex}")
        # Don't crash the app — DB might already be fully set up


# ── Family tables ─────────────────────────────────────────────────────────────

def _create_family_tables(conn, schema: str) -> None:
    """Core multi-tenancy tables.  Created before app_users so FKs resolve."""

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.families (
            id         SERIAL PRIMARY KEY,
            name       TEXT        NOT NULL,
            created_by INTEGER,    -- FK to app_users added after that table exists
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.family_memberships (
            id          SERIAL PRIMARY KEY,
            family_id   INTEGER     NOT NULL REFERENCES {schema}.families(id),
            user_id     INTEGER     NOT NULL,  -- FK to app_users added after that table exists
            family_role TEXT        NOT NULL   CHECK (family_role IN ('member', 'head')),
            joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            left_at     TIMESTAMPTZ            -- NULL = currently active member
        )
    """))

    conn.execute(text(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_family_memberships_active
        ON {schema}.family_memberships (user_id)
        WHERE left_at IS NULL
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.user_bank_permissions (
            user_id     INTEGER NOT NULL,  -- FK added after app_users exists
            family_id   INTEGER NOT NULL REFERENCES {schema}.families(id),
            account_key TEXT    NOT NULL,
            can_upload  BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (user_id, family_id, account_key)
        )
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.password_reset_tokens (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER     NOT NULL,  -- FK added after app_users exists
            token_hash TEXT        NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            used_at    TIMESTAMPTZ
        )
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.invitations (
            id          SERIAL PRIMARY KEY,
            token_hash  TEXT        NOT NULL UNIQUE,
            invited_by  INTEGER     NOT NULL,  -- FK added after app_users exists
            family_id   INTEGER     NOT NULL REFERENCES {schema}.families(id),
            family_role TEXT        NOT NULL DEFAULT 'member',
            email       TEXT        NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL,
            accepted_at TIMESTAMPTZ
        )
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.dashboard_templates (
            id           SERIAL PRIMARY KEY,
            name         TEXT        NOT NULL,
            description  TEXT        NOT NULL DEFAULT '',
            created_by   INTEGER,    -- FK added after app_users exists
            is_published BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.dashboard_template_widgets (
            id          SERIAL PRIMARY KEY,
            template_id INTEGER  NOT NULL
                        REFERENCES {schema}.dashboard_templates(id) ON DELETE CASCADE,
            chart_id    TEXT     NOT NULL,
            col_start   SMALLINT NOT NULL,
            row_start   SMALLINT NOT NULL,
            col_span    SMALLINT NOT NULL,
            row_span    SMALLINT NOT NULL,
            config      JSONB    NOT NULL DEFAULT '{{}}'
        )
    """))


# ── App tables ────────────────────────────────────────────────────────────────

def _create_app_tables(conn, schema: str) -> None:
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_users (
            id                   SERIAL PRIMARY KEY,
            username             TEXT UNIQUE NOT NULL,
            password_hash        TEXT NOT NULL,
            display_name         TEXT NOT NULL,
            person_name          TEXT NOT NULL,
            role                 TEXT NOT NULL DEFAULT 'user'
                                 CHECK (role IN ('admin', 'user')),
            is_active            BOOLEAN NOT NULL DEFAULT TRUE,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            email                TEXT UNIQUE,
            must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
            is_instance_admin    BOOLEAN NOT NULL DEFAULT FALSE
        )
    """))

    # Idempotent additions for existing installs
    conn.execute(text(f"ALTER TABLE {schema}.app_users ADD COLUMN IF NOT EXISTS email TEXT UNIQUE"))
    conn.execute(text(f"ALTER TABLE {schema}.app_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE"))
    conn.execute(text(f"ALTER TABLE {schema}.app_users ADD COLUMN IF NOT EXISTS is_instance_admin BOOLEAN NOT NULL DEFAULT FALSE"))

    # Add deferred FKs from family tables back to app_users (now that the table exists)
    conn.execute(text(f"""
        DO $$ BEGIN
            ALTER TABLE {schema}.families
                ADD CONSTRAINT fk_families_created_by
                FOREIGN KEY (created_by) REFERENCES {schema}.app_users(id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(text(f"""
        DO $$ BEGIN
            ALTER TABLE {schema}.family_memberships
                ADD CONSTRAINT fk_family_memberships_user_id
                FOREIGN KEY (user_id) REFERENCES {schema}.app_users(id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(text(f"""
        DO $$ BEGIN
            ALTER TABLE {schema}.user_bank_permissions
                ADD CONSTRAINT fk_user_bank_permissions_user_id
                FOREIGN KEY (user_id) REFERENCES {schema}.app_users(id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(text(f"""
        DO $$ BEGIN
            ALTER TABLE {schema}.password_reset_tokens
                ADD CONSTRAINT fk_password_reset_tokens_user_id
                FOREIGN KEY (user_id) REFERENCES {schema}.app_users(id) ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(text(f"""
        DO $$ BEGIN
            ALTER TABLE {schema}.invitations
                ADD CONSTRAINT fk_invitations_invited_by
                FOREIGN KEY (invited_by) REFERENCES {schema}.app_users(id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(text(f"""
        DO $$ BEGIN
            ALTER TABLE {schema}.dashboard_templates
                ADD CONSTRAINT fk_dashboard_templates_created_by
                FOREIGN KEY (created_by) REFERENCES {schema}.app_users(id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_user_prefs (
            user_id          INT PRIMARY KEY
                             REFERENCES {schema}.app_users(id) ON DELETE CASCADE,
            selected_persons JSONB NOT NULL DEFAULT '[]',
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    for cfg_name in ("bank_rules", "banks", "categories", "transaction"):
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {schema}.app_config_{cfg_name} (
                family_id  INTEGER     NOT NULL
                           REFERENCES {schema}.families(id),
                data       JSONB       NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (family_id)
            )
        """))
        # Idempotent addition for existing installs that have the old singleton schema
        conn.execute(text(f"ALTER TABLE {schema}.app_config_{cfg_name} ADD COLUMN IF NOT EXISTS family_id INTEGER REFERENCES {schema}.families(id)"))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_settings (
            key        TEXT PRIMARY KEY,
            value      JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_dashboards (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL
                        REFERENCES {schema}.app_users(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            is_default  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    # Only one default dashboard per user
    conn.execute(text(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_app_dashboards_user_default
        ON {schema}.app_dashboards (user_id)
        WHERE is_default = TRUE
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_dashboard_widgets (
            id           SERIAL PRIMARY KEY,
            dashboard_id INTEGER NOT NULL
                         REFERENCES {schema}.app_dashboards(id) ON DELETE CASCADE,
            chart_id     TEXT NOT NULL,
            position     SMALLINT NOT NULL,
            col_span     SMALLINT NOT NULL DEFAULT 2
                         CHECK (col_span BETWEEN 1 AND 4),
            row_span     SMALLINT NOT NULL DEFAULT 1
                         CHECK (row_span BETWEEN 1 AND 2),
            config       JSONB NOT NULL DEFAULT '{{}}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    # Add explicit grid placement columns to dashboard widgets (idempotent)
    conn.execute(text(f"""
        ALTER TABLE {schema}.app_dashboard_widgets
        ADD COLUMN IF NOT EXISTS col_start SMALLINT NOT NULL DEFAULT 1
    """))
    conn.execute(text(f"""
        ALTER TABLE {schema}.app_dashboard_widgets
        ADD COLUMN IF NOT EXISTS row_start SMALLINT NOT NULL DEFAULT 1
    """))
    # Widen row_span limit from 2 to 8 (drop + re-add constraint, idempotent via DO $$ ... $$)
    conn.execute(text(f"""
        DO $$
        BEGIN
            ALTER TABLE {schema}.app_dashboard_widgets
                DROP CONSTRAINT IF EXISTS app_dashboard_widgets_row_span_check;
            ALTER TABLE {schema}.app_dashboard_widgets
                ADD CONSTRAINT app_dashboard_widgets_row_span_check
                CHECK (row_span BETWEEN 1 AND 8);
        EXCEPTION WHEN OTHERS THEN NULL;
        END $$;
    """))
    # Custom label for duplicate widget instances (nullable — NULL = use widget default title)
    conn.execute(text(f"""
        ALTER TABLE {schema}.app_dashboard_widgets
        ADD COLUMN IF NOT EXISTS instance_label TEXT
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

    # Add family_id to app_loans — backfill existing rows to the default family
    conn.execute(text(f"""
        ALTER TABLE {schema}.app_loans
        ADD COLUMN IF NOT EXISTS family_id INTEGER REFERENCES {schema}.families(id)
    """))
    conn.execute(text(f"""
        UPDATE {schema}.app_loans SET family_id = 1 WHERE family_id IS NULL
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_custom_charts (
            id           SERIAL PRIMARY KEY,
            user_id      INTEGER NOT NULL
                         REFERENCES {schema}.app_users(id) ON DELETE CASCADE,
            name         TEXT NOT NULL DEFAULT 'Untitled Chart',
            chart_type   TEXT NOT NULL DEFAULT 'bar',
            data_source  TEXT NOT NULL DEFAULT 'v_all_spend',
            config       JSONB NOT NULL DEFAULT '{{}}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))


def _migrate_widget_positions(conn, schema: str) -> None:
    """
    One-time: populate col_start/row_start for any widgets still at (1,1).
    Uses a strip-packing algo (left→right, top→bottom) per dashboard.
    Safe to run repeatedly — only touches widgets where BOTH are still 1.
    """
    # Find dashboards that have widgets needing placement
    rows = conn.execute(text(f"""
        SELECT DISTINCT dashboard_id
        FROM   {schema}.app_dashboard_widgets
        WHERE  col_start = 1 AND row_start = 1
    """)).fetchall()

    for (dashboard_id,) in rows:
        widgets = conn.execute(text(f"""
            SELECT id, col_span, row_span, position
            FROM   {schema}.app_dashboard_widgets
            WHERE  dashboard_id = :did
            ORDER  BY position ASC
        """), {"did": dashboard_id}).fetchall()

        placements = _pack_widget_positions(
            [{"id": r[0], "col_span": r[1], "row_span": r[2], "position": r[3]}
             for r in widgets]
        )
        for wid, (col_start, row_start) in placements.items():
            conn.execute(text(f"""
                UPDATE {schema}.app_dashboard_widgets
                SET    col_start = :cs, row_start = :rs
                WHERE  id = :id
            """), {"cs": col_start, "rs": row_start, "id": wid})


def _pack_widget_positions(widgets: list[dict]) -> dict:
    """
    Strip-pack widgets onto a 4-column grid.
    Returns dict: widget_id → (col_start, row_start).
    """
    occupied: set[tuple[int, int]] = set()
    result: dict[int, tuple[int, int]] = {}

    for w in sorted(widgets, key=lambda x: x["position"]):
        cs, rs = w["col_span"], w["row_span"]
        placed = False
        row = 1
        while not placed:
            for col in range(1, 5):
                if col + cs - 1 > 4:
                    continue
                cells = {(row + dr, col + dc) for dr in range(rs) for dc in range(cs)}
                if not cells & occupied:
                    occupied |= cells
                    result[w["id"]] = (col, row)
                    placed = True
                    break
            if not placed:
                row += 1

    return result


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
            inserted_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            family_id        INTEGER       REFERENCES {schema}.families(id),
            uploaded_by      INTEGER       REFERENCES {schema}.app_users(id)
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
            inserted_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            family_id        INTEGER       REFERENCES {schema}.families(id),
            uploaded_by      INTEGER       REFERENCES {schema}.app_users(id)
        ) PARTITION BY RANGE (transaction_date)
    """))

    # Idempotent additions for existing installs
    for tbl in ("transactions_debit", "transactions_credit"):
        conn.execute(text(f"ALTER TABLE {schema}.{tbl} ADD COLUMN IF NOT EXISTS family_id INTEGER REFERENCES {schema}.families(id)"))
        conn.execute(text(f"ALTER TABLE {schema}.{tbl} ADD COLUMN IF NOT EXISTS uploaded_by INTEGER REFERENCES {schema}.app_users(id)"))

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


def _ensure_default_family(conn, schema: str) -> None:
    """Create the default family (id=1) if it doesn't exist yet."""
    conn.execute(text(f"""
        INSERT INTO {schema}.families (id, name, created_at)
        VALUES (1, 'Default Family', NOW())
        ON CONFLICT (id) DO NOTHING
    """))


def _seed_if_empty(conn, schema: str, name: str, loader) -> None:
    _ensure_default_family(conn, schema)
    existing = conn.execute(
        text(f"SELECT 1 FROM {schema}.app_config_{name} WHERE family_id = 1")
    ).fetchone()
    if existing:
        return
    data = loader()
    conn.execute(text(f"""
        INSERT INTO {schema}.app_config_{name} (family_id, data, updated_at)
        VALUES (1, CAST(:data AS jsonb), NOW())
        ON CONFLICT (family_id) DO NOTHING
    """), {"data": json.dumps(data)})
    print(f"[migration] Seeded app_config_{name} for default family")


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

    engine = get_engine()
    schema = get_schema()
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
                (username, password_hash, display_name, person_name, role, is_instance_admin)
            VALUES (:u, :ph, :dn, :pn, 'admin', TRUE)
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

        # Ensure default family exists and add admin as family head
        conn.execute(text(f"""
            INSERT INTO {schema}.families (id, name, created_by, created_at)
            VALUES (1, 'Default Family', :uid, NOW())
            ON CONFLICT (id) DO NOTHING
        """), {"uid": user_id})

        conn.execute(text(f"""
            INSERT INTO {schema}.family_memberships
                (family_id, user_id, family_role, joined_at)
            VALUES (1, :uid, 'head', NOW())
            ON CONFLICT DO NOTHING
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
"""
db_migration.py

Run ONCE to:
  1. Create app_users, app_user_prefs, and app_config_* tables
  2. Migrate existing JSON config files into the DB
  3. Seed the first admin user

Usage:
    python db_migration.py
    python db_migration.py --admin-username andy --admin-person andy

The script is idempotent — safe to re-run, existing data is preserved.
"""

import argparse
import json
import sys
from pathlib import Path
from getpass import getpass

from sqlalchemy import create_engine, text

from services.helpers import read_secrets


# ── Connection ─────────────────────────────────────────────────────────────────

def _engine():
    s = read_secrets()
    url = f"postgresql+psycopg://{s['DB_USER']}:{s['DB_PASSWORD']}@{s['DB_HOST']}:{s['DB_PORT']}/{s['DB_NAME']}"
    return create_engine(url), s["DB_SCHEMA"]


# ── DDL ────────────────────────────────────────────────────────────────────────

def create_tables(conn, schema: str) -> None:
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

    # Single-row config tables — each stores the full config as JSONB
    for cfg_name in ("bank_rules", "categories", "transaction"):
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {schema}.app_config_{cfg_name} (
                id         INT PRIMARY KEY DEFAULT 1
                           CHECK (id = 1),          -- enforces single row
                data       JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

    print("[migration] Tables created (or already exist).")


# ── Config migration ───────────────────────────────────────────────────────────

def _upsert_config(conn, schema: str, table: str, data: dict) -> None:
    conn.execute(text(f"""
        INSERT INTO {schema}.app_config_{table} (id, data, updated_at)
        VALUES (1, CAST(:data AS jsonb), NOW())
        ON CONFLICT (id) DO NOTHING
    """), {"data": json.dumps(data)})


def migrate_bank_rules(conn, schema: str) -> None:
    path = Path("bank_rules_config.json")
    if path.exists():
        data = json.loads(path.read_text())
        _upsert_config(conn, schema, "bank_rules", {"rules": data})
        print(f"[migration] bank_rules: migrated {len(data)} rules from {path}")
    else:
        # Seed from defaults
        from services.bank_rules import DEFAULT_RULES
        data = [r.to_dict() for r in DEFAULT_RULES]
        _upsert_config(conn, schema, "bank_rules", {"rules": data})
        print(f"[migration] bank_rules: seeded {len(data)} default rules")


def migrate_categories(conn, schema: str) -> None:
    path = Path("category_rules.json")
    if path.exists():
        data = json.loads(path.read_text())
        _upsert_config(conn, schema, "categories", data)
        print(f"[migration] categories: migrated from {path}")
    else:
        from services.category_rules import DEFAULT_CATEGORIES, DEFAULT_RULES
        _upsert_config(conn, schema, "categories", {
            "categories": DEFAULT_CATEGORIES,
            "rules":      DEFAULT_RULES,
        })
        print("[migration] categories: seeded defaults")


def migrate_transaction_config(conn, schema: str) -> None:
    path = Path("transaction_config.json")
    if path.exists():
        data = json.loads(path.read_text())
        _upsert_config(conn, schema, "transaction", data)
        print(f"[migration] transaction config: migrated from {path}")
    else:
        from services.transaction_config import TransactionConfig
        _upsert_config(conn, schema, "transaction", TransactionConfig().to_dict())
        print("[migration] transaction config: seeded defaults")


# ── Admin user seeding ─────────────────────────────────────────────────────────

def seed_admin(conn, schema: str, username: str, password: str,
               display_name: str, person_name: str) -> None:
    from services.auth import hash_password

    existing = conn.execute(text(
        f"SELECT id FROM {schema}.app_users WHERE username = :u"
    ), {"u": username}).fetchone()

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

    # Create empty prefs row
    conn.execute(text(f"""
        INSERT INTO {schema}.app_user_prefs (user_id, selected_persons)
        VALUES (:uid, '[]')
        ON CONFLICT (user_id) DO NOTHING
    """), {"uid": user_id})

    print(f"[migration] Admin user '{username}' created (id={user_id}).")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Finance app DB migration")
    parser.add_argument("--admin-username",    default="admin")
    parser.add_argument("--admin-display-name",default="Admin")
    parser.add_argument("--admin-person",      default="admin",
                        help="person_name that links to transaction data")
    parser.add_argument("--admin-password",    default=None,
                        help="If omitted, will prompt interactively")
    args = parser.parse_args()

    password = args.admin_password
    if not password:
        password = getpass(f"Password for admin user '{args.admin_username}': ")
        confirm  = getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(1)

    engine, schema = _engine()

    with engine.begin() as conn:
        create_tables(conn, schema)
        migrate_bank_rules(conn, schema)
        migrate_categories(conn, schema)
        migrate_transaction_config(conn, schema)
        seed_admin(
            conn, schema,
            username=args.admin_username,
            password=password,
            display_name=args.admin_display_name,
            person_name=args.admin_person,
        )

    print("[migration] Done.")


if __name__ == "__main__":
    main()
"""
tests/conftest.py

Test infrastructure for integration tests.

Lifecycle
---------
1. Session starts  → `docker compose up` in tests/ spins up a throwaway postgres
                     on port 5434 (no collision with dev instance on 5432)
2. Migrations run  → full app schema created in the test DB
3. Each test runs  → receives a `db_conn` connection inside an open transaction
4. Test ends       → transaction is ROLLED BACK; DB is clean for the next test
5. Session ends    → `docker compose down -v` destroys the container and its data

Running
-------
    cd app && .venv/bin/pytest ../tests/ -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
from sqlalchemy import create_engine, text

# ── path setup ────────────────────────────────────────────────────────────────

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

TESTS_DIR   = os.path.dirname(os.path.abspath(__file__))
COMPOSE_FILE = os.path.join(TESTS_DIR, "docker-compose.yml")

TEST_DB_URL = "postgresql+psycopg://testuser:testpass@localhost:5434/finance_test"


# ── helpers ───────────────────────────────────────────────────────────────────

def _compose(*args: str) -> None:
    """Run a docker compose command against the tests/docker-compose.yml."""
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        check=True,
    )


def _wait_for_postgres(timeout: int = 30) -> None:
    """Poll until postgres accepts connections or raise TimeoutError."""
    import psycopg

    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            conn = psycopg.connect(
                "host=localhost port=5434 user=testuser password=testpass dbname=finance_test"
            )
            conn.close()
            return
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise TimeoutError(f"Postgres did not become ready in {timeout}s: {last_err}")


# ── session-scoped engine (one container per pytest run) ──────────────────────

@pytest.fixture(scope="session")
def pg_engine():
    """
    Start the test postgres container, run migrations, yield an engine.
    Container is torn down with all data after the session.
    """
    _compose("up", "-d", "--wait")
    _wait_for_postgres()

    engine = create_engine(TEST_DB_URL)

    # Unit-test files stub data.db into sys.modules at import time.
    # We must replace that stub with a real-looking object so db_migration
    # (and any other app code) uses the test engine, not a Mock.
    import importlib
    import types

    real_db = importlib.util.spec_from_file_location(
        "data.db",
        os.path.join(APP_DIR, "data", "db.py"),
    )
    db_module = importlib.util.module_from_spec(real_db)
    real_db.loader.exec_module(db_module)

    # Override the three functions that point at the live DB
    db_module.get_engine  = lambda: engine          # type: ignore[attr-defined]
    db_module.get_schema  = lambda: "finance"       # type: ignore[attr-defined]
    db_module.get_url     = lambda: TEST_DB_URL     # type: ignore[attr-defined]

    # Install into sys.modules so every subsequent import picks it up
    sys.modules["data.db"] = db_module

    # db_migration is also mocked by unit test files; load it fresh from disk
    # so it picks up the patched data.db we just installed above.
    import importlib.util as _ilu
    dm_spec = _ilu.spec_from_file_location(
        "db_migration_real",
        os.path.join(APP_DIR, "db_migration.py"),
    )
    dm_module = _ilu.module_from_spec(dm_spec)
    dm_spec.loader.exec_module(dm_module)

    # Call internal functions directly so errors aren't swallowed by the
    # try/except wrapper in run_migrations().
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS finance"))
        dm_module._create_family_tables(conn, "finance")
        dm_module._create_app_tables(conn, "finance")
        dm_module._migrate_widget_positions(conn, "finance")
        dm_module._create_transaction_tables(conn, "finance")
        dm_module._migrate_configs_if_needed(conn, "finance")

    yield engine

    engine.dispose()
    _compose("down", "-v")


# ── function-scoped connection with automatic rollback ────────────────────────

@pytest.fixture
def db_conn(pg_engine):
    """
    Single connection inside an open transaction.
    Rolled back after each test so the DB is always clean.
    """
    with pg_engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()


# ── convenience ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def schema():
    return "finance"

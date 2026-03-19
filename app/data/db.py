"""
data.db.py

Single source of truth for database connectivity.

Every other module imports from here instead of calling read_secrets() directly
for DB purposes.

Usage:
    from data.db import get_engine, get_schema, get_conn_tuple

    engine = get_engine()
    schema = get_schema()
    conn   = get_conn_tuple()   # (user, password, host, port, dbname)
"""

from __future__ import annotations

from functools import lru_cache
from sqlalchemy import Engine, create_engine
from services.helpers import env


# ── Connection config (from env) ──────────────────────────────────────────────

def get_conn_tuple() -> tuple[str, str, str, int, str]:
    """Return (user, password, host, port, dbname) from environment."""
    return (
        env("DB_USER",     "psqlroot"),
        env("DB_PASSWORD", "password"),
        env("DB_HOST",     "localhost"),
        int(env("DB_PORT", "5432")),
        env("DB_NAME",     "finance-manager"),
    )


def get_schema() -> str:
    return env("DB_SCHEMA", "finance")


def get_url() -> str:
    user, password, host, port, db = get_conn_tuple()
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def get_psycopg_dsn() -> str:
    user, password, host, port, db = get_conn_tuple()
    return f"host={host} port={port} user={user} password={password} dbname={db}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Cached SQLAlchemy engine — one per process."""
    return create_engine(get_url())

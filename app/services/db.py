"""
services/db.py

Single source of truth for database connectivity and app-level settings
(archive path, archive enabled flag).

Every other module imports from here instead of calling read_secrets() directly
for DB purposes.

Usage:
    from services.db import get_engine, get_schema, get_conn_tuple, get_archive_cfg

    engine = get_engine()
    schema = get_schema()
    conn   = get_conn_tuple()   # (user, password, host, port, dbname)
    arc    = get_archive_cfg()  # ArchiveConfig(path=..., enabled=...)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import dotenv
from sqlalchemy import Engine, create_engine

dotenv.load_dotenv()


# ── Connection config (from env) ──────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def get_conn_tuple() -> tuple[str, str, str, int, str]:
    """Return (user, password, host, port, dbname) from environment."""
    return (
        _env("DB_USER",     "psqlroot"),
        _env("DB_PASSWORD", "password"),
        _env("DB_HOST",     "localhost"),
        int(_env("DB_PORT", "5432")),
        _env("DB_NAME",     "finance-manager"),
    )


def get_schema() -> str:
    return _env("DB_SCHEMA", "finance")


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


# ── Archive config ─────────────────────────────────────────────────────────────

@dataclass
class ArchiveConfig:
    path:    str  = ".archive"
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"path": self.path, "enabled": self.enabled}

    @staticmethod
    def from_dict(d: dict) -> "ArchiveConfig":
        return ArchiveConfig(
            path    = d.get("path",    ".archive"),
            enabled = d.get("enabled", True),
        )


def get_archive_cfg() -> ArchiveConfig:
    """
    Returns archive config.  Priority:
      1. DB (app_settings table, key='archive') — set via Settings UI
      2. Environment variables (ARCHIVES_FOLDER, ARCHIVE_ENABLED)
      3. Defaults
    """
    try:
        from services.config_repo import load_app_settings
        data = load_app_settings()
        arc = data.get("archive")
        if arc is not None:
            return ArchiveConfig.from_dict(arc)
    except Exception:
        pass

    return ArchiveConfig(
        path    = _env("ARCHIVES_FOLDER",  ".archive"),
        enabled = _env("ARCHIVE_ENABLED",  "true").lower() not in ("false", "0", "no"),
    )
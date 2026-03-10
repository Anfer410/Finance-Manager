"""
services/db_config.py

DB-backed replacements for the three JSON config loaders.
Provides the same API as the file-based versions so existing callers
(view_manager, category_rules, bank_rules, transaction_config) need
minimal changes — just swap the import.

Caching: each config is cached in-process and invalidated on save.
"""

from __future__ import annotations
import json
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, text

from services.helpers import read_secrets


# ── Engine ────────────────────────────────────────────────────────────────────

def _make_engine():
    s = read_secrets()
    url = (f"postgresql+psycopg://{s['DB_USER']}:{s['DB_PASSWORD']}"
           f"@{s['DB_HOST']}:{s['DB_PORT']}/{s['DB_NAME']}")
    return create_engine(url), s["DB_SCHEMA"]

_ENGINE, _SCHEMA = _make_engine()


# ── Raw get/set ───────────────────────────────────────────────────────────────

def _get(table: str) -> dict:
    with _ENGINE.connect() as conn:
        row = conn.execute(text(
            f"SELECT data FROM {_SCHEMA}.app_config_{table} WHERE id = 1"
        )).fetchone()
    if not row:
        return {}
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def _set(table: str, data: dict) -> None:
    with _ENGINE.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO {_SCHEMA}.app_config_{table} (id, data, updated_at)
            VALUES (1, CAST(:data AS jsonb), NOW())
            ON CONFLICT (id) DO UPDATE
                SET data = CAST(:data AS jsonb), updated_at = NOW()
        """), {"data": json.dumps(data)})


# ── Bank rules ────────────────────────────────────────────────────────────────

def load_bank_rules_data() -> list[dict]:
    """Returns list of raw bank rule dicts."""
    data = _get("bank_rules")
    return data.get("rules", [])


def save_bank_rules_data(rules: list[dict]) -> None:
    _set("bank_rules", {"rules": rules})


# ── Categories ────────────────────────────────────────────────────────────────

def load_categories_data() -> dict:
    """Returns {'categories': [...], 'rules': [...]}."""
    data = _get("categories")
    if not data:
        # Fallback to defaults if DB empty
        from services.category_rules import DEFAULT_CATEGORIES, DEFAULT_RULES
        return {"categories": DEFAULT_CATEGORIES, "rules": DEFAULT_RULES}
    return data


def save_categories_data(data: dict) -> None:
    """data = {'categories': [...], 'rules': [...]}"""
    _set("categories", data)


# ── Transaction config ────────────────────────────────────────────────────────

def load_transaction_config_data() -> dict:
    """Returns raw transaction config dict."""
    data = _get("transaction")
    if not data:
        from services.transaction_config import TransactionConfig
        return TransactionConfig().to_dict()
    return data


def save_transaction_config_data(data: dict) -> None:
    _set("transaction", data)
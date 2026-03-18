"""
services/config_repo.py

Single repository for all persisted configuration.
Replaces data.db_config.py — callers should switch to this module.

All functions read from / write to the app_config_* and app_settings tables.
The dataclass models (BankRule, CategoryConfig, TransactionConfig) live in
their own files and remain pure data — no DB imports there.

Table layout (all in the configured schema):
    app_config_bank_rules    — id=1, data JSONB  {rules: [...]}
    app_config_banks         — id=1, data JSONB  {banks: [...]}
    app_config_categories    — id=1, data JSONB  {categories: [...], rules: [...]}
    app_config_transaction   — id=1, data JSONB  {transfer_patterns, employer_patterns, member_aliases}
    app_settings             — key TEXT PK, value JSONB   (generic k/v store)

Public API
──────────
    load_bank_rules()       -> list[dict]
    save_bank_rules(rules)

    load_banks()            -> list[dict]
    save_banks(banks)

    load_categories()       -> dict   {categories, rules}
    save_categories(data)

    load_transaction_cfg()  -> dict
    save_transaction_cfg(data)

    load_app_settings()     -> dict   {archive: {path, enabled}, ...}
    save_app_settings(data)
    patch_app_settings(**kw)          update individual top-level keys
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from data.db import get_engine, get_schema


# ── Internal helpers ──────────────────────────────────────────────────────────

def _engine():
    return get_engine()

def _schema():
    return get_schema()


def _config_get(table_suffix: str) -> dict:
    """Read a single-row config table (id=1).  Returns {} if missing."""
    sql = f"SELECT data FROM {_schema()}.app_config_{table_suffix} WHERE id = 1"
    try:
        with _engine().connect() as conn:
            row = conn.execute(text(sql)).fetchone()
        if not row:
            return {}
        val = row[0]
        return val if isinstance(val, dict) else json.loads(val)
    except Exception as e:
        print(f"[config_repo] read {table_suffix} failed: {e}")
        return {}


def _config_set(table_suffix: str, data: dict) -> None:
    """Upsert a single-row config table (id=1)."""
    sql = f"""
        INSERT INTO {_schema()}.app_config_{table_suffix} (id, data, updated_at)
        VALUES (1, CAST(:data AS jsonb), NOW())
        ON CONFLICT (id) DO UPDATE
            SET data = CAST(:data AS jsonb), updated_at = NOW()
    """
    with _engine().begin() as conn:
        conn.execute(text(sql), {"data": json.dumps(data)})


def _settings_get() -> dict:
    """Read all app_settings rows into a flat dict {key: value}."""
    sql = f"SELECT key, value FROM {_schema()}.app_settings"
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(sql)).fetchall()
        result = {}
        for key, val in rows:
            result[key] = val if isinstance(val, (dict, list)) else json.loads(val)
        return result
    except Exception as e:
        print(f"[config_repo] read app_settings failed: {e}")
        return {}


def _settings_set(data: dict) -> None:
    """Upsert multiple app_settings rows from a dict."""
    sql = f"""
        INSERT INTO {_schema()}.app_settings (key, value, updated_at)
        VALUES (:key, CAST(:value AS jsonb), NOW())
        ON CONFLICT (key) DO UPDATE
            SET value = CAST(:value AS jsonb), updated_at = NOW()
    """
    with _engine().begin() as conn:
        for key, val in data.items():
            conn.execute(text(sql), {"key": key, "value": json.dumps(val)})


# ── Bank rules ────────────────────────────────────────────────────────────────

def load_bank_rules() -> list[dict]:
    """Returns list of raw bank-rule dicts."""
    data = _config_get("bank_rules")
    return data.get("rules", [])


def save_bank_rules(rules: list[dict]) -> None:
    _config_set("bank_rules", {"rules": rules})


# ── Banks ─────────────────────────────────────────────────────────────────────

def load_banks() -> list[dict]:
    """Returns list of BankConfig dicts."""
    data = _config_get("banks")
    return data.get("banks", [])


def save_banks(banks: list[dict]) -> None:
    _config_set("banks", {"banks": banks})


# ── Categories ────────────────────────────────────────────────────────────────

def load_categories() -> dict:
    """Returns {'categories': [...], 'rules': [...]}."""
    data = _config_get("categories")
    if not data:
        # Seed from defaults on first call
        from data.category_rules import DEFAULT_CATEGORIES, DEFAULT_RULES
        return {"categories": DEFAULT_CATEGORIES, "rules": DEFAULT_RULES}
    return data


def save_categories(data: dict) -> None:
    """data must be {'categories': [...], 'rules': [...]}"""
    _config_set("categories", data)


# ── Transaction config ────────────────────────────────────────────────────────

def load_transaction_cfg() -> dict:
    """Returns raw transaction-config dict."""
    data = _config_get("transaction")
    if not data:
        from services.transaction_config import TransactionConfig
        return TransactionConfig().to_dict()
    return data


def save_transaction_cfg(data: dict) -> None:
    _config_set("transaction", data)


# ── App settings (generic k/v) ────────────────────────────────────────────────

def load_app_settings() -> dict:
    """
    Returns merged app settings dict.
    Keys currently in use:
        'archive'  → {'path': str, 'enabled': bool}
    """
    return _settings_get()


def save_app_settings(data: dict) -> None:
    """Replace / upsert all keys in data."""
    _settings_set(data)


def patch_app_settings(**kwargs: Any) -> None:
    """Update individual top-level keys without touching others."""
    _settings_set(kwargs)


# ── Backward-compat shims (for code still importing db_config) ────────────────
# These will be removed once all callers are updated.

def load_bank_rules_data() -> list[dict]:
    return load_bank_rules()

def save_bank_rules_data(rules: list[dict]) -> None:
    save_bank_rules(rules)

def load_categories_data() -> dict:
    return load_categories()

def save_categories_data(data: dict) -> None:
    save_categories(data)

def load_transaction_config_data() -> dict:
    return load_transaction_cfg()

def save_transaction_config_data(data: dict) -> None:
    save_transaction_cfg(data)
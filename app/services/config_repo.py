"""
services/config_repo.py

Single repository for all persisted configuration.

All family-scoped config functions require a `family_id` parameter.
Pass `auth.current_family_id()` from page/component context.
`app_settings` is instance-level (SMTP, archive) and is NOT family-scoped.

Table layout (all in the configured schema):
    app_config_bank_rules  — family_id PK, data JSONB  {rules: [...]}
    app_config_banks       — family_id PK, data JSONB  {banks: [...]}
    app_config_categories  — family_id PK, data JSONB  {categories: [...], rules: [...]}
    app_config_transaction — family_id PK, data JSONB  {transfer_patterns, employer_patterns, member_aliases}
    app_settings           — key TEXT PK, value JSONB  (instance-wide k/v)

Public API
──────────
    load_bank_rules(family_id)       -> list[dict]
    save_bank_rules(rules, family_id)

    load_banks(family_id)            -> list[dict]
    save_banks(banks, family_id)

    load_categories(family_id)       -> dict   {categories, rules}
    save_categories(data, family_id)

    load_transaction_cfg(family_id)  -> dict
    save_transaction_cfg(data, family_id)

    load_app_settings()     -> dict   (instance-wide)
    save_app_settings(data)
    patch_app_settings(**kw)
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from data.db import get_engine, get_schema


def _engine():
    return get_engine()

def _schema():
    return get_schema()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _config_get(table_suffix: str, family_id: int) -> dict:
    """Read a family-scoped config table row. Returns {} if missing."""
    sql = f"SELECT data FROM {_schema()}.app_config_{table_suffix} WHERE family_id = :fid"
    try:
        with _engine().connect() as conn:
            row = conn.execute(text(sql), {"fid": family_id}).fetchone()
        if not row:
            return {}
        val = row[0]
        return val if isinstance(val, dict) else json.loads(val)
    except Exception as e:
        print(f"[config_repo] read {table_suffix} (family={family_id}) failed: {e}")
        return {}


def _config_set(table_suffix: str, family_id: int, data: dict) -> None:
    """Upsert a family-scoped config table row."""
    sql = f"""
        INSERT INTO {_schema()}.app_config_{table_suffix} (family_id, data, updated_at)
        VALUES (:fid, CAST(:data AS jsonb), NOW())
        ON CONFLICT (family_id) DO UPDATE
            SET data = CAST(:data AS jsonb), updated_at = NOW()
    """
    with _engine().begin() as conn:
        conn.execute(text(sql), {"fid": family_id, "data": json.dumps(data)})


def _settings_get() -> dict:
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

def load_bank_rules(family_id: int) -> list[dict]:
    data = _config_get("bank_rules", family_id)
    return data.get("rules", [])


def save_bank_rules(rules: list[dict], family_id: int) -> None:
    _config_set("bank_rules", family_id, {"rules": rules})


# ── Banks ─────────────────────────────────────────────────────────────────────

def load_banks(family_id: int) -> list[dict]:
    data = _config_get("banks", family_id)
    return data.get("banks", [])


def save_banks(banks: list[dict], family_id: int) -> None:
    _config_set("banks", family_id, {"banks": banks})


# ── Categories ────────────────────────────────────────────────────────────────

def load_categories(family_id: int) -> dict:
    data = _config_get("categories", family_id)
    if not data:
        from data.category_rules import DEFAULT_CATEGORIES, DEFAULT_RULES
        return {"categories": DEFAULT_CATEGORIES, "rules": DEFAULT_RULES}
    return data


def save_categories(data: dict, family_id: int) -> None:
    _config_set("categories", family_id, data)


# ── Transaction config ────────────────────────────────────────────────────────

def load_transaction_cfg(family_id: int) -> dict:
    data = _config_get("transaction", family_id)
    if not data:
        from services.transaction_config import TransactionConfig
        return TransactionConfig().to_dict()
    return data


def save_transaction_cfg(data: dict, family_id: int) -> None:
    _config_set("transaction", family_id, data)


# ── App settings (instance-wide, not family-scoped) ───────────────────────────

def load_app_settings() -> dict:
    return _settings_get()


def save_app_settings(data: dict) -> None:
    _settings_set(data)


def patch_app_settings(**kwargs: Any) -> None:
    _settings_set(kwargs)

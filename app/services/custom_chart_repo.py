"""
services/custom_chart_repo.py

CRUD for app_custom_charts table.

Public API
──────────
    list_custom_charts(user_id)                                      → list[dict]
    get_custom_chart(chart_id)                                       → dict | None
    create_custom_chart(user_id, name, chart_type, data_source, config) → int
    update_custom_chart(chart_id, name, chart_type, data_source, config) → None
    delete_custom_chart(chart_id)                                    → None
"""

from __future__ import annotations

import json

from sqlalchemy import text

from data.db import get_engine, get_schema


def _engine():
    return get_engine()


def _schema():
    return get_schema()


def list_custom_charts(user_id: int) -> list[dict]:
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, user_id, name, chart_type, data_source, config, created_at, updated_at
            FROM   {_schema()}.app_custom_charts
            WHERE  user_id = :uid
            ORDER  BY id ASC
        """), {"uid": user_id}).fetchall()
    return [
        {
            "id":          r[0],
            "user_id":     r[1],
            "name":        r[2],
            "chart_type":  r[3],
            "data_source": r[4],
            "config":      r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
            "created_at":  r[6],
            "updated_at":  r[7],
        }
        for r in rows
    ]


def get_custom_chart(chart_id: int) -> dict | None:
    with _engine().connect() as conn:
        row = conn.execute(text(f"""
            SELECT id, user_id, name, chart_type, data_source, config, created_at, updated_at
            FROM   {_schema()}.app_custom_charts
            WHERE  id = :cid
        """), {"cid": chart_id}).fetchone()
    if not row:
        return None
    return {
        "id":          row[0],
        "user_id":     row[1],
        "name":        row[2],
        "chart_type":  row[3],
        "data_source": row[4],
        "config":      row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}"),
        "created_at":  row[6],
        "updated_at":  row[7],
    }


def create_custom_chart(
    user_id: int,
    name: str,
    chart_type: str,
    data_source: str,
    config: dict,
) -> int:
    with _engine().begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {_schema()}.app_custom_charts
                (user_id, name, chart_type, data_source, config)
            VALUES (:uid, :name, :ct, :ds, CAST(:cfg AS jsonb))
            RETURNING id
        """), {
            "uid":  user_id,
            "name": name,
            "ct":   chart_type,
            "ds":   data_source,
            "cfg":  json.dumps(config),
        }).fetchone()
    return row[0]


def update_custom_chart(
    chart_id: int,
    name: str,
    chart_type: str,
    data_source: str,
    config: dict,
) -> None:
    with _engine().begin() as conn:
        conn.execute(text(f"""
            UPDATE {_schema()}.app_custom_charts
            SET    name        = :name,
                   chart_type  = :ct,
                   data_source = :ds,
                   config      = CAST(:cfg AS jsonb),
                   updated_at  = NOW()
            WHERE  id = :cid
        """), {
            "cid":  chart_id,
            "name": name,
            "ct":   chart_type,
            "ds":   data_source,
            "cfg":  json.dumps(config),
        })


def delete_custom_chart(chart_id: int) -> None:
    with _engine().begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {_schema()}.app_custom_charts WHERE id = :cid
        """), {"cid": chart_id})

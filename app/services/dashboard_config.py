"""
services/dashboard_config.py

CRUD for user dashboards and their widgets.

Tables (created by db_migration._create_app_tables):
    app_dashboards         — one named dashboard per row, one default per user
    app_dashboard_widgets  — one widget slot per row, ordered by position

Public API
──────────
    list_dashboards(user_id)                              → list[dict]
    get_or_create_default(user_id)                        → int  (dashboard_id)
    create_dashboard(user_id, name)                       → int
    rename_dashboard(dashboard_id, name)                  → None
    delete_dashboard(dashboard_id, user_id)               → None  (guards default)

    get_widgets(dashboard_id)                             → list[dict]
    save_widget_layout(dashboard_id, widgets)             → None  (full replace)
    add_widget(dashboard_id, chart_id, *, col_span, row_span, config)  → int
    remove_widget(widget_id)                              → None
    update_widget_config(widget_id, config)               → None
    update_widget_layout(widget_id, *, col_span, row_span, position)   → None

Widget dict shape (returned by get_widgets / consumed by save_widget_layout):
    {
        "id":        int,          # absent when passed to save_widget_layout
        "chart_id":  str,          # matches ChartDef.id in dashboard_registry
        "position":  int,          # 0-based display order
        "col_span":  int,          # 1–4
        "row_span":  int,          # 1–2
        "config":    dict,         # JSONB blob — per-widget options
                                   # e.g. {"persons": [1, 2], "inverted": false}
    }

Multi-tenancy note
──────────────────
user_id is the FK to app_users.id.  When tenant isolation is added, a tenant_id
column can be appended to both tables without changing this service's API.
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


# ── Dashboard CRUD ────────────────────────────────────────────────────────────

def list_dashboards(user_id: int) -> list[dict]:
    """Return all dashboards for user ordered: default first, then by id."""
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, name, is_default, created_at, updated_at
            FROM   {_schema()}.app_dashboards
            WHERE  user_id = :uid
            ORDER  BY is_default DESC, id ASC
        """), {"uid": user_id}).fetchall()
    return [
        {"id": r[0], "name": r[1], "is_default": r[2],
         "created_at": r[3], "updated_at": r[4]}
        for r in rows
    ]


def get_or_create_default(user_id: int) -> int:
    """Return the default dashboard id, creating it with all registry widgets if missing."""
    with _engine().connect() as conn:
        row = conn.execute(text(f"""
            SELECT id FROM {_schema()}.app_dashboards
            WHERE  user_id = :uid AND is_default = TRUE
        """), {"uid": user_id}).fetchone()
    if row:
        return row[0]
    return _create_default_dashboard(user_id)


def create_dashboard(user_id: int, name: str) -> int:
    """Create a new (non-default) empty dashboard. Returns its id."""
    with _engine().begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {_schema()}.app_dashboards (user_id, name, is_default)
            VALUES (:uid, :name, FALSE)
            RETURNING id
        """), {"uid": user_id, "name": name}).fetchone()
    return row[0]


def rename_dashboard(dashboard_id: int, name: str) -> None:
    with _engine().begin() as conn:
        conn.execute(text(f"""
            UPDATE {_schema()}.app_dashboards
            SET    name = :name, updated_at = NOW()
            WHERE  id = :did
        """), {"did": dashboard_id, "name": name})


def delete_dashboard(dashboard_id: int, user_id: int) -> None:
    """
    Delete a dashboard and all its widgets (cascade).
    Raises ValueError if it is the user's default dashboard.
    """
    with _engine().connect() as conn:
        row = conn.execute(text(f"""
            SELECT is_default FROM {_schema()}.app_dashboards
            WHERE  id = :did AND user_id = :uid
        """), {"did": dashboard_id, "uid": user_id}).fetchone()

    if not row:
        raise ValueError("Dashboard not found.")
    if row[0]:
        raise ValueError("Cannot delete the default dashboard.")

    with _engine().begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {_schema()}.app_dashboards WHERE id = :did
        """), {"did": dashboard_id})


# ── Widget CRUD ───────────────────────────────────────────────────────────────

def get_widgets(dashboard_id: int) -> list[dict]:
    """Return widgets for a dashboard ordered by position."""
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, chart_id, position, col_span, row_span, config
            FROM   {_schema()}.app_dashboard_widgets
            WHERE  dashboard_id = :did
            ORDER  BY position ASC
        """), {"did": dashboard_id}).fetchall()
    return [
        {
            "id":       r[0],
            "chart_id": r[1],
            "position": r[2],
            "col_span": r[3],
            "row_span": r[4],
            "config":   r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
        }
        for r in rows
    ]


def save_widget_layout(dashboard_id: int, widgets: list[dict]) -> None:
    """
    Full replace of the widget list for a dashboard.
    widgets: list of {chart_id, position, col_span, row_span, config}
    Positions are reassigned from the list order (0, 1, 2, …) so callers
    don't need to manage them explicitly.
    """
    with _engine().begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {_schema()}.app_dashboard_widgets WHERE dashboard_id = :did
        """), {"did": dashboard_id})

        for i, w in enumerate(widgets):
            conn.execute(text(f"""
                INSERT INTO {_schema()}.app_dashboard_widgets
                    (dashboard_id, chart_id, position, col_span, row_span, config)
                VALUES (:did, :cid, :pos, :cs, :rs, CAST(:cfg AS jsonb))
            """), {
                "did": dashboard_id,
                "cid": w["chart_id"],
                "pos": i,
                "cs":  w.get("col_span", 2),
                "rs":  w.get("row_span", 1),
                "cfg": json.dumps(w.get("config", {})),
            })


def add_widget(
    dashboard_id: int,
    chart_id: str,
    *,
    col_span: int = 2,
    row_span: int = 1,
    config: dict | None = None,
) -> int:
    """Append a widget at the end of the dashboard. Returns the new widget id."""
    with _engine().begin() as conn:
        row = conn.execute(text(f"""
            SELECT COALESCE(MAX(position), -1) + 1
            FROM   {_schema()}.app_dashboard_widgets
            WHERE  dashboard_id = :did
        """), {"did": dashboard_id}).fetchone()
        position = row[0]

        result = conn.execute(text(f"""
            INSERT INTO {_schema()}.app_dashboard_widgets
                (dashboard_id, chart_id, position, col_span, row_span, config)
            VALUES (:did, :cid, :pos, :cs, :rs, CAST(:cfg AS jsonb))
            RETURNING id
        """), {
            "did": dashboard_id,
            "cid": chart_id,
            "pos": position,
            "cs":  col_span,
            "rs":  row_span,
            "cfg": json.dumps(config or {}),
        })
    return result.fetchone()[0]


def remove_widget(widget_id: int) -> None:
    with _engine().begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {_schema()}.app_dashboard_widgets WHERE id = :wid
        """), {"wid": widget_id})


def update_widget_config(widget_id: int, config: dict) -> None:
    """Replace the config JSONB blob for a single widget."""
    with _engine().begin() as conn:
        conn.execute(text(f"""
            UPDATE {_schema()}.app_dashboard_widgets
            SET    config = CAST(:cfg AS jsonb)
            WHERE  id = :wid
        """), {"wid": widget_id, "cfg": json.dumps(config)})


def update_widget_layout(
    widget_id: int,
    *,
    col_span: int | None = None,
    row_span: int | None = None,
    position: int | None = None,
) -> None:
    """Update sizing/position fields for a single widget."""
    fields: list[str] = []
    params: dict[str, Any] = {"wid": widget_id}

    if col_span is not None:
        fields.append("col_span = :col_span")
        params["col_span"] = col_span
    if row_span is not None:
        fields.append("row_span = :row_span")
        params["row_span"] = row_span
    if position is not None:
        fields.append("position = :position")
        params["position"] = position

    if not fields:
        return

    with _engine().begin() as conn:
        conn.execute(text(
            f"UPDATE {_schema()}.app_dashboard_widgets "
            f"SET {', '.join(fields)} WHERE id = :wid"
        ), params)


# ── Default dashboard seeding ─────────────────────────────────────────────────

def _create_default_dashboard(user_id: int) -> int:
    """
    Create the default dashboard for a user, pre-populated with every chart
    in the registry using its default col_span and row_span.
    """
    from components.dashboard_registry import REGISTRY

    with _engine().begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {_schema()}.app_dashboards (user_id, name, is_default)
            VALUES (:uid, 'My Dashboard', TRUE)
            RETURNING id
        """), {"uid": user_id}).fetchone()
        dashboard_id = row[0]

        for i, chart in enumerate(REGISTRY):
            conn.execute(text(f"""
                INSERT INTO {_schema()}.app_dashboard_widgets
                    (dashboard_id, chart_id, position, col_span, row_span, config)
                VALUES (:did, :cid, :pos, :cs, :rs, '{{}}')
            """), {
                "did": dashboard_id,
                "cid": chart.id,
                "pos": i,
                "cs":  chart.default_col_span,
                "rs":  chart.default_row_span,
            })

    return dashboard_id

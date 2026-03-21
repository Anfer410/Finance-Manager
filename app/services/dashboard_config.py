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
    """Return widgets for a dashboard ordered by row_start, col_start."""
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, chart_id, position, col_span, row_span, config,
                   col_start, row_start, instance_label
            FROM   {_schema()}.app_dashboard_widgets
            WHERE  dashboard_id = :did
            ORDER  BY row_start ASC, col_start ASC
        """), {"did": dashboard_id}).fetchall()
    return [
        {
            "id":             r[0],
            "chart_id":       r[1],
            "position":       r[2],
            "col_span":       r[3],
            "row_span":       r[4],
            "config":         r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
            "col_start":      r[6],
            "row_start":      r[7],
            "instance_label": r[8],
        }
        for r in rows
    ]


def save_widget_layout(dashboard_id: int, widgets: list[dict]) -> None:
    """
    Full replace of the widget list for a dashboard.
    widgets: list of {chart_id, position, col_span, row_span, col_start, row_start, config}
    """
    with _engine().begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {_schema()}.app_dashboard_widgets WHERE dashboard_id = :did
        """), {"did": dashboard_id})

        for i, w in enumerate(widgets):
            conn.execute(text(f"""
                INSERT INTO {_schema()}.app_dashboard_widgets
                    (dashboard_id, chart_id, position, col_span, row_span, col_start, row_start, config)
                VALUES (:did, :cid, :pos, :cs, :rs, :cst, :rst, CAST(:cfg AS jsonb))
            """), {
                "did": dashboard_id,
                "cid": w["chart_id"],
                "pos": i,
                "cs":  w.get("col_span", 2),
                "rs":  w.get("row_span", 1),
                "cst": w.get("col_start", 1),
                "rst": w.get("row_start", 1),
                "cfg": json.dumps(w.get("config", {})),
            })


def find_free_position(dashboard_id: int, col_span: int, row_span: int) -> tuple[int, int]:
    """
    Find the first free (col_start, row_start) on the grid that fits the given span.
    """
    widgets = get_widgets(dashboard_id)
    occupied: set[tuple[int, int]] = set()
    for w in widgets:
        cs, rs = w["col_start"], w["row_start"]
        for dr in range(w["row_span"]):
            for dc in range(w["col_span"]):
                occupied.add((rs + dr, cs + dc))

    row = 1
    while True:
        for col in range(1, 5):
            if col + col_span - 1 > 4:
                continue
            cells = {(row + dr, col + dc) for dr in range(row_span) for dc in range(col_span)}
            if not cells & occupied:
                return col, row
        row += 1


def add_widget(
    dashboard_id: int,
    chart_id: str,
    *,
    col_span: int = 2,
    row_span: int = 1,
    col_start: int | None = None,
    row_start: int | None = None,
    config: dict | None = None,
) -> int:
    """Append a widget at the next free grid position. Returns the new widget id."""
    if col_start is None or row_start is None:
        col_start, row_start = find_free_position(dashboard_id, col_span, row_span)

    with _engine().begin() as conn:
        row = conn.execute(text(f"""
            SELECT COALESCE(MAX(position), -1) + 1
            FROM   {_schema()}.app_dashboard_widgets
            WHERE  dashboard_id = :did
        """), {"did": dashboard_id}).fetchone()
        position = row[0]

        result = conn.execute(text(f"""
            INSERT INTO {_schema()}.app_dashboard_widgets
                (dashboard_id, chart_id, position, col_span, row_span, col_start, row_start, config)
            VALUES (:did, :cid, :pos, :cs, :rs, :cst, :rst, CAST(:cfg AS jsonb))
            RETURNING id
        """), {
            "did": dashboard_id,
            "cid": chart_id,
            "pos": position,
            "cs":  col_span,
            "rs":  row_span,
            "cst": col_start,
            "rst": row_start,
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


def restore_widgets(dashboard_id: int, snapshot: list[dict]) -> None:
    """Full replace of widgets from a snapshot, preserving all fields including instance_label."""
    with _engine().begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {_schema()}.app_dashboard_widgets WHERE dashboard_id = :did
        """), {"did": dashboard_id})
        for w in snapshot:
            conn.execute(text(f"""
                INSERT INTO {_schema()}.app_dashboard_widgets
                    (dashboard_id, chart_id, position, col_span, row_span,
                     col_start, row_start, config, instance_label)
                VALUES (:did, :cid, :pos, :cs, :rs, :cst, :rst,
                        CAST(:cfg AS jsonb), :lbl)
            """), {
                "did": dashboard_id,
                "cid": w["chart_id"],
                "pos": w["position"],
                "cs":  w["col_span"],
                "rs":  w["row_span"],
                "cst": w["col_start"],
                "rst": w["row_start"],
                "cfg": json.dumps(w["config"]),
                "lbl": w.get("instance_label"),
            })


def update_widget_label(widget_id: int, label: str | None) -> None:
    """Set or clear the custom instance label for a widget."""
    with _engine().begin() as conn:
        conn.execute(text(f"""
            UPDATE {_schema()}.app_dashboard_widgets
            SET    instance_label = :lbl
            WHERE  id = :wid
        """), {"wid": widget_id, "lbl": label or None})


def update_widget_layout(
    widget_id: int,
    *,
    col_span: int | None = None,
    row_span: int | None = None,
    position: int | None = None,
    col_start: int | None = None,
    row_start: int | None = None,
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
    if col_start is not None:
        fields.append("col_start = :col_start")
        params["col_start"] = col_start
    if row_start is not None:
        fields.append("row_start = :row_start")
        params["row_start"] = row_start

    if not fields:
        return

    with _engine().begin() as conn:
        conn.execute(text(
            f"UPDATE {_schema()}.app_dashboard_widgets "
            f"SET {', '.join(fields)} WHERE id = :wid"
        ), params)


# ── Default dashboard seeding ─────────────────────────────────────────────────

# Snapshot of the reference dashboard layout (captured from user 1).
# Each entry: (chart_id, position, col_span, row_span, col_start, row_start).
_DEFAULT_LAYOUT = [
    ("financial_baseline",  0,  4, 1, 1,  1),
    ("kpi_alltime",         1,  2, 1, 1,  2),
    ("kpi_yearly",          2,  2, 1, 3,  2),
    ("spend_income",        3,  4, 2, 1,  3),
    ("fixed_vs_variable",   4,  2, 2, 1,  5),
    ("employer_income",     5,  2, 2, 3,  5),
    ("category_donut",      6,  2, 2, 1,  7),
    ("per_bank",            7,  2, 2, 3,  7),
    ("category_trend",      8,  4, 2, 1,  9),
    ("weekly_transactions", 9,  4, 3, 1, 11),
]


# ── Dashboard sharing ─────────────────────────────────────────────────────────

def set_dashboard_shares(dashboard_id: int, user_ids: list[int]) -> None:
    """Replace the share list for a dashboard (owner call)."""
    with _engine().begin() as conn:
        conn.execute(text(f"""
            DELETE FROM {_schema()}.app_dashboard_shares WHERE dashboard_id = :did
        """), {"did": dashboard_id})
        for uid in user_ids:
            conn.execute(text(f"""
                INSERT INTO {_schema()}.app_dashboard_shares (dashboard_id, shared_with)
                VALUES (:did, :uid) ON CONFLICT DO NOTHING
            """), {"did": dashboard_id, "uid": uid})


def get_dashboard_shares(dashboard_id: int) -> list[int]:
    """Return user_ids this dashboard is currently shared with."""
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT shared_with FROM {_schema()}.app_dashboard_shares
            WHERE dashboard_id = :did
        """), {"did": dashboard_id}).fetchall()
    return [r[0] for r in rows]


def get_shared_with_me(user_id: int) -> list[dict]:
    """
    Return all dashboards shared with this user, including owner info and
    whether the user has subscribed (pinned) each one.
    """
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT d.id, d.name, u.id, u.display_name,
                   (sub.user_id IS NOT NULL) AS is_subscribed
            FROM   {_schema()}.app_dashboard_shares sh
            JOIN   {_schema()}.app_dashboards d ON d.id = sh.dashboard_id
            JOIN   {_schema()}.app_users u ON u.id = d.user_id
            LEFT   JOIN {_schema()}.app_dashboard_subscriptions sub
                   ON sub.dashboard_id = d.id AND sub.user_id = :uid
            WHERE  sh.shared_with = :uid
            ORDER  BY u.display_name, d.name
        """), {"uid": user_id}).fetchall()
    return [
        {
            "dashboard_id": r[0],
            "name":         r[1],
            "owner_id":     r[2],
            "owner_name":   r[3],
            "is_subscribed": bool(r[4]),
        }
        for r in rows
    ]


def set_subscription(dashboard_id: int, user_id: int, subscribed: bool) -> None:
    """Pin or unpin a shared dashboard to the user's tab bar."""
    with _engine().begin() as conn:
        if subscribed:
            conn.execute(text(f"""
                INSERT INTO {_schema()}.app_dashboard_subscriptions (dashboard_id, user_id)
                VALUES (:did, :uid) ON CONFLICT DO NOTHING
            """), {"did": dashboard_id, "uid": user_id})
        else:
            conn.execute(text(f"""
                DELETE FROM {_schema()}.app_dashboard_subscriptions
                WHERE dashboard_id = :did AND user_id = :uid
            """), {"did": dashboard_id, "uid": user_id})


def list_subscribed_shared(user_id: int) -> list[dict]:
    """
    Return shared dashboards the user has pinned to their tab bar.
    Guards against stale subscriptions: only returns dashboards still shared with the user.
    """
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT d.id, d.name, u.display_name
            FROM   {_schema()}.app_dashboard_subscriptions sub
            JOIN   {_schema()}.app_dashboards d ON d.id = sub.dashboard_id
            JOIN   {_schema()}.app_users u ON u.id = d.user_id
            JOIN   {_schema()}.app_dashboard_shares sh
                   ON sh.dashboard_id = d.id AND sh.shared_with = :uid
            WHERE  sub.user_id = :uid
            ORDER  BY u.display_name, d.name
        """), {"uid": user_id}).fetchall()
    return [{"id": r[0], "name": r[1], "owner_name": r[2]} for r in rows]


# ── Default dashboard seeding ─────────────────────────────────────────────────

def _create_default_dashboard(user_id: int) -> int:
    with _engine().begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {_schema()}.app_dashboards (user_id, name, is_default)
            VALUES (:uid, 'My Dashboard', TRUE)
            RETURNING id
        """), {"uid": user_id}).fetchone()
        dashboard_id = row[0]

        for chart_id, pos, cs, rs, cst, rst in _DEFAULT_LAYOUT:
            conn.execute(text(f"""
                INSERT INTO {_schema()}.app_dashboard_widgets
                    (dashboard_id, chart_id, position, col_span, row_span, col_start, row_start, config)
                VALUES (:did, :cid, :pos, :cs, :rs, :cst, :rst, '{{}}')
            """), {
                "did": dashboard_id, "cid": chart_id, "pos": pos,
                "cs": cs, "rs": rs, "cst": cst, "rst": rst,
            })

    return dashboard_id

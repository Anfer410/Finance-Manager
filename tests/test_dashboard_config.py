"""
tests/test_dashboard_config.py

Integration tests for services/dashboard_config.py.

Uses the same importlib reload pattern as test_config_repo.py so the module
picks up the test engine from the patched data.db.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

APP_DIR = Path(__file__).parent.parent / "app"


# ── module loader ─────────────────────────────────────────────────────────────

def _load(pg_engine):
    """Load dashboard_config fresh so it binds to the test engine."""
    path = APP_DIR / "services" / "dashboard_config.py"
    spec = importlib.util.spec_from_file_location("services.dashboard_config", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_user(pg_engine, schema: str, username: str) -> int:
    with pg_engine.begin() as conn:
        row = conn.execute(text(f"""
            INSERT INTO {schema}.app_users (username, password_hash, display_name, person_name)
            VALUES (:u, 'x', :u, :u) RETURNING id
        """), {"u": username}).fetchone()
    return row[0]


def _cleanup_user(pg_engine, schema: str, user_id: int) -> None:
    with pg_engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {schema}.app_dashboard_widgets WHERE dashboard_id IN "
                          f"(SELECT id FROM {schema}.app_dashboards WHERE user_id = :uid)"), {"uid": user_id})
        conn.execute(text(f"DELETE FROM {schema}.app_dashboards WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text(f"DELETE FROM {schema}.app_user_prefs  WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text(f"DELETE FROM {schema}.app_users       WHERE id      = :uid"), {"uid": user_id})


# ── Dashboard CRUD ────────────────────────────────────────────────────────────

class TestDashboardCRUD:
    def test_list_dashboards_empty_for_new_user(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_list_empty")
        try:
            assert dc.list_dashboards(uid) == []
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_create_dashboard_returns_id(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_create")
        try:
            did = dc.create_dashboard(uid, "My Dashboard")
            assert isinstance(did, int) and did > 0
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_created_dashboard_appears_in_list(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_create_list")
        try:
            did = dc.create_dashboard(uid, "Listed")
            ids = [d["id"] for d in dc.list_dashboards(uid)]
            assert did in ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_create_dashboard_not_default(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_not_default")
        try:
            did = dc.create_dashboard(uid, "Extra")
            d = next(d for d in dc.list_dashboards(uid) if d["id"] == did)
            assert d["is_default"] is False
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_rename_dashboard(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_rename")
        try:
            did = dc.create_dashboard(uid, "Old Name")
            dc.rename_dashboard(did, "New Name")
            d = next(d for d in dc.list_dashboards(uid) if d["id"] == did)
            assert d["name"] == "New Name"
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_delete_non_default_dashboard(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_delete")
        try:
            did = dc.create_dashboard(uid, "ToDelete")
            dc.delete_dashboard(did, uid)
            ids = [d["id"] for d in dc.list_dashboards(uid)]
            assert did not in ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_delete_default_raises(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_delete_default")
        try:
            did = dc.get_or_create_default(uid)
            with pytest.raises(ValueError, match="default"):
                dc.delete_dashboard(did, uid)
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_delete_nonexistent_raises(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_del_missing")
        try:
            with pytest.raises(ValueError):
                dc.delete_dashboard(999_999, uid)
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_get_or_create_default_creates_once(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_goc_default")
        try:
            did1 = dc.get_or_create_default(uid)
            did2 = dc.get_or_create_default(uid)
            assert did1 == did2
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_default_dashboard_is_first_in_list(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "dc_default_first")
        try:
            default_id = dc.get_or_create_default(uid)
            dc.create_dashboard(uid, "Extra")
            dashboards = dc.list_dashboards(uid)
            assert dashboards[0]["id"] == default_id
            assert dashboards[0]["is_default"] is True
        finally:
            _cleanup_user(pg_engine, schema, uid)


# ── Widget CRUD ───────────────────────────────────────────────────────────────

class TestWidgetCRUD:
    def _setup(self, pg_engine, schema, username):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, username)
        did = dc.create_dashboard(uid, "Widget Test")
        return dc, uid, did

    def test_get_widgets_empty(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_empty")
        try:
            assert dc.get_widgets(did) == []
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_add_widget_returns_id(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_add")
        try:
            wid = dc.add_widget(did, "spend_kpi", col_span=2, row_span=1)
            assert isinstance(wid, int) and wid > 0
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_add_widget_appears_in_get_widgets(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_appears")
        try:
            wid = dc.add_widget(did, "spend_kpi")
            widgets = dc.get_widgets(did)
            ids = [w["id"] for w in widgets]
            assert wid in ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_widget_dict_shape(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_shape")
        try:
            dc.add_widget(did, "monthly_chart", col_span=3, row_span=2,
                          config={"key": "value"})
            w = dc.get_widgets(did)[0]
            assert w["chart_id"]  == "monthly_chart"
            assert w["col_span"]  == 3
            assert w["row_span"]  == 2
            assert w["config"]    == {"key": "value"}
            assert "col_start" in w
            assert "row_start" in w
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_remove_widget(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_remove")
        try:
            wid = dc.add_widget(did, "spend_kpi")
            dc.remove_widget(wid)
            ids = [w["id"] for w in dc.get_widgets(did)]
            assert wid not in ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_update_widget_config(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_config")
        try:
            wid = dc.add_widget(did, "spend_kpi", config={"old": True})
            dc.update_widget_config(wid, {"new": 42})
            w = next(w for w in dc.get_widgets(did) if w["id"] == wid)
            assert w["config"] == {"new": 42}
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_save_widget_layout_full_replace(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_layout")
        try:
            dc.add_widget(did, "old_widget")
            new_layout = [
                {"chart_id": "new_a", "col_span": 2, "row_span": 1,
                 "col_start": 1, "row_start": 1, "config": {}},
                {"chart_id": "new_b", "col_span": 2, "row_span": 1,
                 "col_start": 3, "row_start": 1, "config": {}},
            ]
            dc.save_widget_layout(did, new_layout)
            widgets = dc.get_widgets(did)
            chart_ids = [w["chart_id"] for w in widgets]
            assert "old_widget" not in chart_ids
            assert "new_a" in chart_ids
            assert "new_b" in chart_ids
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_update_widget_layout_position(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_move")
        try:
            wid = dc.add_widget(did, "spend_kpi",
                                col_start=1, row_start=1, col_span=2, row_span=1)
            dc.update_widget_layout(wid, col_start=3, row_start=2)
            w = next(w for w in dc.get_widgets(did) if w["id"] == wid)
            assert w["col_start"] == 3
            assert w["row_start"] == 2
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_add_widget_auto_positions_without_overlap(self, pg_engine, schema):
        dc, uid, did = self._setup(pg_engine, schema, "wc_autopos")
        try:
            w1 = dc.add_widget(did, "chart_a", col_span=2, row_span=1)
            w2 = dc.add_widget(did, "chart_b", col_span=2, row_span=1)
            widgets = {w["id"]: w for w in dc.get_widgets(did)}
            # Positions should differ — no overlap
            a, b = widgets[w1], widgets[w2]
            cells_a = {(a["row_start"] + dr, a["col_start"] + dc_)
                       for dr in range(a["row_span"]) for dc_ in range(a["col_span"])}
            cells_b = {(b["row_start"] + dr, b["col_start"] + dc_)
                       for dr in range(b["row_span"]) for dc_ in range(b["col_span"])}
            assert not (cells_a & cells_b), "Auto-positioned widgets overlap"
        finally:
            _cleanup_user(pg_engine, schema, uid)


# ── find_free_position ────────────────────────────────────────────────────────

class TestFindFreePosition:
    def test_empty_dashboard_returns_col1_row1(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "ffp_empty")
        did = dc.create_dashboard(uid, "FFP")
        try:
            col, row = dc.find_free_position(did, col_span=2, row_span=1)
            assert col == 1 and row == 1
        finally:
            _cleanup_user(pg_engine, schema, uid)

    def test_occupied_position_skipped(self, pg_engine, schema):
        dc  = _load(pg_engine)
        uid = _make_user(pg_engine, schema, "ffp_skip")
        did = dc.create_dashboard(uid, "FFP Skip")
        try:
            # Fill cols 1-2 at row 1 (col_span=2, row_span=1)
            dc.add_widget(did, "fill", col_span=2, row_span=1, col_start=1, row_start=1)
            col, row = dc.find_free_position(did, col_span=2, row_span=1)
            # Should not start at (1,1) — must skip or go to row 2
            assert not (col == 1 and row == 1)
        finally:
            _cleanup_user(pg_engine, schema, uid)

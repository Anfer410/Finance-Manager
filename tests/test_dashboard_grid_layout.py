"""
tests/test_dashboard_grid_layout.py

Unit tests for services/dashboard_grid_layout.py.

All DB calls (get_widgets, update_widget_layout, remove_widget) are mocked so
these tests run without a database.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Stub DB and NiceGUI before any app imports
for _mod in ("data.db", "nicegui", "nicegui.app"):
    sys.modules.setdefault(_mod, MagicMock())

# dashboard_config imports data.db at module level; stub it before import
sys.modules.setdefault("services.dashboard_config", MagicMock())

import services.dashboard_grid_layout as grid  # noqa: E402


# ── widget factory ────────────────────────────────────────────────────────────

def _w(id: int, col: int, row: int, col_span: int = 1, row_span: int = 1) -> dict:
    return {
        "id": id,
        "col_start": col, "row_start": row,
        "col_span":  col_span, "row_span": row_span,
    }


# ── compact_grid ──────────────────────────────────────────────────────────────

class TestCompactGrid:
    def test_empty_grid_is_noop(self):
        with patch.object(grid, "get_widgets", return_value=[]), \
             patch.object(grid, "update_widget_layout") as mock_upd:
            grid.compact_grid(1)
            mock_upd.assert_not_called()

    def test_widget_already_at_row_1_not_moved(self):
        widgets = [_w(1, col=1, row=1)]
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout") as mock_upd:
            grid.compact_grid(1)
            mock_upd.assert_not_called()

    def test_widget_at_row_3_pulled_to_row_1(self):
        widgets = [_w(1, col=1, row=3)]
        calls = []
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout", side_effect=lambda wid, **kw: calls.append((wid, kw))):
            grid.compact_grid(1)
        assert (1, {"row_start": 1}) in calls

    def test_two_widgets_same_column_compact_to_rows_1_and_2(self):
        # Widget A at row=3, widget B at row=5 — both in col=1 with row_span=1
        widgets = [_w(1, col=1, row=3), _w(2, col=1, row=5)]
        moved: dict[int, int] = {}
        def capture(wid, **kw):
            if "row_start" in kw:
                moved[wid] = kw["row_start"]
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout", side_effect=capture):
            grid.compact_grid(1)
        assert moved.get(1) == 1
        assert moved.get(2) == 2

    def test_widgets_in_different_columns_compact_independently(self):
        # Col1 widget at row=4, Col2 widget at row=4 — both pull to row=1
        widgets = [_w(1, col=1, row=4), _w(2, col=2, row=4)]
        moved: dict[int, int] = {}
        def capture(wid, **kw):
            if "row_start" in kw:
                moved[wid] = kw["row_start"]
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout", side_effect=capture):
            grid.compact_grid(1)
        assert moved.get(1) == 1
        assert moved.get(2) == 1


# ── cascade_push_down ─────────────────────────────────────────────────────────

class TestCascadePushDown:
    def test_no_overlap_is_noop(self):
        widgets = [_w(1, col=1, row=1), _w(2, col=1, row=2)]
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout") as mock_upd:
            grid.cascade_push_down(1)
            mock_upd.assert_not_called()

    def test_overlapping_widgets_get_separated(self):
        # Both widgets at same cell — lower one must be pushed down
        widgets = [_w(1, col=1, row=1), _w(2, col=1, row=1)]
        pushed: dict[int, int] = {}
        def capture(wid, **kw):
            if "row_start" in kw:
                pushed[wid] = kw["row_start"]

        # Need to return updated state after push so the loop terminates
        call_count = [0]
        def get_w(did):
            call_count[0] += 1
            if call_count[0] == 1:
                return widgets
            # After push, return non-overlapping state
            return [_w(1, col=1, row=1), _w(2, col=1, row=2)]

        with patch.object(grid, "get_widgets", side_effect=get_w), \
             patch.object(grid, "update_widget_layout", side_effect=capture):
            grid.cascade_push_down(1)
        # Widget 2 should have been pushed to row 2
        assert 2 in pushed
        assert pushed[2] == 2


# ── apply_move ────────────────────────────────────────────────────────────────

class TestApplyMove:
    def test_move_to_empty_cell(self):
        widgets = [_w(1, col=1, row=1), _w(2, col=3, row=3)]
        updates: list[tuple] = []
        def capture(wid, **kw):
            updates.append((wid, kw))
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout", side_effect=capture):
            grid.apply_move(widget_id=1, new_col=2, new_row=2, dashboard_id=1)
        # Widget 1 moved to (2, 2)
        assert any(wid == 1 and kw.get("col_start") == 2 and kw.get("row_start") == 2
                   for wid, kw in updates)

    def test_move_to_same_position_is_noop(self):
        widgets = [_w(1, col=2, row=2)]
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout") as mock_upd:
            grid.apply_move(widget_id=1, new_col=2, new_row=2, dashboard_id=1)
            mock_upd.assert_not_called()

    def test_move_nonexistent_widget_is_noop(self):
        widgets = [_w(1, col=1, row=1)]
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout") as mock_upd:
            grid.apply_move(widget_id=99, new_col=2, new_row=2, dashboard_id=1)
            mock_upd.assert_not_called()

    def test_move_swaps_with_blocker(self):
        # Widget 1 at (1,1), widget 2 at (2,1). Move widget 1 → (2,1) → they swap.
        w1 = _w(1, col=1, row=1)
        w2 = _w(2, col=2, row=1)
        updates: dict[int, dict] = {}
        def capture(wid, **kw):
            updates[wid] = kw
        with patch.object(grid, "get_widgets", return_value=[w1, w2]), \
             patch.object(grid, "update_widget_layout", side_effect=capture):
            grid.apply_move(widget_id=1, new_col=2, new_row=1, dashboard_id=1)
        # Widget 1 goes to blocker's old position
        assert updates[1]["col_start"] == 2
        # Blocker goes to widget 1's old position
        assert updates[2]["col_start"] == 1

    def test_col_clamped_to_grid_bounds(self):
        """col < 1 should be clamped to 1."""
        widgets = [_w(1, col=2, row=2)]
        updates: dict[int, dict] = {}
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout", side_effect=lambda wid, **kw: updates.update({wid: kw})):
            grid.apply_move(widget_id=1, new_col=-5, new_row=2, dashboard_id=1)
        assert updates[1]["col_start"] == 1


# ── set_col_span / set_row_span ───────────────────────────────────────────────

class TestSetSpan:
    def _mock_cascade_compact(self):
        return (
            patch.object(grid, "cascade_push_down"),
            patch.object(grid, "compact_grid"),
        )

    def test_set_col_span_calls_update(self):
        widgets = [_w(1, col=1, row=1)]
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout") as mock_upd, \
             patch.object(grid, "cascade_push_down"), \
             patch.object(grid, "compact_grid"):
            grid.set_col_span(widget_id=1, col_span=2, dashboard_id=1)
            mock_upd.assert_called_once_with(1, col_span=2)

    def test_set_row_span_calls_update(self):
        widgets = [_w(1, col=1, row=1)]
        with patch.object(grid, "get_widgets", return_value=widgets), \
             patch.object(grid, "update_widget_layout") as mock_upd, \
             patch.object(grid, "cascade_push_down"), \
             patch.object(grid, "compact_grid"):
            grid.set_row_span(widget_id=1, row_span=2, dashboard_id=1)
            mock_upd.assert_called_once_with(1, row_span=2)

    def test_set_col_span_nonexistent_widget_is_noop(self):
        with patch.object(grid, "get_widgets", return_value=[]), \
             patch.object(grid, "update_widget_layout") as mock_upd:
            grid.set_col_span(widget_id=99, col_span=2, dashboard_id=1)
            mock_upd.assert_not_called()


# ── remove_widget ─────────────────────────────────────────────────────────────

class TestRemoveWidget:
    def test_remove_calls_db_remove_and_compact(self):
        widgets_after = [_w(1, col=1, row=1)]
        with patch.object(grid, "_db_remove_widget") as mock_rm, \
             patch.object(grid, "compact_grid") as mock_compact, \
             patch.object(grid, "get_widgets", return_value=widgets_after):
            grid.remove_widget(widget_id=5, dashboard_id=1)
        mock_rm.assert_called_once_with(5)
        mock_compact.assert_called_once_with(1)

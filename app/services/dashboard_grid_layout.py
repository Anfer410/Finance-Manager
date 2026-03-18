"""
services/dashboard_grid_layout.py

Pure grid-layout helpers: compaction, collision resolution, move/resize/remove.
These functions only touch the database — callers are responsible for refreshing the UI.
"""

from __future__ import annotations

from services.dashboard_config import (
    get_widgets, update_widget_layout,
    remove_widget as _db_remove_widget,
)


def cascade_push_down(dashboard_id: int) -> None:
    """Iteratively push the lower widget in any colliding pair downward until stable."""
    while True:
        widgets = get_widgets(dashboard_id)
        pushed = False
        for anchor in widgets:
            anchor_cells = {(anchor['row_start'] + dr, anchor['col_start'] + dc)
                            for dr in range(anchor['row_span'])
                            for dc in range(anchor['col_span'])}
            for other in widgets:
                if other['id'] == anchor['id']:
                    continue
                other_cells = {(other['row_start'] + dr, other['col_start'] + dc)
                               for dr in range(other['row_span'])
                               for dc in range(other['col_span'])}
                if anchor_cells & other_cells:
                    upper = anchor if anchor['row_start'] <= other['row_start'] else other
                    lower = other  if anchor['row_start'] <= other['row_start'] else anchor
                    new_start = upper['row_start'] + upper['row_span']
                    if new_start > lower['row_start']:
                        update_widget_layout(lower['id'], row_start=new_start)
                        pushed = True
                        break
            if pushed:
                break
        if not pushed:
            break


def compact_grid(dashboard_id: int) -> None:
    """Gravity-compact: pull each widget up to the highest row it fits in."""
    widgets = get_widgets(dashboard_id)
    if not widgets:
        return
    occupied: set[tuple[int, int]] = set()
    for w in sorted(widgets, key=lambda x: (x['row_start'], x['col_start'])):
        rs = w['row_span']
        target_row = w['row_start']
        for r in range(1, w['row_start'] + 1):
            cells = {(r + dr, w['col_start'] + dc)
                     for dr in range(rs) for dc in range(w['col_span'])}
            if not cells & occupied:
                target_row = r
                break
        for dr in range(rs):
            for dc in range(w['col_span']):
                occupied.add((target_row + dr, w['col_start'] + dc))
        if target_row != w['row_start']:
            update_widget_layout(w['id'], row_start=target_row)


def set_col_span(widget_id: int, col_span: int, dashboard_id: int) -> None:
    widgets  = get_widgets(dashboard_id)
    w        = next((x for x in widgets if x['id'] == widget_id), None)
    if not w:
        return
    col_span = max(1, min(col_span, 5 - w['col_start']))
    update_widget_layout(widget_id, col_span=col_span)
    cascade_push_down(dashboard_id)
    compact_grid(dashboard_id)


def set_row_span(widget_id: int, row_span: int, dashboard_id: int) -> None:
    widgets  = get_widgets(dashboard_id)
    w        = next((x for x in widgets if x['id'] == widget_id), None)
    if not w:
        return
    update_widget_layout(widget_id, row_span=row_span)
    cascade_push_down(dashboard_id)
    compact_grid(dashboard_id)


def apply_move(widget_id: int, new_col: int, new_row: int, dashboard_id: int) -> None:
    """Move widget to (new_col, new_row), swapping with any blocking widget."""
    widgets = get_widgets(dashboard_id)
    w = next((x for x in widgets if x['id'] == widget_id), None)
    if not w:
        return
    new_col = max(1, min(5 - w['col_span'], new_col))
    new_row = max(1, new_row)
    if new_col == w['col_start'] and new_row == w['row_start']:
        return

    new_cells = {(new_row + dr, new_col + dc)
                 for dr in range(w['row_span']) for dc in range(w['col_span'])}
    blocker = None
    for other in widgets:
        if other['id'] == widget_id:
            continue
        other_cells = {(other['row_start'] + dr, other['col_start'] + dc)
                       for dr in range(other['row_span']) for dc in range(other['col_span'])}
        if new_cells & other_cells:
            blocker = other
            break

    if blocker:
        pending: dict[int, tuple[int, int]] = {}

        w_at_blocker = {(blocker['row_start'] + dr, blocker['col_start'] + dc)
                        for dr in range(w['row_span']) for dc in range(w['col_span'])}
        for other in widgets:
            if other['id'] in (widget_id, blocker['id']):
                continue
            other_cells = {(other['row_start'] + dr, other['col_start'] + dc)
                           for dr in range(other['row_span']) for dc in range(other['col_span'])}
            if other_cells & w_at_blocker:
                pending[other['id']] = (w['col_start'] + other['col_start'] - blocker['col_start'],
                                        w['row_start'] + other['row_start'] - blocker['row_start'])

        blocker_at_w = {(w['row_start'] + dr, w['col_start'] + dc)
                        for dr in range(blocker['row_span']) for dc in range(blocker['col_span'])}
        for other in widgets:
            if other['id'] in (widget_id, blocker['id']) or other['id'] in pending:
                continue
            other_cells = {(other['row_start'] + dr, other['col_start'] + dc)
                           for dr in range(other['row_span']) for dc in range(other['col_span'])}
            if other_cells & blocker_at_w:
                pending[other['id']] = (blocker['col_start'] + other['col_start'] - w['col_start'],
                                        blocker['row_start'] + other['row_start'] - w['row_start'])

        for wid, (nc, nr) in pending.items():
            update_widget_layout(wid, col_start=nc, row_start=nr)
        update_widget_layout(blocker['id'], col_start=w['col_start'], row_start=w['row_start'])
        update_widget_layout(widget_id, col_start=blocker['col_start'], row_start=blocker['row_start'])
    else:
        update_widget_layout(widget_id, col_start=new_col, row_start=new_row)


def remove_widget(widget_id: int, dashboard_id: int) -> None:
    _db_remove_widget(widget_id)
    compact_grid(dashboard_id)

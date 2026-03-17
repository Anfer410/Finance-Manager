"""
finance_dashboard_content.py
"""

from __future__ import annotations

from datetime import datetime
from nicegui import ui
from services.notifications import notify

from services.auth import current_user_id, current_selected_persons
from services.transaction_config import load_config, save_config
from services.view_manager import ViewManager
from services.dashboard_config import (
    get_or_create_default, list_dashboards, create_dashboard,
    delete_dashboard, rename_dashboard,
    get_widgets, save_widget_layout, add_widget, remove_widget, update_widget_layout,
    update_widget_config,
)
from components.dashboard_registry import REGISTRY, REGISTRY_BY_ID
from data.db import get_conn_tuple, get_schema

import data.finance_dashboard_data as data

_DB_CONN = get_conn_tuple()
_SCHEMA  = get_schema()


# ── Transaction settings dialog ───────────────────────────────────────────────

def _open_settings_dialog(on_save_callback) -> None:

    def _chip_list(items: list[str], on_remove) -> None:
        if not items:
            ui.label('None configured.').classes('text-xs text-muted')
            return
        with ui.row().classes('flex-wrap gap-1'):
            for item in items:
                with ui.element('div').classes(
                    'inline-flex items-center gap-1 px-2 py-0.5 rounded-full '
                    'bg-gray-100 text-gray-700 text-xs font-mono'
                ):
                    ui.label(item)
                    ui.button(icon='close', on_click=lambda _, i=item: on_remove(i)) \
                        .props('flat round dense size=xs').classes('text-gray-400')

    with ui.dialog() as dlg, \
         ui.card().classes('w-[600px] rounded-2xl p-0 gap-0 overflow-hidden'):

        with ui.row().classes('items-center justify-between px-6 py-4 border-b border-zinc-100'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('settings').classes('text-zinc-400 text-xl')
                ui.label('Transaction settings').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close) \
                .props('flat round dense').classes('text-zinc-400')

        with ui.scroll_area().style('height: 60vh'):
            with ui.column().classes('w-full gap-4 px-6 py-5'):
                cfg = load_config()

                ui.label('Transfer exclusion patterns').classes('text-sm font-semibold text-gray-700')
                ui.label(
                    'Transactions whose description contains any of these strings are '
                    'excluded from spend and income totals (e.g. credit card payments, Zelle).'
                ).classes('text-xs text-muted')

                @ui.refreshable
                def render_transfer_chips() -> None:
                    _chip_list(cfg.transfer_patterns, on_remove=lambda p: _remove_transfer(p))

                def _remove_transfer(pattern: str) -> None:
                    cfg.transfer_patterns = [p for p in cfg.transfer_patterns if p != pattern]
                    render_transfer_chips.refresh()

                render_transfer_chips()

                with ui.row().classes('items-center gap-2'):
                    transfer_input = ui.input(placeholder='e.g. ONLINE PAYMENT') \
                        .props('outlined dense').classes('flex-1')

                    def _add_transfer() -> None:
                        val = transfer_input.value.strip().upper()
                        if val and val not in cfg.transfer_patterns:
                            cfg.transfer_patterns.append(val)
                            transfer_input.set_value('')
                            render_transfer_chips.refresh()

                    ui.button('Add', icon='add', on_click=_add_transfer) \
                        .props('unelevated dense').classes('bg-gray-700 text-white')

                ui.separator()

                ui.label('Employer / payroll patterns').classes('text-sm font-semibold text-gray-700')
                ui.label(
                    'Incoming transactions matching these strings are counted as income '
                    '(e.g. your employer name, "DIRECT DEP", "PAYROLL").'
                ).classes('text-xs text-muted')

                @ui.refreshable
                def render_employer_chips() -> None:
                    _chip_list(cfg.employer_patterns, on_remove=lambda p: _remove_employer(p))

                def _remove_employer(pattern: str) -> None:
                    cfg.employer_patterns = [p for p in cfg.employer_patterns if p != pattern]
                    render_employer_chips.refresh()

                render_employer_chips()

                with ui.row().classes('items-center gap-2'):
                    employer_input = ui.input(placeholder='e.g. SLALOM') \
                        .props('outlined dense').classes('flex-1')

                    def _add_employer() -> None:
                        val = employer_input.value.strip().upper()
                        if val and val not in cfg.employer_patterns:
                            cfg.employer_patterns.append(val)
                            employer_input.set_value('')
                            render_employer_chips.refresh()

                    ui.button('Add', icon='add', on_click=_add_employer) \
                        .props('unelevated dense').classes('bg-gray-700 text-white')

        with ui.row().classes('items-center justify-between px-6 py-4 border-t border-zinc-100'):
            def _refresh_views() -> None:
                try:
                    ViewManager(_DB_CONN, schema=_SCHEMA).refresh()
                    notify('Views refreshed.', type='positive', position='top')
                except Exception as ex:
                    notify(f'Refresh failed: {ex}', type='negative', position='top')

            ui.button('Refresh views', icon='refresh', on_click=_refresh_views) \
                .props('flat no-caps').classes('text-zinc-500')

            with ui.row().classes('gap-2'):
                ui.button('Cancel', on_click=dlg.close) \
                    .props('flat no-caps').classes('text-zinc-500')

                def _save() -> None:
                    save_config(cfg)
                    notify('Settings saved — refreshing charts.', type='positive', position='top')
                    dlg.close()
                    on_save_callback()

                ui.button('Save & refresh', icon='save', on_click=_save) \
                    .props('unelevated no-caps').classes('bg-zinc-800 text-white px-4 rounded-lg')

    dlg.open()


# ── Main content ──────────────────────────────────────────────────────────────

def content() -> None:
    user_id = current_user_id()
    now     = datetime.now()
    years   = data.get_years()

    state = {
        'year':                years[0] if years else now.year,
        'category':            None,
        'edit_mode':           False,
        'active_dashboard_id': get_or_create_default(user_id),
    }

    # ── Drag-to-move event bus ────────────────────────────────────────────────
    _bus = ui.element('div').style('display:none')

    def _on_drag_move(e) -> None:
        args = e.args or {}
        wid, col, row = args.get('widget_id'), args.get('col'), args.get('row')
        if wid is not None and col is not None and row is not None:
            _apply_move(int(wid), int(col), int(row))

    _bus.on('widget-moved', _on_drag_move, args=['widget_id', 'col', 'row'])

    def _on_drag_resize(e) -> None:
        args = e.args or {}
        wid   = args.get('widget_id')
        rtype = args.get('rtype')
        val   = args.get('val')
        if wid is None or val is None:
            return
        if rtype == 'col':
            _set_col_span(int(wid), int(val))
        elif rtype == 'row':
            _set_row_span(int(wid), int(val))

    _bus.on('widget-resized', _on_drag_resize, args=['widget_id', 'rtype', 'val'])

    _drag_js = """
(function() {
    window.__dragBusId = '__BUS_ID__';
    if (window.__dashboardDragLoaded) return;
    window.__dashboardDragLoaded = true;

    var ROW_H = 180, GAP = 16, THRESH = 5;
    var drag = null, hl = null;

    function getGrid() { return document.querySelector('.dashboard-grid-container'); }

    function calcPos(cx, cy) {
        var g = getGrid(); if (!g) return null;
        var r = g.getBoundingClientRect();
        var cw = (r.width - 3 * GAP) / 4;
        return {
            col: Math.max(1, Math.min(4, Math.floor((cx - r.left) / (cw + GAP)) + 1)),
            row: Math.max(1, Math.floor((cy - r.top) / (ROW_H + GAP)) + 1),
            r: r, cw: cw
        };
    }

    function makeHl() {
        var el = document.createElement('div');
        el.style.cssText = 'position:fixed;pointer-events:none;z-index:9999;' +
            'background:rgba(99,102,241,0.1);border:2px dashed #6366f1;' +
            'border-radius:12px;transition:left 60ms,top 60ms,width 60ms,height 60ms';
        document.body.appendChild(el);
        return el;
    }

    function placeHl(col, row, cs, rs) {
        var g = getGrid(); if (!g || !hl) return;
        var r = g.getBoundingClientRect();
        var cw = (r.width - 3 * GAP) / 4;
        var c = Math.min(col, 5 - cs);
        hl.style.left   = (r.left + (c - 1) * (cw + GAP)) + 'px';
        hl.style.top    = (r.top  + (row - 1) * (ROW_H + GAP)) + 'px';
        hl.style.width  = (cs * cw  + (cs - 1) * GAP) + 'px';
        hl.style.height = (rs * ROW_H + (rs - 1) * GAP) + 'px';
    }

    document.addEventListener('mousedown', function(e) {
        if (e.button !== 0) return;
        var rh = e.target.closest('.dashboard-resize-right');
        var rb = e.target.closest('.dashboard-resize-bottom');
        var mh = e.target.closest('.dashboard-drag-handle');
        if (!rh && !rb && !mh) return;
        if (e.target.closest('button, input, [data-no-drag]')) return;
        var card = (rh || rb || mh).closest('.dashboard-card');
        if (!card) return;
        e.preventDefault();
        var cls = card.className;
        var id = +((cls.match(/\\bwid-(\\d+)\\b/) || [0,0])[1]);
        var cs = +((cls.match(/\\bwcs-(\\d+)\\b/) || [0,1])[1]);
        var rs = +((cls.match(/\\bwrs-(\\d+)\\b/) || [0,1])[1]);
        var co = +((cls.match(/\\bwco-(\\d+)\\b/) || [0,1])[1]);
        var ro = +((cls.match(/\\bwro-(\\d+)\\b/) || [0,1])[1]);
        if (rh)
            drag = {type:'resize-col', id:id, cs:cs, rs:rs, co:co, ro:ro, sx:e.clientX, sy:e.clientY, active:false, val:cs};
        else if (rb)
            drag = {type:'resize-row', id:id, cs:cs, rs:rs, co:co, ro:ro, sx:e.clientX, sy:e.clientY, active:false, val:rs};
        else
            drag = {type:'move', id:id, cs:cs, rs:rs, sx:e.clientX, sy:e.clientY, active:false, col:null, row:null};
    });

    document.addEventListener('mousemove', function(e) {
        if (!drag) return;
        if (!drag.active) {
            if (Math.abs(e.clientX-drag.sx) < THRESH && Math.abs(e.clientY-drag.sy) < THRESH) return;
            drag.active = true;
            hl = makeHl();
            document.body.style.userSelect = 'none';
        }
        var p = calcPos(e.clientX, e.clientY); if (!p) return;
        if (drag.type === 'move') {
            document.body.style.cursor = 'grabbing';
            drag.col = Math.min(p.col, 5 - drag.cs);
            drag.row = p.row;
            placeHl(drag.col, drag.row, drag.cs, drag.rs);
        } else if (drag.type === 'resize-col') {
            document.body.style.cursor = 'ew-resize';
            var new_cs = Math.max(1, Math.min(5 - drag.co, p.col - drag.co + 1));
            drag.val = new_cs;
            placeHl(drag.co, drag.ro, new_cs, drag.rs);
        } else if (drag.type === 'resize-row') {
            document.body.style.cursor = 'ns-resize';
            var new_rs = Math.max(1, p.row - drag.ro + 1);
            drag.val = new_rs;
            placeHl(drag.co, drag.ro, drag.cs, new_rs);
        }
    });

    document.addEventListener('mouseup', function(e) {
        if (!drag) return;
        if (hl) { hl.remove(); hl = null; }
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
        if (drag.active) {
            var bus = document.getElementById(window.__dragBusId);
            if (bus) {
                if (drag.type === 'move' && drag.col !== null) {
                    var evt = new Event('widget-moved');
                    evt.widget_id = drag.id;
                    evt.col = drag.col;
                    evt.row = drag.row;
                    bus.dispatchEvent(evt);
                } else if (drag.type === 'resize-col' || drag.type === 'resize-row') {
                    var evt = new Event('widget-resized');
                    evt.widget_id = drag.id;
                    evt.rtype = drag.type === 'resize-col' ? 'col' : 'row';
                    evt.val = drag.val;
                    bus.dispatchEvent(evt);
                }
            }
        }
        drag = null;
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && drag) {
            if (hl) { hl.remove(); hl = null; }
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
            drag = null;
        }
    });
})();
""".replace('__BUS_ID__', f'c{_bus.id}')
    ui.run_javascript(_drag_js)

    def _persons() -> list[int] | None:
        """Resolve session-level person filter. Empty list → None (all people)."""
        sp = current_selected_persons()
        return sp if sp else None

    # ── Page header ───────────────────────────────────────────────────────────
    with ui.row().classes('w-full items-center justify-between mb-2'):
        with ui.column().classes('gap-0'):
            ui.label('Finance').classes('page-title')
            ui.label('Spend & income across all accounts.').classes('text-sm text-muted')

        with ui.row().classes('items-center gap-2'):
            ui.select(
                options=years, value=state['year'], label='Year',
                on_change=lambda e: (
                    state.update({'year': e.value}),
                    _refresh_all(),
                ),
            ).props('outlined dense').classes('w-28')

            ui.button('Refresh', icon='refresh', on_click=lambda: _refresh_all()) \
                .props('flat no-caps').classes('button button-outline')

            ui.button(icon='settings', on_click=lambda: _open_settings_dialog(
                on_save_callback=_refresh_all,
            )).props('flat round').classes('text-zinc-400').tooltip('Transaction settings')

            edit_btn = ui.button('Edit Dashboard', icon='edit', on_click=lambda: _toggle_edit()) \
                .props('flat no-caps').classes('text-zinc-500')

    ui.element('div').classes('divider mb-2')

    # ── Dashboard tab bar ─────────────────────────────────────────────────────
    @ui.refreshable
    def dashboard_tabs() -> None:
        dbs = list_dashboards(user_id)
        with ui.row().classes('items-center gap-1 mb-4 flex-wrap'):
            for db in dbs:
                is_active = db['id'] == state['active_dashboard_id']

                with ui.row().classes('items-center gap-0'):
                    ui.button(
                        db['name'],
                        on_click=lambda _, did=db['id']: _switch_dashboard(did),
                    ).props('no-caps dense').classes(
                        'text-sm px-3 py-1 rounded-lg ' +
                        ('bg-zinc-800 text-white' if is_active else 'text-zinc-500')
                    )

                    # Rename/delete controls in edit mode for non-default dashboards
                    if state['edit_mode'] and not db['is_default']:
                        ui.button(
                            icon='edit',
                            on_click=lambda _, db=db: _rename_dashboard_dialog(db),
                        ).props('flat round dense size=xs').classes('text-zinc-400') \
                         .tooltip('Rename')
                        ui.button(
                            icon='delete_outline',
                            on_click=lambda _, did=db['id']: _delete_dashboard(did),
                        ).props('flat round dense size=xs').classes('text-red-300') \
                         .tooltip('Delete dashboard')

            ui.button(icon='add', on_click=lambda: _new_dashboard_dialog()) \
                .props('flat round dense size=sm').classes('text-gray-400') \
                .tooltip('New dashboard')

    # ── Active category filter chip ───────────────────────────────────────────
    @ui.refreshable
    def category_chip() -> None:
        if state.get('category'):
            with ui.row().classes('items-center gap-2 mb-3'):
                ui.icon('filter_alt').classes('text-indigo-500').style('font-size:1rem')
                ui.label(f"Filtered: {state['category']}").classes('text-sm font-semibold text-indigo-700')
                ui.button(icon='close', on_click=lambda: _clear_category()) \
                    .props('flat round dense size=xs').classes('text-indigo-400') \
                    .tooltip('Clear category filter')

    # ── Dashboard grid ────────────────────────────────────────────────────────
    @ui.refreshable
    def dashboard_grid(y) -> None:
        persons      = _persons()
        dashboard_id = state['active_dashboard_id']
        widgets      = get_widgets(dashboard_id)
        edit         = state['edit_mode']

        shared_state = {
            'category':            state.get('category'),
            '_on_category_click':  lambda cat: _on_cat_change(cat),
            '_refresh_dashboard':  lambda: dashboard_grid.refresh(state['year']),
            '_refresh_txn_table':  lambda: txn_table.refresh(),
        }

        # Add-widget button shown at the top in edit mode
        if edit:
            with ui.row().classes('mb-3 justify-start'):
                ui.button(
                    'Add Widget', icon='add_circle_outline',
                    on_click=lambda: _add_widget_dialog(),
                ).props('unelevated no-caps').classes('bg-zinc-800 text-white px-4 rounded-lg')

        # Fixed row height — each row unit is 280px; gap is 1rem (16px).
        # grid-auto-rows ensures rows beyond the first 8 also get 280px.
        ROW_H = 180
        with ui.element('div').classes('dashboard-grid-container').style(
            'display:grid;grid-template-columns:repeat(4,1fr);'
            f'grid-auto-rows:{ROW_H}px;'
            'gap:1rem;'
        ):
            for w in widgets:
                chart_def = REGISTRY_BY_ID.get(w['chart_id'])
                if not chart_def:
                    continue

                col_span  = w['col_span']
                row_span  = w['row_span']
                col_start = w['col_start']
                row_start = w['row_start']

                widget_persons = w['config'].get('persons') or persons

                # Card fills its grid area exactly; overflow hidden clips any excess.
                # position:relative lets the edit-bar overlay sit inside it.
                card_el = ui.element('div').classes(
                    f'card dashboard-card wid-{w["id"]} wcs-{col_span} wrs-{row_span} wco-{col_start} wro-{row_start}'
                ).style(
                    f'grid-column:{col_start} / span {col_span};'
                    f'grid-row:{row_start} / span {row_span};'
                    'position:relative;overflow:hidden;'
                    f'height:calc({row_span}*{ROW_H}px + {row_span - 1}*1rem);'
                )

                with card_el:
                    # ── Edit-mode control bar (absolute overlay at top) ───────
                    if edit:
                        CTRL_H = 44
                        with ui.element('div').classes('dashboard-drag-handle').style(
                            'position:absolute;top:0;left:0;right:0;'
                            f'height:{CTRL_H}px;'
                            'display:flex;align-items:center;justify-content:space-between;'
                            'padding:0 8px;'
                            'background:rgba(255,255,255,0.95);'
                            'border-bottom:1px solid #e4e4e7;'
                            'z-index:10;gap:4px;cursor:grab;'
                        ):
                            # Drag grip indicator
                            ui.icon('drag_indicator').classes('text-zinc-400') \
                              .style('font-size:1.1rem;flex-shrink:0;pointer-events:none')

                            ui.element('div').style('flex:1')  # spacer

                            # Settings button (only for widgets with config_fields)
                            if chart_def.config_fields:
                                ui.button(
                                    icon='tune',
                                    on_click=lambda _, wid=w['id'], cd=chart_def, cfg=dict(w['config']): _widget_settings_dialog(wid, cd, cfg),
                                ).props('flat round dense size=xs').classes('text-zinc-400') \
                                 .tooltip('Widget settings')

                            # Remove button
                            ui.button(
                                icon='close',
                                on_click=lambda _, wid=w['id']: _remove_widget(wid),
                            ).props('flat round dense size=xs').classes('text-red-400') \
                             .tooltip('Remove widget')

                        # Push chart content below the overlay bar
                        ui.element('div').style(f'height:{CTRL_H}px;flex-shrink:0')

                        # Right resize handle (col-span)
                        ui.element('div').classes('dashboard-resize-right').style(
                            f'position:absolute;top:{CTRL_H}px;bottom:8px;right:0;width:8px;'
                            'cursor:ew-resize;z-index:10;'
                            'background:rgba(99,102,241,0.15);border-radius:0 8px 8px 0;'
                        )

                        # Bottom resize handle (row-span)
                        ui.element('div').classes('dashboard-resize-bottom').style(
                            'position:absolute;bottom:0;left:8px;right:8px;height:8px;'
                            'cursor:ns-resize;z-index:10;'
                            'background:rgba(99,102,241,0.15);border-radius:0 0 8px 8px;'
                        )

                        # Invisible overlay — blocks chart interactions and acts as drag handle
                        ui.element('div').classes('dashboard-drag-handle').style(
                            f'position:absolute;top:{CTRL_H}px;left:0;right:8px;bottom:8px;'
                            'z-index:9;cursor:grab;'
                        )

                    # ── Standard header ───────────────────────────────────────
                    if not chart_def.has_own_header:
                        with ui.row().classes('items-center justify-between mb-3'):
                            ui.label(chart_def.title).classes('label-text')
                            ui.label(str(y)).classes('text-xs text-muted')

                    # ── Chart content ─────────────────────────────────────────
                    chart_def.render(y, widget_persons, w['config'], shared_state)


    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _refresh_all() -> None:
        category_chip.refresh()
        dashboard_grid.refresh(state['year'])
        txn_table.refresh()

    def _clear_category() -> None:
        state['category'] = None
        category_chip.refresh()
        dashboard_grid.refresh(state['year'])
        txn_table.refresh()

    def _on_cat_change(cat: str) -> None:
        state['category'] = None if cat == state.get('category') else cat
        category_chip.refresh()
        dashboard_grid.refresh(state['year'])
        txn_table.refresh()

    def _toggle_edit() -> None:
        state['edit_mode'] = not state['edit_mode']
        if state['edit_mode']:
            edit_btn.set_text('Done')
            edit_btn.props('icon=check_circle')
        else:
            edit_btn.set_text('Edit Dashboard')
            edit_btn.props('icon=edit')
        dashboard_tabs.refresh()
        dashboard_grid.refresh(state['year'])

    def _switch_dashboard(dashboard_id: int) -> None:
        state['active_dashboard_id'] = dashboard_id
        state['category'] = None
        category_chip.refresh()
        dashboard_tabs.refresh()
        dashboard_grid.refresh(state['year'])
        txn_table.refresh()

    # ── Dashboard management ──────────────────────────────────────────────────

    def _new_dashboard_dialog() -> None:
        with ui.dialog() as dlg, ui.card().classes('w-80 rounded-2xl p-6 gap-4'):
            ui.label('New Dashboard').classes('text-base font-semibold')
            name_input = ui.input(placeholder='e.g. Personal, Savings') \
                .props('outlined dense').classes('w-full')

            with ui.row().classes('justify-end gap-2 mt-1'):
                ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')

                def _create() -> None:
                    name = name_input.value.strip()
                    if not name:
                        return
                    new_id = create_dashboard(user_id, name)
                    state['active_dashboard_id'] = new_id
                    dlg.close()
                    dashboard_tabs.refresh()
                    dashboard_grid.refresh(state['year'])

                ui.button('Create', on_click=_create) \
                    .props('unelevated no-caps').classes('bg-zinc-800 text-white')

        dlg.open()

    def _rename_dashboard_dialog(db: dict) -> None:
        with ui.dialog() as dlg, ui.card().classes('w-80 rounded-2xl p-6 gap-4'):
            ui.label('Rename Dashboard').classes('text-base font-semibold')
            name_input = ui.input(value=db['name']).props('outlined dense').classes('w-full')

            with ui.row().classes('justify-end gap-2 mt-1'):
                ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')

                def _save() -> None:
                    name = name_input.value.strip()
                    if not name:
                        return
                    rename_dashboard(db['id'], name)
                    dlg.close()
                    dashboard_tabs.refresh()

                ui.button('Save', on_click=_save) \
                    .props('unelevated no-caps').classes('bg-zinc-800 text-white')

        dlg.open()

    def _delete_dashboard(dashboard_id: int) -> None:
        try:
            delete_dashboard(dashboard_id, user_id)
        except ValueError as e:
            notify(str(e), type='negative', position='top')
            return
        if state['active_dashboard_id'] == dashboard_id:
            state['active_dashboard_id'] = get_or_create_default(user_id)
            dashboard_grid.refresh(state['year'])
        dashboard_tabs.refresh()

    # ── Widget management ─────────────────────────────────────────────────────


    def _cascade_push_down(dashboard_id: int) -> None:
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
                        bottom = upper['row_start'] + upper['row_span']
                        new_start = bottom  # place immediately after upper widget
                        if new_start > lower['row_start']:
                            update_widget_layout(lower['id'], row_start=new_start)
                            pushed = True
                            break
                if pushed:
                    break
            if not pushed:
                break

    def _compact_grid(dashboard_id: int) -> None:
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

    def _set_col_span(widget_id: int, col_span: int) -> None:
        widgets  = get_widgets(state['active_dashboard_id'])
        w        = next((x for x in widgets if x['id'] == widget_id), None)
        if not w:
            return
        # Clamp so widget stays within the 4-column grid
        col_span = max(1, min(col_span, 5 - w['col_start']))
        update_widget_layout(widget_id, col_span=col_span)
        _cascade_push_down(state['active_dashboard_id'])
        _compact_grid(state['active_dashboard_id'])
        dashboard_grid.refresh(state['year'])

    def _set_row_span(widget_id: int, row_span: int) -> None:
        widgets  = get_widgets(state['active_dashboard_id'])
        w        = next((x for x in widgets if x['id'] == widget_id), None)
        if not w:
            return
        update_widget_layout(widget_id, row_span=row_span)
        _cascade_push_down(state['active_dashboard_id'])
        _compact_grid(state['active_dashboard_id'])
        dashboard_grid.refresh(state['year'])

    def _apply_move(widget_id: int, new_col: int, new_row: int) -> None:
        """Move widget to absolute (new_col, new_row), swapping any blocker."""
        widgets = get_widgets(state['active_dashboard_id'])
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

            # Step 1: W at blocker's origin — displace any collateral there
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

            # Step 2: blocker at W's origin — displace any collateral there
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
        dashboard_grid.refresh(state['year'])

    def _remove_widget(widget_id: int) -> None:
        remove_widget(widget_id)
        _compact_grid(state['active_dashboard_id'])
        dashboard_grid.refresh(state['year'])

    def _widget_settings_dialog(widget_id: int, chart_def, current_config: dict) -> None:
        inputs = {}

        with ui.dialog() as dlg, ui.card().classes('rounded-2xl p-6 gap-3 min-w-80'):
            with ui.row().classes('items-center justify-between w-full mb-1'):
                ui.label(f'{chart_def.title}').classes('text-base font-semibold text-zinc-800')
                ui.button(icon='close', on_click=dlg.close) \
                    .props('flat round dense size=sm').classes('text-zinc-400')

            for field in chart_def.config_fields:
                key     = field['key']
                label   = field['label']
                value   = current_config.get(key, field.get('default'))

                if field['type'] == 'number':
                    inputs[key] = ui.number(
                        label, value=value,
                        min=field.get('min'), max=field.get('max'),
                    ).props('outlined dense').classes('w-full')
                elif field['type'] == 'select':
                    opts = {o: l for o, l in zip(field['options'], field['option_labels'])}
                    inputs[key] = ui.select(opts, value=value, label=label) \
                        .props('outlined dense').classes('w-full')

            def _save():
                new_config = dict(current_config)
                for k, inp in inputs.items():
                    new_config[k] = inp.value
                update_widget_config(widget_id, new_config)
                dashboard_grid.refresh(state['year'])
                dlg.close()

            with ui.row().classes('justify-end gap-2 mt-2 w-full'):
                ui.button('Cancel', on_click=dlg.close) \
                    .props('flat no-caps').classes('text-zinc-500')
                ui.button('Save', on_click=_save) \
                    .props('unelevated no-caps') \
                    .classes('bg-zinc-800 text-white rounded-lg px-4')

        dlg.open()

    def _add_widget_dialog() -> None:
        dashboard_id = state['active_dashboard_id']
        existing     = {w['chart_id'] for w in get_widgets(dashboard_id)}
        available    = [c for c in REGISTRY if c.id not in existing]

        with ui.dialog() as dlg, \
             ui.card().classes('w-[520px] rounded-2xl p-0 gap-0 overflow-hidden'):

            with ui.row().classes('items-center justify-between px-6 py-4 border-b border-zinc-100'):
                ui.label('Add Widget').classes('text-base font-semibold text-zinc-800')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

            with ui.scroll_area().style('height:420px'):
                with ui.column().classes('w-full px-4 py-3 gap-1'):
                    if not available:
                        ui.label('All available widgets are already on this dashboard.') \
                            .classes('text-sm text-muted py-6 text-center w-full')
                    else:
                        from itertools import groupby
                        for category, charts in groupby(available, key=lambda c: c.category):
                            ui.label(category.title()) \
                                .classes('text-xs font-semibold text-zinc-400 uppercase tracking-wide mt-3 mb-1')
                            for chart_def in charts:
                                with ui.row().classes(
                                    'items-center justify-between py-2 px-3 rounded-lg '
                                    'hover:bg-zinc-50 w-full'
                                ):
                                    with ui.row().classes('items-center gap-3'):
                                        ui.icon(chart_def.icon) \
                                            .classes('text-zinc-400').style('font-size:1.3rem')
                                        with ui.column().classes('gap-0'):
                                            ui.label(chart_def.title).classes('text-sm font-medium')
                                            ui.label(chart_def.description).classes('text-xs text-muted')
                                    ui.button(
                                        'Add',
                                        on_click=lambda _, cd=chart_def: _do_add_widget(cd, dlg),
                                    ).props('unelevated dense no-caps size=sm') \
                                     .classes('bg-zinc-800 text-white px-3')

        dlg.open()

    def _do_add_widget(chart_def, dlg) -> None:
        add_widget(
            state['active_dashboard_id'],
            chart_def.id,
            col_span=chart_def.default_col_span,
            row_span=chart_def.default_row_span,
        )
        dashboard_grid.refresh(state['year'])
        dlg.close()

    # ── Initial render ────────────────────────────────────────────────────────
    dashboard_tabs()
    category_chip()
    dashboard_grid(state['year'])

    # ── Transactions table ────────────────────────────────────────────────────
    # Kept outside the grid so filters survive dashboard refreshes.
    with ui.element('div').classes('card w-full mb-4'):

        filter_state = {
            'mode':      'simple',
            'category':  None,
            'cost_type': None,
            'bank':      None,
            'from_date': None,
            'to_date':   None,
            'search':    '',
        }

        def _fset(key: str, val) -> None:
            filter_state[key] = val

        def _clear_filters() -> None:
            for k in ('category', 'cost_type', 'bank', 'from_date', 'to_date', 'search'):
                filter_state[k] = None if k != 'search' else ''

        def _toggle_mode() -> None:
            filter_state['mode'] = 'advanced' if filter_state['mode'] == 'simple' else 'simple'
            adv_btn.set_text('Advanced Search' if filter_state['mode'] == 'simple' else '← Simple Filters')
            adv_btn.props('icon=tune' if filter_state['mode'] == 'simple' else 'icon=arrow_back')
            filter_area.refresh()

        with ui.row().classes('items-center justify-between mb-3'):
            ui.label('All Transactions').classes('label-text')
            adv_btn = ui.button('Advanced Search', icon='tune') \
                .props('flat dense no-caps size=sm').classes('text-gray-400 text-xs')
            adv_btn.on('click', lambda: _toggle_mode())

        @ui.refreshable
        def filter_area() -> None:
            opts = data.get_filter_options(state['year'])

            if filter_state['mode'] == 'simple':
                with ui.row().classes('items-center gap-2 flex-wrap pb-3 border-b border-gray-100 mb-3'):

                    ui.select(
                        options=['All categories'] + opts['categories'],
                        value=filter_state['category'] or 'All categories',
                        label='Category',
                        on_change=lambda e: (
                            _fset('category', None if e.value == 'All categories' else e.value),
                            txn_table.refresh(),
                        ),
                    ).props('outlined dense').classes('w-44')

                    ui.select(
                        options=['Any type'] + opts['cost_types'],
                        value=filter_state['cost_type'] or 'Any type',
                        label='Type',
                        on_change=lambda e: (
                            _fset('cost_type', None if e.value == 'Any type' else e.value),
                            txn_table.refresh(),
                        ),
                    ).props('outlined dense').classes('w-32')

                    ui.select(
                        options=['Any account'] + opts['banks'],
                        value=filter_state['bank'] or 'Any account',
                        label='Account',
                        on_change=lambda e: (
                            _fset('bank', None if e.value == 'Any account' else e.value),
                            txn_table.refresh(),
                        ),
                    ).props('outlined dense').classes('w-44')

                    def _date_label() -> str:
                        f, t = filter_state['from_date'], filter_state['to_date']
                        if f and t:   return f'{f}  →  {t}'
                        if f:         return f'From {f}'
                        if t:         return f'Until {t}'
                        return ''

                    date_input = ui.input(label='Date range', value=_date_label()) \
                        .props('outlined dense readonly').classes('w-56').style('cursor:pointer')

                    with date_input.add_slot('append'):
                        ui.icon('event').classes('cursor-pointer text-gray-400') \
                            .on('click', lambda: date_menu.open())

                    with ui.menu().props('no-parent-event') as date_menu:
                        _init_val = (
                            {'from': filter_state['from_date'], 'to': filter_state['to_date']}
                            if filter_state['from_date'] or filter_state['to_date'] else None
                        )
                        date_picker = ui.date(value=_init_val).props('range')
                        with ui.row().classes('justify-between items-center px-3 pb-3 pt-1 gap-4'):
                            ui.button('Clear', on_click=lambda: (
                                _fset('from_date', None), _fset('to_date', None),
                                date_picker.set_value(None),
                                date_input.set_value(''),
                                date_menu.close(), txn_table.refresh(),
                            )).props('flat dense no-caps size=sm').classes('text-gray-400')
                            ui.button('Apply', on_click=lambda: (
                                _fset('from_date', date_picker.value.get('from') if isinstance(date_picker.value, dict) else None),
                                _fset('to_date',   date_picker.value.get('to')   if isinstance(date_picker.value, dict) else None),
                                date_input.set_value(_date_label()),
                                date_menu.close(), txn_table.refresh(),
                            )).props('flat dense no-caps size=sm').classes('text-blue-500 font-semibold')

                    active = {k: v for k, v in {
                        'category':  filter_state['category'],
                        'type':      filter_state['cost_type'],
                        'account':   filter_state['bank'],
                        'date':      _date_label() or None,
                    }.items() if v}

                    if active:
                        for label, val in active.items():
                            with ui.element('div').classes(
                                'flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium '
                                'bg-blue-50 text-blue-700 border border-blue-200'
                            ):
                                ui.label(f'{label}: {val}')
                                ui.icon('close').classes('text-xs cursor-pointer').on(
                                    'click', lambda _, k=label: (
                                        _fset('category',  None) if k == 'category' else None,
                                        _fset('cost_type', None) if k == 'type'     else None,
                                        _fset('bank',      None) if k == 'account'  else None,
                                        (_fset('from_date', None), _fset('to_date', None)) if k == 'date' else None,
                                        filter_area.refresh(), txn_table.refresh(),
                                    )
                                )

                        ui.button('Clear all', on_click=lambda: (
                            _clear_filters(), filter_area.refresh(), txn_table.refresh()
                        )).props('flat dense no-caps size=sm').classes('text-gray-400 text-xs ml-1')

            else:
                with ui.column().classes('w-full gap-1 pb-3 border-b border-gray-100 mb-3'):
                    def _on_search_keydown(e) -> None:
                        key = (e.args.get('key') if isinstance(e.args, dict) else
                               e.args[0].get('key') if isinstance(e.args, list) else '')
                        if key == 'Enter':
                            txn_table.refresh()

                    ui.input(
                        placeholder='e.g.  costco   or   category=groceries  type=fixed  bank=chase  from=2025-01-01  to=2025-06-30  amount=50',
                        value=filter_state['search'],
                        on_change=lambda e: _fset('search', e.value or ''),
                    ).props('outlined dense clearable').classes('w-full').style('font-size:12px') \
                     .on('keydown', _on_search_keydown)
                    with ui.row().classes('gap-3 flex-wrap items-center'):
                        for hint in ['category=', 'type=fixed|variable', 'bank=', 'from=YYYY-MM-DD', 'to=YYYY-MM-DD', 'amount=']:
                            ui.label(hint).classes(
                                'text-xs font-mono px-1.5 py-0.5 rounded bg-gray-100 text-gray-500'
                            )
                        ui.label('Press Enter to apply').classes('text-xs text-gray-400 italic ml-1')

        filter_area()

        @ui.refreshable
        def txn_table() -> None:
            from components.finance_charts import transactions_table

            persons = _persons()

            if filter_state['mode'] == 'simple':
                transactions_table(data.gettransactions_table(
                    state['year'], persons,
                    category=state.get('category'),
                    filters={
                        'cost_type': filter_state['cost_type'],
                        'bank':      filter_state['bank'],
                        'from_date': filter_state['from_date'],
                        'to_date':   filter_state['to_date'],
                        'category':  filter_state['category'],
                    },
                ))
            else:
                transactions_table(data.gettransactions_table(
                    state['year'], persons,
                    search=filter_state['search'],
                    category=state.get('category'),
                ))

        txn_table()

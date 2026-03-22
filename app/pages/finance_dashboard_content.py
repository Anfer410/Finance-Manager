"""
finance_dashboard_content.py
"""

from __future__ import annotations

import pathlib
from datetime import datetime

from nicegui import ui
from services.notifications import notify

from services.dashboard_config import (
    get_or_create_default, list_dashboards, create_dashboard,
    delete_dashboard, rename_dashboard,
    add_widget, update_widget_config, update_widget_label,
    get_widgets, restore_widgets,
    set_dashboard_shares, get_dashboard_shares,
    get_shared_with_me, set_subscription, list_subscribed_shared,
)
from services.dashboard_grid_layout import (
    set_col_span, set_row_span, apply_move,
    remove_widget as grid_remove_widget,
)
from components.widgets import REGISTRY_BY_ID, RenderContext
from components.widgets.settings_ui import open_widget_settings_dialog
from components.add_widget_dialog import open_add_widget_dialog
from components.dashboard_txn_table import render_txn_table

from services.auth import current_user_id, current_selected_persons, current_family_id
from services.family_service import get_family_members

import data.finance_dashboard_data as data

_DRAG_JS = (
    pathlib.Path(__file__).parent.parent / 'assets' / 'dashboard_drag.js'
).read_text()


def _resolve_widget(chart_id: str):
    if chart_id.startswith('custom:'):
        try:
            cid = int(chart_id.split(':', 1)[1])
        except (ValueError, IndexError):
            return None
        from services.custom_chart_repo import get_custom_chart
        from components.widgets.custom_chart_widget import CustomChartWidget
        record = get_custom_chart(cid)
        return CustomChartWidget(record) if record else None
    return REGISTRY_BY_ID.get(chart_id)


# ── Main content ──────────────────────────────────────────────────────────────

def content() -> None:
    user_id    = current_user_id()
    years      = data.get_years()
    now        = datetime.now()

    state = {
        'year':                years[0] if years else now.year,
        'category':            None,
        'edit_mode':           False,
        'active_dashboard_id': get_or_create_default(user_id),
        'edit_snapshot':       None,   # widget snapshot taken when entering edit mode
        'is_shared_view':      False,  # True when viewing a dashboard owned by someone else
    }

    # ── Drag-to-move/resize event bus ─────────────────────────────────────────
    _bus = ui.element('div').style('display:none')

    def _on_drag_move(e) -> None:
        args = e.args or {}
        wid, col, row = args.get('widget_id'), args.get('col'), args.get('row')
        if wid is not None and col is not None and row is not None:
            apply_move(int(wid), int(col), int(row), state['active_dashboard_id'])
            dashboard_grid.refresh(state['year'])

    def _on_drag_resize(e) -> None:
        args = e.args or {}
        wid, rtype, val = args.get('widget_id'), args.get('rtype'), args.get('val')
        if wid is None or val is None:
            return
        if rtype == 'col':
            set_col_span(int(wid), int(val), state['active_dashboard_id'])
        elif rtype == 'row':
            set_row_span(int(wid), int(val), state['active_dashboard_id'])
        dashboard_grid.refresh(state['year'])

    _bus.on('widget-moved',   _on_drag_move,   args=['widget_id', 'col', 'row'])
    _bus.on('widget-resized', _on_drag_resize, args=['widget_id', 'rtype', 'val'])
    ui.run_javascript(_DRAG_JS.replace('__BUS_ID__', f'c{_bus.id}'))

    def _persons() -> list[int] | None:
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
                on_change=lambda e: (state.update({'year': e.value}), _refresh_all()),
            ).props('outlined dense').classes('w-28')

            ui.button('Refresh', icon='refresh', on_click=lambda: _refresh_all()) \
                .props('flat no-caps').classes('button button-outline')

            edit_btn = ui.button('Edit Dashboard', icon='edit', on_click=lambda: _toggle_edit()) \
                .props('flat no-caps').classes('text-zinc-500')
            cancel_btn = ui.button('Cancel', icon='close', on_click=lambda: _cancel_edit()) \
                .props('flat no-caps').classes('text-zinc-400')
            cancel_btn.set_visibility(False)

    ui.element('div').classes('divider mb-2')

    # ── Dashboard tab bar ─────────────────────────────────────────────────────
    @ui.refreshable
    def dashboard_tabs() -> None:
        own_dbs      = list_dashboards(user_id)
        shared_pinned = list_subscribed_shared(user_id)
        shared_all    = get_shared_with_me(user_id)
        edit          = state['edit_mode']

        with ui.column().classes('gap-2 mb-4 w-full'):
            # ── Tab row ───────────────────────────────────────────────────────
            with ui.row().classes('items-center gap-1 flex-wrap'):
                # Own dashboards
                for db in own_dbs:
                    is_active = db['id'] == state['active_dashboard_id'] and not state['is_shared_view']
                    with ui.row().classes('items-center gap-0'):
                        ui.button(
                            db['name'],
                            on_click=lambda _, did=db['id']: _switch_dashboard(did, is_shared=False),
                        ).props('no-caps dense').classes(
                            'text-sm px-3 py-1 rounded-lg ' +
                            ('bg-zinc-800 text-white' if is_active else 'text-zinc-500')
                        )
                        if edit:
                            if not db['is_default']:
                                ui.button(
                                    icon='edit',
                                    on_click=lambda _, db=db: _rename_dashboard_dialog(db),
                                ).props('flat round dense size=xs').classes('text-zinc-400').tooltip('Rename')
                                ui.button(
                                    icon='delete_outline',
                                    on_click=lambda _, did=db['id']: _delete_dashboard(did),
                                ).props('flat round dense size=xs').classes('text-red-300').tooltip('Delete dashboard')
                            ui.button(
                                icon='group',
                                on_click=lambda _, db=db: _share_dashboard_dialog(db),
                            ).props('flat round dense size=xs').classes('text-indigo-400').tooltip('Share dashboard')

                # Subscribed shared dashboards
                for sd in shared_pinned:
                    is_active = sd['id'] == state['active_dashboard_id'] and state['is_shared_view']
                    with ui.row().classes('items-center gap-0.5'):
                        with ui.element('div').classes('flex flex-col items-start'):
                            ui.button(
                                sd['name'],
                                on_click=lambda _, did=sd['id']: _switch_dashboard(did, is_shared=True),
                            ).props('no-caps dense').classes(
                                'text-sm px-3 py-1 rounded-lg ' +
                                ('bg-indigo-700 text-white' if is_active else 'text-indigo-400')
                            )
                        ui.icon('group').classes('text-indigo-300').style('font-size:0.9rem') \
                            .tooltip(f'Shared by {sd["owner_name"]}')

                # "Shared" pill — always visible so users know something is shared with them
                if shared_all and not edit:
                    _n_new = sum(1 for s in shared_all if not s['is_subscribed'])
                    with ui.element('div').style('position:relative;display:inline-flex'):
                        ui.button(
                            'Shared', icon='group',
                            on_click=lambda: _open_shared_panel(),
                        ).props('flat no-caps dense').classes('text-indigo-400 text-sm px-2')
                        if _n_new:
                            ui.badge(str(_n_new), color='indigo').props('floating rounded')

                ui.button(icon='add', on_click=lambda: _new_dashboard_dialog()) \
                    .props('flat round dense size=sm').classes('text-gray-400').tooltip('New dashboard')

            # ── Shared-with-me section (edit mode only) ───────────────────────
            if edit and shared_all:
                with ui.element('div').classes(
                    'w-full rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3'
                ):
                    ui.label('Shared with me').classes(
                        'text-xs font-semibold text-indigo-400 uppercase tracking-wide mb-2'
                    )
                    for s in shared_all:
                        with ui.row().classes('items-center gap-2 py-1'):
                            ui.icon('dashboard_customize').classes('text-indigo-400').style('font-size:1rem')
                            ui.label(s['name']).classes('text-sm flex-1')
                            ui.label(f'by {s["owner_name"]}').classes('text-xs text-zinc-400')
                            ui.switch(
                                value=s['is_subscribed'],
                                on_change=lambda e, did=s['dashboard_id']:
                                    _toggle_subscription(did, e.value),
                            ).props('dense color=indigo').tooltip(
                                'Show as tab' if not s['is_subscribed'] else 'Hide from tabs'
                            )

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
        from services.dashboard_config import get_widgets
        persons      = _persons()
        dashboard_id = state['active_dashboard_id']
        widgets      = get_widgets(dashboard_id)
        edit         = state['edit_mode'] and not state['is_shared_view']

        shared_state = {
            'category':           state.get('category'),
            '_on_category_click': lambda cat: _on_cat_change(cat),
            '_refresh_dashboard': lambda: dashboard_grid.refresh(state['year']),
            '_refresh_txn_table': lambda: txn_table_refresh(),
        }

        ROW_H = 180
        with ui.element('div').classes('dashboard-grid-container').style(
            'display:grid;grid-template-columns:repeat(4,1fr);'
            f'grid-auto-rows:{ROW_H}px;gap:1rem;'
        ):
            for w in widgets:
                chart_def = _resolve_widget(w['chart_id'])
                if not chart_def:
                    continue

                col_span, row_span   = w['col_span'], w['row_span']
                col_start, row_start = w['col_start'], w['row_start']
                ctx = RenderContext.build(y, persons, w['config'], shared_state)

                card_el = ui.element('div').classes(
                    f'card dashboard-card wid-{w["id"]} wcs-{col_span} wrs-{row_span} '
                    f'wco-{col_start} wro-{row_start}'
                ).style(
                    f'grid-column:{col_start} / span {col_span};'
                    f'grid-row:{row_start} / span {row_span};'
                    'position:relative;overflow:hidden;'
                    f'height:calc({row_span}*{ROW_H}px + {row_span - 1}*1rem);'
                )

                with card_el:
                    if edit:
                        CTRL_H = 44
                        with ui.element('div').classes('dashboard-drag-handle').style(
                            'position:absolute;top:0;left:0;right:0;'
                            f'height:{CTRL_H}px;display:flex;align-items:center;'
                            'justify-content:space-between;padding:0 8px;'
                            'background:rgba(255,255,255,0.95);'
                            'border-bottom:1px solid #e4e4e7;z-index:10;gap:4px;cursor:grab;'
                        ):
                            ui.icon('drag_indicator').classes('text-zinc-400') \
                              .style('font-size:1.1rem;flex-shrink:0;pointer-events:none')
                            ui.element('div').style('flex:1')
                            ui.button(
                                icon='tune',
                                on_click=lambda _, wid=w['id'], cd=chart_def, cfg=dict(w['config']):
                                    _widget_settings(wid, cd, cfg),
                            ).props('flat round dense size=xs').classes('text-zinc-400') \
                             .tooltip('Widget settings')
                            ui.button(
                                icon='close',
                                on_click=lambda _, wid=w['id']: _remove_widget(wid),
                            ).props('flat round dense size=xs').classes('text-red-400') \
                             .tooltip('Remove widget')

                        ui.element('div').style(f'height:{CTRL_H}px;flex-shrink:0')
                        ui.element('div').classes('dashboard-resize-right').style(
                            f'position:absolute;top:{CTRL_H}px;bottom:8px;right:0;width:8px;'
                            'cursor:ew-resize;z-index:10;'
                            'background:rgba(99,102,241,0.15);border-radius:0 8px 8px 0;'
                        )
                        ui.element('div').classes('dashboard-resize-bottom').style(
                            'position:absolute;bottom:0;left:8px;right:8px;height:8px;'
                            'cursor:ns-resize;z-index:10;'
                            'background:rgba(99,102,241,0.15);border-radius:0 0 8px 8px;'
                        )
                        ui.element('div').classes('dashboard-drag-handle').style(
                            f'position:absolute;top:{CTRL_H}px;left:0;right:8px;bottom:8px;'
                            'z-index:9;cursor:grab;'
                        )

                    if not chart_def.has_own_header:
                        _label = w.get('instance_label') or chart_def.title
                        _tm    = w['config'].get('time_mode', 'page_year')
                        if _tm == 'trailing':
                            _hint = f'{w["config"].get("trailing_months", 12)}mo'
                        elif _tm == 'all_time':
                            _hint = 'All Time'
                        elif _tm == 'year':
                            _hint = str(w['config'].get('year', y))
                        else:
                            _hint = str(y)
                        with ui.row().classes('items-center justify-between mb-3'):
                            ui.label(_label).classes('label-text')
                            ui.label(_hint).classes('text-xs text-muted')

                    try:
                        chart_def.render(ctx)
                    except Exception as _err:
                        import traceback, logging
                        logging.getLogger(__name__).error(
                            'Widget %s render failed: %s\n%s',
                            chart_def.id, _err, traceback.format_exc(),
                        )
                        ui.label(f'⚠ Widget error: {_err}').classes('text-xs text-red-400 p-2')

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _refresh_all() -> None:
        category_chip.refresh()
        dashboard_grid.refresh(state['year'])
        txn_table_refresh()

    def _clear_category() -> None:
        state['category'] = None
        category_chip.refresh()
        dashboard_grid.refresh(state['year'])
        txn_table_refresh()

    def _on_cat_change(cat: str) -> None:
        state['category'] = None if cat == state.get('category') else cat
        category_chip.refresh()
        dashboard_grid.refresh(state['year'])
        txn_table_refresh()

    def _toggle_edit() -> None:
        if state['is_shared_view']:
            return
        state['edit_mode'] = not state['edit_mode']
        if state['edit_mode']:
            state['edit_snapshot'] = get_widgets(state['active_dashboard_id'])
        else:
            state['edit_snapshot'] = None
        edit_btn.set_text('Done' if state['edit_mode'] else 'Edit Dashboard')
        edit_btn.props('icon=check_circle' if state['edit_mode'] else 'icon=edit')
        cancel_btn.set_visibility(state['edit_mode'])
        dashboard_tabs.refresh()
        dashboard_grid.refresh(state['year'])
        edit_fab.refresh()

    def _cancel_edit() -> None:
        if state['edit_snapshot'] is not None:
            restore_widgets(state['active_dashboard_id'], state['edit_snapshot'])
        state['edit_mode'] = False
        state['edit_snapshot'] = None
        edit_btn.set_text('Edit Dashboard')
        edit_btn.props('icon=edit')
        cancel_btn.set_visibility(False)
        dashboard_tabs.refresh()
        dashboard_grid.refresh(state['year'])
        edit_fab.refresh()

    def _switch_dashboard(dashboard_id: int, *, is_shared: bool = False) -> None:
        # Exit edit mode when switching to a shared dashboard
        if is_shared and state['edit_mode']:
            state['edit_mode'] = False
            state['edit_snapshot'] = None
            edit_btn.set_text('Edit Dashboard')
            edit_btn.props('icon=edit')
            cancel_btn.set_visibility(False)
            edit_fab.refresh()
        state['is_shared_view'] = is_shared
        state['active_dashboard_id'] = dashboard_id
        state['category'] = None
        edit_btn.set_visibility(not is_shared)
        category_chip.refresh()
        dashboard_tabs.refresh()
        dashboard_grid.refresh(state['year'])
        txn_table_refresh()

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
                    state['active_dashboard_id'] = create_dashboard(user_id, name)
                    dlg.close()
                    dashboard_tabs.refresh()
                    dashboard_grid.refresh(state['year'])
                ui.button('Create', on_click=_create).props('unelevated no-caps').classes('bg-zinc-800 text-white')
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
                ui.button('Save', on_click=_save).props('unelevated no-caps').classes('bg-zinc-800 text-white')
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

    # ── Share management ──────────────────────────────────────────────────────

    def _share_dashboard_dialog(db: dict) -> None:
        fid     = current_family_id()
        members = [m for m in get_family_members(fid) if m.user_id != user_id] if fid else []
        current = set(get_dashboard_shares(db['id']))

        with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
            ui.label(f'Share "{db["name"]}"').classes('text-base font-semibold')
            if not members:
                ui.label('No other family members to share with.') \
                    .classes('text-sm text-zinc-400')
            else:
                ui.label('Select who can see this dashboard:') \
                    .classes('text-sm text-zinc-500')
                checkboxes: dict[int, ui.checkbox] = {}
                for m in members:
                    cb = ui.checkbox(
                        m.display_name,
                        value=m.user_id in current,
                    ).classes('text-sm')
                    checkboxes[m.user_id] = cb

            with ui.row().classes('justify-end gap-2 mt-2'):
                ui.button('Cancel', on_click=dlg.close) \
                    .props('flat no-caps').classes('text-zinc-500')
                if members:
                    def _save() -> None:
                        selected = [uid for uid, cb in checkboxes.items() if cb.value]
                        set_dashboard_shares(db['id'], selected)
                        dlg.close()
                        notify('Sharing updated.', type='positive', position='top')
                        dashboard_tabs.refresh()
                    ui.button('Save', on_click=_save) \
                        .props('unelevated no-caps').classes('bg-zinc-800 text-white')
        dlg.open()

    def _open_shared_panel() -> None:
        shared = get_shared_with_me(user_id)
        with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
            ui.label('Shared with me').classes('text-base font-semibold')
            if not shared:
                ui.label('No dashboards have been shared with you.') \
                    .classes('text-sm text-zinc-400')
            else:
                ui.label('Toggle which shared dashboards appear as tabs.') \
                    .classes('text-sm text-zinc-500 mb-2')
                for s in shared:
                    with ui.row().classes('items-center gap-2 py-1'):
                        ui.icon('dashboard_customize').classes('text-indigo-400').style('font-size:1rem')
                        with ui.column().classes('gap-0 flex-1'):
                            ui.label(s['name']).classes('text-sm font-medium')
                            ui.label(f'by {s["owner_name"]}').classes('text-xs text-zinc-400')
                        ui.switch(
                            value=s['is_subscribed'],
                            on_change=lambda e, did=s['dashboard_id']:
                                _toggle_subscription(did, e.value),
                        ).props('dense color=indigo')
            with ui.row().classes('justify-end mt-2'):
                ui.button('Close', on_click=dlg.close) \
                    .props('flat no-caps').classes('text-zinc-500')
        dlg.open()

    def _toggle_subscription(dashboard_id: int, subscribed: bool) -> None:
        set_subscription(dashboard_id, user_id, subscribed)
        # If unsubscribing from the currently active shared dashboard, go home
        if not subscribed and state['active_dashboard_id'] == dashboard_id:
            state['active_dashboard_id'] = get_or_create_default(user_id)
            state['is_shared_view'] = False
            edit_btn.set_visibility(True)
            dashboard_grid.refresh(state['year'])
            txn_table_refresh()
        dashboard_tabs.refresh()

    # ── Widget management ─────────────────────────────────────────────────────

    def _remove_widget(widget_id: int) -> None:
        grid_remove_widget(widget_id, state['active_dashboard_id'])
        dashboard_grid.refresh(state['year'])

    def _widget_settings(widget_id: int, chart_def, current_config: dict) -> None:
        def _on_save(new_config: dict, instance_label: str | None) -> None:
            update_widget_config(widget_id, new_config)
            if instance_label is not None:
                update_widget_label(widget_id, instance_label)
            dashboard_grid.refresh(state['year'])
        open_widget_settings_dialog(
            widget_id=widget_id,
            widget_def=chart_def,
            current_config=current_config,
            on_save=_on_save,
            page_year=state['year'],
        )

    def _add_widget_dialog() -> None:
        open_add_widget_dialog(
            user_id=user_id,
            on_add_builtin=lambda cd: (
                add_widget(state['active_dashboard_id'], cd.id,
                           col_span=cd.default_col_span, row_span=cd.default_row_span),
                dashboard_grid.refresh(state['year']),
            ),
            on_add_custom=lambda r: (
                add_widget(state['active_dashboard_id'], f"custom:{r['id']}", col_span=2, row_span=1),
                dashboard_grid.refresh(state['year']),
            ),
        )

    # ── Floating add-widget FAB (edit mode only) ──────────────────────────────
    @ui.refreshable
    def edit_fab() -> None:
        if not state['edit_mode']:
            return
        with ui.element('div').style('position:fixed;bottom:48px;right:48px;z-index:200'):
            ui.button(
                'Add Widget', icon='add_circle_outline',
                on_click=_add_widget_dialog,
            ).props('unelevated no-caps').classes('bg-zinc-800 text-white px-5 py-5 rounded-xl shadow-lg')

    # ── Initial render ────────────────────────────────────────────────────────
    dashboard_tabs()
    category_chip()
    dashboard_grid(state['year'])
    edit_fab()

    # ── Transactions table ────────────────────────────────────────────────────
    with ui.element('div').classes('card w-full mb-4'):
        txn_table_refresh = render_txn_table(
            get_year=lambda: state['year'],
            get_persons=_persons,
            get_category=lambda: state.get('category'),
        )

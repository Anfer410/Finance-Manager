"""
pages/chart_builder_content.py

Interactive custom chart builder.
"""

from __future__ import annotations

from nicegui import ui, app

from services.auth import current_user_id
from services.custom_chart_repo import (
    get_custom_chart, create_custom_chart, update_custom_chart,
)
from services.custom_chart_query import (
    get_available_sources, get_source_columns, execute_chart_query,
    COMPUTED_OVERLAYS,
)
from services.dashboard_config import list_dashboards, add_widget
from components.custom_chart_renderer import render_custom_chart


_CHART_TYPE_OPTIONS = {
    'bar':         'Bar',
    'line':        'Line',
    'stacked_bar': 'Stacked Bar',
    'donut':       'Donut',
    'area_line':   'Area + Line',
    'mixed':       'Mixed',
}

_AGG_OPTIONS = {
    'sum':   'Sum',
    'count': 'Count',
    'avg':   'Average',
}

_TRUNC_OPTIONS = {
    'day':     'Day',
    'week':    'Week',
    'month':   'Month',
    'quarter': 'Quarter',
    'year':    'Year',
}

_FORMAT_OPTIONS = {
    'dollar':  'Dollar ($)',
    'number':  'Number',
    'percent': 'Percent (%)',
    'none':    'None',
}

_OP_OPTIONS = ['=', '!=', '>', '<', '>=', '<=', 'LIKE', 'NOT LIKE']

_OVERLAY_TYPE_OPTIONS = {
    'query':    'Query',
    'computed': 'Computed',
}

_COMPUTED_OVERLAY_OPTIONS = {
    'rolling_surplus': 'Rolling Surplus (Income − Spend)',
}

_TIME_MODE_OPTIONS = {
    'all_time':   'All Time',
    'trailing':   'Trailing Months',
    'year':       'Specific Year',
    'date_range': 'Date Range',
}

_TRAILING_OPTIONS = {
    3:  '3 months',
    6:  '6 months',
    12: '1 year (12 mo)',
    24: '2 years (24 mo)',
    36: '3 years (36 mo)',
    48: '4 years (48 mo)',
    60: '5 years (60 mo)',
}


def content() -> None:
    user_id = current_user_id()

    # Per-connection mutable state
    state: dict = {
        'chart_type':   'bar',
        'data_source':  'v_all_spend',
        'x_column':     'transaction_date',
        'y_column':     'amount',
        'y_agg':        'sum',
        'series_column': None,
        'date_trunc':   'month',
        'filters':      [],
        'show_legend':     True,
        'legend_position': 'top',
        'chart_height':    '300px',
        'label_format':    'dollar',
        'time_mode':       'all_time',
        'trailing_months': 12,
        'fixed_year':      None,
        'date_from':       '',
        'date_to':         '',
        'overlay_series':  [],
        'editing_id':      None,
        'chart_name':      '',
    }

    # ── Load existing chart if requested ──────────────────────────────────────
    load_id = app.storage.user.pop('chart_builder_load_id', None)
    if load_id is not None:
        rec = get_custom_chart(int(load_id))
        if rec:
            cfg = rec.get('config', {})
            state['editing_id']   = rec['id']
            state['chart_name']   = rec['name']
            state['chart_type']   = rec['chart_type']
            state['data_source']  = rec['data_source']
            state['x_column']     = cfg.get('x_column', state['x_column'])
            state['y_column']     = cfg.get('y_column', state['y_column'])
            state['y_agg']        = cfg.get('y_agg', state['y_agg'])
            state['series_column'] = cfg.get('series_column')
            state['date_trunc']   = cfg.get('date_trunc', state['date_trunc'])
            state['filters']      = cfg.get('filters', [])
            state['show_legend']     = cfg.get('show_legend', True)
            state['legend_position'] = cfg.get('legend_position', 'top')
            state['chart_height']    = cfg.get('chart_height', '300px')
            state['label_format']    = cfg.get('label_format', 'dollar')
            state['time_mode']       = cfg.get('time_mode', 'all_time')
            state['trailing_months'] = cfg.get('trailing_months', 12)
            state['fixed_year']      = cfg.get('fixed_year')
            state['date_from']       = cfg.get('date_from', '')
            state['date_to']         = cfg.get('date_to', '')
            state['overlay_series']  = cfg.get('overlay_series', [])
    else:
        # ── Clone from a built-in widget (pre-fill chart type + name) ──────
        clone_type = app.storage.user.pop('chart_builder_clone_type', None)
        clone_name = app.storage.user.pop('chart_builder_clone_name', None)
        if clone_type:
            state['chart_type'] = clone_type
        if clone_name:
            state['chart_name'] = clone_name

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_config() -> dict:
        return {
            'data_source':   state['data_source'],
            'x_column':      state['x_column'],
            'y_column':      state['y_column'],
            'y_agg':         state['y_agg'],
            'series_column': state['series_column'],
            'date_trunc':    state['date_trunc'],
            'filters':       list(state['filters']),
            'show_legend':     state['show_legend'],
            'legend_position': state['legend_position'],
            'chart_height':    state['chart_height'],
            'label_format':    state['label_format'],
            'time_mode':       state['time_mode'],
            'trailing_months': state['trailing_months'],
            'fixed_year':      state['fixed_year'],
            'date_from':       state['date_from'],
            'date_to':         state['date_to'],
            'chart_type':      state['chart_type'],
            'overlay_series':  list(state.get('overlay_series', [])),
        }

    def _columns() -> list[str]:
        try:
            return get_source_columns(state['data_source'])
        except Exception:
            return []

    # ── Save dialog ───────────────────────────────────────────────────────────
    def _open_save_dialog() -> None:
        dashboards     = list_dashboards(user_id)
        dash_options   = {str(d['id']): d['name'] for d in dashboards}
        save_name_ref  = {'v': state.get('chart_name', '')}
        save_dash_ref  = {'v': None}
        can_overwrite  = state['editing_id'] is not None

        mode_options   = ['Save (Overwrite)', 'Save as...']
        mode_ref       = {'v': 'Save (Overwrite)' if can_overwrite else 'Save as...'}

        with ui.dialog() as dlg, \
             ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
            ui.label('Save Chart').classes('text-lg font-semibold')

            mode_toggle = ui.toggle(
                mode_options,
                value=mode_ref['v'],
                on_change=lambda e: mode_ref.update({'v': e.value}),
            ).classes('w-full')
            if not can_overwrite:
                mode_toggle.set_value('Save as...')
                mode_toggle.disable()

            name_input = ui.input(
                'Chart Name',
                value=save_name_ref['v'],
                on_change=lambda e: save_name_ref.update({'v': e.value}),
            ).classes('w-full')

            dash_select = ui.select(
                dash_options,
                label='Add to Dashboard (optional)',
                value=None,
                on_change=lambda e: save_dash_ref.update({'v': e.value}),
            ).classes('w-full')
            dash_select.props('clearable')

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=dlg.close).props('flat')

                def _save_and_go():
                    _do_save()
                    if save_dash_ref['v']:
                        chart_id = f"custom:{state['editing_id']}"
                        add_widget(int(save_dash_ref['v']), chart_id, col_span=2, row_span=1)
                    dlg.close()
                    ui.navigate.to('/')

                go_btn = ui.button('Save and go to Dashboard', on_click=_save_and_go)
                go_btn.bind_enabled_from(save_dash_ref, 'v',
                                         backward=lambda v: v is not None)

                def _do_save():
                    name = save_name_ref['v'] or 'Untitled Chart'
                    state['chart_name'] = name
                    cfg = _build_config()
                    if mode_ref['v'] == 'Save (Overwrite)' and state['editing_id'] is not None:
                        update_custom_chart(
                            state['editing_id'], name,
                            state['chart_type'], state['data_source'], cfg,
                        )
                    else:
                        new_id = create_custom_chart(
                            user_id, name, state['chart_type'], state['data_source'], cfg,
                        )
                        state['editing_id'] = new_id

                def _save_only():
                    _do_save()
                    if save_dash_ref['v']:
                        chart_id = f"custom:{state['editing_id']}"
                        add_widget(int(save_dash_ref['v']), chart_id, col_span=2, row_span=1)
                    dlg.close()
                    ui.notify('Chart saved.', color='positive')

                ui.button('Save', on_click=_save_only)

        dlg.open()

    # ── Header row ────────────────────────────────────────────────────────────
    with ui.row().classes('w-full items-center justify-between mb-4 px-6 pt-6'):
        if state['editing_id']:
            _title = 'Edit Chart'
        elif state['chart_name']:
            _title = f'New Chart — {state["chart_name"]}'
        else:
            _title = 'New Chart'
        title_lbl = ui.label(_title).classes('text-2xl font-bold text-zinc-900')
        with ui.row().classes('items-center gap-2'):
            ui.button('Cancel', on_click=lambda: ui.navigate.to('/charts')) \
                .props('flat no-caps').classes('text-zinc-500')
            ui.button('Save', icon='save', on_click=_open_save_dialog).props('unelevated color=primary')

    # ── Three-panel splitter layout ───────────────────────────────────────────
    with ui.splitter(value=20).classes('w-full flex-1 px-6 pb-6').props('disable') as outer_split:

        # ── LEFT: column panel ────────────────────────────────────────────────
        with outer_split.before:
            with ui.column().classes('w-full pr-2 gap-3'):
                ui.label('Data Source').classes('text-xs font-semibold text-zinc-500 uppercase tracking-wide')

                source_select = ui.select(
                    get_available_sources(),
                    value=state['data_source'],
                    label='Source',
                ).classes('w-full')

                ui.separator()
                ui.label('Columns').classes('text-xs font-semibold text-zinc-500 uppercase tracking-wide')

                @ui.refreshable
                def column_list() -> None:
                    cols = _columns()
                    if not cols:
                        ui.label('No columns found.').classes('text-xs text-zinc-400')
                        return
                    with ui.element('div').classes('w-full'):
                        for col in cols:
                            ui.label(col).classes('text-xs text-zinc-700 py-1 border-b border-zinc-100 w-full')

                def _on_source_change(e) -> None:
                    state['data_source'] = e.value
                    # Reset column selections if they're not valid for new source
                    cols = _columns()
                    if state['x_column'] not in cols and cols:
                        state['x_column'] = cols[0]
                    if state['y_column'] not in cols and cols:
                        state['y_column'] = cols[0]
                    column_list.refresh()
                    settings_panel.refresh()
                    preview_area.refresh()

                source_select.on_value_change(_on_source_change)
                column_list()

        # ── RIGHT: tabs + preview ─────────────────────────────────────────────
        with outer_split.after:
            with ui.splitter(value=35).classes('w-full h-full').props('disable') as inner_split:

                # ── MIDDLE: settings panel ────────────────────────────────────
                with inner_split.before:
                    with ui.column().classes('w-full px-4 gap-3 pt-3').style('overflow-y:auto'):
                        ui.label('Chart Type').classes(
                            'text-xs font-semibold text-zinc-500 uppercase tracking-wide'
                        )
                        ui.select(
                            _CHART_TYPE_OPTIONS,
                            value=state['chart_type'],
                            label='Type',
                            on_change=lambda e: _on_chart_type(e.value),
                        ).classes('w-full')

                        ui.separator()

                        @ui.refreshable
                        def settings_panel() -> None:
                            cols = _columns()
                            col_opts = {c: c for c in cols}
                            col_opts_none = {'': 'None', **col_opts}

                            def _valid(val, opts):
                                """Return val if it's a valid option key, else first key or None."""
                                if val in opts:
                                    return val
                                return next(iter(opts), None)

                            if not col_opts:
                                ui.label('No columns available — upload data first.') \
                                    .classes('text-sm text-zinc-400 py-4')
                                return

                            with ui.column().classes('w-full gap-3'):
                                ui.select(
                                    col_opts,
                                    value=_valid(state.get('x_column'), col_opts),
                                    label='X Axis',
                                    on_change=lambda e: _update_state_refresh('x_column', e.value),
                                ).classes('w-full')

                                ui.select(
                                    col_opts,
                                    value=_valid(state.get('y_column'), col_opts),
                                    label='Y Axis',
                                    on_change=lambda e: _update_state('y_column', e.value),
                                ).classes('w-full')

                                ui.select(
                                    _AGG_OPTIONS,
                                    value=state.get('y_agg', 'sum'),
                                    label='Aggregation',
                                    on_change=lambda e: _update_state('y_agg', e.value),
                                ).classes('w-full')

                                ui.select(
                                    col_opts_none,
                                    value=_valid(state.get('series_column') or '', col_opts_none),
                                    label='Series Column (optional)',
                                    on_change=lambda e: _update_state(
                                        'series_column', e.value or None
                                    ),
                                ).classes('w-full')

                                if state.get('x_column') == 'transaction_date':
                                    ui.select(
                                        _TRUNC_OPTIONS,
                                        value=state.get('date_trunc', 'month'),
                                        label='Date Grouping',
                                        on_change=lambda e: _update_state('date_trunc', e.value),
                                    ).classes('w-full')

                                ui.select(
                                    _FORMAT_OPTIONS,
                                    value=state.get('label_format', 'dollar'),
                                    label='Label Format',
                                    on_change=lambda e: _update_state('label_format', e.value),
                                ).classes('w-full')

                                ui.input(
                                    'Chart Height',
                                    value=state.get('chart_height', '300px'),
                                    on_change=lambda e: _update_state('chart_height', e.value),
                                ).classes('w-full')

                                ui.switch(
                                    'Show Legend',
                                    value=state.get('show_legend', True),
                                    on_change=lambda e: _update_state('show_legend', e.value),
                                )

                                ui.select(
                                    {'top': 'Top', 'bottom': 'Bottom', 'left': 'Left', 'right': 'Right'},
                                    value=state.get('legend_position', 'top'),
                                    label='Legend Position',
                                    on_change=lambda e: _update_state('legend_position', e.value),
                                ).classes('w-full')

                                # ── Time Range ────────────────────────────────
                                ui.separator()
                                ui.label('Time Range').classes(
                                    'text-xs font-semibold text-zinc-500 uppercase tracking-wide'
                                )
                                ui.select(
                                    _TIME_MODE_OPTIONS,
                                    value=state.get('time_mode', 'all_time'),
                                    label='Date Filter',
                                    on_change=lambda e: _update_state_refresh('time_mode', e.value),
                                ).classes('w-full')
                                _tm = state.get('time_mode', 'all_time')
                                if _tm == 'trailing':
                                    ui.select(
                                        _TRAILING_OPTIONS,
                                        value=int(state.get('trailing_months', 12)),
                                        label='Lookback Period',
                                        on_change=lambda e: _update_state('trailing_months', e.value),
                                    ).classes('w-full')
                                elif _tm == 'year':
                                    ui.input(
                                        'Year (YYYY)',
                                        value=str(state.get('fixed_year') or ''),
                                        on_change=lambda e: _update_state(
                                            'fixed_year', int(e.value) if e.value.isdigit() else None
                                        ),
                                    ).classes('w-full')
                                elif _tm == 'date_range':
                                    ui.input(
                                        'From (YYYY-MM-DD)',
                                        value=state.get('date_from', ''),
                                        on_change=lambda e: _update_state('date_from', e.value),
                                    ).classes('w-full')
                                    ui.input(
                                        'To (YYYY-MM-DD)',
                                        value=state.get('date_to', ''),
                                        on_change=lambda e: _update_state('date_to', e.value),
                                    ).classes('w-full')

                                # ── Filters ───────────────────────────────────
                                def _add_filter() -> None:
                                    cols_now = _columns()
                                    state['filters'].append({
                                        'column': cols_now[0] if cols_now else '',
                                        'op':     '=',
                                        'value':  '',
                                    })
                                    filter_list.refresh()

                                def _update_filter(idx: int, key: str, val) -> None:
                                    if idx < len(state['filters']):
                                        state['filters'][idx][key] = val

                                def _remove_filter(idx: int) -> None:
                                    if idx < len(state['filters']):
                                        state['filters'].pop(idx)
                                    filter_list.refresh()

                                @ui.refreshable
                                def filter_list() -> None:
                                    if not state['filters']:
                                        ui.label('No filters.').classes('text-xs text-zinc-400')
                                        return
                                    for idx, f in enumerate(state['filters']):
                                        with ui.row().classes('w-full gap-1 items-center flex-wrap'):
                                            ui.select(
                                                col_opts,
                                                value=f.get('column', ''),
                                                label='Col',
                                                on_change=lambda e, i=idx: _update_filter(i, 'column', e.value),
                                            ).classes('flex-1 min-w-0')
                                            ui.select(
                                                {o: o for o in _OP_OPTIONS},
                                                value=f.get('op', '='),
                                                label='Op',
                                                on_change=lambda e, i=idx: _update_filter(i, 'op', e.value),
                                            ).style('width:80px')
                                            ui.input(
                                                'Value',
                                                value=f.get('value', ''),
                                                on_change=lambda e, i=idx: _update_filter(i, 'value', e.value),
                                            ).classes('flex-1 min-w-0')
                                            ui.button(
                                                icon='close',
                                                on_click=lambda _, i=idx: _remove_filter(i),
                                            ).props('flat round dense size=xs').classes('text-red-400')

                                ui.separator()
                                with ui.row().classes('w-full items-center justify-between'):
                                    ui.label('Filters').classes(
                                        'text-xs font-semibold text-zinc-500 uppercase tracking-wide'
                                    )
                                    ui.button(
                                        'Add Filter', icon='add',
                                        on_click=_add_filter,
                                    ).props('flat dense').classes('text-xs')

                                filter_list()

                                # ── Overlay Lines ──────────────────────────────
                                def _add_overlay() -> None:
                                    state.setdefault('overlay_series', []).append({
                                        '_type':       'query',
                                        'label':       'Line',
                                        'data_source': 'v_all_spend',
                                        'y_column':    'amount',
                                        'y_agg':       'sum',
                                    })
                                    overlay_list.refresh()

                                def _update_overlay(idx: int, key: str, val) -> None:
                                    ovs = state.get('overlay_series', [])
                                    if idx < len(ovs):
                                        ovs[idx][key] = val
                                        if key in ('data_source', '_type'):
                                            overlay_list.refresh()

                                def _remove_overlay(idx: int) -> None:
                                    ovs = state.get('overlay_series', [])
                                    if idx < len(ovs):
                                        ovs.pop(idx)
                                    overlay_list.refresh()

                                @ui.refreshable
                                def overlay_list() -> None:
                                    ovs = state.get('overlay_series', [])
                                    if not ovs:
                                        ui.label('No overlay lines.').classes('text-xs text-zinc-400')
                                        return
                                    for idx, ov in enumerate(ovs):
                                        ov_type = ov.get('_type', 'query')
                                        with ui.card().classes('w-full p-2 gap-2').style('background:#f9fafb'):
                                            with ui.row().classes('w-full items-center justify-between'):
                                                ui.label(f'Line {idx + 1}').classes('text-xs font-semibold text-zinc-500')
                                                ui.button(
                                                    icon='close',
                                                    on_click=lambda _, i=idx: _remove_overlay(i),
                                                ).props('flat round dense size=xs').classes('text-red-400')
                                            ui.input(
                                                'Label',
                                                value=ov.get('label', 'Line'),
                                                on_change=lambda e, i=idx: _update_overlay(i, 'label', e.value),
                                            ).props('outlined dense').classes('w-full')
                                            ui.select(
                                                _OVERLAY_TYPE_OPTIONS,
                                                value=ov_type,
                                                label='Type',
                                                on_change=lambda e, i=idx: _update_overlay(i, '_type', e.value),
                                            ).props('outlined dense').classes('w-full')
                                            if ov_type == 'computed':
                                                ui.select(
                                                    _COMPUTED_OVERLAY_OPTIONS,
                                                    value=ov.get('computed', 'rolling_surplus'),
                                                    label='Formula',
                                                    on_change=lambda e, i=idx: _update_overlay(i, 'computed', e.value),
                                                ).props('outlined dense').classes('w-full')
                                            else:
                                                ov_src = ov.get('data_source', 'v_all_spend')
                                                try:
                                                    ov_cols = get_source_columns(ov_src)
                                                except Exception:
                                                    ov_cols = []
                                                ov_col_opts = {c: c for c in ov_cols}
                                                ui.select(
                                                    get_available_sources(),
                                                    value=ov_src,
                                                    label='Source',
                                                    on_change=lambda e, i=idx: _update_overlay(i, 'data_source', e.value),
                                                ).props('outlined dense').classes('w-full')
                                                ui.select(
                                                    ov_col_opts,
                                                    value=ov.get('y_column', 'amount'),
                                                    label='Y Column',
                                                    on_change=lambda e, i=idx: _update_overlay(i, 'y_column', e.value),
                                                ).props('outlined dense').classes('w-full')
                                                ui.select(
                                                    _AGG_OPTIONS,
                                                    value=ov.get('y_agg', 'sum'),
                                                    label='Aggregation',
                                                    on_change=lambda e, i=idx: _update_overlay(i, 'y_agg', e.value),
                                                ).props('outlined dense').classes('w-full')

                                ui.separator()
                                with ui.row().classes('w-full items-center justify-between'):
                                    ui.label('Overlay Lines').classes(
                                        'text-xs font-semibold text-zinc-500 uppercase tracking-wide'
                                    )
                                    ui.button(
                                        'Add Line', icon='add',
                                        on_click=_add_overlay,
                                    ).props('flat dense').classes('text-xs')

                                overlay_list()

                        settings_panel()

                # ── RIGHT: preview panel ──────────────────────────────────────
                with inner_split.after:
                    with ui.column().classes('w-full pl-2 gap-3'):
                        ui.label('Preview').classes(
                            'text-xs font-semibold text-zinc-500 uppercase tracking-wide'
                        )

                        @ui.refreshable
                        def preview_area() -> None:
                            try:
                                cfg  = _build_config()
                                data = execute_chart_query(cfg)
                                render_custom_chart(cfg, data)
                            except Exception as ex:
                                ui.label(f'Query error: {ex}').classes(
                                    'text-sm text-red-500 p-4 bg-red-50 rounded-lg w-full'
                                )

                        preview_area()

                        ui.button(
                            '↻ Refresh',
                            on_click=preview_area.refresh,
                        ).props('flat dense').classes('text-xs self-start')

    # ── State update helpers (defined after refreshables exist) ───────────────
    def _update_state(key: str, val) -> None:
        state[key] = val

    def _update_state_refresh(key: str, val) -> None:
        state[key] = val
        settings_panel.refresh()

    def _on_chart_type(val: str) -> None:
        state['chart_type'] = val
        preview_area.refresh()

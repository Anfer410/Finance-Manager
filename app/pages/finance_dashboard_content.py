"""
finance_dashboard_content.py
"""

import data.finance_dashboard_data as data

from __future__ import annotations
from datetime import datetime
from nicegui import ui

from services.notifications import notify
from services.transaction_config import load_config, save_config
from services.view_manager import ViewManager
from data.db import get_conn_tuple, get_schema
_DB_CONN = get_conn_tuple()
_SCHEMA  = get_schema()


# ── Charts components ────────────────────────────────────────────────

from components.finance_charts import (
    kpi_card, spend_income_chart, per_bank_chart, employer_income_chart,
    category_donut, fixed_vs_variable_chart, category_trend_chart,
    weekly_transactions_chart, transactions_table,
)


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

        # ── Dialog header ──────────────────────────────────────────────────────
        with ui.row().classes('items-center justify-between px-6 py-4 border-b border-zinc-100'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('settings').classes('text-zinc-400 text-xl')
                ui.label('Transaction settings').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close) \
                .props('flat round dense').classes('text-zinc-400')

        # ── Scrollable body ────────────────────────────────────────────────────
        with ui.scroll_area().style('height: 60vh'):
            with ui.column().classes('w-full gap-4 px-6 py-5'):

                cfg = load_config()

                # Transfer exclusions
                ui.label('Transfer exclusion patterns') \
                    .classes('text-sm font-semibold text-gray-700')
                ui.label(
                    'Transactions whose description contains any of these strings are '
                    'excluded from spend and income totals (e.g. credit card payments, Zelle).'
                ).classes('text-xs text-muted')

                @ui.refreshable
                def render_transfer_chips() -> None:
                    _chip_list(cfg.transfer_patterns,
                               on_remove=lambda p: _remove_transfer(p))

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

                # Employer patterns
                ui.label('Employer / payroll patterns') \
                    .classes('text-sm font-semibold text-gray-700')
                ui.label(
                    'Incoming transactions matching these strings are counted as income '
                    '(e.g. your employer name, "DIRECT DEP", "PAYROLL").'
                ).classes('text-xs text-muted')

                @ui.refreshable
                def render_employer_chips() -> None:
                    _chip_list(cfg.employer_patterns,
                               on_remove=lambda p: _remove_employer(p))

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


        # ── Dialog footer ──────────────────────────────────────────────────────
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
    now   = datetime.now()
    years = data.get_years()
    state = {'year': years[0] if years else now.year, 'person': None, 'category': None}

    # ── Header ────────────────────────────────────────────────────────────────
    with ui.row().classes('w-full items-center justify-between mb-2'):
        with ui.column().classes('gap-0'):
            ui.label('Finance').classes('page-title')
            ui.label('Spend & income across all accounts.').classes('text-sm text-muted')

        with ui.row().classes('items-center gap-2'):
            year_sel = ui.select(
                options=years, value=state['year'], label='Year',
                on_change=lambda e: (state.update({'year': e.value}), dashboard.refresh(e.value), txn_table.refresh()),
            ).props('outlined dense').classes('w-28')


            ui.button('Refresh', icon='refresh', on_click=lambda: dashboard.refresh(state['year'])) \
                .props('flat no-caps').classes('button button-outline')

            ui.button(icon='settings', on_click=lambda: _open_settings_dialog(
                on_save_callback=lambda: (dashboard.refresh(state['year']), txn_table.refresh())
            )).props('flat round').classes('text-zinc-400').tooltip('Transaction settings')

    ui.element('div').classes('divider mb-4')

    # ── Refreshable — inside content() so each session is isolated.
    #    Reads year from state dict; caller must update state BEFORE calling refresh().
    @ui.refreshable
    def dashboard(y) -> None:

        # ── Active category filter chip ───────────────────────────────────────
        if state.get('category'):
            with ui.row().classes('items-center gap-2 mb-3'):
                ui.icon('filter_alt').classes('text-indigo-500').style('font-size:1rem')
                ui.label(f"Filtered: {state['category']}").classes('text-sm font-semibold text-indigo-700')
                ui.button(icon='close', on_click=lambda: _clear_category()) \
                    .props('flat round dense size=xs').classes('text-indigo-400') \
                    .tooltip('Clear category filter')

        with ui.row().classes('w-full gap-4 flex-wrap mb-4'):
            kpi_card('All Time',   'all_inclusive',  data.get_alltime_kpi())
            kpi_card(f'{y} Total', 'calendar_today', data.get_yearly_kpi(y))

        with ui.row().classes('w-full gap-4 flex-wrap mb-4'):
            with ui.element('div').classes('card flex-1').style('min-width:320px'):
                with ui.row().classes('items-center justify-between mb-3'):
                    ui.label('Monthly Spend vs Income').classes('section-title')
                    ui.label(str(y)).classes('text-xs text-muted')
                spend_income_chart(data.get_monthly_spend_series(y))

        person = state.get('person') or None

        with ui.row().classes('w-full gap-4 flex-wrap mb-4'):
            with ui.element('div').classes('card flex-1').style('min-width:280px'):
                with ui.row().classes('items-center justify-between mb-3'):
                    ui.label('Spend per Account').classes('section-title')
                    ui.label(str(y)).classes('text-xs text-muted')
                per_bank_chart(data.get_spend_per_bank_series(y))

            with ui.element('div').classes('card flex-1').style('min-width:280px'):
                with ui.row().classes('items-center justify-between mb-3'):
                    ui.label('Monthly Payroll Income').classes('section-title')
                    ui.label(str(y)).classes('text-xs text-muted')
                employer_income_chart(data.get_employer_income_series(y))

        # Category spend donut + fixed vs variable
        with ui.row().classes('w-full gap-4 flex-wrap mb-4'):
            with ui.element('div').classes('card flex-1').style('min-width:280px'):
                donut_state = {'inverted': False}

                @ui.refreshable
                def _donut_view() -> None:
                    category_donut(data.get_spend_by_category(y, person), inverted=donut_state['inverted'])

                def _toggle_invert() -> None:
                    donut_state['inverted'] = not donut_state['inverted']
                    _donut_view.refresh()

                with ui.row().classes('items-center justify-between mb-3'):
                    ui.label('Spend by Category').classes('section-title')
                    with ui.row().classes('items-center gap-2'):
                        ui.label(str(y)).classes('text-xs text-muted')
                        ui.button(icon='swap_vert', on_click=_toggle_invert) \
                            .props('flat round dense') \
                            .classes('text-gray-400') \
                            .tooltip('Invert: show % of total instead of amount')
                _donut_view()

            with ui.element('div').classes('card flex-1').style('min-width:280px'):
                with ui.row().classes('items-center justify-between mb-3'):
                    ui.label('Fixed vs Variable').classes('section-title')
                    ui.label(str(y)).classes('text-xs text-muted')
                fixed_vs_variable_chart(data.get_fixed_vs_variable(y, person))

        # Category trend stacked bar
        with ui.element('div').classes('card w-full mb-4'):
            with ui.row().classes('items-center justify-between mb-3'):
                ui.label('Spend Trend by Category').classes('section-title')
                ui.label(str(y)).classes('text-xs text-muted')
            def _on_cat_click(cat: str) -> None:
                state['category'] = None if cat == state.get('category') else cat
                dashboard.refresh(y)
                txn_table.refresh()

            category_trend_chart(
                data.get_category_trend(y, person),
                on_category_click=_on_cat_click,
                active_category=state.get('category'),
            )

        # Weekly transaction drill-down
        with ui.element('div').classes('card w-full mb-4'):
            with ui.row().classes('items-center justify-between mb-3'):
                ui.label('Weekly Transactions').classes('section-title')
                ui.label(str(y)).classes('text-xs text-muted')
            weekly_transactions_chart(
                data.get_weekly_transactions(y, person, state.get('category')),
                on_category_click=_on_cat_click,
                active_category=state.get('category'),
            )

    def _clear_category() -> None:
        state['category'] = None
        dashboard.refresh(state['year'])
        txn_table.refresh()

    dashboard(state['year'])

    # ── Transactions table — outside dashboard() so filters survive refreshes ──
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

        # ── Header ────────────────────────────────────────────────────────────
        with ui.row().classes('items-center justify-between mb-3'):
            ui.label('All Transactions').classes('section-title')
            adv_btn = ui.button('Advanced Search', icon='tune') \
                .props('flat dense no-caps size=sm').classes('text-gray-400 text-xs')
            adv_btn.on('click', lambda: _toggle_mode())

        # ── Filter area ───────────────────────────────────────────────────────
        @ui.refreshable
        def filter_area() -> None:
            opts = data.get_filter_options(state['year'])

            if filter_state['mode'] == 'simple':
                with ui.row().classes('items-center gap-2 flex-wrap pb-3 border-b border-gray-100 mb-3'):

                    ui.select(
                        options=['All categories'] + opts['categories'],
                        value=filter_state['category'] or 'All categories',
                        label='Category',
                        on_change=lambda e: (_fset('category', None if e.value == 'All categories' else e.value), txn_table.refresh()),
                    ).props('outlined dense').classes('w-44')

                    ui.select(
                        options=['Any type'] + opts['cost_types'],
                        value=filter_state['cost_type'] or 'Any type',
                        label='Type',
                        on_change=lambda e: (_fset('cost_type', None if e.value == 'Any type' else e.value), txn_table.refresh()),
                    ).props('outlined dense').classes('w-32')

                    ui.select(
                        options=['Any account'] + opts['banks'],
                        value=filter_state['bank'] or 'Any account',
                        label='Account',
                        on_change=lambda e: (_fset('bank', None if e.value == 'Any account' else e.value), txn_table.refresh()),
                    ).props('outlined dense').classes('w-44')

                    # ── Date range picker ──────────────────────────────────
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

                    # Active filter chips
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

                        ui.button('Clear all', on_click=lambda: (_clear_filters(), filter_area.refresh(), txn_table.refresh())) \
                            .props('flat dense no-caps size=sm').classes('text-gray-400 text-xs ml-1')

            else:
                # ── Advanced search ───────────────────────────────────────────
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

        # ── Table ─────────────────────────────────────────────────────────────
        @ui.refreshable
        def txn_table() -> None:
            if filter_state['mode'] == 'simple':
                transactions_table(data.gettransactions_table(
                    state['year'], state.get('person'),
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
                    state['year'], state.get('person'),
                    search=filter_state['search'],
                    category=state.get('category'),
                ))

        txn_table()
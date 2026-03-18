"""
components/dashboard_txn_table.py

Transaction filter bar + table for the finance dashboard.
Rendered inside a card; returns txn_table.refresh so the parent can trigger refreshes.
"""

from __future__ import annotations

from typing import Callable

from nicegui import ui

import data.finance_dashboard_data as _data


def render_txn_table(
    get_year: Callable[[], int],
    get_persons: Callable[[], list | None],
    get_category: Callable[[], str | None],
) -> Callable[[], None]:
    """Render the transaction filter + table section into the current NiceGUI context.

    Returns txn_table.refresh so the caller can trigger a data refresh.
    """
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
        opts = _data.get_filter_options(get_year())

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
        year     = get_year()
        persons  = get_persons()
        category = get_category()

        if filter_state['mode'] == 'simple':
            transactions_table(_data.gettransactions_table(
                year, persons,
                category=category,
                filters={
                    'cost_type': filter_state['cost_type'],
                    'bank':      filter_state['bank'],
                    'from_date': filter_state['from_date'],
                    'to_date':   filter_state['to_date'],
                    'category':  filter_state['category'],
                },
            ))
        else:
            transactions_table(_data.gettransactions_table(
                year, persons,
                search=filter_state['search'],
                category=category,
            ))

    txn_table()
    return txn_table.refresh

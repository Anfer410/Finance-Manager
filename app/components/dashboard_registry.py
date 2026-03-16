"""
components/dashboard_registry.py

Central registry of all available dashboard widgets.

Each ChartDef describes one widget that can appear on a dashboard:
  - id:              unique slug stored in app_dashboard_widgets.chart_id
  - title:           display name shown in the card header and widget picker
  - description:     shown in the widget picker ("add chart" panel)
  - icon:            Material icon name
  - category:        grouping in the widget picker
  - default_col_span / default_row_span: initial size on a 4-column grid
  - has_own_header:  True  → render() draws its own title/controls header
                     False → dashboard renders a standard "Title / year" header
  - supports_person_filter: whether a per-widget person override makes sense
  - render:          fn(year, persons, widget_config, shared_state) → None

Render contract
───────────────
  year          int           selected year
  persons       list[int]|None  user IDs to filter on; None = all people
  widget_config dict          the JSONB blob from app_dashboard_widgets.config
                              (col_span / row_span already consumed by the grid)
  shared_state  dict          mutable page-level state; relevant keys:
                                'category'          – active category filter (str|None)
                                '_refresh_dashboard' – callable → triggers dashboard refresh
                                '_refresh_txn_table' – callable → triggers txn-table refresh

Render functions render the INNER content of the card (no outer card wrapper).
The dashboard grid is responsible for:
  1. Creating the card container with the correct CSS col-span
  2. Calling render()

Adding a new chart
──────────────────
  1. Write a _render_<name> function here.
  2. Append a ChartDef to REGISTRY.
  Nothing else needs to change — the dashboard page iterates REGISTRY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ── Descriptor ────────────────────────────────────────────────────────────────

@dataclass
class ChartDef:
    id: str
    title: str
    description: str
    icon: str
    category: str                          # "overview" | "spend" | "income" | "trends"
    default_col_span: int = 4              # 1–4 grid columns
    default_row_span: int = 1              # 1–2 grid rows
    has_own_header: bool = False           # render() draws its own header?
    supports_person_filter: bool = True
    render: Callable = field(default=None, repr=False)


# ── Render wrappers ───────────────────────────────────────────────────────────
#
# Each function bridges the generic render signature to the concrete chart
# functions in finance_charts.py, which remain untouched.
#
# TODO (person filter): data functions currently ignore the `persons` arg.
# Once finance_dashboard_data.py is updated to accept persons: list[int]|None,
# replace the placeholder comments with the actual argument.

import data.finance_dashboard_data as _data

from components.finance_charts import (
    kpi_card                  as _kpi_card,
    spend_income_chart        as _spend_income,
    per_bank_chart            as _per_bank,
    employer_income_chart     as _employer_income,
    category_donut            as _cat_donut,
    fixed_vs_variable_chart   as _fixed_vs_var,
    category_trend_chart      as _cat_trend,
    weekly_transactions_chart as _weekly_txns,
)


# ── Overview ──────────────────────────────────────────────────────────────────

def _render_kpi_alltime(year, persons, cfg, state):
    # has_own_header=True — kpi_card renders its own title + icon row
    _kpi_card('All Time', 'all_inclusive', _data.get_alltime_kpi())
    # TODO persons: _data.get_alltime_kpi(persons)


def _render_kpi_yearly(year, persons, cfg, state):
    _kpi_card(f'{year} Total', 'calendar_today', _data.get_yearly_kpi(year))
    # TODO persons: _data.get_yearly_kpi(year, persons)


# ── Spend ─────────────────────────────────────────────────────────────────────

def _render_spend_income(year, persons, cfg, state):
    _spend_income(_data.get_monthly_spend_series(year))
    # TODO persons: _data.get_monthly_spend_series(year, persons)


def _render_per_bank(year, persons, cfg, state):
    _per_bank(_data.get_spend_per_bank_series(year))
    # TODO persons: _data.get_spend_per_bank_series(year, persons)


def _render_category_donut(year, persons, cfg, state):
    from nicegui import ui

    # Donut has a toggle control — it manages its own header
    donut_state = {'inverted': cfg.get('inverted', False)}

    @ui.refreshable
    def _view():
        _cat_donut(_data.get_spend_by_category(year, persons), inverted=donut_state['inverted'])
        # TODO persons: already threaded through

    def _toggle():
        donut_state['inverted'] = not donut_state['inverted']
        _view.refresh()

    with ui.row().classes('items-center justify-between mb-3'):
        from nicegui import ui as _ui
        _ui.label('Spend by Category').classes('section-title')
        with _ui.row().classes('items-center gap-2'):
            _ui.label(str(year)).classes('text-xs text-muted')
            _ui.button(icon='swap_vert', on_click=_toggle) \
                .props('flat round dense').classes('text-gray-400') \
                .tooltip('Toggle: % of total vs amount')
    _view()


def _render_fixed_vs_variable(year, persons, cfg, state):
    _fixed_vs_var(_data.get_fixed_vs_variable(year, persons))
    # TODO persons: already threaded through


# ── Income ────────────────────────────────────────────────────────────────────

def _render_employer_income(year, persons, cfg, state):
    _employer_income(_data.get_employer_income_series(year))
    # TODO persons: _data.get_employer_income_series(year, persons)


# ── Trends ────────────────────────────────────────────────────────────────────

def _render_category_trend(year, persons, cfg, state):
    def _on_cat_click(cat: str):
        state['category'] = None if cat == state.get('category') else cat
        if callable(state.get('_refresh_dashboard')):
            state['_refresh_dashboard']()
        if callable(state.get('_refresh_txn_table')):
            state['_refresh_txn_table']()

    _cat_trend(
        _data.get_category_trend(year, persons),
        # TODO persons: already threaded through
        on_category_click=_on_cat_click,
        active_category=state.get('category'),
    )


def _render_weekly_txns(year, persons, cfg, state):
    def _on_cat_click(cat: str):
        state['category'] = None if cat == state.get('category') else cat
        if callable(state.get('_refresh_dashboard')):
            state['_refresh_dashboard']()
        if callable(state.get('_refresh_txn_table')):
            state['_refresh_txn_table']()

    _weekly_txns(
        _data.get_weekly_transactions(year, persons, state.get('category')),
        # TODO persons: already threaded through
        on_category_click=_on_cat_click,
        active_category=state.get('category'),
    )


# ── Registry ──────────────────────────────────────────────────────────────────

REGISTRY: list[ChartDef] = [
    ChartDef(
        id='kpi_alltime',
        title='All-Time KPI',
        description='Total spend, income, and net across all uploaded data.',
        icon='all_inclusive',
        category='overview',
        default_col_span=2,
        default_row_span=1,
        has_own_header=True,
        render=_render_kpi_alltime,
    ),
    ChartDef(
        id='kpi_yearly',
        title='Yearly KPI',
        description='Spend, income, and net for the selected year.',
        icon='calendar_today',
        category='overview',
        default_col_span=2,
        default_row_span=1,
        has_own_header=True,
        render=_render_kpi_yearly,
    ),
    ChartDef(
        id='spend_income',
        title='Monthly Spend vs Income',
        description='Monthly bar/line chart comparing spend and income with optional budget line.',
        icon='bar_chart',
        category='spend',
        default_col_span=4,
        default_row_span=1,
        render=_render_spend_income,
    ),
    ChartDef(
        id='per_bank',
        title='Spend per Account',
        description='Monthly spend broken down by bank account.',
        icon='account_balance',
        category='spend',
        default_col_span=2,
        default_row_span=1,
        render=_render_per_bank,
    ),
    ChartDef(
        id='employer_income',
        title='Monthly Payroll Income',
        description='Payroll vs other income stacked by month.',
        icon='payments',
        category='income',
        default_col_span=2,
        default_row_span=1,
        render=_render_employer_income,
    ),
    ChartDef(
        id='category_donut',
        title='Spend by Category',
        description='Donut chart of total spend per category with % toggle.',
        icon='donut_large',
        category='spend',
        default_col_span=2,
        default_row_span=1,
        has_own_header=True,
        render=_render_category_donut,
    ),
    ChartDef(
        id='fixed_vs_variable',
        title='Fixed vs Variable',
        description='Monthly fixed versus variable spending.',
        icon='stacked_bar_chart',
        category='spend',
        default_col_span=2,
        default_row_span=1,
        render=_render_fixed_vs_variable,
    ),
    ChartDef(
        id='category_trend',
        title='Spend Trend by Category',
        description='Stacked monthly bars, click a category to filter the transaction table.',
        icon='trending_up',
        category='trends',
        default_col_span=4,
        default_row_span=1,
        render=_render_category_trend,
    ),
    ChartDef(
        id='weekly_transactions',
        title='Weekly Transactions',
        description='~52 weekly bars with per-transaction drill-down tooltip.',
        icon='view_week',
        category='trends',
        default_col_span=4,
        default_row_span=2,
        render=_render_weekly_txns,
    ),
]

# Fast lookup by id
REGISTRY_BY_ID: dict[str, ChartDef] = {c.id: c for c in REGISTRY}

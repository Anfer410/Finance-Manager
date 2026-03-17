"""
components/dashboard_registry.py

Central registry of all available dashboard widgets.

Each ChartDef describes one widget that can appear on a dashboard:
  - id:              unique slug stored in app_dashboard_widgets.chart_id
  - title:           display name shown in the card header and widget picker
  - description:     shown in the "Add widget" panel
  - icon:            Material icon name
  - category:        grouping in the widget picker ("overview"|"spend"|"income"|"trends")
  - default_col_span / default_row_span: initial size on the 4-column grid
  - has_own_header:  True  → render() draws its own title/controls header
                     False → the dashboard grid renders a standard "Title / Year" header
  - supports_person_filter: whether a per-widget person override makes sense
  - render:          fn(year, persons, widget_config, shared_state) → None

Render contract
───────────────
  year          int             selected year
  persons       list[int]|None  user IDs to filter on; None/[] = all people
  widget_config dict            JSONB blob from app_dashboard_widgets.config
                                  e.g. {"persons": [1, 2]}  ← per-widget person override
  shared_state  dict            page-level communication; keys:
                                  'category'            active category filter (str|None)
                                  '_on_category_click'  callable(cat: str) — chart clicked
                                  '_refresh_dashboard'  callable() — re-render grid
                                  '_refresh_txn_table'  callable() — re-render txn table

Render functions render the INNER CONTENT of the card only — no outer card wrapper.
The dashboard grid creates the card container and (for has_own_header=False charts)
the standard title/year header.

Adding a new chart
──────────────────
  1. Write a _render_<name> function here.
  2. Append a ChartDef to REGISTRY.
  Nothing else changes — the dashboard page iterates REGISTRY at render time.
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
    config_fields: list[dict] | None = None  # optional per-widget settings schema
    render: Callable = field(default=None, repr=False)

# config_fields schema — each entry is a dict with:
#   key:           str          — key stored in widget_config JSONB
#   label:         str          — shown in settings dialog
#   type:          str          — 'number' | 'select'
#   default:       any          — value used when not set in config
#   min/max:       int          — for type='number'
#   options:       list         — for type='select', the values
#   option_labels: list[str]    — for type='select', display labels (parallel to options)


# ── Lazy imports — only resolved at render time, not at module load ───────────

def _data():
    import data.finance_dashboard_data as d
    return d


# ── Overview ──────────────────────────────────────────────────────────────────

def _render_kpi_alltime(year, persons, cfg, shared_state):
    from nicegui import ui
    from styles.dashboards import C_SPEND, C_INCOME, C_NET_POS, C_NET_NEG

    kpi = _data().get_alltime_kpi(persons)
    net = kpi['net']
    net_color = C_NET_POS if net >= 0 else C_NET_NEG

    with ui.row().classes('items-center justify-between mb-3'):
        ui.label('All Time').classes('label-text')
        ui.icon('all_inclusive').style('font-size:1.2rem;color:var(--muted-fg)')
    with ui.row().classes('items-center justify-between'):
        ui.label('Spend').classes('text-xs text-muted')
        ui.label(f"${kpi['spend']:,.0f}").classes('text-sm font-semibold').style(f'color:{C_SPEND}')
    with ui.row().classes('items-center justify-between'):
        ui.label('Income').classes('text-xs text-muted')
        ui.label(f"${kpi['income']:,.0f}").classes('text-sm font-semibold').style(f'color:{C_INCOME}')
    ui.separator().classes('my-2')
    ui.label(f"{'▲' if net >= 0 else '▼'} ${abs(net):,.0f}") \
        .classes('text-xl font-bold').style(f'color:{net_color}')
    ui.label('net').classes('text-xs text-muted')


def _render_kpi_yearly(year, persons, cfg, shared_state):
    from nicegui import ui
    from styles.dashboards import C_SPEND, C_INCOME, C_NET_POS, C_NET_NEG

    kpi = _data().get_yearly_kpi(year, persons)
    net = kpi['net']
    net_color = C_NET_POS if net >= 0 else C_NET_NEG

    with ui.row().classes('items-center justify-between mb-3'):
        ui.label(f'{year} Total').classes('label-text')
        ui.icon('calendar_today').style('font-size:1.2rem;color:var(--muted-fg)')
    with ui.row().classes('items-center justify-between'):
        ui.label('Spend').classes('text-xs text-muted')
        ui.label(f"${kpi['spend']:,.0f}").classes('text-sm font-semibold').style(f'color:{C_SPEND}')
    with ui.row().classes('items-center justify-between'):
        ui.label('Income').classes('text-xs text-muted')
        ui.label(f"${kpi['income']:,.0f}").classes('text-sm font-semibold').style(f'color:{C_INCOME}')
    ui.separator().classes('my-2')
    ui.label(f"{'▲' if net >= 0 else '▼'} ${abs(net):,.0f}") \
        .classes('text-xl font-bold').style(f'color:{net_color}')
    ui.label('net').classes('text-xs text-muted')


# ── Spend ─────────────────────────────────────────────────────────────────────

def _render_spend_income(year, persons, cfg, shared_state):
    from components.finance_charts import spend_income_chart
    spend_income_chart(_data().get_monthly_spend_series(year, persons))


def _render_per_bank(year, persons, cfg, shared_state):
    from components.finance_charts import per_bank_chart
    per_bank_chart(_data().get_spend_per_bank_series(year, persons))


def _render_category_donut(year, persons, cfg, shared_state):
    from nicegui import ui
    from components.finance_charts import category_donut

    donut_state = {'inverted': cfg.get('inverted', False)}

    @ui.refreshable
    def _view():
        category_donut(_data().get_spend_by_category(year, persons), inverted=donut_state['inverted'])

    def _toggle():
        donut_state['inverted'] = not donut_state['inverted']
        _view.refresh()

    with ui.row().classes('items-center justify-between mb-3'):
        ui.label('Spend by Category').classes('label-text')
        with ui.row().classes('items-center gap-2'):
            ui.label(str(year)).classes('text-xs text-muted')
            ui.button(icon='swap_vert', on_click=_toggle) \
                .props('flat round dense').classes('text-gray-400') \
                .tooltip('Toggle: % of total vs amount')
    _view()


def _render_fixed_vs_variable(year, persons, cfg, shared_state):
    from components.finance_charts import fixed_vs_variable_chart
    fixed_vs_variable_chart(_data().get_fixed_vs_variable(year, persons))


# ── Income ────────────────────────────────────────────────────────────────────

def _render_employer_income(year, persons, cfg, shared_state):
    from components.finance_charts import employer_income_chart
    employer_income_chart(_data().get_employer_income_series(year, persons))


# ── Loans ─────────────────────────────────────────────────────────────────────

def _render_loan_kpi(year, persons, cfg, shared_state):
    from nicegui import ui
    from services.loan_service import load_loans, compute_stats, get_baseline

    loans    = load_loans()
    baseline = get_baseline(months=12)

    total_balance = sum(l.current_balance for l in loans)
    total_monthly = sum(l.monthly_payment  for l in loans)
    dti = baseline['dti']

    if baseline['avg_surplus'] < 0:
        dti_color, dti_label = '#ef4444', 'Overspending'
    elif dti < 28:
        dti_color, dti_label = '#22c55e', 'Healthy'
    elif dti < 36:
        dti_color, dti_label = '#f59e0b', 'Moderate'
    elif dti < 43:
        dti_color, dti_label = '#f97316', 'High'
    else:
        dti_color, dti_label = '#ef4444', 'Stretched'

    with ui.row().classes('items-center justify-between mb-3'):
        ui.label('Loan Overview').classes('label-text')
        ui.icon('account_balance_wallet').style('font-size:1.2rem;color:var(--muted-fg)')
    with ui.row().classes('items-center justify-between'):
        ui.label('Total balance').classes('text-xs text-muted')
        ui.label(f'${total_balance:,.0f}').classes('text-sm font-semibold text-zinc-800')
    with ui.row().classes('items-center justify-between'):
        ui.label('Monthly payments').classes('text-xs text-muted')
        ui.label(f'${total_monthly:,.0f}').classes('text-sm font-semibold text-zinc-800')
    ui.separator().classes('my-2')
    with ui.row().classes('items-center gap-2'):
        ui.label(f'DTI {dti:.1f}%').classes('text-xl font-bold text-zinc-800')
        with ui.element('span').classes('text-xs px-1.5 py-0.5 rounded-full font-medium') \
                .style(f'background:{dti_color}22;color:{dti_color}'):
            ui.label(dti_label)
    ui.label(f'{len(loans)} active loan{"s" if len(loans) != 1 else ""}') \
        .classes('text-xs text-muted')


def _render_loan_balances(year, persons, cfg, shared_state):
    from nicegui import ui
    from services.loan_service import load_loans, compute_stats

    _COLORS = ['#6366f1', '#f59e0b', '#10b981', '#f43f5e', '#3b82f6', '#8b5cf6']
    _TYPE_COLOR = {
        'mortgage': '#6366f1', 'auto': '#f59e0b', 'student': '#10b981',
        'personal': '#f43f5e', 'heloc': '#3b82f6', 'other': '#8b5cf6',
    }

    loans = load_loans()
    if not loans:
        with ui.column().classes('items-center justify-center h-full gap-2 py-8'):
            ui.icon('account_balance_wallet').classes('text-5xl text-zinc-200')
            ui.label('No loans added yet').classes('text-sm text-zinc-400')
        return

    series = []
    for i, loan in enumerate(loans):
        stats = compute_stats(loan)
        amort = stats.amortization
        if not amort:
            continue
        sampled = amort[::6]
        if amort[-1] not in sampled:
            sampled = sampled + [amort[-1]]
        color = _TYPE_COLOR.get(loan.loan_type, _COLORS[i % len(_COLORS)])
        series.append({
            'name': loan.name,
            'type': 'line',
            'data': [[str(r.date), r.balance] for r in sampled],
            'smooth': 0.4, 'symbol': 'none',
            'lineStyle': {'width': 2, 'color': color},
            'areaStyle': {'color': {
                'type': 'linear', 'x': 0, 'y': 0, 'x2': 0, 'y2': 1,
                'colorStops': [
                    {'offset': 0, 'color': color + '20'},
                    {'offset': 1, 'color': color + '05'},
                ],
            }},
            'itemStyle': {'color': color},
        })

    ui.echart({
        'tooltip': {
            'trigger': 'axis',
            'backgroundColor': '#fff', 'borderColor': '#e4e4e7',
            'textStyle': {'color': '#09090b', 'fontSize': 11},
            ':formatter': "params => params[0].name + '<br/>' + params.map(p => p.marker + ' ' + p.seriesName + ': $' + p.value[1].toLocaleString(undefined,{maximumFractionDigits:0})).join('<br/>')",
        },
        'legend': {'top': 0, 'textStyle': {'color': '#71717a', 'fontSize': 11}},
        'grid': {'left': '2%', 'right': '2%', 'top': '30px', 'bottom': '8%', 'containLabel': True},
        'xAxis': {
            'type': 'time', 'boundaryGap': False,
            'axisLine': {'lineStyle': {'color': '#e4e4e7'}},
            'axisTick': {'show': False},
            'axisLabel': {
                'color': '#71717a', 'fontSize': 9,
                ':formatter': "v => { let d = new Date(v); return d.toLocaleDateString('en-US',{month:'short',year:'2-digit'}); }",
            },
        },
        'yAxis': {
            'type': 'value',
            'splitLine': {'lineStyle': {'color': '#f4f4f5', 'type': 'dashed'}},
            'axisLabel': {':formatter': "v => '$' + (v/1000).toFixed(0) + 'k'",
                          'color': '#71717a', 'fontSize': 9},
        },
        'series': series,
    }).classes('w-full h-full')


def _render_loan_spend_24m(year, persons, cfg, shared_state):
    from components.finance_charts import spend_income_chart
    from data.finance_dashboard_data import get_year_over_year_monthly_spend_series
    year_back = int(cfg.get('year_back', 2))
    spend_income_chart(get_year_over_year_monthly_spend_series(year_back=year_back, persons=persons))


# ── Trends ────────────────────────────────────────────────────────────────────

def _render_category_trend(year, persons, cfg, shared_state):
    from components.finance_charts import category_trend_chart

    def _on_click(cat: str):
        cb = shared_state.get('_on_category_click')
        if callable(cb):
            cb(cat)

    category_trend_chart(
        _data().get_category_trend(year, persons),
        on_category_click=_on_click,
        active_category=shared_state.get('category'),
    )


def _render_weekly_txns(year, persons, cfg, shared_state):
    from components.finance_charts import weekly_transactions_chart

    def _on_click(cat: str):
        cb = shared_state.get('_on_category_click')
        if callable(cb):
            cb(cat)

    weekly_transactions_chart(
        _data().get_weekly_transactions(year, persons, shared_state.get('category')),
        on_category_click=_on_click,
        active_category=shared_state.get('category'),
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
        description='Stacked monthly bars — click a category to filter the transaction table.',
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
    ChartDef(
        id='loan_kpi',
        title='Loan Overview',
        description='Total outstanding balance, monthly payments, and debt-to-income ratio.',
        icon='account_balance_wallet',
        category='overview',
        default_col_span=2,
        default_row_span=1,
        has_own_header=True,
        supports_person_filter=False,
        render=_render_loan_kpi,
    ),
    ChartDef(
        id='loan_balances',
        title='Loan Balance Projections',
        description='Balance payoff curves for all loans on one chart.',
        icon='trending_down',
        category='overview',
        default_col_span=4,
        default_row_span=1,
        supports_person_filter=False,
        render=_render_loan_balances,
    ),
    ChartDef(
        id='loan_spend_24m',
        title='Spend vs Income — trailing months',
        description='Trailing spend and income across years. Configurable lookback period.',
        icon='show_chart',
        category='spend',
        default_col_span=4,
        default_row_span=1,
        config_fields=[{
            'key':           'year_back',
            'label':         'Lookback period',
            'type':          'select',
            'default':       2,
            'options':       [1, 2, 3, 4, 5],
            'option_labels': ['1 year (12 mo)', '2 years (24 mo)', '3 years (36 mo)',
                              '4 years (48 mo)', '5 years (60 mo)'],
        }],
        render=_render_loan_spend_24m,
    ),
]

# Fast lookup by id
REGISTRY_BY_ID: dict[str, ChartDef] = {c.id: c for c in REGISTRY}

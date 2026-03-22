"""
components/widgets/registry.py

All concrete widget definitions and the REGISTRY / REGISTRY_BY_ID exports.

Each widget is a singleton instance of a Widget subclass.  The dashboard
grid iterates REGISTRY at render time; nothing else needs to change when
a new widget is added.

Widget catalogue
────────────────
  Overview
    kpi_alltime      — All-Time KPI (spend / income / net)
    kpi_yearly       — Yearly KPI (spend / income / net)

  Spend
    spend_income     — Monthly Spend vs Income (mixed bar+line)
    per_bank         — Spend per Account (line chart)
    category_donut   — Spend by Category (donut)
    fixed_vs_variable— Fixed vs Variable Spend (bar)
    loan_spend_24m   — Trailing Spend vs Income (configurable lookback)
    person_spend     — Spend Comparison by Person (bar)

  Income
    employer_income  — Monthly Payroll Income (stacked bar)

  Trends
    category_trend   — Spend Trend by Category (stacked bar, clickable)
    weekly_transactions — Weekly Transactions (stacked bar, drill-down)

  Loans
    loan_kpi         — Loan Overview (balance / DTI)
    loan_balances    — Loan Balance Projections (area+line)
    loan_detail_kpi  — Single-Loan KPI (balance / rate / payoff / interest)
    loan_amortization— Single-Loan Amortization (principal vs interest)
"""

from __future__ import annotations

from components.widgets.base import (
    Widget, WidgetType, TimeMode, RenderContext, ConfigField,
)
from components.widgets.kpi        import KPIWidget
import services.auth as _auth
from components.widgets.echart     import (
    EChartWidget, BarChartWidget, LineChartWidget, MixedChartWidget,
    StackedBarChartWidget, DonutChartWidget, AreaLineChartWidget,
)


def _cur() -> str:
    """Currency prefix for labels — e.g. 'PLN ' or '' when showing all currencies."""
    return _auth.current_currency_prefix()


# ── Shared rendering helpers ───────────────────────────────────────────────────

def _render_spend_income_kpi(kpi: dict, title: str, icon: str) -> None:
    """Standard spend / income / net KPI card layout."""
    from nicegui import ui
    from styles.dashboards import C_SPEND, C_INCOME, C_NET_POS, C_NET_NEG

    net       = kpi['net']
    net_color = C_NET_POS if net >= 0 else C_NET_NEG

    with ui.row().classes('items-center justify-between mb-3'):
        ui.label(title).classes('label-text')
        ui.icon(icon).style('font-size:1.2rem;color:var(--muted-fg)')
    with ui.row().classes('items-center justify-between'):
        ui.label('Spend').classes('text-xs text-muted')
        ui.label(f"{_cur()}{kpi['spend']:,.0f}") \
          .classes('text-sm font-semibold').style(f'color:{C_SPEND}')
    with ui.row().classes('items-center justify-between'):
        ui.label('Income').classes('text-xs text-muted')
        ui.label(f"{_cur()}{kpi['income']:,.0f}") \
          .classes('text-sm font-semibold').style(f'color:{C_INCOME}')
    ui.separator().classes('my-2')
    ui.label(f"{'▲' if net >= 0 else '▼'} {_cur()}{abs(net):,.0f}") \
      .classes('text-xl font-bold').style(f'color:{net_color}')
    ui.label('net').classes('text-xs text-muted')


def _data():
    import data.finance_dashboard_data as d
    return d


# ══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

class _AllTimeKPI(KPIWidget):
    id          = 'kpi_alltime'
    title       = 'All-Time KPI'
    description = 'Total spend, income, and net across all uploaded data.'
    icon        = 'all_inclusive'
    category    = 'overview'
    supports_time_range = False  # always shows all-time data

    def render(self, ctx: RenderContext) -> None:
        kpi = _data().get_alltime_kpi(ctx.persons)
        _render_spend_income_kpi(kpi, 'All Time', 'all_inclusive')


class _YearlyKPI(KPIWidget):
    id          = 'kpi_yearly'
    title       = 'Yearly KPI'
    description = 'Spend, income, and net for the selected year.'
    icon        = 'calendar_today'
    category    = 'overview'

    def render(self, ctx: RenderContext) -> None:
        if ctx.time_mode == TimeMode.ALL_TIME:
            kpi = _data().get_alltime_kpi(ctx.persons)
            _render_spend_income_kpi(kpi, 'All Time', 'calendar_today')
        else:
            kpi = _data().get_yearly_kpi(ctx.year, ctx.persons)
            _render_spend_income_kpi(kpi, f'{ctx.year} Total', 'calendar_today')


# ══════════════════════════════════════════════════════════════════════════════
# SPEND
# ══════════════════════════════════════════════════════════════════════════════

class _SpendIncome(MixedChartWidget):
    id               = 'spend_income'
    title            = 'Monthly Spend vs Income'
    description      = 'Monthly bar/line chart comparing spend and income with rolling budget.'
    icon             = 'bar_chart'
    category         = 'spend'
    default_col_span = 4
    default_row_span = 2
    chart_height     = '300px'
    supports_time_range = True

    def render(self, ctx: RenderContext) -> None:
        from components.finance_charts import spend_income_chart

        if ctx.time_mode == TimeMode.TRAILING:
            months   = ctx.trailing_months or 24
            year_back = max(1, (months + 11) // 12)
            series = _data().get_year_over_year_monthly_spend_series(
                year_back=year_back, persons=ctx.persons
            )
        else:
            series = _data().get_monthly_spend_series(ctx.year, ctx.persons)

        spend_income_chart(series, legend_position=ctx.config.get('legend_position', 'top'))


class _PerBank(LineChartWidget):
    id               = 'per_bank'
    title            = 'Spend per Account'
    description      = 'Monthly spend broken down by bank account.'
    icon             = 'account_balance'
    category         = 'spend'
    default_col_span = 2
    default_row_span = 2
    supports_time_range = True

    def render(self, ctx: RenderContext) -> None:
        from components.finance_charts import per_bank_chart
        per_bank_chart(_data().get_spend_per_bank_series(ctx.year, ctx.persons),
                       legend_position=ctx.config.get('legend_position', 'top'))


class _CategoryDonut(DonutChartWidget):
    id               = 'category_donut'
    title            = 'Spend by Category'
    description      = 'Donut chart of total spend per category with % toggle.'
    icon             = 'donut_large'
    category         = 'spend'
    default_col_span = 2
    default_row_span = 2
    has_own_header   = True
    supports_time_range = True
    config_schema    = [
        ConfigField(
            key='inverted', label='Show percentages on slices',
            type='toggle', default=False,
        )
    ]

    def render(self, ctx: RenderContext) -> None:
        from nicegui import ui
        from components.finance_charts import category_donut

        donut_state = {'inverted': bool(ctx.config.get('inverted', False))}

        @ui.refreshable
        def _view():
            category_donut(
                _data().get_spend_by_category(ctx.year, ctx.persons),
                inverted=donut_state['inverted'],
            )

        def _toggle():
            donut_state['inverted'] = not donut_state['inverted']
            _view.refresh()

        # Own header
        with ui.row().classes('items-center justify-between mb-3'):
            ui.label('Spend by Category').classes('label-text')
            with ui.row().classes('items-center gap-2'):
                ui.label(str(ctx.year)).classes('text-xs text-muted')
                ui.button(icon='swap_vert', on_click=_toggle) \
                  .props('flat round dense').classes('text-gray-400') \
                  .tooltip('Toggle: % of total vs amount')
        _view()


class _FixedVsVariable(BarChartWidget):
    id               = 'fixed_vs_variable'
    title            = 'Fixed vs Variable'
    description      = 'Monthly fixed versus variable spending.'
    icon             = 'stacked_bar_chart'
    category         = 'spend'
    default_col_span = 2
    default_row_span = 2
    supports_time_range = True

    def render(self, ctx: RenderContext) -> None:
        from components.finance_charts import fixed_vs_variable_chart
        fixed_vs_variable_chart(_data().get_fixed_vs_variable(ctx.year, ctx.persons),
                                legend_position=ctx.config.get('legend_position', 'top'))


class _TrailingSpend(MixedChartWidget):
    id               = 'loan_spend_24m'
    title            = 'Trailing Spend vs Income'
    description      = 'Spend and income across trailing months. Configurable lookback.'
    icon             = 'show_chart'
    category         = 'spend'
    default_col_span = 4
    default_row_span = 2
    chart_height     = '300px'
    supports_time_range = False  # time is always trailing; controlled by year_back field
    config_schema    = [
        ConfigField(
            key='year_back', label='Lookback period',
            type='select', default=2,
            options=[1, 2, 3, 4, 5],
            option_labels=['1 year (12 mo)', '2 years (24 mo)', '3 years (36 mo)',
                           '4 years (48 mo)', '5 years (60 mo)'],
        )
    ]

    def render(self, ctx: RenderContext) -> None:
        from components.finance_charts import spend_income_chart
        year_back = int(ctx.config.get('year_back', 2))
        series = _data().get_year_over_year_monthly_spend_series(
            year_back=year_back, persons=ctx.persons
        )
        spend_income_chart(series, legend_position=ctx.config.get('legend_position', 'top'))


class _PersonSpend(BarChartWidget):
    id               = 'person_spend'
    title            = 'Spend by Person'
    description      = 'Side-by-side monthly spend comparison across people.'
    icon             = 'people'
    category         = 'spend'
    default_col_span = 4
    default_row_span = 2
    chart_height     = '300px'
    supports_time_range = True
    supports_person_filter = False  # always shows all persons for comparison

    def render(self, ctx: RenderContext) -> None:
        from nicegui import ui
        from styles.dashboards import TT_AXIS, BANK_COLORS, legend_pos, grid_for_legend
        lp = ctx.config.get('legend_position', 'top')

        if ctx.time_mode == TimeMode.TRAILING and ctx.date_from and ctx.date_to:
            rows = _data().get_spend_by_person_monthly(
                ctx.year, date_from=ctx.date_from, date_to=ctx.date_to
            )
        else:
            rows = _data().get_spend_by_person_monthly(ctx.year)
        if not rows or not rows.get('persons'):
            ui.label('No per-person spend data for this year.') \
              .classes('text-sm text-muted py-8 text-center w-full')
            return

        from nicegui import ui as _ui
        series = []
        for i, (person, values) in enumerate(rows['persons'].items()):
            color = BANK_COLORS[i % len(BANK_COLORS)]
            series.append({
                'name': person, 'type': 'bar', 'data': values,
                'barMaxWidth': 24,
                'itemStyle': {'color': color, 'borderRadius': [4, 4, 0, 0]},
                'label': {
                    'show': True, 'position': 'top',
                    'color': '#71717a', 'fontSize': 10,
                    ':formatter': f'v => v.value > 0 ? "{_cur()}" + (v.value/1000).toFixed(1) + "k" : ""',
                },
            })

        _ui.echart({
            'tooltip': {**TT_AXIS, 'axisPointer': {'type': 'shadow'}},
            'legend': legend_pos(lp, data=list(rows['persons'].keys())),
            'grid': grid_for_legend(lp),
            'xAxis': {
                'type': 'category', 'data': rows['months'],
                'axisLine': {'lineStyle': {'color': '#e4e4e7'}},
                'axisTick': {'show': False},
                'axisLabel': {'color': '#71717a', 'fontSize': 11},
            },
            'yAxis': {
                'type': 'value',
                'splitLine': {'lineStyle': {'color': '#f4f4f5', 'type': 'dashed'}},
                'axisLabel': {':formatter': f'v => "{_cur()}" + v.toLocaleString()',
                              'color': '#71717a', 'fontSize': 11},
            },
            'series': series,
        }).classes('w-full').style(f'height:{self.chart_height}')


# ══════════════════════════════════════════════════════════════════════════════
# INCOME
# ══════════════════════════════════════════════════════════════════════════════

class _EmployerIncome(StackedBarChartWidget):
    id               = 'employer_income'
    title            = 'Monthly Payroll Income'
    description      = 'Payroll vs other income stacked by month.'
    icon             = 'payments'
    category         = 'income'
    default_col_span = 2
    default_row_span = 2
    chart_height     = '280px'
    supports_time_range = True

    def render(self, ctx: RenderContext) -> None:
        from components.finance_charts import employer_income_chart
        employer_income_chart(_data().get_employer_income_series(ctx.year, ctx.persons),
                              legend_position=ctx.config.get('legend_position', 'top'))


# ══════════════════════════════════════════════════════════════════════════════
# TRENDS
# ══════════════════════════════════════════════════════════════════════════════

class _CategoryTrend(StackedBarChartWidget):
    id               = 'category_trend'
    title            = 'Spend Trend by Category'
    description      = 'Stacked monthly bars — click a category to filter the transaction table.'
    icon             = 'trending_up'
    category         = 'trends'
    default_col_span = 4
    default_row_span = 2
    chart_height     = '320px'
    supports_time_range = True

    def render(self, ctx: RenderContext) -> None:
        from components.finance_charts import category_trend_chart

        def _on_click(cat: str):
            cb = ctx.shared_state.get('_on_category_click')
            if callable(cb):
                cb(cat)

        category_trend_chart(
            _data().get_category_trend(ctx.year, ctx.persons),
            on_category_click=_on_click,
            active_category=ctx.shared_state.get('category'),
            legend_position=ctx.config.get('legend_position', 'top'),
        )


class _WeeklyTransactions(StackedBarChartWidget):
    id               = 'weekly_transactions'
    title            = 'Weekly Transactions'
    description      = '~52 weekly bars with per-transaction drill-down tooltip.'
    icon             = 'view_week'
    category         = 'trends'
    default_col_span = 4
    default_row_span = 3
    chart_height     = '460px'
    supports_time_range = True

    def render(self, ctx: RenderContext) -> None:
        from components.finance_charts import weekly_transactions_chart

        def _on_click(cat: str):
            cb = ctx.shared_state.get('_on_category_click')
            if callable(cb):
                cb(cat)

        weekly_transactions_chart(
            _data().get_weekly_transactions(
                ctx.year, ctx.persons, ctx.shared_state.get('category')
            ),
            on_category_click=_on_click,
            active_category=ctx.shared_state.get('category'),
        )


# ══════════════════════════════════════════════════════════════════════════════
# LOANS
# ══════════════════════════════════════════════════════════════════════════════

class _LoanKPI(KPIWidget):
    id                     = 'loan_kpi'
    title                  = 'Loan Overview'
    description            = 'Total outstanding balance, monthly payments, and debt-to-income ratio.'
    icon                   = 'account_balance_wallet'
    category               = 'loans'
    supports_person_filter = False
    supports_time_range    = False
    supports_loan_select   = False  # summary of all loans

    def render(self, ctx: RenderContext) -> None:
        from nicegui import ui
        from services.loan_service import load_loans, get_baseline

        loans    = load_loans(ctx.family_id)
        baseline = get_baseline(months=12, family_id=ctx.family_id)

        total_balance = sum(l.current_balance for l in loans)
        total_monthly = sum(l.monthly_payment  for l in loans)
        dti           = baseline['dti']

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
            ui.label(f'{_cur()}{total_balance:,.0f}').classes('text-sm font-semibold text-zinc-800')
        with ui.row().classes('items-center justify-between'):
            ui.label('Monthly payments').classes('text-xs text-muted')
            ui.label(f'{_cur()}{total_monthly:,.0f}').classes('text-sm font-semibold text-zinc-800')
        ui.separator().classes('my-2')
        with ui.row().classes('items-center gap-2'):
            ui.label(f'DTI {dti:.1f}%').classes('text-xl font-bold text-zinc-800')
            with ui.element('span').classes(
                'text-xs px-1.5 py-0.5 rounded-full font-medium'
            ).style(f'background:{dti_color}22;color:{dti_color}'):
                ui.label(dti_label)
        ui.label(f'{len(loans)} active loan{"s" if len(loans) != 1 else ""}') \
          .classes('text-xs text-muted')


class _LoanBalances(AreaLineChartWidget):
    id                     = 'loan_balances'
    title                  = 'Loan Balance Projections'
    description            = 'Balance payoff curves for all configured loans.'
    icon                   = 'trending_down'
    category               = 'loans'
    default_col_span       = 4
    default_row_span       = 2
    supports_person_filter = False
    supports_time_range    = False
    supports_loan_select   = True

    def render(self, ctx: RenderContext) -> None:
        from nicegui import ui
        from services.loan_service import load_loans, compute_stats

        _COLORS = ['#6366f1', '#f59e0b', '#10b981', '#f43f5e', '#3b82f6', '#8b5cf6']
        _TYPE_COLOR = {
            'mortgage': '#6366f1', 'auto': '#f59e0b', 'student': '#10b981',
            'personal': '#f43f5e', 'heloc': '#3b82f6', 'other': '#8b5cf6',
        }

        all_loans = load_loans(ctx.family_id)
        # Filter to specific loan if loan_id set
        if ctx.loan_id is not None:
            loans = [l for l in all_loans if l.id == ctx.loan_id]
        else:
            loans = all_loans

        if not loans:
            with ui.column().classes('items-center justify-center h-full gap-2 py-8'):
                ui.icon('account_balance_wallet').classes('text-5xl text-zinc-200')
                ui.label('No loans to display.').classes('text-sm text-zinc-400')
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
                ':formatter': "params => params[0].name + '<br/>' + params.map(p => "
                              f"p.marker + ' ' + p.seriesName + ': {_cur()}' + "
                              "p.value[1].toLocaleString(undefined,{maximumFractionDigits:0})"
                              ").join('<br/>')",
            },
            'legend': {'top': 0, 'textStyle': {'color': '#71717a', 'fontSize': 11}},
            'grid': {'left': '2%', 'right': '2%', 'top': '30px',
                     'bottom': '8%', 'containLabel': True},
            'xAxis': {
                'type': 'time', 'boundaryGap': False,
                'axisLine': {'lineStyle': {'color': '#e4e4e7'}},
                'axisTick': {'show': False},
                'axisLabel': {
                    'color': '#71717a', 'fontSize': 9,
                    ':formatter': "v => { let d = new Date(v); return d.toLocaleDateString"
                                  "('en-US',{month:'short',year:'2-digit'}); }",
                },
            },
            'yAxis': {
                'type': 'value',
                'splitLine': {'lineStyle': {'color': '#f4f4f5', 'type': 'dashed'}},
                'axisLabel': {':formatter': f"v => '{_cur()}' + (v/1000).toFixed(0) + 'k'",
                              'color': '#71717a', 'fontSize': 9},
            },
            'series': series,
        }).classes('w-full h-full')


class _LoanDetailKPI(KPIWidget):
    """KPI card for a single specific loan — selected via loan_select in settings."""
    id                     = 'loan_detail_kpi'
    title                  = 'Loan Detail'
    description            = 'Balance, rate, payoff date and total interest for one loan.'
    icon                   = 'receipt_long'
    category               = 'loans'
    supports_person_filter = False
    supports_time_range    = False
    supports_loan_select   = True

    def render(self, ctx: RenderContext) -> None:
        from nicegui import ui
        from services.loan_service import load_loans, compute_stats

        loans = load_loans(ctx.family_id)
        if not loans:
            ui.label('No loans configured.').classes('text-sm text-muted py-4')
            return

        # If no specific loan selected, use the first one
        loan = next((l for l in loans if l.id == ctx.loan_id), loans[0])
        stats = compute_stats(loan)

        equity_pct = loan.current_balance / loan.original_principal * 100 if loan.original_principal else 0

        with ui.row().classes('items-center justify-between mb-3'):
            ui.label(loan.name).classes('label-text')
            ui.icon('receipt_long').style('font-size:1.2rem;color:var(--muted-fg)')

        def _row(label: str, value: str) -> None:
            with ui.row().classes('items-center justify-between'):
                ui.label(label).classes('text-xs text-muted')
                ui.label(value).classes('text-sm font-semibold text-zinc-800')

        _row('Balance',          f'{_cur()}{loan.current_balance:,.0f}')
        _row('Monthly payment',  f'{_cur()}{loan.monthly_payment:,.0f}')
        _row('Interest rate',    f'{loan.interest_rate:.2f}%')
        _row('Payoff date',      stats.payoff_date.strftime('%b %Y'))

        ui.separator().classes('my-2')
        _row('Interest remaining', f'{_cur()}{stats.total_interest_remaining:,.0f}')
        _row('Equity',             f'{100 - equity_pct:.1f}%')


class _LoanAmortization(AreaLineChartWidget):
    """Amortization chart (principal vs interest) for a single loan."""
    id                     = 'loan_amortization'
    title                  = 'Loan Amortization'
    description            = 'Monthly principal vs interest breakdown for one loan.'
    icon                   = 'area_chart'
    category               = 'loans'
    default_col_span       = 4
    default_row_span       = 2
    chart_height           = '280px'
    supports_person_filter = False
    supports_time_range    = False
    supports_loan_select   = True

    def render(self, ctx: RenderContext) -> None:
        from nicegui import ui
        from services.loan_service import load_loans, compute_stats

        loans = load_loans(ctx.family_id)
        if not loans:
            ui.label('No loans configured.').classes('text-sm text-muted py-4')
            return

        loan  = next((l for l in loans if l.id == ctx.loan_id), loans[0])
        stats = compute_stats(loan)
        amort = stats.amortization

        if not amort:
            ui.label('No amortization data.').classes('text-sm text-muted py-4')
            return

        # Sample every 3 months for readability
        sampled = amort[::3]

        dates     = [str(r.date) for r in sampled]
        principal = [round(r.principal, 2) for r in sampled]
        interest  = [round(r.interest,  2) for r in sampled]

        ui.echart({
            'tooltip': {
                'trigger': 'axis', 'axisPointer': {'type': 'cross'},
                'backgroundColor': '#fff', 'borderColor': '#e4e4e7',
                'textStyle': {'color': '#09090b', 'fontSize': 11},
            },
            'legend': {'top': 0, 'data': ['Principal', 'Interest'],
                       'textStyle': {'color': '#71717a', 'fontSize': 11}},
            'grid': {'left': '2%', 'right': '2%', 'top': '30px',
                     'bottom': '8%', 'containLabel': True},
            'xAxis': {
                'type': 'category', 'data': dates, 'boundaryGap': False,
                'axisLine': {'lineStyle': {'color': '#e4e4e7'}},
                'axisTick': {'show': False},
                'axisLabel': {'color': '#71717a', 'fontSize': 9,
                              ':formatter': "v => { let d = new Date(v); "
                                            "return d.toLocaleDateString('en-US',"
                                            "{month:'short',year:'2-digit'}); }"},
            },
            'yAxis': {
                'type': 'value',
                'splitLine': {'lineStyle': {'color': '#f4f4f5', 'type': 'dashed'}},
                'axisLabel': {':formatter': f"v => '{_cur()}' + v.toLocaleString()",
                              'color': '#71717a', 'fontSize': 9},
            },
            'series': [
                {
                    'name': 'Principal', 'type': 'line', 'data': principal,
                    'smooth': 0.3, 'symbol': 'none',
                    'lineStyle': {'width': 2, 'color': '#6366f1'},
                    'areaStyle': {'color': '#6366f120'},
                    'itemStyle': {'color': '#6366f1'},
                },
                {
                    'name': 'Interest', 'type': 'line', 'data': interest,
                    'smooth': 0.3, 'symbol': 'none',
                    'lineStyle': {'width': 2, 'color': '#f43f5e'},
                    'areaStyle': {'color': '#f43f5e20'},
                    'itemStyle': {'color': '#f43f5e'},
                },
            ],
        }).classes('w-full').style(f'height:{self.chart_height}')


class _FinancialBaseline(KPIWidget):
    """
    Financial baseline widget — mirrors the section at the top of the loans page.

    Shows trailing-average income / spend / surplus / debt payments and a
    colour-coded DTI gauge with 28 / 36 / 43 % threshold markers.
    """
    id                     = 'financial_baseline'
    title                  = 'Financial Baseline'
    description            = 'Trailing-average income, spend, surplus, DTI gauge.'
    icon                   = 'bar_chart'
    category               = 'loans'
    default_col_span       = 4
    default_row_span       = 1
    has_own_header         = True
    supports_person_filter = False
    supports_time_range    = False
    config_schema          = [
        ConfigField(
            key='months', label='Lookback period',
            type='select', default=18,
            options=[6, 12, 18, 24, 36],
            option_labels=['6 months', '12 months', '18 months', '24 months', '36 months'],
        )
    ]

    def render(self, ctx: RenderContext) -> None:
        from nicegui import ui
        from services.loan_service import get_baseline

        months   = int(ctx.config.get('months', 18))
        baseline = get_baseline(months=months, family_id=ctx.family_id)

        dti      = baseline['dti']
        headroom = baseline['headroom']

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

        # ── Own header ────────────────────────────────────────────────────────
        with ui.row().classes('items-center justify-between mb-3'):
            with ui.row().classes('items-center gap-2'):
                ui.label('Financial Baseline').classes('label-text')
                ui.label(f'trailing {months}mo') \
                  .classes('text-xs text-zinc-300 ml-1')
            ui.icon('bar_chart').style('font-size:1.2rem;color:var(--muted-fg)')

        # ── KPI row ───────────────────────────────────────────────────────────
        with ui.row().classes('gap-0 flex-wrap mb-3'):
            def _stat(label: str, value: str, sub: str = '') -> None:
                with ui.column().classes('gap-0 pr-5 min-w-28'):
                    ui.label(label).classes('text-xs text-zinc-400')
                    ui.label(value).classes('text-sm font-semibold text-zinc-800')
                    if sub:
                        ui.label(sub).classes('text-xs text-zinc-300')

            def _sep() -> None:
                ui.element('div').classes('w-px bg-zinc-100 self-stretch mr-5')

            _stat('Avg monthly income',  f"{_cur()}{baseline['avg_income']:,.0f}",  'per month')
            _sep()
            _stat('Avg monthly spend',   f"{_cur()}{baseline['avg_spend']:,.0f}",   'per month')
            _sep()
            _stat('Avg surplus',         f"{_cur()}{baseline['avg_surplus']:,.0f}", 'after expenses')
            _sep()
            _stat('Total debt payments', f"{_cur()}{baseline['monthly_debt']:,.0f}", 'per month')
            _sep()

            # DTI with badge
            with ui.column().classes('gap-0 pr-5 min-w-28'):
                ui.label('Debt-to-income').classes('text-xs text-zinc-400')
                with ui.row().classes('items-center gap-2'):
                    ui.label(f'{dti:.1f}%').classes('text-sm font-semibold text-zinc-800')
                    with ui.element('span') \
                            .classes('text-xs px-1.5 py-0.5 rounded-full font-medium') \
                            .style(f'background:{dti_color}22;color:{dti_color}'):
                        ui.label(dti_label)
                ui.label(f'{_cur()}{headroom:,.0f} headroom').classes('text-xs text-zinc-300')

        # ── DTI gauge bar ─────────────────────────────────────────────────────
        with ui.column().classes('gap-1 w-full'):
            with ui.row().classes('items-center justify-between w-full'):
                ui.label('DTI ratio').classes('text-xs text-zinc-400')
                ui.label('28% / 36% / 43% thresholds').classes('text-xs text-zinc-300')
            with ui.element('div').classes('relative w-full bg-zinc-100 rounded-full h-2'):
                for pct in (28, 36, 43):
                    ui.element('div') \
                      .classes('absolute top-0 h-2 w-px bg-zinc-300') \
                      .style(f'left:{min(pct, 100)}%')
                fill = min(dti, 100)
                ui.element('div') \
                  .classes('h-2 rounded-full') \
                  .style(
                      f'width:{fill:.1f}%;'
                      f'background-color:{dti_color};'
                      'transition:width 0.5s'
                  )


# ══════════════════════════════════════════════════════════════════════════════
# REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

REGISTRY: list[Widget] = [
    # Overview
    _AllTimeKPI(),
    _YearlyKPI(),
    # Spend
    _SpendIncome(),
    _PerBank(),
    _CategoryDonut(),
    _FixedVsVariable(),
    _TrailingSpend(),
    _PersonSpend(),
    # Income
    _EmployerIncome(),
    # Trends
    _CategoryTrend(),
    _WeeklyTransactions(),
    # Loans
    _FinancialBaseline(),
    _LoanKPI(),
    _LoanBalances(),
    _LoanDetailKPI(),
    _LoanAmortization(),
]

REGISTRY_BY_ID: dict[str, Widget] = {w.id: w for w in REGISTRY}

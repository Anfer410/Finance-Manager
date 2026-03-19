"""
pages/loan_planning_content.py

Loan Planning page.
  - Financial baseline (trailing 12-month averages + DTI)
  - New loan calculator with affordability verdict and DTI impact
  - Extra payment scenario: select an existing loan and see payoff acceleration
"""

from __future__ import annotations

from nicegui import ui

from services.loan_service import (
    load_loans, compute_stats, payoff_with_extra,
    calculate_loan, get_baseline,
)


# ── Page entry point ──────────────────────────────────────────────────────────

def content() -> None:
    import services.auth as auth
    if not auth.is_instance_admin():
        ui.navigate.to("/")
        return

    with ui.column().classes("w-full px-4 py-6 gap-6"):
        with ui.row().classes("items-center gap-3 mb-2"):
            ui.icon("calculate").classes("text-zinc-400 text-2xl")
            ui.label("Loan Planning").classes("text-2xl font-bold text-zinc-800")

        _baseline_section()
        _calculator_section()
        _extra_payment_section()


# ── Financial baseline ────────────────────────────────────────────────────────

def _baseline_section(months: int = 18) -> None:
    baseline = get_baseline(months=months)

    dti      = baseline["dti"]
    headroom = baseline["headroom"]

    # Color-code DTI — overspending takes priority over DTI ratio
    if baseline["avg_surplus"] < 0:
        dti_color = "#ef4444"
        dti_label = "Overspending"
    elif dti < 28:
        dti_color = "#22c55e"
        dti_label = "Healthy"
    elif dti < 36:
        dti_color = "#f59e0b"
        dti_label = "Moderate"
    elif dti < 43:
        dti_color = "#f97316"
        dti_label = "High"
    else:
        dti_color = "#ef4444"
        dti_label = "Stretched"

    with ui.card().classes("w-full rounded-2xl shadow-none border border-zinc-100 p-0 gap-0"):
        with ui.row().classes("items-center gap-3 px-6 py-4 border-b border-zinc-100"):
            ui.icon("bar_chart").classes("text-zinc-400 text-xl")
            ui.label("Financial baseline").classes("text-base font-semibold text-zinc-700")
            ui.label(f"trailing {months} months").classes("text-xs text-zinc-300 ml-1")

        with ui.row().classes("gap-0 px-6 py-5 flex-wrap"):
            _kpi("Avg monthly income",  f"${baseline['avg_income']:,.0f}",  "per month")
            _kpi_sep()
            _kpi("Avg monthly spend",   f"${baseline['avg_spend']:,.0f}",   "per month")
            _kpi_sep()
            _kpi("Avg surplus",         f"${baseline['avg_surplus']:,.0f}", "after expenses")
            _kpi_sep()
            _kpi("Total debt payments", f"${baseline['monthly_debt']:,.0f}", "per month")
            _kpi_sep()

            # DTI with colored badge
            with ui.column().classes("gap-0 pr-6 min-w-32"):
                ui.label("Debt-to-income").classes("text-xs text-zinc-400")
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{dti:.1f}%").classes("text-sm font-semibold text-zinc-800")
                    with ui.element("span") \
                            .classes("text-xs px-1.5 py-0.5 rounded-full font-medium") \
                            .style(f"background:{dti_color}22;color:{dti_color}"):
                        ui.label(dti_label)
                ui.label(f"${headroom:,.0f} headroom").classes("text-xs text-zinc-300")

        # DTI bar
        with ui.column().classes("px-6 pb-5 gap-1 w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label("DTI ratio").classes("text-xs text-zinc-400")
                ui.label("28% / 36% / 43% thresholds").classes("text-xs text-zinc-300")
            with ui.element("div").classes("relative w-full bg-zinc-100 rounded-full h-2"):
                # threshold markers
                for pct, label in ((28, "28%"), (36, "36%"), (43, "43%")):
                    ui.element("div") \
                        .classes("absolute top-0 h-2 w-px bg-zinc-300") \
                        .style(f"left:{min(pct,100)}%")
                fill = min(dti, 100)
                ui.element("div") \
                    .classes("h-2 rounded-full") \
                    .style(f"width:{fill:.1f}%;background-color:{dti_color};transition:width 0.5s")


# ── New loan calculator ───────────────────────────────────────────────────────

def _calculator_section() -> None:
    result_state: dict = {}

    with ui.card().classes("w-full rounded-2xl shadow-none border border-zinc-100 p-0 gap-0"):
        with ui.row().classes("items-center gap-3 px-6 py-4 border-b border-zinc-100"):
            ui.icon("request_quote").classes("text-zinc-400 text-xl")
            ui.label("New loan calculator").classes("text-base font-semibold text-zinc-700")

        with ui.column().classes("px-6 py-5 gap-4 w-full"):

            with ui.row().classes("gap-4 w-full flex-wrap"):
                amount_in = ui.number("Loan amount ($)", value=300_000, min=0, format="%.0f") \
                    .props("outlined dense").classes("flex-1 min-w-36")
                rate_in   = ui.number("Interest rate (%)", value=6.5, min=0, max=30, format="%.3f") \
                    .props("outlined dense").classes("flex-1 min-w-36")
                term_in   = ui.number("Term (months)", value=360, min=1, max=600, format="%.0f") \
                    .props("outlined dense").classes("flex-1 min-w-36")

            @ui.refreshable
            def calc_result() -> None:
                if not result_state:
                    ui.label("Fill in the fields above and click Calculate.") \
                        .classes("text-sm text-zinc-400")
                    return

                r         = result_state
                baseline  = r["baseline"]
                calc      = r["calc"]
                new_dti   = r["new_dti"]
                old_dti   = baseline["dti"]
                headroom  = baseline["headroom"]
                affordable = headroom >= calc["monthly_payment"]

                if new_dti < 28:
                    verdict_color, verdict = "#22c55e", "Likely affordable"
                    verdict_icon = "check_circle"
                elif new_dti < 36:
                    verdict_color, verdict = "#f59e0b", "Manageable — review budget"
                    verdict_icon = "warning"
                elif new_dti < 43:
                    verdict_color, verdict = "#f97316", "Stretching — proceed carefully"
                    verdict_icon = "error_outline"
                else:
                    verdict_color, verdict = "#ef4444", "High risk — exceeds 43% DTI"
                    verdict_icon = "cancel"

                with ui.row().classes("gap-6 flex-wrap items-start w-full"):
                    # Metrics block
                    with ui.column().classes("gap-3 flex-1 min-w-48"):
                        with ui.row().classes("gap-4 flex-wrap"):
                            _kpi("Monthly payment",    f"${calc['monthly_payment']:,.2f}", "per month")
                            _kpi("Total interest",     f"${calc['total_interest']:,.0f}",  "over life of loan")
                            _kpi("Total cost",         f"${calc['total_cost']:,.0f}",       "principal + interest")
                            _kpi("Payoff date",        calc["payoff_date"].strftime("%b %Y"), "")

                        # DTI impact row
                        with ui.row().classes("gap-4 flex-wrap mt-1"):
                            _kpi("Current DTI",  f"{old_dti:.1f}%",  "before this loan")
                            _kpi("New DTI",      f"{new_dti:.1f}%",  "after this loan")
                            _kpi("Remaining headroom",
                                 f"${headroom - calc['monthly_payment']:,.0f}",
                                 "monthly surplus after loan")

                    # Verdict badge
                    with ui.column().classes("items-center justify-center gap-2 min-w-44 py-4"):
                        ui.icon(verdict_icon).style(f"color:{verdict_color};font-size:2.5rem")
                        ui.label(verdict) \
                            .classes("text-sm font-semibold text-center") \
                            .style(f"color:{verdict_color}")
                        if not affordable:
                            ui.label("Payment exceeds monthly surplus") \
                                .classes("text-xs text-zinc-400 text-center")

            calc_result()

            def _calculate():
                try:
                    amount = float(amount_in.value or 0)
                    rate   = float(rate_in.value   or 0)
                    term   = int(term_in.value      or 360)
                except (TypeError, ValueError):
                    return

                baseline = get_baseline(months=12)
                calc     = calculate_loan(amount, rate, term)
                new_monthly_debt = baseline["monthly_debt"] + calc["monthly_payment"]
                new_dti = round(new_monthly_debt / baseline["avg_income"] * 100, 1) \
                    if baseline["avg_income"] > 0 else 0.0

                result_state.update(baseline=baseline, calc=calc, new_dti=new_dti)
                calc_result.refresh()

            ui.button("Calculate", icon="calculate", on_click=_calculate) \
                .props("unelevated no-caps") \
                .classes("bg-zinc-800 text-white rounded-lg px-4 self-start")


# ── Extra payment scenario ────────────────────────────────────────────────────

def _extra_payment_section() -> None:
    loans = load_loans()
    scenario_state: dict = {}

    with ui.card().classes("w-full rounded-2xl shadow-none border border-zinc-100 p-0 gap-0"):
        with ui.row().classes("items-center gap-3 px-6 py-4 border-b border-zinc-100"):
            ui.icon("trending_up").classes("text-zinc-400 text-xl")
            ui.label("Extra payment scenario").classes("text-base font-semibold text-zinc-700")

        with ui.column().classes("px-6 py-5 gap-4 w-full"):
            if not loans:
                with ui.row().classes("items-center gap-3 py-4"):
                    ui.icon("info_outline").classes("text-zinc-300 text-xl")
                    ui.label("Add loans on the Loans & Mortgages page first.") \
                        .classes("text-sm text-zinc-400")
                return

            loan_options = {loan.name: loan for loan in loans}

            with ui.row().classes("gap-4 w-full flex-wrap items-end"):
                loan_select = ui.select(
                    label="Select loan",
                    options=list(loan_options.keys()),
                    value=loans[0].name,
                ).props("outlined dense").classes("flex-1 min-w-48")
                extra_in = ui.number("Extra monthly payment ($)", value=200, min=0, format="%.0f") \
                    .props("outlined dense").classes("flex-1 min-w-36")

            @ui.refreshable
            def scenario_result() -> None:
                if not scenario_state:
                    ui.label("Select a loan and extra payment amount, then click Run scenario.") \
                        .classes("text-sm text-zinc-400")
                    return

                s          = scenario_state
                loan       = s["loan"]
                stats      = s["stats"]
                new_payoff = s["new_payoff"]
                saved_int  = s["saved_int"]
                mo_saved   = s["mo_saved"]
                extra      = s["extra"]

                yr_saved = mo_saved // 12
                mo_rem   = mo_saved % 12

                with ui.row().classes("gap-6 flex-wrap items-start w-full"):
                    # Before column
                    with ui.column().classes("gap-3 flex-1 min-w-44"):
                        ui.label("Without extra payment") \
                            .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1")
                        _kpi("Monthly payment",   f"${loan.monthly_payment:,.0f}", "per month")
                        _kpi("Payoff date",        stats.payoff_date.strftime("%b %Y"),
                             f"{stats.months_remaining} months remaining")
                        _kpi("Interest remaining", f"${stats.total_interest_remaining:,.0f}", "")

                    ui.element("div").classes("self-stretch w-px bg-zinc-100")

                    # After column
                    with ui.column().classes("gap-3 flex-1 min-w-44"):
                        ui.label(f"With +${extra:,.0f}/mo extra") \
                            .classes("text-xs font-semibold text-indigo-500 uppercase tracking-wide mb-1")
                        _kpi("Monthly payment",
                             f"${loan.monthly_payment + extra:,.0f}",
                             f"+${extra:,.0f} extra")
                        _kpi("New payoff date",    new_payoff.strftime("%b %Y"),
                             f"{stats.months_remaining - mo_saved} months remaining")
                        _kpi("Interest remaining",
                             f"${stats.total_interest_remaining - saved_int:,.0f}",
                             f"saves ${saved_int:,.0f}")

                    ui.element("div").classes("self-stretch w-px bg-zinc-100")

                    # Savings highlight
                    with ui.column().classes("items-center justify-center gap-1 min-w-44 py-4"):
                        ui.icon("savings").classes("text-indigo-400 text-3xl mb-1")
                        ui.label("You save").classes("text-xs text-zinc-400")
                        ui.label(f"${saved_int:,.0f}") \
                            .classes("text-2xl font-bold text-indigo-600")
                        ui.label("in interest").classes("text-xs text-zinc-400")
                        if mo_saved > 0:
                            time_saved_str = (
                                f"{yr_saved}yr {mo_rem}mo" if yr_saved else f"{mo_rem}mo"
                            )
                            ui.label(f"Pay off {time_saved_str} sooner") \
                                .classes("text-xs font-semibold text-green-600 mt-1")

                # Comparison chart
                _payoff_comparison_chart(loan, extra, stats, new_payoff)

            scenario_result()

            def _run():
                loan_name = loan_select.value
                extra     = float(extra_in.value or 0)
                if loan_name not in loan_options:
                    return
                loan  = loan_options[loan_name]
                stats = compute_stats(loan)
                new_payoff, saved_int, mo_saved = payoff_with_extra(loan, extra)
                scenario_state.update(
                    loan=loan, stats=stats, extra=extra,
                    new_payoff=new_payoff, saved_int=saved_int, mo_saved=mo_saved,
                )
                scenario_result.refresh()

            ui.button("Run scenario", icon="play_arrow", on_click=_run) \
                .props("unelevated no-caps") \
                .classes("bg-indigo-600 text-white rounded-lg px-4 self-start")


def _payoff_comparison_chart(loan, extra: float, stats, new_payoff) -> None:
    from services.loan_service import compute_amortization
    from dataclasses import replace as dc_replace

    base_amort = stats.amortization
    new_amort  = compute_amortization(dc_replace(loan, monthly_payment=loan.monthly_payment + extra))

    if not base_amort or not new_amort:
        return

    # Sample every 6 months
    def _sample(amort):
        s = amort[::6]
        if amort[-1] not in s:
            s = s + [amort[-1]]
        return s

    base_s = _sample(base_amort)
    new_s  = _sample(new_amort)

    base_dates = [str(r.date) for r in base_s]
    base_bals  = [r.balance  for r in base_s]
    new_dates  = [str(r.date) for r in new_s]
    new_bals   = [r.balance  for r in new_s]

    ui.label("Balance comparison").classes("text-xs font-medium text-zinc-400 mt-4 mb-1")
    ui.echart({
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "#fff", "borderColor": "#e4e4e7",
            "textStyle": {"color": "#09090b", "fontSize": 11},
        },
        "legend": {
            "data": ["Normal", f"+${extra:,.0f}/mo"],
            "top": 0, "textStyle": {"color": "#71717a", "fontSize": 11},
        },
        "grid": {"left": "2%", "right": "2%", "top": "30px", "bottom": "8%", "containLabel": True},
        "xAxis": [
            {
                "id": "base_x",
                "type": "category", "data": base_dates, "boundaryGap": False,
                "axisLine": {"lineStyle": {"color": "#e4e4e7"}},
                "axisTick": {"show": False},
                "axisLabel": {
                    "color": "#71717a", "fontSize": 9,
                    ":formatter": "v => { let d = new Date(v); return d.toLocaleDateString('en-US',{month:'short',year:'2-digit'}); }",
                },
            },
            {
                "id": "new_x",
                "type": "category", "data": new_dates, "boundaryGap": False,
                "show": False,
            },
        ],
        "yAxis": {
            "type": "value",
            "splitLine": {"lineStyle": {"color": "#f4f4f5", "type": "dashed"}},
            "axisLabel": {":formatter": "v => '$' + (v/1000).toFixed(0) + 'k'",
                          "color": "#71717a", "fontSize": 9},
        },
        "series": [
            {
                "name": "Normal", "type": "line",
                "xAxisIndex": 0,
                "data": base_bals, "smooth": 0.3, "symbol": "none",
                "lineStyle": {"width": 2, "color": "#94a3b8"},
                "areaStyle": {"color": {
                    "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                    "colorStops": [
                        {"offset": 0, "color": "#94a3b815"},
                        {"offset": 1, "color": "#94a3b805"},
                    ],
                }},
                "itemStyle": {"color": "#94a3b8"},
            },
            {
                "name": f"+${extra:,.0f}/mo", "type": "line",
                "xAxisIndex": 1,
                "data": new_bals, "smooth": 0.3, "symbol": "none",
                "lineStyle": {"width": 2, "color": "#6366f1"},
                "areaStyle": {"color": {
                    "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                    "colorStops": [
                        {"offset": 0, "color": "#6366f120"},
                        {"offset": 1, "color": "#6366f105"},
                    ],
                }},
                "itemStyle": {"color": "#6366f1"},
            },
        ],
    }).classes("w-full").style("height:220px")


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _kpi(label: str, value: str, sub: str = "") -> None:
    with ui.column().classes("gap-0 pr-6 min-w-32"):
        ui.label(label).classes("text-xs text-zinc-400")
        ui.label(value).classes("text-sm font-semibold text-zinc-800")
        if sub:
            ui.label(sub).classes("text-xs text-zinc-300")


def _kpi_sep() -> None:
    ui.element("div").classes("w-px bg-zinc-100 self-stretch mr-6")

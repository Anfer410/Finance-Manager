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

from components.widgets.registry import REGISTRY_BY_ID
import services.auth as _auth


def _cur() -> str:
    return _auth.current_currency_prefix()


# ── Page entry point ──────────────────────────────────────────────────────────

def content() -> None:
    import services.auth as auth
    if not auth.is_authenticated():
        ui.navigate.to("/login")
        return

    family_id = auth.current_family_id()

    with ui.column().classes("w-full px-4 py-6 gap-6"):
        with ui.row().classes("items-center gap-3 mb-2"):
            ui.icon("calculate").classes("text-zinc-400 text-2xl")
            ui.label("Loan Planning").classes("text-2xl font-bold text-zinc-800")

        with ui.element("div").classes("card w-full"):
            REGISTRY_BY_ID["financial_baseline"].render_standalone(
                year=0, family_id=family_id, config={"months": 18}
            )
        _calculator_section(family_id=family_id)
        _extra_payment_section(family_id=family_id)


# ── New loan calculator ───────────────────────────────────────────────────────

def _calculator_section(family_id: int | None = None) -> None:
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
                            _kpi("Monthly payment",    f"{_cur()}{calc['monthly_payment']:,.2f}", "per month")
                            _kpi("Total interest",     f"{_cur()}{calc['total_interest']:,.0f}",  "over life of loan")
                            _kpi("Total cost",         f"{_cur()}{calc['total_cost']:,.0f}",       "principal + interest")
                            _kpi("Payoff date",        calc["payoff_date"].strftime("%b %Y"), "")

                        # DTI impact row
                        with ui.row().classes("gap-4 flex-wrap mt-1"):
                            _kpi("Current DTI",  f"{old_dti:.1f}%",  "before this loan")
                            _kpi("New DTI",      f"{new_dti:.1f}%",  "after this loan")
                            _kpi("Remaining headroom",
                                 f"{_cur()}{headroom - calc['monthly_payment']:,.0f}",
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

                baseline = get_baseline(months=12, family_id=family_id)
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

def _extra_payment_section(family_id: int | None = None) -> None:
    loans = load_loans(family_id) if family_id is not None else []
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
                        _kpi("Monthly payment",   f"{_cur()}{loan.monthly_payment:,.0f}", "per month")
                        _kpi("Payoff date",        stats.payoff_date.strftime("%b %Y"),
                             f"{stats.months_remaining} months remaining")
                        _kpi("Interest remaining", f"{_cur()}{stats.total_interest_remaining:,.0f}", "")

                    ui.element("div").classes("self-stretch w-px bg-zinc-100")

                    # After column
                    with ui.column().classes("gap-3 flex-1 min-w-44"):
                        ui.label(f"With +{_cur()}{extra:,.0f}/mo extra") \
                            .classes("text-xs font-semibold text-indigo-500 uppercase tracking-wide mb-1")
                        _kpi("Monthly payment",
                             f"{_cur()}{loan.monthly_payment + extra:,.0f}",
                             f"+{_cur()}{extra:,.0f} extra")
                        _kpi("New payoff date",    new_payoff.strftime("%b %Y"),
                             f"{stats.months_remaining - mo_saved} months remaining")
                        _kpi("Interest remaining",
                             f"{_cur()}{stats.total_interest_remaining - saved_int:,.0f}",
                             f"saves {_cur()}{saved_int:,.0f}")

                    ui.element("div").classes("self-stretch w-px bg-zinc-100")

                    # Savings highlight
                    with ui.column().classes("items-center justify-center gap-1 min-w-44 py-4"):
                        ui.icon("savings").classes("text-indigo-400 text-3xl mb-1")
                        ui.label("You save").classes("text-xs text-zinc-400")
                        ui.label(f"{_cur()}{saved_int:,.0f}") \
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
            "data": ["Normal", f"+{_cur()}{extra:,.0f}/mo"],
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
            "axisLabel": {":formatter": f"v => '{_cur()}' + (v/1000).toFixed(0) + 'k'",
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

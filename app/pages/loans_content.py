"""
pages/loans_content.py

Loans & Mortgages overview page.
  - 36-month spend / income / surplus overview chart
  - Per-loan cards: key stats, balance projection, matched payments, amortization table
  - Add / Edit / Delete via dialog
"""

from __future__ import annotations

from datetime import date

from nicegui import ui

from services.loan_service import (
    LoanRecord, LoanStats,
    load_loans, save_loan, delete_loan,
    compute_stats, match_payments, get_monthly_spend_income,
)

from components.finance_charts import spend_income_chart
from data.finance_dashboard_data import get_year_over_year_monthly_spend_series
from pages.loan_planning_content import _baseline_section

LOAN_TYPES  = ["mortgage", "auto", "student", "personal", "heloc", "other"]
_TYPE_ICON  = {
    "mortgage": "home", "auto": "directions_car", "student": "school",
    "personal": "person", "heloc": "account_balance", "other": "payments",
}
_TYPE_COLOR = {
    "mortgage": "#6366f1", "auto": "#f59e0b", "student": "#10b981",
    "personal": "#f43f5e", "heloc": "#3b82f6", "other": "#8b5cf6",
}


# ── Page entry point ──────────────────────────────────────────────────────────

def content() -> None:
    import services.auth as auth
    if not auth.is_authenticated():
        ui.navigate.to("/login")
        return

    family_id = auth.current_family_id()

    with ui.column().classes("w-full px-4 py-6 gap-6"):
        with ui.row().classes("items-center gap-3 mb-2"):
            ui.icon("account_balance_wallet").classes("text-zinc-400 text-2xl")
            ui.label("Loans & Mortgages").classes("text-2xl font-bold text-zinc-800")

        _baseline_section(family_id=family_id)
        _overview_chart(family_id)

        @ui.refreshable
        def loan_list() -> None:
            loans = load_loans(family_id)
            with ui.row().classes("items-center justify-between w-full"):
                ui.label(f"{len(loans)} loan{'s' if len(loans) != 1 else ''}") \
                    .classes("text-sm text-zinc-400")
                if auth.is_family_head():
                    ui.button("Add loan", icon="add",
                        on_click=lambda: _loan_dialog(None, loan_list.refresh, family_id)) \
                        .props("unelevated no-caps") \
                        .classes("bg-zinc-800 text-white rounded-lg px-4")

            if not loans:
                with ui.card().classes(
                    "w-full rounded-2xl shadow-none border border-zinc-100 p-10 items-center gap-3"
                ):
                    ui.icon("account_balance_wallet").classes("text-5xl text-zinc-200")
                    ui.label("No loans added yet").classes("text-zinc-500 text-sm")
                    ui.label(
                        "Track mortgages, auto loans, and more "
                        "to project payoff dates and total interest."
                    ).classes("text-xs text-zinc-300 text-center max-w-xs")
            else:
                for loan in loans:
                    _loan_card(loan, loan_list.refresh, family_id)

        loan_list()


# ── 36-month overview chart ───────────────────────────────────────────────────

def _overview_chart(family_id: int | None = None) -> None:
    series   = get_year_over_year_monthly_spend_series()
    has_data = any(v for v in series["spend"] + series["income"] if v)

    with ui.card().classes("w-full rounded-2xl shadow-none border border-zinc-100 p-0 gap-0"):
        with ui.row().classes("items-center gap-3 px-6 py-4 border-b border-zinc-100"):
            ui.icon("show_chart").classes("text-zinc-400 text-xl")
            ui.label("Spend vs Income — last 24 months") \
                .classes("text-base font-semibold text-zinc-700")

        with ui.element("div").classes("px-4 py-4 w-full"):
            if not has_data:
                with ui.column().classes("items-center justify-center gap-2 py-12 w-full"):
                    ui.icon("show_chart").classes("text-5xl text-zinc-200")
                    ui.label("No transaction data yet").classes("text-sm text-zinc-400")
                return
            spend_income_chart(series)


# ── Loan card ─────────────────────────────────────────────────────────────────

def _loan_card(loan: LoanRecord, on_refresh, family_id: int | None = None) -> None:
    stats    = compute_stats(loan)
    payments = match_payments(loan, limit=12, family_id=family_id)
    color    = _TYPE_COLOR.get(loan.loan_type, "#8b5cf6")
    icon     = _TYPE_ICON.get(loan.loan_type, "payments")
    yr       = stats.months_remaining // 12
    mo       = stats.months_remaining % 12
    time_str = f"{yr}yr {mo}mo" if yr else f"{mo}mo"

    with ui.card().classes("w-full rounded-2xl shadow-none border border-zinc-100 p-0 gap-0"):

        # ── Header ───────────────────────────────────────────────────────────
        with ui.row().classes("items-center gap-3 px-6 py-4 border-b border-zinc-100"):
            ui.icon(icon).style(f"color:{color};font-size:1.3rem")
            ui.label(loan.name).classes("text-base font-semibold text-zinc-800")

            with ui.element("span") \
                    .classes("text-xs px-2 py-0.5 rounded-full font-medium") \
                    .style(f"background:{color}22;color:{color}"):
                ui.label(loan.loan_type.title())

            ui.label(f"{loan.interest_rate:.2f}% {loan.rate_type.upper()}") \
                .classes("text-xs text-zinc-400 font-mono")

            if loan.lender:
                ui.label(f"· {loan.lender}").classes("text-xs text-zinc-300")

            ui.space()
            import services.auth as auth
            if auth.is_family_head():
                ui.button(icon="edit",
                      on_click=lambda l=loan: _loan_dialog(l, on_refresh, family_id)) \
                    .props("flat round dense").classes("text-zinc-400")
                ui.button(icon="delete_outline",
                      on_click=lambda l=loan: _confirm_delete(l, on_refresh, family_id)) \
                    .props("flat round dense").classes("text-red-300")

        # ── KPI metrics ──────────────────────────────────────────────────────
        with ui.row().classes("gap-0 px-6 py-5 flex-wrap border-b border-zinc-50"):
            _metric("Balance",
                    f"${loan.current_balance:,.0f}",
                    f"as of {loan.balance_as_of.strftime('%b %d, %Y')}")
            _metric_sep()
            _metric("Payoff", stats.payoff_date.strftime("%b %Y"), time_str)
            _metric_sep()
            _metric("Daily interest", f"${stats.daily_interest:,.2f}", "per day")
            _metric_sep()
            _metric("Equity", f"{stats.equity_pct:.1f}%",
                    f"${stats.principal_paid:,.0f} paid")
            _metric_sep()
            _metric("Interest remaining",
                    f"${stats.total_interest_remaining:,.0f}",
                    f"${stats.interest_paid:,.0f} paid to date")

        # ── Chart + summary panel ────────────────────────────────────────────
        with ui.row().classes("gap-4 px-4 py-4 w-full items-start"):
            with ui.element("div").classes("flex-1 min-w-0"):
                ui.label("Payoff projection").classes("text-xs font-medium text-zinc-400 mb-1")
                _balance_chart(stats)

            with ui.element("div").classes("w-52 flex-none"):
                ui.label("Summary").classes("text-xs font-medium text-zinc-400 mb-2")
                _summary_panel(loan, stats)

        # ── Matched payments ─────────────────────────────────────────────────
        if payments:
            with ui.expansion(
                f"Matched payments ({len(payments)})", icon="receipt_long"
            ).classes("w-full border-t border-zinc-50"):
                with ui.column().classes("gap-1 py-2 px-4"):
                    for p in payments:
                        with ui.row().classes("items-center gap-3"):
                            ui.label(str(p["date"])) \
                                .classes("text-zinc-400 font-mono text-xs w-24 flex-none")
                            ui.label(p["description"][:55]) \
                                .classes("text-zinc-600 flex-1 truncate text-xs")
                            ui.label(f"${p['amount']:,.2f}") \
                                .classes("text-zinc-800 font-semibold text-xs flex-none")

        # ── Amortization table ───────────────────────────────────────────────
        with ui.expansion("Amortization schedule", icon="table_rows") \
                .classes("w-full border-t border-zinc-50"):
            _amortization_table(stats.amortization)


def _metric(label: str, value: str, sub: str = "") -> None:
    with ui.column().classes("gap-0 pr-6 min-w-32"):
        ui.label(label).classes("text-xs text-zinc-400")
        ui.label(value).classes("text-sm font-semibold text-zinc-800")
        if sub:
            ui.label(sub).classes("text-xs text-zinc-300")


def _metric_sep() -> None:
    ui.element("div").classes("w-px bg-zinc-100 self-stretch mr-6")


def _balance_chart(stats: LoanStats) -> None:
    amort = stats.amortization
    if not amort:
        ui.label("No projection data.").classes("text-sm text-zinc-400 text-center py-8")
        return

    # Sample every 6 months for readability
    sampled = amort[::6]
    if amort[-1] not in sampled:
        sampled = sampled + [amort[-1]]

    dates = [str(r.date) for r in sampled]
    bals  = [r.balance  for r in sampled]

    ui.echart({
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "#fff", "borderColor": "#e4e4e7",
            "textStyle": {"color": "#09090b", "fontSize": 11},
            ":formatter": "p => p[0].name + '<br/>' + p[0].marker + ' $' + p[0].value.toLocaleString(undefined,{maximumFractionDigits:0})",
        },
        "grid": {"left": "2%", "right": "2%", "top": "8%", "bottom": "8%", "containLabel": True},
        "xAxis": {
            "type": "category", "data": dates, "boundaryGap": False,
            "axisLine": {"lineStyle": {"color": "#e4e4e7"}},
            "axisTick": {"show": False},
            "axisLabel": {
                "color": "#71717a", "fontSize": 9,
                ":formatter": "v => { let d = new Date(v); return d.toLocaleDateString('en-US',{month:'short',year:'2-digit'}); }",
            },
        },
        "yAxis": {
            "type": "value",
            "splitLine": {"lineStyle": {"color": "#f4f4f5", "type": "dashed"}},
            "axisLabel": {":formatter": "v => '$' + (v/1000).toFixed(0) + 'k'",
                          "color": "#71717a", "fontSize": 9},
        },
        "series": [{
            "type": "line", "data": bals, "smooth": 0.4, "symbol": "none",
            "lineStyle": {"width": 2, "color": "#6366f1"},
            "areaStyle": {"color": {
                "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                "colorStops": [
                    {"offset": 0, "color": "#6366f120"},
                    {"offset": 1, "color": "#6366f105"},
                ],
            }},
            "itemStyle": {"color": "#6366f1"},
        }],
    }).classes("w-full").style("height:200px")


def _summary_panel(loan: LoanRecord, stats: LoanStats) -> None:
    pct = min(stats.equity_pct, 100)
    with ui.column().classes("gap-3 w-full"):
        with ui.column().classes("gap-1 w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label("Principal paid").classes("text-xs text-zinc-400")
                ui.label(f"{pct:.0f}%").classes("text-xs font-semibold text-zinc-700")
            with ui.element("div").classes("w-full bg-zinc-100 rounded-full h-1.5"):
                ui.element("div").classes("bg-indigo-500 h-1.5 rounded-full") \
                    .style(f"width:{pct:.0f}%")

        _srow("Original loan",  f"${loan.original_principal:,.0f}")
        _srow("Remaining",      f"${loan.current_balance:,.0f}")
        _srow("Monthly pmt",    f"${loan.monthly_payment:,.0f}")
        if loan.monthly_insurance:
            pi = loan.monthly_payment - loan.monthly_insurance
            _srow("  P&I",       f"${pi:,.0f}")
            _srow("  Insurance", f"${loan.monthly_insurance:,.0f}")
        _srow("Interest rate",  f"{loan.interest_rate:.2f}%")
        _srow("Term",           f"{loan.term_months // 12}yr {loan.term_months % 12}mo")
        _srow("Start date",     loan.start_date.strftime("%b %Y"))


def _srow(label: str, value: str) -> None:
    with ui.row().classes("items-center justify-between w-full"):
        ui.label(label).classes("text-xs text-zinc-400")
        ui.label(value).classes("text-xs font-semibold text-zinc-700")


def _amortization_table(amort) -> None:
    if not amort:
        ui.label("No data.").classes("text-sm text-zinc-400 py-4 px-4")
        return

    rows = [
        {
            "month":     r.month_num,
            "date":      r.date.strftime("%b %Y"),
            "payment":   f"${r.payment:,.2f}",
            "principal": f"${r.principal:,.2f}",
            "interest":  f"${r.interest:,.2f}",
            "balance":   f"${r.balance:,.2f}",
        }
        for r in amort[:48]
    ]

    columns = [
        {"name": "month",     "label": "#",        "field": "month",     "align": "right"},
        {"name": "date",      "label": "Date",      "field": "date",      "align": "left"},
        {"name": "payment",   "label": "Payment",   "field": "payment",   "align": "right"},
        {"name": "principal", "label": "Principal", "field": "principal", "align": "right"},
        {"name": "interest",  "label": "Interest",  "field": "interest",  "align": "right"},
        {"name": "balance",   "label": "Balance",   "field": "balance",   "align": "right"},
    ]

    tbl = ui.table(
        columns=columns, rows=rows, row_key="month",
        pagination={"rowsPerPage": 12},
    ).classes("w-full text-xs")

    tbl.add_slot("body-cell-principal", """
        <q-td :props="props">
            <span style="color:#22c55e;font-weight:600">{{ props.value }}</span>
        </q-td>
    """)
    tbl.add_slot("body-cell-interest", """
        <q-td :props="props">
            <span style="color:#f87171">{{ props.value }}</span>
        </q-td>
    """)

    if len(amort) > 48:
        ui.label(f"Showing 48 of {len(amort)} months") \
            .classes("text-xs text-zinc-400 px-4 pb-2")


# ── Dialogs ───────────────────────────────────────────────────────────────────

def _confirm_delete(loan: LoanRecord, on_refresh, family_id: int | None = None) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-80 rounded-2xl p-6 gap-4"):
        ui.label(f'Remove "{loan.name}"?') \
            .classes("text-base font-semibold text-zinc-800")
        ui.label("This removes the loan from your dashboard. Transaction data is unaffected.") \
            .classes("text-sm text-zinc-400")
        with ui.row().classes("gap-2 justify-end w-full"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps").classes("text-zinc-500")

            def _do():
                delete_loan(loan.id, family_id)
                dlg.close()
                on_refresh()

            ui.button("Remove", on_click=_do, icon="delete") \
                .props("unelevated no-caps") \
                .classes("bg-red-500 text-white rounded-lg px-4")
    dlg.open()


def _loan_dialog(loan: LoanRecord | None, on_refresh, family_id: int | None = None) -> None:
    """Add / edit loan dialog."""
    state = {"error": ""}

    with ui.dialog() as dlg, ui.card().classes("max-w-2xl rounded-2xl p-6 gap-4"):
        with ui.row().classes("items-center justify-between w-full mb-1"):
            ui.label("Edit loan" if loan else "Add loan") \
                .classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        def _inp(label, value="", **kw):
            return ui.input(label, value=value, **kw) \
                .props("outlined dense").classes("w-full")

        def _num(label, value=0, fmt="%.2f", **kw):
            return ui.number(label, value=value, format=fmt, **kw) \
                .props("outlined dense")

        def _lbl(text):
            ui.label(text).classes("text-xs text-zinc-500 mb-1")

        # ── Basic info ────────────────────────────────────────────────────
        ui.label("Basic info") \
            .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
        _lbl("Name")
        name_in = _inp("", loan.name if loan else "",
                       placeholder="e.g. Primary Mortgage, Honda CR-V Loan")

        with ui.row().classes("gap-3 w-full"):
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Loan type")
                type_in = ui.select(
                    options=LOAN_TYPES,
                    value=loan.loan_type if loan else "mortgage",
                ).props("outlined dense").classes("w-full")
            with ui.column().classes("w-32 gap-0"):
                _lbl("Rate type")
                rate_type_in = ui.select(
                    options=["fixed", "arm"],
                    value=loan.rate_type if loan else "fixed",
                ).props("outlined dense").classes("w-full")

        _lbl("Lender (optional)")
        lender_in = _inp("", loan.lender if loan else "")

        # ── Loan terms ────────────────────────────────────────────────────
        ui.separator()
        ui.label("Loan terms") \
            .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

        with ui.row().classes("gap-3 w-full"):
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Interest rate %")
                rate_in = _num("", loan.interest_rate if loan else 6.5,
                                fmt="%.3f", min=0, max=30).classes("w-full")
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Monthly payment $ (total)")
                payment_in = _num("", loan.monthly_payment if loan else 0,
                                   min=0).classes("w-full")
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Homeowner insurance $/mo")
                insurance_in = _num("", loan.monthly_insurance if loan else 0.0,
                                     min=0).classes("w-full")

        with ui.row().classes("gap-3 w-full"):
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Original principal $")
                principal_in = _num("", loan.original_principal if loan else 0,
                                     min=0).classes("w-full")
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Term (months)")
                term_in = _num("", loan.term_months if loan else 360,
                                fmt="%d", min=1, max=600).classes("w-full")

        _lbl("Start date")
        with ui.input("", value=str(loan.start_date) if loan else "") \
                .props("outlined dense").classes("w-full") as start_in:
            with ui.menu().props("no-parent-event") as _start_menu:
                ui.date(mask="YYYY-MM-DD").bind_value(start_in)
            with start_in.add_slot("append"):
                ui.icon("edit_calendar").classes("cursor-pointer") \
                    .on("click", _start_menu.open)

        # ── Current balance snapshot ───────────────────────────────────────
        ui.separator()
        ui.label("Current balance snapshot") \
            .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
        ui.label("Enter your latest statement balance and the date it was from.") \
            .classes("text-xs text-zinc-400 -mt-2")

        with ui.row().classes("gap-3 w-full"):
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Current balance $")
                balance_in = _num("", loan.current_balance if loan else 0,
                                   min=0).classes("w-full")
            with ui.column().classes("flex-1 gap-0"):
                _lbl("As of date")
                with ui.input("", value=str(loan.balance_as_of) if loan else str(date.today())) \
                        .props("outlined dense").classes("w-full") as balance_date_in:
                    with ui.menu().props("no-parent-event") as _bal_menu:
                        ui.date(mask="YYYY-MM-DD").bind_value(balance_date_in)
                    with balance_date_in.add_slot("append"):
                        ui.icon("edit_calendar").classes("cursor-pointer") \
                            .on("click", _bal_menu.open)

        # ── ARM details ───────────────────────────────────────────────────
        ui.separator()
        ui.label("ARM details (ignored for fixed rate)") \
            .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

        with ui.row().classes("gap-3 w-full"):
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Adjustment period (months)")
                arm_period_in = _num("", loan.arm_adjustment_period_months or 60 if loan else 60,
                                      fmt="%d", min=1).classes("w-full")
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Per-adjustment cap %")
                arm_cap_in = _num("", loan.arm_rate_cap or 2.0 if loan else 2.0,
                                   fmt="%.2f").classes("w-full")
            with ui.column().classes("flex-1 gap-0"):
                _lbl("Lifetime cap %")
                arm_life_in = _num("", loan.arm_lifetime_cap or 5.0 if loan else 5.0,
                                    fmt="%.2f").classes("w-full")

        # ── Transaction matching ───────────────────────────────────────────
        ui.separator()
        ui.label("Transaction matching (optional)") \
            .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
        ui.label("Finds actual payments in your transaction data to show payment history.") \
            .classes("text-xs text-zinc-400 -mt-2")

        _lbl("Description pattern")
        pattern_in = _inp("", loan.payment_description_pattern if loan else "",
                           placeholder="e.g. QUICKEN LOANS, HONDA FINANCIAL")
        _lbl("Account key (optional)")
        account_in = _inp("", loan.payment_account_key if loan else "",
                           placeholder="e.g. wf_checking — leave blank to search all")

        # ── Notes ──────────────────────────────────────────────────────────
        _lbl("Notes")
        notes_in = _inp("", loan.notes if loan else "")

        @ui.refreshable
        def feedback():
            if state["error"]:
                ui.label(state["error"]).classes("text-sm text-red-500")

        feedback()

        def _save():
            state["error"] = ""
            if not name_in.value.strip():
                state["error"] = "Name is required."
                feedback.refresh()
                return
            try:
                start_date = date.fromisoformat(start_in.value.strip())
            except ValueError:
                state["error"] = "Invalid start date — use YYYY-MM-DD."
                feedback.refresh()
                return
            try:
                balance_date = date.fromisoformat(balance_date_in.value.strip())
            except ValueError:
                state["error"] = "Invalid balance date — use YYYY-MM-DD."
                feedback.refresh()
                return

            is_arm = rate_type_in.value == "arm"
            new_loan = LoanRecord(
                id                          = loan.id if loan else None,
                name                        = name_in.value.strip(),
                loan_type                   = type_in.value,
                rate_type                   = rate_type_in.value,
                interest_rate               = float(rate_in.value or 0),
                original_principal          = float(principal_in.value or 0),
                term_months                 = int(term_in.value or 360),
                start_date                  = start_date,
                monthly_payment             = float(payment_in.value or 0),
                monthly_insurance           = float(insurance_in.value or 0),
                current_balance             = float(balance_in.value or 0),
                balance_as_of               = balance_date,
                arm_adjustment_period_months= int(arm_period_in.value or 60) if is_arm else None,
                arm_rate_cap                = float(arm_cap_in.value or 2.0) if is_arm else None,
                arm_lifetime_cap            = float(arm_life_in.value or 5.0) if is_arm else None,
                payment_description_pattern = pattern_in.value.strip(),
                payment_account_key         = account_in.value.strip(),
                lender                      = lender_in.value.strip(),
                notes                       = notes_in.value.strip(),
            )
            save_loan(new_loan, family_id)
            dlg.close()
            on_refresh()

        with ui.row().classes("gap-2 justify-end w-full"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps").classes("text-zinc-500")
            ui.button("Save", on_click=_save, icon="save") \
                .props("unelevated no-caps") \
                .classes("bg-zinc-800 text-white rounded-lg px-4")

    dlg.open()

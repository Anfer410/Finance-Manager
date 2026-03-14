"""
services/loan_service.py

Loan and mortgage management — data model, DB persistence, amortization math.

Public API
──────────
    # Data classes
    LoanRecord, AmortizationRow, LoanStats

    # DB CRUD
    load_loans()                        -> list[LoanRecord]
    save_loan(loan)                     -> int   (id)
    delete_loan(loan_id)

    # Amortization math
    compute_amortization(loan)          -> list[AmortizationRow]
    compute_stats(loan)                 -> LoanStats
    payoff_with_extra(loan, extra)      -> (payoff_date, interest_saved, months_saved)
    calculate_loan(amount, rate, term)  -> dict

    # Data queries
    match_payments(loan, limit)         -> list[dict]
    get_monthly_spend_income(months)    -> dict  {labels, spend, income, budget}
    get_baseline(months)                -> dict  {avg_income, avg_spend, ...}
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional

from sqlalchemy import text


# ── Helpers ───────────────────────────────────────────────────────────────────

def _engine():
    from data.db import get_engine
    return get_engine()

def _schema() -> str:
    from data.db import get_schema
    return get_schema()

def _add_months(d: date, n: int) -> date:
    """Add n months to date d, clamping day to month boundary."""
    month = d.month - 1 + n
    year  = d.year + month // 12
    month = month % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

def _months_between(d1: date, d2: date) -> int:
    """Non-negative integer months from d1 to d2."""
    return max(0, (d2.year - d1.year) * 12 + (d2.month - d1.month))

def _q(sql: str, **params):
    try:
        with _engine().connect() as conn:
            return conn.execute(text(sql), params).fetchall()
    except Exception:
        return []


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class LoanRecord:
    name:                         str
    loan_type:                    str            # mortgage|auto|student|personal|heloc|other
    rate_type:                    str            # fixed|arm
    interest_rate:                float          # annual %, e.g. 6.75
    original_principal:           float
    term_months:                  int
    start_date:                   date
    monthly_payment:              float
    current_balance:              float
    balance_as_of:                date
    id:                           Optional[int]   = None
    arm_adjustment_period_months: Optional[int]   = None
    arm_rate_cap:                 Optional[float] = None
    arm_lifetime_cap:             Optional[float] = None
    payment_description_pattern:  str = ""
    payment_account_key:          str = ""
    lender:                       str = ""
    notes:                        str = ""
    is_active:                    bool = True
    monthly_insurance:            float = 0.0


@dataclass
class AmortizationRow:
    month_num: int
    date:      date
    payment:   float
    principal: float
    interest:  float
    balance:   float


@dataclass
class LoanStats:
    monthly_payment:          float
    daily_interest:           float
    payoff_date:              date
    months_remaining:         int
    total_interest_remaining: float
    principal_paid:           float
    interest_paid:            float
    equity_pct:               float
    amortization:             list[AmortizationRow]


# ── DB CRUD ───────────────────────────────────────────────────────────────────

def load_loans() -> list[LoanRecord]:
    schema = _schema()
    try:
        rows = _q(f"""
            SELECT id, name, loan_type, rate_type, interest_rate,
                   original_principal, term_months, start_date,
                   monthly_payment, current_balance, balance_as_of,
                   arm_adjustment_period_months, arm_rate_cap, arm_lifetime_cap,
                   payment_description_pattern, payment_account_key,
                   lender, notes, is_active,
                   COALESCE(monthly_insurance, 0) AS monthly_insurance
            FROM {schema}.app_loans
            WHERE is_active = TRUE
            ORDER BY id
        """)
        return [_row_to_loan(r) for r in rows]
    except Exception as e:
        print(f"[loan_service] load_loans failed: {e}")
        return []


def save_loan(loan: LoanRecord) -> int:
    schema = _schema()
    data = {
        "name":                         loan.name,
        "loan_type":                    loan.loan_type,
        "rate_type":                    loan.rate_type,
        "interest_rate":                loan.interest_rate,
        "original_principal":           loan.original_principal,
        "term_months":                  loan.term_months,
        "start_date":                   loan.start_date,
        "monthly_payment":              loan.monthly_payment,
        "monthly_insurance":            loan.monthly_insurance or 0.0,
        "current_balance":              loan.current_balance,
        "balance_as_of":                loan.balance_as_of,
        "arm_adjustment_period_months": loan.arm_adjustment_period_months,
        "arm_rate_cap":                 loan.arm_rate_cap,
        "arm_lifetime_cap":             loan.arm_lifetime_cap,
        "payment_description_pattern":  loan.payment_description_pattern or "",
        "payment_account_key":          loan.payment_account_key or "",
        "lender":                       loan.lender or "",
        "notes":                        loan.notes or "",
        "is_active":                    True,
    }
    with _engine().begin() as conn:
        if loan.id is None:
            result = conn.execute(text(f"""
                INSERT INTO {schema}.app_loans
                    (name, loan_type, rate_type, interest_rate,
                     original_principal, term_months, start_date,
                     monthly_payment, monthly_insurance, current_balance, balance_as_of,
                     arm_adjustment_period_months, arm_rate_cap, arm_lifetime_cap,
                     payment_description_pattern, payment_account_key,
                     lender, notes, is_active, updated_at)
                VALUES
                    (:name, :loan_type, :rate_type, :interest_rate,
                     :original_principal, :term_months, :start_date,
                     :monthly_payment, :monthly_insurance, :current_balance, :balance_as_of,
                     :arm_adjustment_period_months, :arm_rate_cap, :arm_lifetime_cap,
                     :payment_description_pattern, :payment_account_key,
                     :lender, :notes, :is_active, NOW())
                RETURNING id
            """), data)
            return result.fetchone()[0]
        else:
            data["id"] = loan.id
            conn.execute(text(f"""
                UPDATE {schema}.app_loans SET
                    name = :name, loan_type = :loan_type, rate_type = :rate_type,
                    interest_rate = :interest_rate,
                    original_principal = :original_principal,
                    term_months = :term_months, start_date = :start_date,
                    monthly_payment = :monthly_payment,
                    monthly_insurance = :monthly_insurance,
                    current_balance = :current_balance, balance_as_of = :balance_as_of,
                    arm_adjustment_period_months = :arm_adjustment_period_months,
                    arm_rate_cap = :arm_rate_cap, arm_lifetime_cap = :arm_lifetime_cap,
                    payment_description_pattern = :payment_description_pattern,
                    payment_account_key = :payment_account_key,
                    lender = :lender, notes = :notes, updated_at = NOW()
                WHERE id = :id
            """), data)
            return loan.id


def delete_loan(loan_id: int) -> None:
    schema = _schema()
    with _engine().begin() as conn:
        conn.execute(
            text(f"UPDATE {schema}.app_loans SET is_active = FALSE, updated_at = NOW() WHERE id = :id"),
            {"id": loan_id},
        )


def _row_to_loan(r) -> LoanRecord:
    return LoanRecord(
        id=r[0], name=r[1], loan_type=r[2], rate_type=r[3],
        interest_rate=float(r[4]),
        original_principal=float(r[5]),
        term_months=int(r[6]),
        start_date=r[7],
        monthly_payment=float(r[8]),
        current_balance=float(r[9]),
        balance_as_of=r[10],
        arm_adjustment_period_months=r[11],
        arm_rate_cap=float(r[12]) if r[12] is not None else None,
        arm_lifetime_cap=float(r[13]) if r[13] is not None else None,
        payment_description_pattern=r[14] or "",
        payment_account_key=r[15] or "",
        lender=r[16] or "",
        notes=r[17] or "",
        is_active=r[18],
        monthly_insurance=float(r[19]) if r[19] is not None else 0.0,
    )


# ── Amortization math ─────────────────────────────────────────────────────────

def compute_amortization(loan: LoanRecord) -> list[AmortizationRow]:
    """
    Full amortization schedule from current_balance / balance_as_of.
    Uses flat rate (ARM held constant at current rate).
    """
    monthly_rate = (loan.interest_rate / 100) / 12
    balance      = loan.current_balance
    # Insurance is not applied to principal/interest — use P&I portion only
    payment      = loan.monthly_payment - (loan.monthly_insurance or 0.0)
    current_date = loan.balance_as_of
    rows         = []
    MAX_MONTHS   = loan.term_months + 24   # safety cap

    for month_num in range(1, MAX_MONTHS + 1):
        if balance <= 0.005:
            break
        current_date = _add_months(current_date, 1)
        interest     = round(balance * monthly_rate, 2) if monthly_rate > 0 else 0.0
        actual_pay   = min(payment, balance + interest)
        principal    = round(actual_pay - interest, 2)
        balance      = round(max(balance - principal, 0.0), 2)
        rows.append(AmortizationRow(
            month_num = month_num,
            date      = current_date,
            payment   = round(actual_pay, 2),
            principal = principal,
            interest  = interest,
            balance   = balance,
        ))
        if balance <= 0:
            break

    return rows


def compute_stats(loan: LoanRecord) -> LoanStats:
    amort = compute_amortization(loan)

    if amort:
        payoff_date              = amort[-1].date
        months_remaining         = len(amort)
        total_interest_remaining = round(sum(r.interest for r in amort), 2)
    else:
        payoff_date              = loan.balance_as_of
        months_remaining         = 0
        total_interest_remaining = 0.0

    months_elapsed  = _months_between(loan.start_date, loan.balance_as_of)
    principal_paid  = round(max(loan.original_principal - loan.current_balance, 0.0), 2)
    pi_payment      = loan.monthly_payment - (loan.monthly_insurance or 0.0)
    interest_paid   = round(max(pi_payment * months_elapsed - principal_paid, 0.0), 2)
    equity_pct     = round(principal_paid / loan.original_principal * 100, 1) if loan.original_principal > 0 else 0.0
    daily_interest = round(loan.current_balance * (loan.interest_rate / 100) / 365, 2)

    return LoanStats(
        monthly_payment          = loan.monthly_payment,
        daily_interest           = daily_interest,
        payoff_date              = payoff_date,
        months_remaining         = months_remaining,
        total_interest_remaining = total_interest_remaining,
        principal_paid           = principal_paid,
        interest_paid            = interest_paid,
        equity_pct               = equity_pct,
        amortization             = amort,
    )


def payoff_with_extra(loan: LoanRecord, extra: float) -> tuple[date, float, int]:
    """Returns (new_payoff_date, interest_saved, months_saved)."""
    base_amort = compute_amortization(loan)
    new_amort  = compute_amortization(replace(loan, monthly_payment=loan.monthly_payment + extra))
    interest_saved = round(
        sum(r.interest for r in base_amort) - sum(r.interest for r in new_amort), 2
    )
    new_payoff   = new_amort[-1].date if new_amort else loan.balance_as_of
    months_saved = max(0, len(base_amort) - len(new_amort))
    return new_payoff, interest_saved, months_saved


def calculate_loan(amount: float, annual_rate: float, term_months: int) -> dict:
    """Standard amortization payment calculator."""
    if annual_rate <= 0 or term_months <= 0:
        monthly_pmt    = round(amount / max(term_months, 1), 2)
        total_interest = 0.0
    else:
        r = (annual_rate / 100) / 12
        monthly_pmt    = round(amount * r * (1 + r) ** term_months / ((1 + r) ** term_months - 1), 2)
        total_interest = round(monthly_pmt * term_months - amount, 2)

    return {
        "monthly_payment": monthly_pmt,
        "total_interest":  total_interest,
        "total_cost":      round(amount + total_interest, 2),
        "payoff_date":     _add_months(date.today(), term_months),
    }


# ── Transaction matching ──────────────────────────────────────────────────────

def match_payments(loan: LoanRecord, limit: int = 24) -> list[dict]:
    """Finds actual payment transactions matching this loan's description pattern."""
    if not loan.payment_description_pattern:
        return []

    schema = _schema()
    where  = ["description ILIKE :pattern"]
    params: dict = {"pattern": f"%{loan.payment_description_pattern}%", "limit": limit}

    if loan.payment_account_key:
        where.append("account_key = :ak")
        params["ak"] = loan.payment_account_key

    try:
        rows = _q(f"""
            SELECT transaction_date, description, ABS(amount) AS amount, account_key
            FROM {schema}.transactions_debit
            WHERE {' AND '.join(where)}
            ORDER BY transaction_date DESC
            LIMIT :limit
        """, **params)
        return [
            {"date": r[0], "description": r[1], "amount": float(r[2]), "account": r[3]}
            for r in rows
        ]
    except Exception as e:
        print(f"[loan_service] match_payments failed: {e}")
        return []


# ── 36-month spend / income series ───────────────────────────────────────────

def get_monthly_spend_income(months: int = 36) -> dict:
    """Monthly spend + income for the last N months with rolling surplus."""
    schema = _schema()
    today  = date.today()
    start  = _add_months(today.replace(day=1), -(months - 1))

    spend_rows  = _q(f"""
        SELECT DATE_TRUNC('month', transaction_date)::DATE AS mo,
               COALESCE(SUM(amount), 0)
        FROM {schema}.v_all_spend
        WHERE transaction_date >= :start
        GROUP BY mo ORDER BY mo
    """, start=start)

    income_rows = _q(f"""
        SELECT DATE_TRUNC('month', transaction_date)::DATE AS mo,
               COALESCE(SUM(amount), 0)
        FROM {schema}.v_income
        WHERE transaction_date >= :start
        GROUP BY mo ORDER BY mo
    """, start=start)

    # Build ordered month list
    month_dates: list[date] = []
    m = start
    while m <= today.replace(day=1):
        month_dates.append(m)
        m = _add_months(m, 1)

    spend_map  = {r[0]: float(r[1]) for r in spend_rows}
    income_map = {r[0]: float(r[1]) for r in income_rows}

    labels = [d.strftime("%b '%y") for d in month_dates]
    spend  = [round(spend_map.get(d, 0.0),  2) for d in month_dates]
    income = [round(income_map.get(d, 0.0), 2) for d in month_dates]

    # Rolling surplus (cumulative, same logic as finance dashboard)
    budget: list[float | None] = []
    rolling = 0.0
    for s, inc in zip(spend, income):
        if s == 0 and inc == 0:
            budget.append(None)
        else:
            rolling = round(rolling + inc - s, 2)
            budget.append(rolling)

    return {"labels": labels, "spend": spend, "income": income, "budget": budget}


# ── Financial baseline ────────────────────────────────────────────────────────

def get_baseline(months: int = 12) -> dict:
    """Avg monthly income/spend + current debt load for trailing N months."""
    schema = _schema()
    start  = _add_months(date.today().replace(day=1), -(months - 1))

    spend_rows  = _q(f"SELECT COALESCE(SUM(amount),0) FROM {schema}.v_all_spend WHERE transaction_date >= :s", s=start)
    income_rows = _q(f"SELECT COALESCE(SUM(amount),0) FROM {schema}.v_income     WHERE transaction_date >= :s", s=start)

    avg_spend   = round(float(spend_rows[0][0])  / months, 2) if spend_rows  else 0.0
    avg_income  = round(float(income_rows[0][0]) / months, 2) if income_rows else 0.0
    avg_surplus = round(avg_income - avg_spend, 2)

    monthly_debt = round(sum(l.monthly_payment for l in load_loans()), 2)
    dti          = round(monthly_debt / avg_income * 100, 1) if avg_income > 0 else 0.0
    headroom     = round(avg_surplus - monthly_debt, 2)

    return {
        "avg_income":   avg_income,
        "avg_spend":    avg_spend,
        "avg_surplus":  avg_surplus,
        "monthly_debt": monthly_debt,
        "dti":          dti,
        "headroom":     headroom,
    }

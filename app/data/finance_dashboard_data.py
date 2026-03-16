"""
services/finance_dashboard_data.py

Queries the purpose-built views:
  v_all_spend     — deduplicated spend (credit purchases + debit outflows)
  v_credit_spend  — credit card purchases only
  v_debit_spend   — checking outflows only (transfers/payments excluded)
  v_income        — employer payroll deposits
"""

from __future__ import annotations
from datetime import datetime
from sqlalchemy import text

from data.db import get_engine, get_schema

_SCHEMA        = get_schema()
V_ALL_SPEND    = f"{_SCHEMA}.v_all_spend"
V_CREDIT_SPEND = f"{_SCHEMA}.v_credit_spend"
V_DEBIT_SPEND  = f"{_SCHEMA}.v_debit_spend"
V_INCOME       = f"{_SCHEMA}.v_income"

MONTH_LABELS = ["Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]


def _engine():
    return get_engine()

def _q(sql: str, **params):
    try:
        with _engine().connect() as conn:
            return conn.execute(text(sql), params).fetchall()
    except Exception as e:
        # View doesn't exist yet (fresh DB, no data uploaded) — return empty
        msg = str(e)
        if "does not exist" in msg or "UndefinedTable" in msg or "undefined_table" in msg:
            return []
        raise

def _kpi(spend: float, income: float) -> dict:
    return {"spend": round(spend, 2), "income": round(income, 2),
            "net": round(income - spend, 2)}


# ── Available years ───────────────────────────────────────────────────────────

def get_years() -> list[int]:
    rows = _q(
        f"SELECT DISTINCT EXTRACT(YEAR FROM transaction_date)::INT AS y "
        f"FROM {V_ALL_SPEND} ORDER BY y DESC"
    )
    return [r[0] for r in rows] or [datetime.now().year]


# ── KPI helpers ───────────────────────────────────────────────────────────────

def _spend_income_kpi(view: str, year: int | None = None) -> dict:
    where = "WHERE EXTRACT(YEAR FROM transaction_date) = :year" if year else "WHERE 1=1"
    rows  = _q(f"""
        SELECT
            COALESCE(SUM(amount), 0) AS spend
        FROM {view}
        {where}
    """, **({"year": year} if year else {}))
    spend = float(rows[0][0]) if rows else 0.0

    income_rows = _q(f"""
        SELECT COALESCE(SUM(amount), 0) AS income
        FROM {V_INCOME}
        {where}
    """, **({"year": year} if year else {}))
    income = float(income_rows[0][0]) if income_rows else 0.0

    return _kpi(spend, income)


def get_alltime_kpi() -> dict:
    return _spend_income_kpi(V_ALL_SPEND)

def get_yearly_kpi(year: int) -> dict:
    return _spend_income_kpi(V_ALL_SPEND, year)


# ── Monthly spend + income series ─────────────────────────────────────────────

def get_monthly_spend_series(year: int) -> dict:
    spend_rows = _q(f"""
        SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS spend
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
        GROUP BY m ORDER BY m
    """, year=year)

    income_rows = _q(f"""
        SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS income
        FROM {V_INCOME}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
        GROUP BY m ORDER BY m
    """, year=year)

    spend_by_m  = {r[0]: float(r[1]) for r in spend_rows}
    income_by_m = {r[0]: float(r[1]) for r in income_rows}

    spend_vals  = [round(spend_by_m.get(m, 0.0),  2) for m in range(1, 13)]
    income_vals = [round(income_by_m.get(m, 0.0), 2) for m in range(1, 13)]

    # Rolling budget: each month = leftover from previous + (income - spend)
    # Stops accumulating once we hit a month with no data (both 0)
    budget: list[float] = []
    rolling = 0.0
    for s, inc in zip(spend_vals, income_vals):
        if s == 0 and inc == 0:
            budget.append(None)  # no data yet — don't plot
        else:
            rolling = round(rolling + inc - s, 2)
            budget.append(rolling)

    return {
        "months": MONTH_LABELS,
        "spend":  spend_vals,
        "income": income_vals,
        "budget": budget,
    }


# ── Year-over-year 3-year monthly series ─────────────────────────────────────

def get_year_over_year_monthly_spend_series(year_back: int = 2) -> dict:
    """
    Monthly spend + income from Jan of (today.year - year_back) through current month,
    with a rolling surplus that carries forward across months.
    Returns the same shape as get_monthly_spend_series() so spend_income_chart
    can consume it directly.
    """
    from datetime import date

    today = date.today()
    start = date(today.year - year_back, 1, 1)
    end   = today.replace(day=1)

    spend_rows = _q(f"""
        SELECT DATE_TRUNC('month', transaction_date)::DATE AS mo,
               COALESCE(SUM(amount), 0)
        FROM {V_ALL_SPEND}
        WHERE transaction_date >= :start
        GROUP BY mo ORDER BY mo
    """, start=start)

    income_rows = _q(f"""
        SELECT DATE_TRUNC('month', transaction_date)::DATE AS mo,
               COALESCE(SUM(amount), 0)
        FROM {V_INCOME}
        WHERE transaction_date >= :start
        GROUP BY mo ORDER BY mo
    """, start=start)

    # Build full month list
    month_dates: list[date] = []
    cur = start
    while cur <= end:
        month_dates.append(cur)
        cur = date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)

    spend_map  = {r[0]: float(r[1]) for r in spend_rows}
    income_map = {r[0]: float(r[1]) for r in income_rows}

    labels = [d.strftime("%b '%y") for d in month_dates]
    spend  = [round(spend_map.get(d, 0.0),  2) for d in month_dates]
    income = [round(income_map.get(d, 0.0), 2) for d in month_dates]

    budget: list[float | None] = []
    rolling = 0.0
    for s, inc in zip(spend, income):
        if s == 0 and inc == 0:
            budget.append(None)
        else:
            rolling = round(rolling + inc - s, 2)
            budget.append(rolling)

    return {"months": labels, "spend": spend, "income": income, "budget": budget}


# ── Spend per bank series ─────────────────────────────────────────────────────

def get_spend_per_bank_series(year: int) -> dict:
    rows = _q(f"""
        SELECT bank,
               EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS spend
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
        GROUP BY bank, m
        ORDER BY bank, m
    """, year=year)

    banks: dict[str, list[float]] = {}
    for bank, m, spend in rows:
        if bank not in banks:
            banks[bank] = [0.0] * 12
        banks[bank][int(m) - 1] = round(float(spend), 2)

    return {"months": MONTH_LABELS, "banks": banks}


# ── Employer income series ────────────────────────────────────────────────────

def get_employer_income_series(year: int) -> dict:
    """
    Returns monthly payroll and other income series.
    - payroll: rows matching employer patterns (empty list if none configured)
    - other:   all remaining income rows
    Both sum to total income for the year.
    """
    from services.transaction_config import load_config
    cfg = load_config()

    if cfg.employer_patterns:
        employer_clause = "(" + " OR ".join(
            f"description ILIKE '%{p}%'" for p in cfg.employer_patterns
        ) + ")"

        payroll_rows = _q(f"""
            SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
                   COALESCE(SUM(amount), 0) AS income
            FROM {V_INCOME}
            WHERE EXTRACT(YEAR FROM transaction_date) = :year
              AND {employer_clause}
            GROUP BY m ORDER BY m
        """, year=year)

        other_rows = _q(f"""
            SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
                   COALESCE(SUM(amount), 0) AS income
            FROM {V_INCOME}
            WHERE EXTRACT(YEAR FROM transaction_date) = :year
              AND NOT {employer_clause}
            GROUP BY m ORDER BY m
        """, year=year)
    else:
        payroll_rows = []
        other_rows   = _q(f"""
            SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
                   COALESCE(SUM(amount), 0) AS income
            FROM {V_INCOME}
            WHERE EXTRACT(YEAR FROM transaction_date) = :year
            GROUP BY m ORDER BY m
        """, year=year)

    payroll_by_m = {r[0]: float(r[1]) for r in payroll_rows}
    other_by_m   = {r[0]: float(r[1]) for r in other_rows}

    return {
        "months":            MONTH_LABELS,
        "payroll":           [round(payroll_by_m.get(m, 0.0), 2) for m in range(1, 13)],
        "other":             [round(other_by_m.get(m, 0.0),   2) for m in range(1, 13)],
        "has_employer_patterns": bool(cfg.employer_patterns),
    }


# ── Category queries ──────────────────────────────────────────────────────────

def get_spend_by_category(year: int, person: int | None = None) -> dict:
    """Total spend per category for the year, sorted descending."""
    person_filter = "AND :person_id = ANY(person)" if person else ""
    rows = _q(f"""
        SELECT category, cost_type, COALESCE(SUM(amount), 0) AS total
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
        {person_filter}
        GROUP BY category, cost_type
        ORDER BY total DESC
    """, year=year, **({"person_id": person} if person else {}))

    from data.category_rules import load_category_config
    cfg_cat   = load_category_config()
    color_map = {c.name: c.color for c in cfg_cat.categories}

    return {
        "categories": [r[0] for r in rows],
        "cost_types":  [r[1] for r in rows],
        "totals":      [round(float(r[2]), 2) for r in rows],
        "colors":      [color_map.get(r[0], "#d1d5db") for r in rows],
    }


def get_category_trend(year: int, person: int | None = None) -> dict:
    """Monthly spend per category — for stacked bar trend chart."""
    person_filter = "AND :person_id = ANY(person)" if person else ""
    rows = _q(f"""
        SELECT category, EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS total
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
        {person_filter}
        GROUP BY category, m
        ORDER BY category, m
    """, year=year, **({"person_id": person} if person else {}))

    from data.category_rules import load_category_config
    cfg_cat   = load_category_config()
    color_map = {c.name: c.color for c in cfg_cat.categories}

    by_cat: dict[str, list[float]] = {}
    for cat, m, total in rows:
        if cat not in by_cat:
            by_cat[cat] = [0.0] * 12
        by_cat[cat][int(m) - 1] = round(float(total), 2)

    return {
        "months":     MONTH_LABELS,
        "categories": {
            cat: {"values": vals, "color": color_map.get(cat, "#d1d5db")}
            for cat, vals in by_cat.items()
        },
    }


def get_fixed_vs_variable(year: int, person: int | None = None) -> dict:
    """Monthly fixed vs variable spend split."""
    person_filter = "AND :person_id = ANY(person)" if person else ""
    rows = _q(f"""
        SELECT cost_type, EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS total
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
        {person_filter}
        GROUP BY cost_type, m
        ORDER BY cost_type, m
    """, year=year, **({"person_id": person} if person else {}))

    fixed    = [0.0] * 12
    variable = [0.0] * 12
    for ctype, m, total in rows:
        idx = int(m) - 1
        if ctype == "fixed":
            fixed[idx]    = round(float(total), 2)
        else:
            variable[idx] = round(float(total), 2)

    return {
        "months":   MONTH_LABELS,
        "fixed":    fixed,
        "variable": variable,
    }


def get_persons() -> list[str]:
    """Distinct person_name values across all spend (resolved from INTEGER[] user IDs)."""
    rows = _q(f"""
        SELECT DISTINCT u.person_name
        FROM {_SCHEMA}.app_users u
        WHERE u.id IN (
            SELECT DISTINCT unnest(person)
            FROM {V_ALL_SPEND}
            WHERE cardinality(person) > 0
        )
        ORDER BY u.person_name
    """)
    return [r[0] for r in rows]


def get_filter_options(year: int) -> dict:
    """Distinct values for each filterable column for the given year — used by dropdown filters."""
    def _distinct(col: str) -> list[str]:
        rows = _q(f"""
            SELECT DISTINCT {col} FROM {V_ALL_SPEND}
            WHERE EXTRACT(YEAR FROM transaction_date) = :year
              AND {col} IS NOT NULL
            ORDER BY {col}
        """, year=year)
        return [str(r[0]) for r in rows]

    persons_rows = _q(f"""
        SELECT DISTINCT u.person_name
        FROM {_SCHEMA}.app_users u
        WHERE u.id IN (
            SELECT DISTINCT unnest(person)
            FROM {V_ALL_SPEND}
            WHERE EXTRACT(YEAR FROM transaction_date) = :year
              AND cardinality(person) > 0
        )
        ORDER BY u.person_name
    """, year=year)

    return {
        "categories": _distinct("category"),
        "cost_types":  _distinct("cost_type"),
        "banks":       _distinct("bank"),
        "persons":     [r[0] for r in persons_rows],
    }


def get_weekly_transactions(year: int, person: int | None = None, category: str | None = None) -> dict:
    """
    Returns all individual transactions for a full year grouped by ISO week.
    Produces ~52 buckets labelled by the week's Monday date (e.g. "Jan 6").
    """
    from collections import defaultdict
    import datetime as dt

    person_filter   = "AND :person_id = ANY(person)" if person   else ""
    category_filter = "AND category = :category"    if category else ""
    rows = _q(f"""
        SELECT
            transaction_date,
            category,
            description,
            amount
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
          {person_filter}
          {category_filter}
        ORDER BY transaction_date, category, amount DESC
    """, year=year,
       **( {"person_id": person}   if person   else {}),
       **( {"category":  category} if category else {}))

    # Key = Monday of the ISO week, formatted as "Jan 6"
    by_week: dict[str, list[dict]] = defaultdict(list)
    week_order: list[str] = []

    for txn_date, category, description, amount in rows:
        if hasattr(txn_date, 'isocalendar'):
            # Monday of that week
            monday = txn_date - dt.timedelta(days=txn_date.weekday())
            label  = monday.strftime("%b %-d")
        else:
            label = str(txn_date)[:10]

        if label not in week_order:
            week_order.append(label)
        by_week[label].append({
            "category":    category or "Other",
            "description": description or "",
            "amount":      round(float(amount), 2),
        })

    return {
        "weeks":   week_order,
        "by_week": dict(by_week),
    }



def _parse_search(search: str):
    """
    Parse search string into (col_filters, free_text, date_from, date_to).
    Supports: category=groceries  type=fixed  bank=citi  amount=150
              from=2025-01-01  to=2025-03-31  (or start= / end=)
    Unrecognised tokens → description search.
    """
    ALIASES = {
        "category":    "category",
        "cat":         "category",
        "type":        "cost_type",
        "cost_type":   "cost_type",
        "bank":        "bank",
        "account":     "bank",
        "person":      "person",
        "date":        "transaction_date",
        "amount":      "amount",
        "amt":         "amount",
        "name":        "description",
        "desc":        "description",
        "description": "description",
    }
    DATE_FROM_KEYS = {"from", "start", "after",  "date_from", "datefrom"}
    DATE_TO_KEYS   = {"to",   "end",   "before", "date_to",   "dateto"}

    import re
    col_filters = []
    remaining   = []
    date_from   = None
    date_to     = None

    for token in re.split(r'\s+', search.strip()):
        if '=' in token:
            key, _, val = token.partition('=')
            kl = key.lower()
            if kl in DATE_FROM_KEYS and val:
                date_from = val
                continue
            if kl in DATE_TO_KEYS and val:
                date_to = val
                continue
            col = ALIASES.get(kl)
            if col and val:
                col_filters.append((col, val))
                continue
        if token:
            remaining.append(token)

    free_text = ' '.join(remaining) or None
    return col_filters, free_text, date_from, date_to


def gettransactions_table(
    year: int,
    person: int | None = None,
    search: str = "",
    category: str | None = None,
    filters: dict | None = None,   # explicit filters from simple mode — bypasses _parse_search
) -> list[dict]:
    """
    Returns all spend transactions for the year as a list of row dicts.
    `filters` dict (from simple mode): keys = cost_type, bank, from_date, to_date
    `search` string (from advanced mode): supports category=x  type=x  from=  to=  free text
    """
    person_filter   = "AND :person_id = ANY(person)" if person   else ""
    category_filter = "AND category = :category"     if category else ""

    extra_clauses: list[str] = []
    extra_params:  dict      = {}

    if filters:
        # Simple mode — direct param binding, safe for values with spaces
        if filters.get('category'):
            extra_clauses.append("category ILIKE :f_category")
            extra_params['f_category'] = f"%{filters['category']}%"
        if filters.get('cost_type'):
            extra_clauses.append("cost_type ILIKE :f_cost_type")
            extra_params['f_cost_type'] = f"%{filters['cost_type']}%"
        if filters.get('bank'):
            extra_clauses.append("bank ILIKE :f_bank")
            extra_params['f_bank'] = f"%{filters['bank']}%"
        if filters.get('from_date'):
            extra_clauses.append("transaction_date >= :f_from")
            extra_params['f_from'] = filters['from_date']
        if filters.get('to_date'):
            extra_clauses.append("transaction_date <= :f_to")
            extra_params['f_to'] = filters['to_date']
    else:
        # Advanced / search string mode
        col_filters, free_text, date_from, date_to = _parse_search(search)

        # Group filters by column so same-column entries become OR, cross-column is AND
        # e.g. category=groceries category=gas → (category ILIKE %groceries% OR category ILIKE %gas%)
        from collections import defaultdict
        grouped: dict[str, list[str]] = defaultdict(list)
        for col, val in col_filters:
            grouped[col].append(val)

        for col, vals in grouped.items():
            or_parts = []
            for i, val in enumerate(vals):
                key = f"sf_{col}_{i}"
                if col == "amount":
                    or_parts.append(f"CAST(amount AS TEXT) ILIKE :{key}")
                elif col == "transaction_date":
                    or_parts.append(f"CAST(transaction_date AS TEXT) ILIKE :{key}")
                else:
                    or_parts.append(f"{col} ILIKE :{key}")
                extra_params[key] = f"%{val}%"
            # Wrap in parens if multiple values for this column
            clause = " OR ".join(or_parts)
            extra_clauses.append(f"({clause})" if len(or_parts) > 1 else clause)

        if date_from:
            extra_clauses.append("transaction_date >= :date_from")
            extra_params["date_from"] = date_from
        if date_to:
            extra_clauses.append("transaction_date <= :date_to")
            extra_params["date_to"] = date_to
        if free_text:
            extra_clauses.append("description ILIKE :free_text")
            extra_params["free_text"] = f"%{free_text}%"

    extra_filter = ("AND " + " AND ".join(extra_clauses)) if extra_clauses else ""

    rows = _q(f"""
        SELECT
            transaction_date,
            description,
            category,
            cost_type,
            amount,
            bank,
            (
                SELECT STRING_AGG(u.person_name, ', ' ORDER BY u.id)
                FROM {_SCHEMA}.app_users u
                WHERE u.id = ANY(s.person)
            ) AS person_names
        FROM {V_ALL_SPEND} s
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
          {person_filter}
          {category_filter}
          {extra_filter}
        ORDER BY transaction_date DESC
    """, year=year,
       **( {"person_id": person}   if person   else {}),
       **( {"category":  category} if category else {}),
       **extra_params)

    return [
        {
            "date":        r[0].strftime("%Y-%m-%d") if hasattr(r[0], "strftime") else str(r[0]),
            "description": r[1] or "",
            "category":    r[2] or "Other",
            "cost_type":   r[3] or "",
            "amount":      round(float(r[4]), 2),
            "bank":        r[5] or "",
            "person":      r[6] or "",
        }
        for r in rows
    ]
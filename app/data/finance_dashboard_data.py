"""
data/finance_dashboard_data.py

Queries the purpose-built views:
  v_all_spend     — deduplicated spend (credit purchases + debit outflows)
  v_credit_spend  — credit card purchases only
  v_debit_spend   — checking outflows only (transfers/payments excluded)
  v_income        — employer payroll deposits

Person filtering
────────────────
All query functions accept `persons: list[int] | None`.
  None or []  → no filter (show all people)
  [1, 2]      → show only transactions where any of those user IDs appears
                in the transaction's person INTEGER[] column.
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
        msg = str(e)
        if "does not exist" in msg or "UndefinedTable" in msg or "undefined_table" in msg:
            return []
        raise

def _kpi(spend: float, income: float) -> dict:
    return {"spend": round(spend, 2), "income": round(income, 2),
            "net": round(income - spend, 2)}

def _persons_filter(persons: list[int] | None) -> tuple[str, dict]:
    """
    Returns (sql_clause, params_dict) for filtering transactions by person IDs.
    Uses PostgreSQL array overlap: transactions where any of the given user IDs
    is present in the transaction's person INTEGER[] column.
    persons=None or persons=[] → returns ("", {}) — no filter applied.
    """
    if not persons:
        return "", {}
    arr = "{" + ",".join(str(int(p)) for p in persons) + "}"
    return "AND person && CAST(:_persons AS integer[])", {"_persons": arr}


# ── Available years ───────────────────────────────────────────────────────────

def get_years() -> list[int]:
    rows = _q(
        f"SELECT DISTINCT EXTRACT(YEAR FROM transaction_date)::INT AS y "
        f"FROM {V_ALL_SPEND} ORDER BY y DESC"
    )
    return [r[0] for r in rows] or [datetime.now().year]


# ── KPI helpers ───────────────────────────────────────────────────────────────

def _spend_income_kpi(
    view: str,
    year: int | None = None,
    persons: list[int] | None = None,
) -> dict:
    person_clause, person_params = _persons_filter(persons)
    year_clause = "EXTRACT(YEAR FROM transaction_date) = :year" if year else "1=1"
    year_params = {"year": year} if year else {}

    rows = _q(f"""
        SELECT COALESCE(SUM(amount), 0) AS spend
        FROM {view}
        WHERE {year_clause} {person_clause}
    """, **year_params, **person_params)
    spend = float(rows[0][0]) if rows else 0.0

    income_rows = _q(f"""
        SELECT COALESCE(SUM(amount), 0) AS income
        FROM {V_INCOME}
        WHERE {year_clause} {person_clause}
    """, **year_params, **person_params)
    income = float(income_rows[0][0]) if income_rows else 0.0

    return _kpi(spend, income)


def get_alltime_kpi(persons: list[int] | None = None) -> dict:
    return _spend_income_kpi(V_ALL_SPEND, persons=persons)

def get_yearly_kpi(year: int, persons: list[int] | None = None) -> dict:
    return _spend_income_kpi(V_ALL_SPEND, year, persons=persons)


# ── Monthly spend + income series ─────────────────────────────────────────────

def get_monthly_spend_series(year: int, persons: list[int] | None = None) -> dict:
    person_clause, person_params = _persons_filter(persons)

    spend_rows = _q(f"""
        SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS spend
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year {person_clause}
        GROUP BY m ORDER BY m
    """, year=year, **person_params)

    income_rows = _q(f"""
        SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS income
        FROM {V_INCOME}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year {person_clause}
        GROUP BY m ORDER BY m
    """, year=year, **person_params)

    spend_by_m  = {r[0]: float(r[1]) for r in spend_rows}
    income_by_m = {r[0]: float(r[1]) for r in income_rows}

    spend_vals  = [round(spend_by_m.get(m, 0.0),  2) for m in range(1, 13)]
    income_vals = [round(income_by_m.get(m, 0.0), 2) for m in range(1, 13)]

    # Rolling budget: each month = leftover from previous + (income - spend)
    budget: list[float] = []
    rolling = 0.0
    for s, inc in zip(spend_vals, income_vals):
        if s == 0 and inc == 0:
            budget.append(None)
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

def get_year_over_year_monthly_spend_series(
    year_back: int = 2,
    persons: list[int] | None = None,
) -> dict:
    from datetime import date

    today = date.today()
    start = date(today.year - year_back, 1, 1)
    end   = today.replace(day=1)

    person_clause, person_params = _persons_filter(persons)

    spend_rows = _q(f"""
        SELECT DATE_TRUNC('month', transaction_date)::DATE AS mo,
               COALESCE(SUM(amount), 0)
        FROM {V_ALL_SPEND}
        WHERE transaction_date >= :start {person_clause}
        GROUP BY mo ORDER BY mo
    """, start=start, **person_params)

    income_rows = _q(f"""
        SELECT DATE_TRUNC('month', transaction_date)::DATE AS mo,
               COALESCE(SUM(amount), 0)
        FROM {V_INCOME}
        WHERE transaction_date >= :start {person_clause}
        GROUP BY mo ORDER BY mo
    """, start=start, **person_params)

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

def get_spend_per_bank_series(year: int, persons: list[int] | None = None) -> dict:
    person_clause, person_params = _persons_filter(persons)

    rows = _q(f"""
        SELECT bank,
               EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS spend
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year {person_clause}
        GROUP BY bank, m
        ORDER BY bank, m
    """, year=year, **person_params)

    banks: dict[str, list[float]] = {}
    for bank, m, spend in rows:
        if bank not in banks:
            banks[bank] = [0.0] * 12
        banks[bank][int(m) - 1] = round(float(spend), 2)

    return {"months": MONTH_LABELS, "banks": banks}


# ── Employer income series ────────────────────────────────────────────────────

def get_employer_income_series(year: int, persons: list[int] | None = None) -> dict:
    from services.transaction_config import load_config
    cfg = load_config()

    person_clause, person_params = _persons_filter(persons)

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
              {person_clause}
            GROUP BY m ORDER BY m
        """, year=year, **person_params)

        other_rows = _q(f"""
            SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
                   COALESCE(SUM(amount), 0) AS income
            FROM {V_INCOME}
            WHERE EXTRACT(YEAR FROM transaction_date) = :year
              AND NOT {employer_clause}
              {person_clause}
            GROUP BY m ORDER BY m
        """, year=year, **person_params)
    else:
        payroll_rows = []
        other_rows   = _q(f"""
            SELECT EXTRACT(MONTH FROM transaction_date)::INT AS m,
                   COALESCE(SUM(amount), 0) AS income
            FROM {V_INCOME}
            WHERE EXTRACT(YEAR FROM transaction_date) = :year {person_clause}
            GROUP BY m ORDER BY m
        """, year=year, **person_params)

    payroll_by_m = {r[0]: float(r[1]) for r in payroll_rows}
    other_by_m   = {r[0]: float(r[1]) for r in other_rows}

    return {
        "months":                MONTH_LABELS,
        "payroll":               [round(payroll_by_m.get(m, 0.0), 2) for m in range(1, 13)],
        "other":                 [round(other_by_m.get(m, 0.0),   2) for m in range(1, 13)],
        "has_employer_patterns": bool(cfg.employer_patterns),
    }


# ── Category queries ──────────────────────────────────────────────────────────

def get_spend_by_category(year: int, persons: list[int] | None = None) -> dict:
    """Total spend per category for the year, sorted descending."""
    person_clause, person_params = _persons_filter(persons)

    rows = _q(f"""
        SELECT category, cost_type, COALESCE(SUM(amount), 0) AS total
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year {person_clause}
        GROUP BY category, cost_type
        ORDER BY total DESC
    """, year=year, **person_params)

    from data.category_rules import load_category_config
    cfg_cat   = load_category_config()
    color_map = {c.name: c.color for c in cfg_cat.categories}

    return {
        "categories": [r[0] for r in rows],
        "cost_types":  [r[1] for r in rows],
        "totals":      [round(float(r[2]), 2) for r in rows],
        "colors":      [color_map.get(r[0], "#d1d5db") for r in rows],
    }


def get_category_trend(year: int, persons: list[int] | None = None) -> dict:
    """Monthly spend per category — for stacked bar trend chart."""
    person_clause, person_params = _persons_filter(persons)

    rows = _q(f"""
        SELECT category, EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS total
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year {person_clause}
        GROUP BY category, m
        ORDER BY category, m
    """, year=year, **person_params)

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


def get_fixed_vs_variable(year: int, persons: list[int] | None = None) -> dict:
    """Monthly fixed vs variable spend split."""
    person_clause, person_params = _persons_filter(persons)

    rows = _q(f"""
        SELECT cost_type, EXTRACT(MONTH FROM transaction_date)::INT AS m,
               COALESCE(SUM(amount), 0) AS total
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year {person_clause}
        GROUP BY cost_type, m
        ORDER BY cost_type, m
    """, year=year, **person_params)

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


def get_persons_with_ids() -> list[dict]:
    """Return [{id, name}, …] for users that appear in any spend transaction."""
    rows = _q(f"""
        SELECT DISTINCT u.id, u.person_name
        FROM {_SCHEMA}.app_users u
        WHERE u.id IN (
            SELECT DISTINCT unnest(person)
            FROM {V_ALL_SPEND}
            WHERE cardinality(person) > 0
        )
        ORDER BY u.person_name
    """)
    return [{'id': r[0], 'name': r[1]} for r in rows]


def get_spend_by_person_monthly(
    year: int,
    date_from=None,
    date_to=None,
) -> dict:
    """
    Monthly spend per person.

    When date_from/date_to are provided the query spans those dates and
    month labels are generated dynamically (e.g. trailing-months mode).
    Otherwise the query covers the full calendar year given by `year`.

    Returns {'months': [...], 'persons': {name: [v1..vN], ...}}.
    """
    if date_from is not None and date_to is not None:
        rows = _q(f"""
            SELECT
                u.person_name,
                TO_CHAR(transaction_date, 'Mon YY') AS label,
                DATE_TRUNC('month', transaction_date) AS month_start,
                COALESCE(SUM(amount), 0) AS spend
            FROM {V_ALL_SPEND} s
            JOIN LATERAL unnest(s.person) AS pid ON TRUE
            JOIN {_SCHEMA}.app_users u ON u.id = pid
            WHERE transaction_date >= :df AND transaction_date < :dt
            GROUP BY u.person_name, label, month_start
            ORDER BY u.person_name, month_start
        """, df=date_from, dt=date_to)

        # Single pass: collect ordered month labels and per-person spend together
        month_order: list[str] = []
        seen_labels: set[str] = set()
        by_person: dict[str, dict[str, float]] = {}
        for row in rows:
            name, label, spend = row[0], row[1], row[3]
            if label not in seen_labels:
                month_order.append(label)
                seen_labels.add(label)
            if name not in by_person:
                by_person[name] = {}
            by_person[name][label] = round(float(spend), 2)

        return {
            'months': month_order,
            'persons': {
                name: [vals.get(lbl, 0.0) for lbl in month_order]
                for name, vals in by_person.items()
            },
        }

    # ── Full calendar year ────────────────────────────────────────────────────
    rows = _q(f"""
        SELECT
            u.person_name,
            EXTRACT(MONTH FROM transaction_date)::INT AS m,
            COALESCE(SUM(amount), 0) AS spend
        FROM {V_ALL_SPEND} s
        JOIN LATERAL unnest(s.person) AS pid ON TRUE
        JOIN {_SCHEMA}.app_users u ON u.id = pid
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
        GROUP BY u.person_name, m
        ORDER BY u.person_name, m
    """, year=year)

    by_person_yr: dict[str, list[float]] = {}
    for name, m, spend in rows:
        if name not in by_person_yr:
            by_person_yr[name] = [0.0] * 12
        by_person_yr[name][int(m) - 1] = round(float(spend), 2)

    return {'months': MONTH_LABELS, 'persons': by_person_yr}


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


def get_weekly_transactions(
    year: int,
    persons: list[int] | None = None,
    category: str | None = None,
) -> dict:
    """
    Returns all individual transactions for a full year grouped by ISO week.
    Produces ~52 buckets labelled by the week's Monday date (e.g. "Jan 6").
    """
    from collections import defaultdict
    import datetime as dt

    person_clause, person_params = _persons_filter(persons)
    category_filter = "AND category = :category" if category else ""

    rows = _q(f"""
        SELECT
            transaction_date,
            category,
            description,
            amount
        FROM {V_ALL_SPEND}
        WHERE EXTRACT(YEAR FROM transaction_date) = :year
          {person_clause}
          {category_filter}
        ORDER BY transaction_date, category, amount DESC
    """, year=year, **person_params,
       **( {"category": category} if category else {}))

    by_week: dict[str, list[dict]] = defaultdict(list)
    week_order: list[str] = []

    for txn_date, txn_cat, description, amount in rows:
        if hasattr(txn_date, 'isocalendar'):
            monday = txn_date - dt.timedelta(days=txn_date.weekday())
            label  = monday.strftime("%b %-d")
        else:
            label = str(txn_date)[:10]

        if label not in week_order:
            week_order.append(label)
        by_week[label].append({
            "category":    txn_cat or "Other",
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
    persons: list[int] | None = None,
    search: str = "",
    category: str | None = None,
    filters: dict | None = None,
) -> list[dict]:
    """
    Returns all spend transactions for the year as a list of row dicts.
    `filters` dict (simple mode): keys = cost_type, bank, from_date, to_date, category
    `search` string (advanced mode): supports category=x  type=x  from=  to=  free text
    """
    person_clause, person_params = _persons_filter(persons)
    category_filter = "AND category = :category" if category else ""

    extra_clauses: list[str] = []
    extra_params:  dict      = {}

    if filters:
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
        col_filters, free_text, date_from, date_to = _parse_search(search)

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
          {person_clause}
          {category_filter}
          {extra_filter}
        ORDER BY transaction_date DESC
    """, year=year, **person_params,
       **( {"category": category} if category else {}),
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

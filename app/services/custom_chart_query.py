"""
services/custom_chart_query.py

Query builder and executor for custom charts.

Public API
──────────
    get_available_sources() → list[str]
    get_source_columns(source) → list[str]
    execute_chart_query(config) → dict   {"x": [...], "series": {"Name": [...]}}
"""

from __future__ import annotations

from datetime import date as _date
from sqlalchemy import text

from data.db import get_engine, get_schema

# Sentinel: pass to execute_chart_query date_from/date_to to signal
# "resolve time range from config".  Explicit None = no date filter.
_UNSET = object()


ALLOWED_SOURCES = frozenset([
    'v_all_spend', 'v_credit_spend', 'v_debit_spend',
    'v_income', 'v_credit_payments',
])
DATE_COLUMNS = frozenset(['transaction_date'])
ALLOWED_AGGREGATIONS = frozenset(['sum', 'count', 'avg'])
ALLOWED_OPS = frozenset(['=', '!=', '>', '<', '>=', '<=', 'LIKE', 'NOT LIKE'])
TRUNC_VALUES = frozenset(['day', 'week', 'month', 'quarter', 'year'])

_col_cache: dict[str, list[str]] = {}
_person_cache: dict[int, str] | None = None

PERSON_COLUMNS = frozenset(['person'])


def _get_person_name_map() -> dict[int, str]:
    """Return {user_id: display_name} for all users. Cached for the process lifetime."""
    global _person_cache
    if _person_cache is not None:
        return _person_cache
    schema = get_schema()
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT id, display_name FROM {schema}.app_users ORDER BY id"
        )).fetchall()
    _person_cache = {r[0]: r[1] for r in rows}
    return _person_cache


def _fmt_person(val, person_map: dict) -> str:
    """Convert an INTEGER[] person value to a display name string."""
    if val is None:
        return '(none)'
    if isinstance(val, list):
        ids = [int(i) for i in val if i is not None]
    elif isinstance(val, str):
        inner = val.strip('{}')
        ids = [int(x) for x in inner.split(',') if x.strip().lstrip('-').isdigit()] if inner else []
    else:
        return str(val)
    names = [person_map.get(i, str(i)) for i in ids]
    return ', '.join(names) if names else '(none)'


def _resolve_time_range(config: dict) -> tuple[_date | None, _date | None]:
    """Return (date_from, date_to) from config's time_mode, or (None, None) for all-time."""
    mode = config.get('time_mode', 'all_time')
    if mode == 'trailing':
        months = int(config.get('trailing_months', 12))
        today  = _date.today()
        total  = today.year * 12 + (today.month - 1) - months
        y, m   = divmod(total, 12)
        return _date(y, m + 1, 1), today
    if mode == 'year':
        year = config.get('fixed_year')
        if year:
            y = int(year)
            return _date(y, 1, 1), _date(y, 12, 31)
    if mode == 'date_range':
        def _parse(v) -> _date | None:
            try:
                return _date.fromisoformat(str(v)) if v else None
            except ValueError:
                return None
        return _parse(config.get('date_from')), _parse(config.get('date_to'))
    return None, None  # all_time


COMPUTED_OVERLAYS = frozenset(['rolling_surplus'])


def _compute_rolling_surplus(
    x_expr: str,
    format_dates: bool,
    x_ordered: list,
    date_from,
    date_to,
    schema: str,
) -> list:
    """Cumulative (income − spend) accumulated across x_ordered buckets."""
    params: dict = {}
    where_parts: list[str] = []
    if date_from is not None:
        where_parts.append("transaction_date >= :__df")
        params['__df'] = date_from
    if date_to is not None:
        where_parts.append("transaction_date <= :__dt")
        params['__dt'] = date_to
    where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    def _run(source: str) -> dict:
        sql = (
            f"SELECT {x_expr} AS x_val, SUM(amount) AS y_val "
            f"FROM {schema}.{source} {where_sql} "
            f"GROUP BY x_val ORDER BY x_val ASC"
        )
        with get_engine().connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return {
            (_fmt_date(r[0]) if format_dates else (str(r[0]) if r[0] is not None else '')):
            float(r[1]) if r[1] is not None else 0.0
            for r in rows
        }

    spend_map  = _run('v_all_spend')
    income_map = _run('v_income')

    result: list = []
    rolling = 0.0
    for x in x_ordered:
        s   = spend_map.get(x, 0.0)
        inc = income_map.get(x, 0.0)
        if s == 0.0 and inc == 0.0:
            result.append(None)
        else:
            rolling = round(rolling + inc - s, 2)
            result.append(rolling)
    return result


def _execute_overlay_queries(
    overlay_configs: list,
    x_expr: str,
    format_dates: bool,
    x_ordered: list,
    date_from,
    date_to,
    schema: str,
) -> dict:
    """Execute one series per overlay config and align to x_ordered."""
    overlay: dict[str, list] = {}
    for ov in overlay_configs:
        label = ov.get('label') or 'Line'

        # ── Computed series ───────────────────────────────────────────────────
        computed = ov.get('computed')
        if computed == 'rolling_surplus':
            overlay[label] = _compute_rolling_surplus(
                x_expr, format_dates, x_ordered, date_from, date_to, schema,
            )
            continue
        if computed:
            continue  # unknown computed type — skip

        # ── Query series ──────────────────────────────────────────────────────
        ov_source = ov.get('data_source', 'v_all_spend')
        if ov_source not in ALLOWED_SOURCES:
            continue
        ov_y   = ov.get('y_column', 'amount')
        ov_agg = (ov.get('y_agg') or 'sum').lower()
        if ov_agg not in ALLOWED_AGGREGATIONS:
            continue
        ov_cols = frozenset(get_source_columns(ov_source))
        if ov_y not in ov_cols:
            continue
        ov_where_parts: list[str] = []
        ov_params: dict = {}
        if date_from is not None:
            ov_where_parts.append("transaction_date >= :__odf")
            ov_params['__odf'] = date_from
        if date_to is not None:
            ov_where_parts.append("transaction_date <= :__odt")
            ov_params['__odt'] = date_to
        ov_where_sql = ('WHERE ' + ' AND '.join(ov_where_parts)) if ov_where_parts else ''
        ov_sql = (
            f"SELECT {x_expr} AS x_val, {ov_agg}({ov_y}) AS y_val "
            f"FROM {schema}.{ov_source} {ov_where_sql} "
            f"GROUP BY x_val ORDER BY x_val ASC"
        )
        with get_engine().connect() as conn:
            ov_rows = conn.execute(text(ov_sql), ov_params).fetchall()
        ov_map = {
            (_fmt_date(r[0]) if format_dates else (str(r[0]) if r[0] is not None else '')):
            float(r[1]) if r[1] is not None else 0.0
            for r in ov_rows
        }
        overlay[label] = [ov_map.get(x, 0.0) for x in x_ordered]
    return overlay


def get_available_sources() -> list[str]:
    return sorted(ALLOWED_SOURCES)


def get_source_columns(source: str) -> list[str]:
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"Unknown source: {source!r}")
    if source in _col_cache:
        return _col_cache[source]
    schema = get_schema()
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT column_name
            FROM   information_schema.columns
            WHERE  table_schema = :schema
              AND  table_name   = :tname
            ORDER  BY ordinal_position
        """), {"schema": schema, "tname": source}).fetchall()
    cols = [r[0] for r in rows]
    if cols:  # don't cache empty — view may not exist yet
        _col_cache[source] = cols
    return cols


def execute_chart_query(
    config: dict,
    date_from=_UNSET,
    date_to=_UNSET,
) -> dict:
    source = config.get('data_source', 'v_all_spend')
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"Unknown data_source: {source!r}")

    schema = get_schema()

    # Resolve and validate column names
    available_cols = get_source_columns(source)
    allowed_cols = frozenset(available_cols)

    x_column = config.get('x_column') or 'transaction_date'
    if x_column not in allowed_cols:
        raise ValueError(f"Unknown x_column: {x_column!r}")

    y_column = config.get('y_column') or 'amount'
    if y_column not in allowed_cols:
        raise ValueError(f"Unknown y_column: {y_column!r}")

    y_agg = (config.get('y_agg') or 'sum').lower()
    if y_agg not in ALLOWED_AGGREGATIONS:
        raise ValueError(f"Unknown aggregation: {y_agg!r}")

    series_column = config.get('series_column') or None
    if series_column and series_column not in allowed_cols:
        raise ValueError(f"Unknown series_column: {series_column!r}")

    date_trunc = config.get('date_trunc') or 'month'
    if date_trunc not in TRUNC_VALUES:
        date_trunc = 'month'

    # Build x expression — safe because x_column and date_trunc are whitelist-validated
    if x_column in DATE_COLUMNS and date_trunc:
        x_expr = f"DATE_TRUNC('{date_trunc}', {x_column})::date"
        format_dates = True
    else:
        x_expr = x_column
        format_dates = False

    # Build WHERE clause from filters (only values go into params)
    where_parts = []
    params: dict = {}
    filters = config.get('filters') or []
    for i, f in enumerate(filters):
        col = f.get('column', '')
        op  = f.get('op', '')
        val = f.get('value', '')
        if col not in allowed_cols:
            continue
        if op not in ALLOWED_OPS:
            continue
        param_key = f'fv_{i}'
        where_parts.append(f"{col} {op} :{param_key}")
        params[param_key] = val

    # Resolve time range — explicit override or from config
    if date_from is _UNSET:
        date_from, date_to = _resolve_time_range(config)
    if date_from is not None:
        where_parts.append("transaction_date >= :__df")
        params['__df'] = date_from
    if date_to is not None:
        where_parts.append("transaction_date <= :__dt")
        params['__dt'] = date_to

    where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    # Build GROUP BY and SELECT
    if series_column:
        select_sql = (
            f"SELECT {x_expr} AS x_val, {series_column} AS series_val, "
            f"{y_agg}({y_column}) AS y_val "
            f"FROM {schema}.{source} "
            f"{where_sql} "
            f"GROUP BY x_val, series_val "
            f"ORDER BY x_val ASC, series_val ASC"
        )
    else:
        select_sql = (
            f"SELECT {x_expr} AS x_val, "
            f"{y_agg}({y_column}) AS y_val "
            f"FROM {schema}.{source} "
            f"{where_sql} "
            f"GROUP BY x_val "
            f"ORDER BY x_val ASC"
        )

    with get_engine().connect() as conn:
        rows = conn.execute(text(select_sql), params).fetchall()

    x_is_person      = x_column in PERSON_COLUMNS
    series_is_person = bool(series_column and series_column in PERSON_COLUMNS)
    person_map       = _get_person_name_map() if (x_is_person or series_is_person) else {}

    if series_column:
        result = _pivot_series(rows, format_dates, y_column, person_map, x_is_person, series_is_person)
    else:
        result = _single_series(rows, format_dates, y_column, person_map, x_is_person)

    overlay_cfgs = config.get('overlay_series') or []
    if overlay_cfgs:
        result['overlay'] = _execute_overlay_queries(
            overlay_cfgs, x_expr, format_dates,
            result.get('x', []), date_from, date_to, schema,
        )

    return result


def _fmt_date(val) -> str:
    try:
        return val.strftime('%b %Y')
    except Exception:
        return str(val)


def _single_series(
    rows, format_dates: bool, y_column: str,
    person_map: dict | None = None, x_is_person: bool = False,
) -> dict:
    x_vals = []
    y_vals = []
    for row in rows:
        if x_is_person:
            x = _fmt_person(row[0], person_map or {})
        else:
            x = _fmt_date(row[0]) if format_dates else str(row[0]) if row[0] is not None else ''
        y = float(row[1]) if row[1] is not None else 0.0
        x_vals.append(x)
        y_vals.append(y)
    return {"x": x_vals, "series": {y_column: y_vals}}


def _pivot_series(
    rows, format_dates: bool, y_column: str,
    person_map: dict | None = None, x_is_person: bool = False, series_is_person: bool = False,
) -> dict:
    # Collect ordered x values and series names
    x_ordered: list = []
    seen_x: set = set()
    series_names: list[str] = []
    seen_series: set = set()

    raw: dict[str, dict] = {}  # series_name -> {x_val: y_val}
    _pmap = person_map or {}

    for row in rows:
        x_raw = row[0]
        y_val  = float(row[2]) if row[2] is not None else 0.0

        if series_is_person:
            s_name = _fmt_person(row[1], _pmap)
        else:
            s_name = str(row[1]) if row[1] is not None else '(none)'

        if x_is_person:
            x_key = _fmt_person(x_raw, _pmap)
        else:
            x_key = _fmt_date(x_raw) if format_dates else str(x_raw) if x_raw is not None else ''

        if x_key not in seen_x:
            seen_x.add(x_key)
            x_ordered.append(x_key)
        if s_name not in seen_series:
            seen_series.add(s_name)
            series_names.append(s_name)
            raw[s_name] = {}
        raw[s_name][x_key] = y_val

    series_out: dict[str, list] = {}
    for s in series_names:
        series_out[s] = [raw[s].get(x, 0.0) for x in x_ordered]

    return {"x": x_ordered, "series": series_out}

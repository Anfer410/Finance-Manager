from nicegui import ui
from styles.dashboards import _GRID, _TT_AXIS, _LEGEND, _C_SPEND, _C_INCOME, _C_PAYROLL, _C_NET_POS, _C_NET_NEG, _BANK_COLORS

# ── KPI card ──────────────────────────────────────────────────────────────────

def _kpi_card(title: str, icon: str, kpi: dict) -> None:
    net = kpi['net']
    net_color = _C_NET_POS if net >= 0 else _C_NET_NEG
    with ui.element('div').classes('card flex-1').style('min-width:200px'):
        with ui.row().classes('items-center justify-between mb-3'):
            ui.label(title).classes('label-text')
            ui.icon(icon).style('font-size:1.2rem;color:var(--muted-fg)')
        with ui.row().classes('items-center justify-between'):
            ui.label('Spend').classes('text-xs text-muted')
            ui.label(f"${kpi['spend']:,.0f}").classes('text-sm font-semibold').style(f'color:{_C_SPEND}')
        with ui.row().classes('items-center justify-between'):
            ui.label('Income').classes('text-xs text-muted')
            ui.label(f"${kpi['income']:,.0f}").classes('text-sm font-semibold').style(f'color:{_C_INCOME}')
        ui.separator().classes('my-2')
        ui.label(f"{'▲' if net >= 0 else '▼'} ${abs(net):,.0f}") \
            .classes('text-xl font-bold').style(f'color:{net_color}')
        ui.label('net').classes('text-xs text-muted')


# ── Chart builders ────────────────────────────────────────────────────────────

def _spend_income_chart(series: dict) -> None:
    import json
    budget      = series.get('budget', [None] * 12)
    budget_json = json.dumps(budget)

    ui.echart({
        'tooltip': {
            **_TT_AXIS, 'axisPointer': {'type': 'shadow'},
            ':formatter': f"""params => {{
                let lines = params.map(p => {{
                    if (p.value == null) return null;
                    let abs = '$' + Math.abs(p.value).toLocaleString(undefined, {{maximumFractionDigits:0}});
                    let sign = p.seriesName === 'Budget' ? (p.value >= 0 ? '▲ ' : '▼ ') : '';
                    return p.marker + p.seriesName + ': ' + sign + abs;
                }}).filter(Boolean);
                return params[0].name + '<br/>' + lines.join('<br/>');
            }}""",
        },
        'legend': {**_LEGEND, 'data': ['Spend', 'Income', 'Budget'], 'left': 'center', 'top': 0},
        'grid': _GRID,
        'xAxis': {
            'type': 'category', 'data': series['months'],
            'axisLine': {'lineStyle': {'color': '#e4e4e7'}},
            'axisTick': {'show': False},
            'axisLabel': {'color': '#71717a', 'fontSize': 11},
        },
        'yAxis': {
            'type': 'value',
            'splitLine': {'lineStyle': {'color': '#f4f4f5', 'type': 'dashed'}},
            'axisLabel': {':formatter': 'v => "$" + v.toLocaleString()', 'color': '#71717a', 'fontSize': 11},
        },
        'series': [
            {
                'name': 'Spend', 'type': 'bar',
                'data': series['spend'],
                'barMaxWidth': 28,
                'itemStyle': {'color': _C_SPEND, 'borderRadius': [4, 4, 0, 0]},
                'label': {'show': True, 'position': 'top', 'color': '#71717a', 'fontSize': 10,
                          ':formatter': 'v => v.value > 0 ? "$" + (v.value/1000).toFixed(1) + "k" : ""'},
            },
            {
                'name': 'Income', 'type': 'line', 'smooth': 0.3,
                'data': series['income'],
                'symbol': 'circle', 'symbolSize': 6,
                'lineStyle': {'width': 2.5, 'color': _C_INCOME},
                'itemStyle': {'color': _C_INCOME, 'borderWidth': 2, 'borderColor': '#fff'},
                'label': {'show': True, 'position': 'top', 'color': '#71717a', 'fontSize': 10,
                          ':formatter': 'v => v.value > 0 ? "$" + (v.value/1000).toFixed(1) + "k" : ""'},
            },
            {
                'name': 'Budget', 'type': 'line', 'smooth': 0.3,
                'data': budget,
                'symbol': 'circle', 'symbolSize': 7,
                'lineStyle': {'width': 2, 'type': 'dashed', 'color': '#a78bfa'},
                ':itemStyle': f"""(params) => {{
                    let v = ({budget_json})[params.dataIndex];
                    return {{ color: v >= 0 ? '{_C_NET_POS}' : '{_C_NET_NEG}', borderColor: '#fff', borderWidth: 2 }};
                }}""",
                'label': {
                    'show': True, 'position': 'top', 'fontSize': 10,
                    ':color': f"""(params) => {{
                        let v = ({budget_json})[params.dataIndex];
                        return v == null ? 'transparent' : v >= 0 ? '{_C_NET_POS}' : '{_C_NET_NEG}';
                    }}""",
                    ':formatter': f"""(params) => {{
                        let v = ({budget_json})[params.dataIndex];
                        if (v == null) return '';
                        return (v >= 0 ? '▲' : '▼') + ' $' + Math.abs(v).toLocaleString(undefined, {{maximumFractionDigits:0}});
                    }}""",
                },
                'connectNulls': False,
            },
        ],
    }).classes('w-full').style('height:300px')


def _per_bank_chart(series: dict) -> None:
    banks = series['banks']
    if not banks:
        ui.label('No spend data for this year.').classes('text-sm text-muted py-8 text-center w-full')
        return
    ui.echart({
        'tooltip': {**_TT_AXIS, 'axisPointer': {'type': 'cross'}},
        'legend': {**_LEGEND, 'data': list(banks.keys()), 'left': 'center', 'top': 0},
        'grid': _GRID,
        'xAxis': {
            'type': 'category', 'data': series['months'],
            'axisLine': {'lineStyle': {'color': '#e4e4e7'}},
            'axisTick': {'show': False},
            'axisLabel': {'color': '#71717a', 'fontSize': 11},
        },
        'yAxis': {
            'type': 'value',
            'splitLine': {'lineStyle': {'color': '#f4f4f5', 'type': 'dashed'}},
            'axisLabel': {':formatter': 'v => "$" + v.toLocaleString()', 'color': '#71717a', 'fontSize': 11},
        },
        'series': [
            {
                'name': bank, 'type': 'line', 'smooth': 0.3, 'data': values,
                'symbol': 'circle', 'symbolSize': 5,
                'lineStyle': {'width': 2, 'color': _BANK_COLORS[i % len(_BANK_COLORS)]},
                'itemStyle': {'color': _BANK_COLORS[i % len(_BANK_COLORS)]},
                'emphasis': {'focus': 'series'},
                'label': {'show': True, 'position': 'top', 'color': '#71717a', 'fontSize': 10,
                          ':formatter': 'v => v.value > 0 ? "$" + (v.value/1000).toFixed(1) + "k" : ""'},
            }
            for i, (bank, values) in enumerate(banks.items())
        ],
    }).classes('w-full').style('height:300px')


def _employer_income_chart(series: dict) -> None:
    legend  = []
    charts  = []

    _label = {'show': True, 'position': 'inside', 'color': '#fff', 'fontSize': 10,
               ':formatter': 'v => v.value > 0 ? "$" + (v.value/1000).toFixed(1) + "k" : ""'}
    _top_label = {'show': True, 'position': 'top', 'color': '#71717a', 'fontSize': 10,
                  ':formatter': 'v => v.value > 0 ? "$" + (v.value/1000).toFixed(1) + "k" : ""'}

    if series['has_employer_patterns']:
        legend.append('Payroll')
        charts.append({
            'name': 'Payroll', 'type': 'bar', 'data': series['payroll'],
            'stack': 'income', 'barMaxWidth': 28,
            'itemStyle': {'color': _C_PAYROLL, 'borderRadius': [0, 0, 0, 0]},
            'label': _label,
        })

    legend.append('Other Income')
    charts.append({
        'name': 'Other Income', 'type': 'bar', 'data': series['other'],
        'stack': 'income', 'barMaxWidth': 28,
        'itemStyle': {
            'color': '#a3e635',
            'borderRadius': [4, 4, 0, 0],
        },
        'label': _top_label,
    })

    ui.echart({
        'tooltip': {**_TT_AXIS, 'axisPointer': {'type': 'shadow'}},
        'legend': {**_LEGEND, 'data': legend, 'left': 'center', 'top': 0},
        'grid': _GRID,
        'xAxis': {
            'type': 'category', 'data': series['months'],
            'axisLine': {'lineStyle': {'color': '#e4e4e7'}},
            'axisTick': {'show': False},
            'axisLabel': {'color': '#71717a', 'fontSize': 11},
        },
        'yAxis': {
            'type': 'value',
            'splitLine': {'lineStyle': {'color': '#f4f4f5', 'type': 'dashed'}},
            'axisLabel': {':formatter': 'v => "$" + v.toLocaleString()', 'color': '#71717a', 'fontSize': 11},
        },
        'series': charts,
    }).classes('w-full').style('height:260px')


# ── Category charts ─────────────────────────────────────────────────────────

def _category_donut(series: dict, inverted: bool = False) -> None:
    if not series["categories"]:
        ui.label("No data.").classes("text-sm text-muted text-center py-8 w-full")
        return
    pie_data = [
        {"value": round(t, 2), "name": cat, "itemStyle": {"color": col}}
        for cat, t, col in zip(series["categories"], series["totals"], series["colors"])
        if t > 0
    ]
    grand_total = sum(d["value"] for d in pie_data)

    if inverted:
        # Show % on each slice, amount in tooltip
        label_cfg = {
            "show": True,
            "position": "outside",
            "color": "#374151",
            "fontSize": 11,
            ":formatter": "p => p.percent + '%'",
        }
        center_text = f"{len(pie_data)} categories"
        tooltip_fmt = 'p => p.name + ": $" + p.value.toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0}) + " (" + p.percent + "%)"'
    else:
        # Default: no slice labels, amount in tooltip
        label_cfg   = {"show": False}
        center_text = f"${grand_total:,.0f}"
        tooltip_fmt = 'p => p.name + ": $" + p.value.toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0}) + " (" + p.percent + "%)"'

    ui.echart({
        "tooltip": {"trigger": "item", ":formatter": tooltip_fmt},
        "legend": {**_LEGEND, "orient": "vertical", "right": "2%", "top": "center",
                   "textStyle": {"fontSize": 11, "color": "#71717a"}},
        "series": [{
            "type": "pie", "radius": ["42%", "68%"],
            "center": ["38%", "50%"],
            "data": pie_data,
            "label": label_cfg,
            "labelLine": {"show": inverted},
            "emphasis": {"itemStyle": {"shadowBlur": 8, "shadowColor": "rgba(0,0,0,0.2)"}},
        }],
        "graphic": [{
            "type": "text",
            "left": "38%", "top": "middle",
            "style": {
                "text": center_text,
                "fontSize": 16 if not inverted else 12,
                "fontWeight": "bold", "fill": "#09090b",
                "textAlign": "center",
            },
        }],
    }).classes("w-full").style("height:300px")


def _category_trend_chart(series: dict, on_category_click=None, active_category: str | None = None) -> None:
    cats = series["categories"]
    if not cats:
        ui.label("No data.").classes("text-sm text-muted text-center py-8 w-full")
        return

    import json
    monthly_totals = [
        round(sum(info["values"][m] for info in cats.values()), 2)
        for m in range(12)
    ]
    monthly_totals_json = json.dumps(monthly_totals)
    cat_names = list(cats.keys())

    cat_items = list(cats.items())
    cat_series = []
    for i, (cat, info) in enumerate(cat_items):
        is_last    = (i == len(cat_items) - 1)
        is_active  = active_category is None or cat == active_category
        color      = info["color"] if is_active else "#e5e7eb"
        entry = {
            "name": cat, "type": "bar", "stack": "cat",
            "data": info["values"],
            "itemStyle": {"color": color},
            "emphasis": {"focus": "series"},
        }
        if is_last:
            entry["label"] = {
                "show": True, "position": "top",
                "color": "#71717a", "fontSize": 10, "fontWeight": "bold",
                ":formatter": f"(v => (w => w > 0 ? '$' + w.toLocaleString(undefined,{{maximumFractionDigits:0}}) : '')(({monthly_totals_json})[v.dataIndex]))",
            }
        else:
            entry["label"] = {"show": False}
        cat_series.append(entry)

    chart = ui.echart({
        "tooltip": {
            **_TT_AXIS, "axisPointer": {"type": "shadow"},
            ":formatter": """params => {
                let visible = params.filter(p => p.value > 0);
                let total   = visible.reduce((s, p) => s + p.value, 0);
                let lines   = visible.map(p => p.marker + p.seriesName + ': $' + p.value.toLocaleString(undefined,{maximumFractionDigits:0}));
                lines.push('<b>Total: $' + total.toLocaleString(undefined,{maximumFractionDigits:0}) + '</b>');
                return params[0].name + '<br/>' + lines.join('<br/>');
            }""",
        },
        "legend": {**_LEGEND, "top": 0, "left": "center",
                   "data": cat_names, "textStyle": {"fontSize": 10}},
        "grid": _GRID,
        "xAxis": {
            "type": "category", "data": series["months"],
            "axisLine": {"lineStyle": {"color": "#e4e4e7"}},
            "axisTick": {"show": False},
            "axisLabel": {"color": "#71717a", "fontSize": 11},
        },
        "yAxis": {
            "type": "value",
            "splitLine": {"lineStyle": {"color": "#f4f4f5", "type": "dashed"}},
            "axisLabel": {":formatter": "v => '$' + v.toLocaleString()", "color": "#71717a", "fontSize": 11},
        },
        "series": cat_series,
    }, on_point_click=lambda e: on_category_click(e.series_name) if on_category_click and e.series_name != '_total' else None
    ).classes("w-full").style("height:320px; cursor:pointer")


def _fixed_vs_variable_chart(series: dict) -> None:
    ui.echart({
        "tooltip": {**_TT_AXIS, "axisPointer": {"type": "shadow"}},
        "legend": {**_LEGEND, "data": ["Fixed", "Variable"], "left": "center", "top": 0},
        "grid": _GRID,
        "xAxis": {
            "type": "category", "data": series["months"],
            "axisLine": {"lineStyle": {"color": "#e4e4e7"}},
            "axisTick": {"show": False},
            "axisLabel": {"color": "#71717a", "fontSize": 11},
        },
        "yAxis": {
            "type": "value",
            "splitLine": {"lineStyle": {"color": "#f4f4f5", "type": "dashed"}},
            "axisLabel": {":formatter": "v => '$' + v.toLocaleString()", "color": "#71717a", "fontSize": 11},
        },
        "series": [
            {
                "name": "Fixed", "type": "bar", "data": series["fixed"],
                "barMaxWidth": 24,
                "itemStyle": {"color": "#60a5fa", "borderRadius": [4, 4, 0, 0]},
                "label": {"show": True, "position": "top", "color": "#71717a", "fontSize": 10,
                           ":formatter": 'v => v.value > 0 ? "$" + (v.value/1000).toFixed(1) + "k" : ""'},
            },
            {
                "name": "Variable", "type": "bar", "data": series["variable"],
                "barMaxWidth": 24,
                "itemStyle": {"color": "#fb923c", "borderRadius": [4, 4, 0, 0]},
                "label": {"show": True, "position": "top", "color": "#71717a", "fontSize": 10,
                           ":formatter": 'v => v.value > 0 ? "$" + (v.value/1000).toFixed(1) + "k" : ""'},
            },
        ],
    }).classes("w-full").style("height:280px")


# ── Daily transaction drill-down ─────────────────────────────────────────────

def _weekly_transactions_chart(series: dict, on_category_click=None, active_category: str | None = None) -> None:
    """
    ~52 bars for the year, one per ISO week (Mon–Sun), stacked by category.
    Tooltip shows every individual transaction in that week.
    """
    if not series["weeks"]:
        ui.label("No transactions for this period.").classes("text-sm text-muted text-center py-8 w-full")
        return

    import json
    from services.category_rules import load_category_config
    cfg_cat   = load_category_config()
    color_map = {c.name: c.color for c in cfg_cat.categories}

    weeks   = series["weeks"]
    by_week = series["by_week"]

    # Build a JS-safe lookup: index → list of {cat, desc, amt}
    # Keyed by week index so the tooltip formatter can do txnMap[params[0].dataIndex]
    txn_map = {
        i: [{"cat": t["category"], "desc": t["description"], "amt": t["amount"]}
            for t in by_week.get(w, [])]
        for i, w in enumerate(weeks)
    }
    txn_map_json = json.dumps(txn_map)

    # Collect ordered categories
    seen_cats: list[str] = []
    for txns in by_week.values():
        for t in txns:
            if t["category"] not in seen_cats:
                seen_cats.append(t["category"])

    # One series per category — plain numeric values only
    # The last series carries the total label so it sits right on top of the bar
    weekly_totals = [
        round(sum(t["amount"] for t in by_week.get(w, [])), 2)
        for w in weeks
    ]
    weekly_totals_json = json.dumps(weekly_totals)

    cat_series = []
    for i, cat in enumerate(seen_cats):
        is_last = (i == len(seen_cats) - 1)
        series_entry = {
            "name":        cat,
            "type":        "bar",
            "stack":       "txns",
            "barMaxWidth": 32,
            "data": [
                round(sum(t["amount"] for t in by_week.get(w, []) if t["category"] == cat), 2)
                for w in weeks
            ],
            "itemStyle":  {"color": color_map.get(cat, "#d1d5db") if (active_category is None or cat == active_category) else "#e5e7eb"},
            "emphasis":   {"focus": "series"},
        }
        if is_last:
            series_entry["label"] = {
                "show": True, "position": "top",
                "color": "#374151", "fontSize": 10, "fontWeight": "bold",
                ":formatter": f"(v => (w => w > 0 ? '$' + w.toLocaleString(undefined,{{maximumFractionDigits:0}}) : '')(({weekly_totals_json})[v.dataIndex]))",
            }
        else:
            series_entry["label"] = {"show": False}
        cat_series.append(series_entry)

    # Inline the txn lookup directly into the formatter string
    formatter = f"""params => {{
        const txnMap = {txn_map_json};
        const idx  = params[0].dataIndex;
        const txns = txnMap[idx] || [];
        if (!txns.length) return params[0].axisValue;
        const total = txns.reduce((s, t) => s + t.amt, 0);
        const rows  = txns.map(t =>
            '<tr>' +
            '<td style="padding:2px 8px 2px 0;color:#6b7280;font-size:11px;white-space:nowrap">' + t.cat + '</td>' +
            '<td style="padding:2px 8px 2px 0;font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + t.desc + '</td>' +
            '<td style="padding:2px 0;font-weight:600;text-align:right;white-space:nowrap">$' + t.amt.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</td>' +
            '</tr>'
        ).join('');
        return '<b>Week of ' + params[0].axisValue + '</b>' +
               '<table style="border-collapse:collapse;margin-top:6px;width:100%">' + rows + '</table>' +
               '<div style="border-top:1px solid #e4e4e7;margin-top:6px;padding-top:4px;font-weight:700;text-align:right">Total: $' + total.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div>';
    }}"""

    ui.echart({
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "shadow"},
            "backgroundColor": "#fff",
            "borderColor": "#e4e4e7",
            "extraCssText": "max-height:380px;overflow-y:auto;min-width:300px;",
            "textStyle": {"color": "#09090b", "fontSize": 12},
            ":formatter": formatter,
        },
        "legend": {"show": False},
        "grid": {"left": "3%", "right": "3%", "top": "6%", "bottom": "50px", "containLabel": True},
        "xAxis": {
            "type":      "category",
            "data":      weeks,
            "axisLine":  {"lineStyle": {"color": "#e4e4e7"}},
            "axisTick":  {"show": False},
            "axisLabel": {"color": "#71717a", "fontSize": 10, "rotate": 45},
        },
        "yAxis": {
            "type": "value",
            "splitLine": {"lineStyle": {"color": "#f4f4f5", "type": "dashed"}},
            "axisLabel": {":formatter": "v => '$' + v.toLocaleString()", "color": "#71717a", "fontSize": 10},
        },
        "series": cat_series,
        "dataZoom": [
            {"type": "slider", "start": 0, "end": 100, "height": 18, "bottom": 4,
             "borderColor": "#e4e4e7", "fillerColor": "rgba(99,102,241,0.1)",
             "handleStyle": {"color": "#6366f1"}},
            {"type": "inside"},
        ],
    }, on_point_click=lambda e: on_category_click(e.series_name) if on_category_click and e.series_name != '_total' else None
    ).classes("w-full").style("height:460px; cursor:pointer")


# ── Transactions table ────────────────────────────────────────────────────────

def _transactions_table(rows: list[dict]) -> None:
    """
    Paginated, searchable table of all spend transactions.
    The search input is handled at the dashboard level (triggers a data refresh).
    This function just renders the static table for the given rows.
    """
    from services.category_rules import load_category_config
    cfg_cat   = load_category_config()
    color_map = {c.name: c.color for c in cfg_cat.categories}

    total_spend = sum(r["amount"] for r in rows)

    # Summary row
    with ui.row().classes("w-full items-center justify-between mb-3"):
        ui.label(f"{len(rows):,} transactions").classes("text-sm text-muted")
        ui.label(f"Total: ${total_spend:,.0f}").classes("text-sm font-semibold text-gray-700")

    if not rows:
        ui.label("No transactions found.").classes("text-sm text-muted text-center py-8 w-full")
        return

    columns = [
        {"name": "date",        "label": "Date",        "field": "date",        "sortable": True,  "align": "left"},
        {"name": "description", "label": "Description", "field": "description", "sortable": True,  "align": "left"},
        {"name": "category",    "label": "Category",    "field": "category",    "sortable": True,  "align": "left"},
        {"name": "cost_type",   "label": "Type",        "field": "cost_type",   "sortable": True,  "align": "left"},
        {"name": "bank",        "label": "Account",     "field": "bank",        "sortable": True,  "align": "left"},
        {"name": "person",      "label": "Person",      "field": "person",      "sortable": True,  "align": "left"},
        {"name": "amount",      "label": "Amount",      "field": "amount",      "sortable": True,  "align": "right"},
    ]

    table = ui.table(
        columns=columns,
        rows=rows,
        row_key="description",
        pagination={"rowsPerPage": 25, "sortBy": "date", "descending": True},
    ).classes("w-full text-sm")

    # Custom cell slots — category chip + amount colour
    table.add_slot("body-cell-category", """
        <q-td :props="props">
            <span
                :style="{
                    background: props.row._cat_color + '22',
                    color: props.row._cat_color,
                    border: '1px solid ' + props.row._cat_color + '55',
                    padding: '1px 8px',
                    borderRadius: '9999px',
                    fontSize: '11px',
                    fontWeight: 600,
                    whiteSpace: 'nowrap',
                }"
            >{{ props.value }}</span>
        </q-td>
    """)

    table.add_slot("body-cell-amount", """
        <q-td :props="props" style="text-align:right">
            <span style="font-weight:600;color:#f87171">${{ props.value.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}) }}</span>
        </q-td>
    """)

    table.add_slot("body-cell-cost_type", """
        <q-td :props="props">
            <span :style="{
                background: props.value === 'fixed' ? '#dbeafe' : '#ffedd5',
                color:      props.value === 'fixed' ? '#1d4ed8' : '#c2410c',
                padding: '1px 7px', borderRadius: '9999px', fontSize: '11px', fontWeight: 600
            }">{{ props.value }}</span>
        </q-td>
    """)

    # Inject _cat_color into each row so the slot can use it
    for row in rows:
        row["_cat_color"] = color_map.get(row["category"], "#9ca3af")

    table.rows[:] = rows
    table.update()
"""
components/custom_chart_renderer.py

Converts (config, data) into NiceGUI ui.echart() calls.

Public API
──────────
    render_custom_chart(config, data) → None
"""

from __future__ import annotations

from nicegui import ui

from styles.dashboards import TT_AXIS, LEGEND, BANK_COLORS, legend_pos, grid_for_legend


def _legend_opts(config: dict) -> dict:
    if not config.get('show_legend', True):
        return {'show': False}
    return legend_pos(config.get('legend_position', 'top'))


def _grid_opts(config: dict) -> dict:
    if not config.get('show_legend', True):
        from styles.dashboards import GRID
        return dict(GRID)
    return grid_for_legend(config.get('legend_position', 'top'))


def _label_formatter(label_format: str | None) -> str:
    if label_format == 'dollar':
        return 'v => "$" + v.value.toLocaleString(undefined,{maximumFractionDigits:0})'
    if label_format == 'percent':
        return 'v => v.value + "%"'
    return 'v => v.value.toLocaleString()'


def _tooltip_formatter(label_format: str | None) -> str:
    if label_format == 'dollar':
        row = 'p.marker + " " + p.seriesName + ": $" + (+p.value).toLocaleString(undefined,{maximumFractionDigits:0})'
    elif label_format == 'percent':
        row = 'p.marker + " " + p.seriesName + ": " + p.value + "%"'
    else:
        row = 'p.marker + " " + p.seriesName + ": " + (+p.value).toLocaleString()'
    return (
        'params => {'
        '  if (!Array.isArray(params)) params = [params];'
        f'  return params[0].name + "<br/>" + params.map(p => {row}).join("<br/>");'
        '}'
    )


def _append_overlay_series(series_list: list, data: dict, label_format: str | None) -> None:
    """Append overlay lines from data['overlay'] to an existing series list."""
    overlay = data.get('overlay') or {}
    offset  = len(series_list)
    for i, (name, values) in enumerate(overlay.items()):
        color = BANK_COLORS[(offset + i) % len(BANK_COLORS)]
        entry: dict = {
            'name':   name,
            'type':   'line',
            'data':   values,
            'color':  color,
            'smooth': 0.3,
        }
        if label_format and label_format != 'none':
            entry['label'] = {':formatter': _label_formatter(label_format)}
        series_list.append(entry)


def _build_series_list(data: dict, chart_type: str, label_format: str | None) -> list[dict]:
    series_data = data.get('series', {})
    result = []
    for i, (name, values) in enumerate(series_data.items()):
        color = BANK_COLORS[i % len(BANK_COLORS)]
        entry: dict = {
            'name':  name,
            'type':  chart_type,
            'data':  values,
            'color': color,
        }
        if label_format and label_format != 'none':
            entry['label'] = {':formatter': _label_formatter(label_format)}
        result.append(entry)
    return result


def _bar_opts(config: dict, data: dict) -> dict:
    label_format = config.get('label_format')
    series_list = _build_series_list(data, 'bar', label_format)
    _append_overlay_series(series_list, data, label_format)
    return {
        'tooltip': {**TT_AXIS, ':formatter': _tooltip_formatter(label_format)},
        'legend':  _legend_opts(config),
        'grid':    _grid_opts(config),
        'xAxis':   {'type': 'category', 'data': data.get('x', [])},
        'yAxis':   {'type': 'value'},
        'series':  series_list,
    }


def _line_opts(config: dict, data: dict) -> dict:
    label_format = config.get('label_format')
    series_list = _build_series_list(data, 'line', label_format)
    for s in series_list:
        s['smooth'] = 0.3
    return {
        'tooltip': {**TT_AXIS, ':formatter': _tooltip_formatter(label_format)},
        'legend':  _legend_opts(config),
        'grid':    _grid_opts(config),
        'xAxis':   {'type': 'category', 'data': data.get('x', [])},
        'yAxis':   {'type': 'value'},
        'series':  series_list,
    }


def _stacked_bar_opts(config: dict, data: dict) -> dict:
    label_format = config.get('label_format')
    series_list = _build_series_list(data, 'bar', label_format)
    for s in series_list:
        s['stack'] = 'total'
    _append_overlay_series(series_list, data, label_format)
    return {
        'tooltip': {**TT_AXIS, ':formatter': _tooltip_formatter(label_format)},
        'legend':  _legend_opts(config),
        'grid':    _grid_opts(config),
        'xAxis':   {'type': 'category', 'data': data.get('x', [])},
        'yAxis':   {'type': 'value'},
        'series':  series_list,
    }


def _donut_opts(config: dict, data: dict) -> dict:
    label_format = config.get('label_format')
    x_vals  = data.get('x', [])
    series_data = data.get('series', {})

    # Sum all series values per x label to get one value per slice
    totals: dict[str, float] = {}
    for s_vals in series_data.values():
        for i, v in enumerate(s_vals):
            label = x_vals[i] if i < len(x_vals) else str(i)
            totals[label] = totals.get(label, 0.0) + (float(v) if v is not None else 0.0)

    pie_data = [{'name': k, 'value': v} for k, v in totals.items()]

    fmt = _label_formatter(label_format)
    return {
        'tooltip': {'trigger': 'item'},
        'legend':  _legend_opts(config),
        'series': [{
            'type':      'pie',
            'radius':    ['40%', '70%'],
            'data':      pie_data,
            'emphasis':  {'itemStyle': {'shadowBlur': 10, 'shadowOffsetX': 0, 'shadowColor': 'rgba(0,0,0,0.5)'}},
            'label':     {':formatter': fmt},
        }],
    }


def _area_line_opts(config: dict, data: dict) -> dict:
    label_format = config.get('label_format')
    series_list = _build_series_list(data, 'line', label_format)
    for s in series_list:
        s['smooth'] = 0.3
        s['areaStyle'] = {
            ':color': (
                'new echarts.graphic.LinearGradient(0,0,0,1,'
                '[{offset:0,color:"rgba(96,165,250,0.4)"},{offset:1,color:"rgba(96,165,250,0.05)"}])'
            )
        }
    return {
        'tooltip': {**TT_AXIS, ':formatter': _tooltip_formatter(label_format)},
        'legend':  _legend_opts(config),
        'grid':    _grid_opts(config),
        'xAxis':   {'type': 'category', 'data': data.get('x', [])},
        'yAxis':   {'type': 'value'},
        'series':  series_list,
    }


def _mixed_opts(config: dict, data: dict) -> dict:
    label_format = config.get('label_format')
    series_data = data.get('series', {})
    series_list = []
    for i, (name, values) in enumerate(series_data.items()):
        color = BANK_COLORS[i % len(BANK_COLORS)]
        s_type = 'bar' if i == 0 else 'line'
        entry: dict = {
            'name':   name,
            'type':   s_type,
            'data':   values,
            'color':  color,
        }
        if s_type == 'line':
            entry['smooth'] = 0.3
        if label_format and label_format != 'none':
            entry['label'] = {':formatter': _label_formatter(label_format)}
        series_list.append(entry)
    _append_overlay_series(series_list, data, label_format)
    return {
        'tooltip': {**TT_AXIS, ':formatter': _tooltip_formatter(label_format)},
        'legend':  _legend_opts(config),
        'grid':    _grid_opts(config),
        'xAxis':   {'type': 'category', 'data': data.get('x', [])},
        'yAxis':   {'type': 'value'},
        'series':  series_list,
    }


_BUILDERS = {
    'bar':         _bar_opts,
    'line':        _line_opts,
    'stacked_bar': _stacked_bar_opts,
    'donut':       _donut_opts,
    'area_line':   _area_line_opts,
    'mixed':       _mixed_opts,
}


def render_custom_chart(config: dict, data: dict) -> None:
    x_vals = data.get('x', [])
    if not x_vals:
        ui.label('No data for this configuration.').classes(
            'text-sm text-muted text-center py-8 w-full'
        )
        return

    chart_type = config.get('chart_type', 'bar')
    height     = config.get('chart_height', '300px')
    builder    = _BUILDERS.get(chart_type, _bar_opts)
    opts       = builder(config, data)
    ui.echart(opts).classes('w-full').style(f'height:{height}')

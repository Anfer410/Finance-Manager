"""
components/widgets/custom_chart_widget.py

Widget subclass wrapping user-created custom charts for use in dashboards.
"""

from __future__ import annotations

from components.widgets.base import Widget, WidgetType, RenderContext


class CustomChartWidget(Widget):
    widget_type             = WidgetType.BAR
    has_own_header          = False
    supports_person_filter  = False
    supports_time_range     = True
    config_schema           = []
    default_col_span        = 2
    default_row_span        = 2

    def __init__(self, record: dict):
        self._record = record
        self.id          = f"custom:{record['id']}"
        self.title       = record['name']
        self.description = f"Custom {record['chart_type']} chart"
        self.icon        = 'bar_chart'
        self.category    = 'custom'

        _type_map = {
            'bar':         WidgetType.BAR,
            'line':        WidgetType.LINE,
            'stacked_bar': WidgetType.STACKED_BAR,
            'donut':       WidgetType.DONUT,
            'area_line':   WidgetType.AREA_LINE,
            'mixed':       WidgetType.MIXED,
        }
        self.widget_type = _type_map.get(record.get('chart_type', 'bar'), WidgetType.BAR)

    def render(self, ctx: RenderContext) -> None:
        from datetime import date as _date
        from services.custom_chart_query import execute_chart_query
        from components.custom_chart_renderer import render_custom_chart
        from components.widgets.base import TimeMode
        try:
            config = self._record.get('config', {})

            # Resolve date range from RenderContext (dashboard widget settings win)
            if ctx.time_mode == TimeMode.PAGE_YEAR:
                df: _date | None = _date(ctx.year, 1, 1)
                dt: _date | None = _date(ctx.year, 12, 31)
            elif ctx.time_mode == TimeMode.ALL_TIME:
                df, dt = None, None   # explicit None = no filter
            else:
                df, dt = ctx.date_from, ctx.date_to

            data = execute_chart_query(config, date_from=df, date_to=dt)

            # Dashboard widget settings override chart-level display config
            display_config = dict(config)
            for key in ('legend_position', 'show_legend'):
                if key in ctx.config:
                    display_config[key] = ctx.config[key]

            render_custom_chart(display_config, data)
        except Exception as e:
            from nicegui import ui
            ui.label(f'Error rendering chart: {e}').classes('text-sm text-red-500 p-4')

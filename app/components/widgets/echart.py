"""
components/widgets/echart.py

EChartWidget — base class for Apache ECharts widgets, and typed subclasses
for each chart category (bar, line, mixed, stacked bar, donut, area+line).

These classes exist to:
  1. Set widget_type and chart_height defaults per chart category.
  2. Expose a _render_chart() helper that calls ui.echart with the right
     sizing and optional click handler.
  3. Serve as a clear type hierarchy that documents the rendering intent
     of each concrete widget.

Concrete widgets in registry.py inherit from the appropriate subclass
and implement render(ctx) using the chart building functions in
finance_charts.py (or inline echart option dicts for complex cases).
"""

from __future__ import annotations

from components.widgets.base import Widget, WidgetType, RenderContext


class EChartWidget(Widget):
    """Base for all Apache ECharts widgets."""

    chart_height: str = '280px'

    def _render_chart(self, opts: dict, on_click=None) -> None:
        """
        Render an echart with standard sizing.
        Pass on_click to wire up point-click events.
        """
        from nicegui import ui
        if on_click:
            ui.echart(opts, on_point_click=on_click) \
              .classes('w-full').style(f'height:{self.chart_height}')
        else:
            ui.echart(opts) \
              .classes('w-full').style(f'height:{self.chart_height}')


class BarChartWidget(EChartWidget):
    """Grouped bar chart (e.g. fixed vs variable spend)."""
    widget_type  = WidgetType.BAR
    chart_height = '280px'


class LineChartWidget(EChartWidget):
    """Multi-series line chart (e.g. spend per account)."""
    widget_type  = WidgetType.LINE
    chart_height = '300px'


class MixedChartWidget(EChartWidget):
    """Bar + line combo chart (e.g. monthly spend vs income)."""
    widget_type  = WidgetType.MIXED
    chart_height = '300px'


class StackedBarChartWidget(EChartWidget):
    """Stacked bar chart (e.g. category trend, weekly transactions)."""
    widget_type  = WidgetType.STACKED_BAR
    chart_height = '320px'


class DonutChartWidget(EChartWidget):
    """Pie / donut chart (e.g. spend by category)."""
    widget_type  = WidgetType.DONUT
    chart_height = '300px'


class AreaLineChartWidget(EChartWidget):
    """Area + line curves (e.g. loan balance projections)."""
    widget_type  = WidgetType.AREA_LINE
    chart_height = '300px'

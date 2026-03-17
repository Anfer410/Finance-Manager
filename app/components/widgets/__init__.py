"""
components/widgets/

Widget system — standardized, composable widgets for dashboards and pages.

Quick usage
───────────
  from components.widgets import REGISTRY, REGISTRY_BY_ID, RenderContext

  # Render a widget standalone (e.g. on the loans page):
  widget = REGISTRY_BY_ID['spend_income']
  widget.render_standalone(year=2025, persons=[1])

  # From the dashboard grid (full context):
  ctx = RenderContext.build(page_year, page_persons, w['config'], shared_state)
  widget.render(ctx)

Public API
──────────
  Base classes / types
    Widget, RenderContext, ConfigField, WidgetType, TimeMode

  Type bases (for isinstance checks / future extension)
    KPIWidget
    EChartWidget, BarChartWidget, LineChartWidget, MixedChartWidget,
    StackedBarChartWidget, DonutChartWidget, AreaLineChartWidget
    TableWidget

  Registry
    REGISTRY         — list[Widget], all registered widgets in display order
    REGISTRY_BY_ID   — dict[str, Widget], keyed by widget id

  Settings dialog
    open_widget_settings_dialog(widget_id, widget_def, current_config,
                                on_save, page_year)
"""

from components.widgets.base import (
    Widget, RenderContext, ConfigField, WidgetType, TimeMode,
)
from components.widgets.kpi         import KPIWidget
from components.widgets.echart      import (
    EChartWidget, BarChartWidget, LineChartWidget, MixedChartWidget,
    StackedBarChartWidget, DonutChartWidget, AreaLineChartWidget,
)
from components.widgets.table_widget  import TableWidget
from components.widgets.settings_ui   import open_widget_settings_dialog
from components.widgets.registry      import REGISTRY, REGISTRY_BY_ID

__all__ = [
    # Base
    'Widget', 'RenderContext', 'ConfigField', 'WidgetType', 'TimeMode',
    # Type classes
    'KPIWidget',
    'EChartWidget', 'BarChartWidget', 'LineChartWidget', 'MixedChartWidget',
    'StackedBarChartWidget', 'DonutChartWidget', 'AreaLineChartWidget',
    'TableWidget',
    # Registry
    'REGISTRY', 'REGISTRY_BY_ID',
    # Settings
    'open_widget_settings_dialog',
]

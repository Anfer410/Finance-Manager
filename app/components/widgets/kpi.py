"""
components/widgets/kpi.py

KPIWidget — base class for KPI stat cards.

Subclasses set widget metadata (id, title, etc.) and implement render().
The class sets sensible defaults: 2-col span, own header, KPI widget type.
"""

from __future__ import annotations

from components.widgets.base import Widget, WidgetType, RenderContext


class KPIWidget(Widget):
    """
    Base class for KPI stat cards.

    Provides sane defaults for the compact 2-column card layout.
    Subclasses implement render() with their own specific data + layout.
    """
    widget_type      = WidgetType.KPI
    default_col_span = 2
    default_row_span = 2
    has_own_header   = True

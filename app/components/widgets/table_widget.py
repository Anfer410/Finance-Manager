"""
components/widgets/table_widget.py

TableWidget — base class for data table widgets.
"""

from __future__ import annotations

from components.widgets.base import Widget, WidgetType, RenderContext


class TableWidget(Widget):
    """
    Base class for data table widgets.

    Subclasses implement render() to produce a NiceGUI ui.table or
    equivalent, typically with pagination and column slots.
    """
    widget_type      = WidgetType.TABLE
    default_col_span = 4
    default_row_span = 2

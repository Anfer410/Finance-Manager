"""
components/add_widget_dialog.py

Add-widget picker dialog: two tabs for built-in and custom charts.
"""

from __future__ import annotations

from itertools import groupby
from typing import Callable

from nicegui import ui

from components.widgets import REGISTRY
from services.custom_chart_repo import list_custom_charts


def open_add_widget_dialog(
    user_id: int,
    on_add_builtin: Callable,
    on_add_custom: Callable,
) -> None:
    """Open the Add Widget picker dialog.

    Args:
        user_id:        Current user, used to load custom charts.
        on_add_builtin: Called with (chart_def) when a built-in widget is chosen.
        on_add_custom:  Called with (rec dict) when a custom chart is chosen.
    """
    available     = list(REGISTRY)
    custom_charts = list_custom_charts(user_id)

    with ui.dialog() as dlg, \
         ui.card().classes('w-[560px] rounded-2xl p-0 gap-0 overflow-hidden'):

        with ui.row().classes('items-center justify-between px-6 py-4 border-b border-zinc-100'):
            ui.label('Add Widget').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

        with ui.tabs().classes('px-4 border-b border-zinc-100') as tabs:
            tab_builtin = ui.tab('Built-in')
            tab_custom  = ui.tab(f'Custom ({len(custom_charts)})')

        with ui.tab_panels(tabs, value=tab_builtin).classes('w-full'):

            with ui.tab_panel(tab_builtin):
                with ui.scroll_area().style('height:380px'):
                    with ui.column().classes('w-full px-4 py-3 gap-1'):
                        for category, charts in groupby(available, key=lambda c: c.category):
                            ui.label(category.title()) \
                                .classes('text-xs font-semibold text-zinc-400 uppercase tracking-wide mt-3 mb-1')
                            for chart_def in charts:
                                with ui.row().classes(
                                    'items-center py-2 px-3 rounded-lg '
                                    'hover:bg-zinc-50 w-full flex-nowrap gap-3'
                                ):
                                    with ui.column().classes('flex-1 min-w-0 gap-0'):
                                        with ui.row().classes('items-center gap-2'):
                                            ui.icon(chart_def.icon) \
                                                .classes('text-zinc-400 shrink-0').style('font-size:1.3rem')
                                            ui.label(chart_def.title).classes('text-sm font-medium')
                                        ui.label(chart_def.description).classes('text-xs text-muted line-clamp-2')
                                    ui.button(
                                        'Add',
                                        on_click=lambda _, cd=chart_def: (dlg.close(), on_add_builtin(cd)),
                                    ).props('unelevated dense no-caps size=sm') \
                                     .classes('bg-zinc-800 text-white px-3 shrink-0')

            with ui.tab_panel(tab_custom):
                with ui.scroll_area().style('height:380px'):
                    with ui.column().classes('w-full px-4 py-3 gap-1'):
                        if not custom_charts:
                            ui.label('No custom charts yet.') \
                                .classes('text-sm text-zinc-400 py-2')
                            ui.button(
                                'Create a chart',
                                icon='add',
                                on_click=lambda: ui.navigate.to('/chart-builder'),
                            ).props('flat dense').classes('text-primary text-sm')
                        else:
                            for rec in custom_charts:
                                with ui.row().classes(
                                    'items-center py-2 px-3 rounded-lg '
                                    'hover:bg-zinc-50 w-full flex-nowrap gap-3'
                                ):
                                    with ui.column().classes('flex-1 min-w-0 gap-0'):
                                        with ui.row().classes('items-center gap-2'):
                                            ui.icon('bar_chart') \
                                                .classes('text-zinc-400 shrink-0').style('font-size:1.3rem')
                                            ui.label(rec['name']).classes('text-sm font-medium')
                                        ui.label(rec['chart_type']).classes('text-xs text-muted')
                                    ui.button(
                                        'Add',
                                        on_click=lambda _, r=rec: (dlg.close(), on_add_custom(r)),
                                    ).props('unelevated dense no-caps size=sm') \
                                     .classes('bg-zinc-800 text-white px-3 shrink-0')

    dlg.open()

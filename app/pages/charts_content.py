"""
pages/charts_content.py

Gallery page: built-in widget catalog + user's custom charts.
"""

from __future__ import annotations

from nicegui import ui, app

from components.widgets import REGISTRY
from services.custom_chart_repo import list_custom_charts, delete_custom_chart
from services.dashboard_config import list_dashboards, add_widget
from services.auth import current_user_id


def content() -> None:
    user_id = current_user_id()

    # ── Page title ────────────────────────────────────────────────────────────
    with ui.column().classes('w-full px-6 pt-6 pb-2 gap-1'):
        ui.label('Charts').classes('text-2xl font-bold text-zinc-900')
        ui.label('Browse built-in charts or manage your custom charts.') \
          .classes('text-sm text-zinc-500')

    # ── Add-to-dashboard dialog ───────────────────────────────────────────────
    def _open_add_dialog(chart_id: str) -> None:
        dashboards = list_dashboards(user_id)
        options    = {str(d['id']): d['name'] for d in dashboards}

        with ui.dialog() as dlg, \
             ui.card().classes('w-80 rounded-2xl p-6 gap-4'):
            ui.label('Add to Dashboard').classes('text-lg font-semibold')
            sel = ui.select(options, label='Dashboard').classes('w-full')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=dlg.close).props('flat')
                def _do_add():
                    if not sel.value:
                        ui.notify('Please select a dashboard.', color='warning')
                        return
                    add_widget(int(sel.value), chart_id, col_span=2, row_span=1)
                    dlg.close()
                    ui.notify('Widget added to dashboard.', color='positive')
                ui.button('Add', on_click=_do_add)
        dlg.open()

    # ── Built-in charts section ───────────────────────────────────────────────
    with ui.column().classes('w-full px-6 py-4 gap-3'):
        ui.label('Built-in Charts').classes('text-lg font-semibold text-zinc-800')

        with ui.grid(columns=3).classes('w-full gap-4'):
            for widget in REGISTRY:
                with ui.card().classes('rounded-xl p-4 gap-2 flex flex-col'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon(widget.icon).classes('text-zinc-500').style('font-size:1.5rem')
                        ui.label(widget.title).classes('font-semibold text-zinc-800 text-sm')
                    ui.label(widget.description).classes('text-xs text-zinc-500 flex-1')
                    ui.badge(widget.widget_type.value) \
                      .classes('self-start text-xs').props('color=grey-3 text-color=grey-8')
                    ui.button(
                        '+ Add to Dashboard',
                        on_click=lambda _, wid=widget.id: _open_add_dialog(wid),
                    ).props('flat dense').classes('text-xs text-primary self-start mt-1')

    ui.separator().classes('mx-6')

    # ── Custom charts section (refreshable) ───────────────────────────────────
    @ui.refreshable
    def charts_list() -> None:
        charts = list_custom_charts(user_id)
        with ui.column().classes('w-full px-6 py-4 gap-3'):
            with ui.row().classes('w-full items-center justify-between'):
                ui.label('My Charts').classes('text-lg font-semibold text-zinc-800')
                ui.button(
                    'New Chart',
                    icon='add',
                    on_click=lambda: ui.navigate.to('/chart-builder'),
                ).props('flat dense').classes('text-primary')

            if not charts:
                ui.label('No custom charts yet. Click "New Chart" to create one.') \
                  .classes('text-sm text-zinc-400 py-4')
                return

            with ui.grid(columns=3).classes('w-full gap-4'):
                for rec in charts:
                    with ui.card().classes('rounded-xl p-4 gap-2 flex flex-col'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('bar_chart').classes('text-zinc-500').style('font-size:1.5rem')
                            ui.label(rec['name']).classes('font-semibold text-zinc-800 text-sm')
                        ui.badge(rec['chart_type']) \
                          .classes('self-start text-xs').props('color=grey-3 text-color=grey-8')

                        with ui.row().classes('w-full gap-1 mt-1 flex-wrap'):
                            ui.button(
                                'Edit',
                                icon='edit',
                                on_click=lambda _, r=rec: _edit_chart(r),
                            ).props('flat dense').classes('text-xs')
                            ui.button(
                                'Delete',
                                icon='delete',
                                on_click=lambda _, r=rec: _confirm_delete(r),
                            ).props('flat dense').classes('text-xs text-red-500')
                            ui.button(
                                '+ Dashboard',
                                on_click=lambda _, r=rec: _open_add_dialog(f"custom:{r['id']}"),
                            ).props('flat dense').classes('text-xs text-primary')

    def _edit_chart(rec: dict) -> None:
        app.storage.user['chart_builder_load_id'] = rec['id']
        ui.navigate.to('/chart-builder')

    def _confirm_delete(rec: dict) -> None:
        with ui.dialog() as dlg, \
             ui.card().classes('w-72 rounded-2xl p-6 gap-4'):
            ui.label(f'Delete "{rec["name"]}"?').classes('font-semibold')
            ui.label('This cannot be undone.').classes('text-sm text-zinc-500')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=dlg.close).props('flat')
                def _do_delete():
                    delete_custom_chart(rec['id'])
                    dlg.close()
                    charts_list.refresh()
                    ui.notify('Chart deleted.', color='positive')
                ui.button('Delete', on_click=_do_delete).props('color=negative')
        dlg.open()

    charts_list()

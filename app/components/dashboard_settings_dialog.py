"""
components/dashboard_settings_dialog.py

Transaction settings dialog (transfer/employer patterns + view refresh).
"""

from __future__ import annotations

from typing import Callable

from nicegui import ui

from services.notifications import notify
from services.transaction_config import load_config, save_config
from services.view_manager import ViewManager
from data.db import get_conn_tuple, get_schema


def open_settings_dialog(on_save_callback: Callable) -> None:

    def _chip_list(items: list[str], on_remove) -> None:
        if not items:
            ui.label('None configured.').classes('text-xs text-muted')
            return
        with ui.row().classes('flex-wrap gap-1'):
            for item in items:
                with ui.element('div').classes(
                    'inline-flex items-center gap-1 px-2 py-0.5 rounded-full '
                    'bg-gray-100 text-gray-700 text-xs font-mono'
                ):
                    ui.label(item)
                    ui.button(icon='close', on_click=lambda _, i=item: on_remove(i)) \
                        .props('flat round dense size=xs').classes('text-gray-400')

    with ui.dialog() as dlg, \
         ui.card().classes('w-[600px] rounded-2xl p-0 gap-0 overflow-hidden'):

        with ui.row().classes('items-center justify-between px-6 py-4 border-b border-zinc-100'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('settings').classes('text-zinc-400 text-xl')
                ui.label('Transaction settings').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close) \
                .props('flat round dense').classes('text-zinc-400')

        with ui.scroll_area().style('height: 60vh'):
            with ui.column().classes('w-full gap-4 px-6 py-5'):
                cfg = load_config()

                ui.label('Transfer exclusion patterns').classes('text-sm font-semibold text-gray-700')
                ui.label(
                    'Transactions whose description contains any of these strings are '
                    'excluded from spend and income totals (e.g. credit card payments, Zelle).'
                ).classes('text-xs text-muted')

                @ui.refreshable
                def render_transfer_chips() -> None:
                    _chip_list(cfg.transfer_patterns, on_remove=lambda p: _remove_transfer(p))

                def _remove_transfer(pattern: str) -> None:
                    cfg.transfer_patterns = [p for p in cfg.transfer_patterns if p != pattern]
                    render_transfer_chips.refresh()

                render_transfer_chips()

                with ui.row().classes('items-center gap-2'):
                    transfer_input = ui.input(placeholder='e.g. ONLINE PAYMENT') \
                        .props('outlined dense').classes('flex-1')

                    def _add_transfer() -> None:
                        val = transfer_input.value.strip().upper()
                        if val and val not in cfg.transfer_patterns:
                            cfg.transfer_patterns.append(val)
                            transfer_input.set_value('')
                            render_transfer_chips.refresh()

                    ui.button('Add', icon='add', on_click=_add_transfer) \
                        .props('unelevated dense').classes('bg-gray-700 text-white')

                ui.separator()

                ui.label('Employer / payroll patterns').classes('text-sm font-semibold text-gray-700')
                ui.label(
                    'Incoming transactions matching these strings are counted as income '
                    '(e.g. your employer name, "DIRECT DEP", "PAYROLL").'
                ).classes('text-xs text-muted')

                @ui.refreshable
                def render_employer_chips() -> None:
                    _chip_list(cfg.employer_patterns, on_remove=lambda p: _remove_employer(p))

                def _remove_employer(pattern: str) -> None:
                    cfg.employer_patterns = [p for p in cfg.employer_patterns if p != pattern]
                    render_employer_chips.refresh()

                render_employer_chips()

                with ui.row().classes('items-center gap-2'):
                    employer_input = ui.input(placeholder='e.g. SLALOM') \
                        .props('outlined dense').classes('flex-1')

                    def _add_employer() -> None:
                        val = employer_input.value.strip().upper()
                        if val and val not in cfg.employer_patterns:
                            cfg.employer_patterns.append(val)
                            employer_input.set_value('')
                            render_employer_chips.refresh()

                    ui.button('Add', icon='add', on_click=_add_employer) \
                        .props('unelevated dense').classes('bg-gray-700 text-white')

        with ui.row().classes('items-center justify-between px-6 py-4 border-t border-zinc-100'):
            def _refresh_views() -> None:
                try:
                    ViewManager(get_conn_tuple(), schema=get_schema()).refresh()
                    notify('Views refreshed.', type='positive', position='top')
                except Exception as ex:
                    notify(f'Refresh failed: {ex}', type='negative', position='top')

            ui.button('Refresh views', icon='refresh', on_click=_refresh_views) \
                .props('flat no-caps').classes('text-zinc-500')

            with ui.row().classes('gap-2'):
                ui.button('Cancel', on_click=dlg.close) \
                    .props('flat no-caps').classes('text-zinc-500')

                def _save() -> None:
                    save_config(cfg)
                    notify('Settings saved — refreshing charts.', type='positive', position='top')
                    dlg.close()
                    on_save_callback()

                ui.button('Save & refresh', icon='save', on_click=_save) \
                    .props('unelevated no-caps').classes('bg-zinc-800 text-white px-4 rounded-lg')

    dlg.open()

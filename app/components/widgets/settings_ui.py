"""
components/widgets/settings_ui.py

open_widget_settings_dialog() — builds the per-widget settings dialog.

Sections rendered (each conditional on widget capabilities):
  • Widget Label    — always shown; lets user give a custom name to this instance
  • Time Range      — if widget.supports_time_range: mode + sub-fields
  • Person Filter   — if widget.supports_person_filter: checkboxes per user
  • Loan            — if widget.supports_loan_select: dropdown of configured loans
  • Custom fields   — widget.config_schema entries (number / select / toggle)

The caller supplies an on_save callback that receives the updated config dict.
"""

from __future__ import annotations

from typing import Callable


def open_widget_settings_dialog(
    widget_id:      int,
    widget_def,               # Widget instance
    current_config: dict,
    on_save:        Callable,  # on_save(new_config: dict, instance_label: str | None) -> None
    page_year:      int,
) -> None:
    """
    Open the settings dialog for a specific widget instance.
    Modifies a local copy of config; calls on_save only when the user clicks Save.
    """
    from nicegui import ui

    # Working copy — we mutate this freely; only commit on Save
    cfg   = dict(current_config)
    label = {'value': None}   # instance_label (stored separately from config JSONB)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_available_years() -> list[int]:
        try:
            from data.finance_dashboard_data import get_years
            return get_years()
        except Exception:
            return [page_year]

    def _get_persons() -> list[dict]:
        """Returns [{id, name}, …] for all users that appear in transactions."""
        try:
            from data.finance_dashboard_data import get_persons_with_ids
            return get_persons_with_ids()
        except Exception:
            return []

    def _get_loans() -> list:
        try:
            from services.loan_service import load_loans
            return load_loans()
        except Exception:
            return []

    # ── Dialog shell ──────────────────────────────────────────────────────────

    with ui.dialog() as dlg, \
         ui.card().classes('w-[460px] rounded-2xl p-0 gap-0 overflow-hidden'):

        # Header
        with ui.row().classes(
            'items-center justify-between px-6 py-4 border-b border-zinc-100'
        ):
            with ui.row().classes('items-center gap-2'):
                ui.icon('tune').classes('text-zinc-400 text-xl')
                ui.label(f'Widget Settings').classes(
                    'text-base font-semibold text-zinc-800'
                )
            ui.button(icon='close', on_click=dlg.close) \
              .props('flat round dense').classes('text-zinc-400')

        with ui.scroll_area().style('height: 62vh'):
            with ui.column().classes('w-full gap-0 px-6 py-5'):

                # ── Widget label ──────────────────────────────────────────────
                _section_header('Widget Label', 'label')
                label_input = ui.input(
                    placeholder=widget_def.title,
                    value=cfg.get('_label', '') or '',
                ).props('outlined dense').classes('w-full mb-4')
                ui.label(
                    'Leave blank to use the default title.'
                ).classes('text-xs text-zinc-400 -mt-3 mb-4')

                # ── Time range ────────────────────────────────────────────────
                if widget_def.supports_time_range:
                    _section_header('Time Range', 'schedule')
                    available_years = _get_available_years()

                    time_mode_opts = {
                        'page_year':  'Page Year (default)',
                        'trailing':   'Trailing Months',
                        'year':       'Specific Year',
                        'date_range': 'Date Range',
                        'all_time':   'All Time',
                    }

                    @ui.refreshable
                    def _time_sub_fields():
                        mode = cfg.get('time_mode', 'page_year')
                        if mode == 'trailing':
                            trailing_opts = {
                                3:  '3 months',
                                6:  '6 months',
                                12: '1 year (12 mo)',
                                24: '2 years (24 mo)',
                                36: '3 years (36 mo)',
                                48: '4 years (48 mo)',
                                60: '5 years (60 mo)',
                            }
                            ui.select(
                                trailing_opts,
                                value=int(cfg.get('trailing_months', 24)),
                                label='Lookback Period',
                                on_change=lambda e: cfg.update(
                                    {'trailing_months': e.value}
                                ),
                            ).props('outlined dense').classes('w-full mt-2')

                        elif mode == 'year':
                            ui.select(
                                available_years,
                                value=int(cfg.get('year', page_year)),
                                label='Year',
                                on_change=lambda e: cfg.update({'year': e.value}),
                            ).props('outlined dense').classes('w-full mt-2')

                        elif mode == 'date_range':
                            ui.input(
                                'From (YYYY-MM-DD)',
                                value=cfg.get('date_from', ''),
                                on_change=lambda e: cfg.update({'date_from': e.value}),
                            ).props('outlined dense').classes('w-full mt-2')
                            ui.input(
                                'To (YYYY-MM-DD)',
                                value=cfg.get('date_to', ''),
                                on_change=lambda e: cfg.update({'date_to': e.value}),
                            ).props('outlined dense').classes('w-full mt-2')

                    def _on_time_mode(e):
                        cfg['time_mode'] = e.value
                        _time_sub_fields.refresh()

                    ui.select(
                        time_mode_opts,
                        value=cfg.get('time_mode', 'page_year'),
                        label='Mode',
                        on_change=_on_time_mode,
                    ).props('outlined dense').classes('w-full')
                    _time_sub_fields()
                    ui.element('div').classes('mb-4')

                # ── Person filter ─────────────────────────────────────────────
                if widget_def.supports_person_filter:
                    persons_list = _get_persons()
                    if persons_list:
                        _section_header('Person Filter', 'people')
                        ui.label(
                            'Leave all unchecked to inherit the page-level filter.'
                        ).classes('text-xs text-zinc-400 mb-2')

                        current_persons = set(cfg.get('persons') or [])

                        def _toggle_person(uid: int, checked: bool):
                            p = set(cfg.get('persons') or [])
                            if checked:
                                p.add(uid)
                            else:
                                p.discard(uid)
                            cfg['persons'] = sorted(p) if p else None

                        for person in persons_list:
                            ui.checkbox(
                                person['name'],
                                value=person['id'] in current_persons,
                                on_change=lambda e, uid=person['id']: _toggle_person(
                                    uid, e.value
                                ),
                            ).classes('text-sm')
                        ui.element('div').classes('mb-4')

                # ── Legend position ───────────────────────────────────────────
                if widget_def.widget_type.value not in ('kpi', 'table'):
                    _section_header('Legend', 'legend_toggle')
                    ui.select(
                        {'top': 'Top', 'bottom': 'Bottom', 'left': 'Left', 'right': 'Right'},
                        value=cfg.get('legend_position', 'top'),
                        label='Legend Position',
                        on_change=lambda e: cfg.update({'legend_position': e.value}),
                    ).props('outlined dense').classes('w-full mb-4')

                # ── Loan selector ─────────────────────────────────────────────
                if widget_def.supports_loan_select:
                    loans = _get_loans()
                    if loans:
                        _section_header('Loan', 'account_balance')
                        loan_opts = {None: 'All loans (summary)'}
                        for ln in loans:
                            loan_opts[ln.id] = ln.name
                        current_loan = cfg.get('loan_id')
                        ui.select(
                            loan_opts,
                            value=current_loan,
                            label='Show loan',
                            on_change=lambda e: cfg.update({'loan_id': e.value}),
                        ).props('outlined dense').classes('w-full mb-4')

                # ── Custom config_schema fields ───────────────────────────────
                if widget_def.config_schema:
                    _section_header('Widget Options', 'settings')
                    for fld in widget_def.config_schema:
                        key   = fld.key
                        val   = cfg.get(key, fld.default)
                        if fld.description:
                            ui.label(fld.description).classes(
                                'text-xs text-zinc-400 mb-1'
                            )

                        if fld.type == 'number':
                            ui.number(
                                fld.label, value=val,
                                min=fld.min, max=fld.max,
                                on_change=lambda e, k=key: cfg.update({k: e.value}),
                            ).props('outlined dense').classes('w-full mb-3')

                        elif fld.type == 'select':
                            opts = dict(zip(fld.options, fld.option_labels)) \
                                   if fld.option_labels else {o: str(o) for o in fld.options}
                            ui.select(
                                opts, value=val, label=fld.label,
                                on_change=lambda e, k=key: cfg.update({k: e.value}),
                            ).props('outlined dense').classes('w-full mb-3')

                        elif fld.type == 'toggle':
                            ui.checkbox(
                                fld.label, value=bool(val),
                                on_change=lambda e, k=key: cfg.update({k: e.value}),
                            ).classes('text-sm mb-3')

        # Footer — Cancel / Save
        with ui.row().classes(
            'items-center justify-end gap-2 px-6 py-4 border-t border-zinc-100'
        ):
            ui.button('Cancel', on_click=dlg.close) \
              .props('flat no-caps').classes('text-zinc-500')

            def _save():
                # Persist the label into config so RenderContext can read it
                raw_label = label_input.value.strip()
                cfg['_label'] = raw_label or None
                on_save(cfg, raw_label or None)
                dlg.close()

            ui.button('Save', on_click=_save) \
              .props('unelevated no-caps') \
              .classes('bg-zinc-800 text-white rounded-lg px-4')

    dlg.open()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _section_header(text: str, icon: str) -> None:
    """Small section header label with icon."""
    from nicegui import ui
    with ui.row().classes('items-center gap-1 mb-2'):
        ui.icon(icon).classes('text-zinc-400 text-base')
        ui.label(text).classes('text-xs font-semibold text-zinc-500 uppercase tracking-wide')

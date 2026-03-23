"""
categories_content.py

Settings page for managing spend categories and classification rules.
Route: /categories
"""

from __future__ import annotations
from nicegui import ui
import services.auth as auth
from data.category_rules import (
    load_category_config,
    save_category_config,
    Category, CategoryRule, CategoryConfig,
)
from services.view_manager import ViewManager
from data.db import get_engine, get_schema
from services.notifications import notify
from services.ui_inputs import labeled_input, labeled_select
from styles.dashboards import COST_TYPES, FIXED_COLOR, VAR_COLOR

SCHEMA = get_schema()
ENGINE = get_engine()



def content() -> None:
    cfg = load_category_config(auth.current_family_id())
    vm  = ViewManager(engine=ENGINE, schema=SCHEMA)

    with ui.row().classes('w-full items-center justify-between mb-2'):
        with ui.column().classes('gap-0'):
            ui.label('Categories').classes('page-title')
            ui.label('Manage spend categories and classification rules.').classes('text-sm text-muted')
        ui.button('Save & rebuild views', icon='save', on_click=lambda: _save(vm, cfg)) \
            .props('unelevated').classes('bg-gray-800 text-white')

    ui.element('div').classes('divider mb-4')

    # ── Preview ──────────────────────────────────────────────────────────────
    with ui.element('div').classes('card w-full mt-4'):
        ui.label('Rule preview').classes('section-title mb-2')
        ui.label('Test how a description resolves to a category.').classes('text-xs text-muted mb-3')
        with ui.row().classes('items-center gap-3'):
            preview_input  = labeled_input('Description', placeholder='e.g. KROGER #344 JOHNS CREEK GA', compact=True, classes='flex-1')
            preview_result = ui.label('').classes('text-sm font-semibold')

            def _preview(_=None) -> None:
                desc = preview_input.value.strip()
                if not desc:
                    preview_result.set_text('')
                    return
                cat = cfg.resolve(desc)
                color = {c.name: c.color for c in cfg.categories}.get(cat, '#d1d5db')
                preview_result.style(f'color:{color}')
                preview_result.set_text(f'→  {cat}')

            preview_input.on('keyup', _preview)
            ui.button('Test', icon='search', on_click=_preview) \
                .props('unelevated dense').classes('bg-gray-700 text-white')


    with ui.row().classes('w-full gap-4 items-start flex-wrap'):

        # ── Left: Rules list ────────────────────────────────────────────────
        with ui.element('div').classes('card flex-1').style('min-width:400px'):
            with ui.row().classes('items-center justify-between mb-3'):
                ui.label('Classification rules').classes('section-title')
                ui.label('Checked in priority order — first match wins.').classes('text-xs text-muted')
                ui.button(icon='add', on_click=lambda: _add_rule_dialog(cfg, rule_table)) \
                    .props('flat round dense').classes('text-gray-500')

            # Header
            with ui.row().classes('w-full px-1 pb-1 gap-2 text-xs text-muted font-medium'):
                ui.label('Pri').style('width:36px')
                ui.label('Pattern').classes('flex-1')
                ui.label('Type').style('width:44px')
                ui.label('Category').style('width:120px')
                ui.label('').style('width:56px')

            @ui.refreshable
            def rule_table() -> None:
                for rule in cfg.sorted_rules():
                    with ui.row().classes('items-center gap-2 py-1 border-b border-gray-50 w-full'):
                        ui.label(str(rule.priority)).classes('text-xs text-muted').style('width:36px')
                        ui.label(rule.pattern).classes('text-xs font-mono flex-1') \
                            
                        ui.label('regex' if rule.is_regex else 'substr') \
                            .classes('text-xs px-1 rounded') \
                            .style(f'width:44px;background:{"#fef3c7" if rule.is_regex else "#f3f4f6"};'
                                   f'color:{"#92400e" if rule.is_regex else "#374151"}')
                        ui.label(rule.category).classes('text-xs').style('width:120px')
                        ui.button(icon='edit',
                                  on_click=lambda _, r=rule: _edit_rule_dialog(r, cfg, rule_table)) \
                            .props('flat round dense size=xs').classes('text-gray-400')
                        ui.button(icon='delete',
                                  on_click=lambda _, r=rule: _delete_rule(r, cfg, rule_table)) \
                            .props('flat round dense size=xs').classes('text-red-300')

            rule_table()
        # ── Right: Category list ──────────────────────────────────────────────
        with ui.element('div').classes('card flex-1').style('min-width:280px;max-width:380px'):
            with ui.row().classes('items-center justify-between mb-3'):
                ui.label('Categories').classes('section-title')
                ui.button(icon='add', on_click=lambda: _add_category_dialog(cfg, category_table)) \
                    .props('flat round dense').classes('text-gray-500')

            @ui.refreshable
            def category_table() -> None:
                for cat in cfg.categories:
                    with ui.row().classes('items-center gap-2 py-1 border-b border-gray-50 w-full'):
                        ui.element('span').classes('w-3 h-3 rounded-full flex-shrink-0') \
                            .style(f'background:{cat.color};min-width:12px')
                        ui.label(cat.name).classes('flex-1 text-sm')
                        ctype_color = FIXED_COLOR if cat.cost_type == "fixed" else VAR_COLOR
                        ui.element('span') \
                            .classes('px-1.5 py-0.5 rounded text-xs font-medium text-white') \
                            .style(f'background:{ctype_color}') \
                            .text = cat.cost_type
                        ui.button(icon='edit',
                                  on_click=lambda _, c=cat: _edit_category_dialog(c, cfg, category_table)) \
                            .props('flat round dense size=xs').classes('text-gray-400')
                        ui.button(icon='delete',
                                  on_click=lambda _, c=cat: _delete_category(c, cfg, category_table, rule_table)) \
                            .props('flat round dense size=xs').classes('text-red-300')

            category_table()


# ── Dialogs ───────────────────────────────────────────────────────────────────

def _add_category_dialog(cfg: CategoryConfig, refresh_fn) -> None:
    with ui.dialog() as dlg, ui.card().classes('w-80 gap-3'):
        ui.label('Add category').classes('text-base font-semibold')
        name_in  = labeled_input('Name', placeholder='e.g. Entertainment')
        type_in  = labeled_select('Cost type', COST_TYPES, value='variable')
        color_in = ui.color_input(label='Color', value='#d1d5db').classes('w-full')
        with ui.row().classes('justify-end gap-2 w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat')
            def _ok():
                name = name_in.value.strip()
                if name and name not in cfg.category_names():
                    cfg.categories.append(Category(name=name, cost_type=type_in.value, color=color_in.value))
                    refresh_fn.refresh()
                dlg.close()
            ui.button('Add', on_click=_ok).props('unelevated').classes('bg-gray-800 text-white')
    dlg.open()


def _edit_category_dialog(cat: Category, cfg: CategoryConfig, refresh_fn) -> None:
    with ui.dialog() as dlg, ui.card().classes('w-80 gap-3'):
        ui.label(f'Edit — {cat.name}').classes('text-base font-semibold')
        type_in  = labeled_select('Cost type', COST_TYPES, value=cat.cost_type)
        color_in = ui.color_input(label='Color', value=cat.color).classes('w-full')
        with ui.row().classes('justify-end gap-2 w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat')
            def _ok():
                cat.cost_type = type_in.value
                cat.color     = color_in.value
                refresh_fn.refresh()
                dlg.close()
            ui.button('Save', on_click=_ok).props('unelevated').classes('bg-gray-800 text-white')
    dlg.open()


def _delete_category(cat: Category, cfg: CategoryConfig, cat_fn, rule_fn) -> None:
    cfg.categories = [c for c in cfg.categories if c.name != cat.name]
    cat_fn.refresh()
    rule_fn.refresh()


def _add_rule_dialog(cfg: CategoryConfig, refresh_fn) -> None:
    with ui.dialog() as dlg, ui.card().classes('w-96 gap-3'):
        ui.label('Add rule').classes('text-base font-semibold')
        pattern_in  = labeled_input('Pattern', placeholder='e.g. KROGER or LYFT.*RIDE')
        cat_in      = labeled_select('Category', cfg.category_names())
        is_regex_in = ui.checkbox('Regular expression (regex)')
        priority_in = ui.number('Priority (lower = checked first)', value=100, min=1, max=9999).props('outlined dense').classes('w-full')
        with ui.row().classes('justify-end gap-2 w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat')
            def _ok():
                pat = pattern_in.value.strip()
                cat = cat_in.value
                if pat and cat:
                    cfg.rules.append(CategoryRule(
                        pattern=pat, category=cat,
                        is_regex=is_regex_in.value,
                        priority=int(priority_in.value),
                    ))
                    refresh_fn.refresh()
                dlg.close()
            ui.button('Add', on_click=_ok).props('unelevated').classes('bg-gray-800 text-white')
    dlg.open()


def _edit_rule_dialog(rule: CategoryRule, cfg: CategoryConfig, refresh_fn) -> None:
    with ui.dialog() as dlg, ui.card().classes('w-96 gap-3'):
        ui.label('Edit rule').classes('text-base font-semibold')
        pattern_in  = labeled_input('Pattern', value=rule.pattern)
        cat_in      = labeled_select('Category', cfg.category_names(), value=rule.category)
        is_regex_in = ui.checkbox('Regular expression (regex)', value=rule.is_regex)
        priority_in = ui.number('Priority', value=rule.priority, min=1, max=9999).props('outlined dense').classes('w-full')
        with ui.row().classes('justify-end gap-2 w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat')
            def _ok():
                rule.pattern  = pattern_in.value.strip()
                rule.category = cat_in.value
                rule.is_regex = is_regex_in.value
                rule.priority = int(priority_in.value)
                refresh_fn.refresh()
                dlg.close()
            ui.button('Save', on_click=_ok).props('unelevated').classes('bg-gray-800 text-white')
    dlg.open()


def _delete_rule(rule: CategoryRule, cfg: CategoryConfig, refresh_fn) -> None:
    cfg.rules = [r for r in cfg.rules if r is not rule]
    refresh_fn.refresh()


def _save(vm, cfg: CategoryConfig) -> None:
    fid = auth.current_family_id()
    save_category_config(cfg, fid)
    try:
        vm.refresh()
        notify('Saved & views rebuilt.', type='positive', position='top')
    except Exception as e:
        notify(f'Saved but view rebuild failed: {e}', type='warning', position='top')
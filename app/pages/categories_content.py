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
from data.finance_dashboard_data import get_uncategorized_clusters

SCHEMA = get_schema()
ENGINE = get_engine()


def _auto_priority(pattern: str) -> int:
    """More words = lower number = checked first (higher precedence)."""
    return max(10, 100 - len(pattern.strip().split()) * 20)


def _find_overlaps(pattern: str, all_clusters: list[dict]) -> list[dict]:
    """
    More-specific clusters whose pattern starts with `pattern` followed by a space.
    Adding a substring rule for `pattern` would absorb these clusters.
    """
    p = pattern.upper()
    return [
        c for c in all_clusters
        if c["pattern"] != pattern and c["pattern"].upper().startswith(p + " ")
    ]


def content() -> None:
    cfg = load_category_config(auth.current_family_id())
    vm  = ViewManager(engine=ENGINE, schema=SCHEMA)

    suggestions_state: dict = {"clusters": []}

    # ── Header ────────────────────────────────────────────────────────────────
    with ui.row().classes('w-full items-center justify-between mb-2'):
        with ui.column().classes('gap-0'):
            ui.label('Categories').classes('page-title')
            ui.label('Manage spend categories and classification rules.').classes('text-sm text-muted')
        ui.button('Save & rebuild views', icon='save', on_click=lambda: _save(vm, cfg)) \
            .props('unelevated').classes('bg-gray-800 text-white')

    ui.element('div').classes('divider mb-4')

    # ── Rule preview ──────────────────────────────────────────────────────────
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

    # ── Smart Suggestions panel ───────────────────────────────────────────────
    with ui.element('div').classes('card w-full mt-4'):
        with ui.row().classes('items-center justify-between mb-1'):
            with ui.column().classes('gap-0'):
                ui.label('Smart Suggestions').classes('section-title')
                ui.label(
                    'Uncategorized descriptions (5+ transactions) grouped by common pattern, '
                    'most specific first.'
                ).classes('text-xs text-muted')

            @ui.refreshable
            def suggestions_body() -> None:
                clusters = suggestions_state["clusters"]

                if not clusters:
                    with ui.column().classes('items-center py-8 gap-3'):
                        ui.icon('auto_awesome').classes('text-4xl text-gray-300')
                        ui.label('Click Run to analyse your uncategorized transactions.') \
                            .classes('text-sm text-muted')
                        ui.button('Run analysis', icon='play_arrow',
                                  on_click=lambda: _run_suggestions(
                                      suggestions_state, suggestions_body,
                                      rule_table, vm, cfg)) \
                            .props('unelevated').classes('bg-indigo-600 text-white')
                    return

                # Column headers
                with ui.row().classes('w-full px-1 pb-1 mt-3 gap-2 text-xs text-muted font-medium border-b border-gray-100'):
                    ui.label('Suggested pattern').classes('flex-1')
                    ui.label('Transactions').style('width:90px')
                    ui.label('Total spend').style('width:100px')
                    ui.label('').style('width:32px')

                for cluster in clusters:
                    overlaps = _find_overlaps(cluster["pattern"], clusters)
                    with ui.element('div').classes('border-b border-gray-50 py-2'):
                        with ui.row().classes('items-center gap-2 w-full'):
                            ui.label(cluster["pattern"]) \
                                .classes('px-2 py-0.5 rounded font-mono text-xs bg-gray-100 text-gray-800 flex-shrink-0')
                            ui.label('').classes('flex-1')
                            ui.label(f'{cluster["cnt"]} txn').classes('text-xs text-muted').style('width:90px')
                            cur = auth.current_currency_prefix() or ''
                            ui.label(f'{cur}{cluster["total"]:,.2f}').classes('text-xs text-muted').style('width:100px')
                            ui.button(icon='add',
                                      on_click=lambda _, c=cluster, o=overlaps: _suggest_rule_dialog(
                                          c, o, cfg, rule_table,
                                          suggestions_state, suggestions_body, vm)) \
                                .props('flat round dense size=xs').classes('text-indigo-500') \
                                .tooltip('Create rule for this pattern')

                        # Example raw descriptions
                        with ui.row().classes('flex-wrap gap-1 mt-1'):
                            for ex in cluster["examples"]:
                                ui.element('span') \
                                    .classes('text-xs text-gray-400 italic bg-gray-50 px-1.5 py-0.5 rounded') \
                                    .text = ex

                        # Overlap warning
                        if overlaps:
                            with ui.row().classes('items-center gap-1 mt-1'):
                                ui.icon('warning_amber').classes('text-amber-400').style('font-size:14px')
                                names = ', '.join(f'"{o["pattern"]}"' for o in overlaps)
                                ui.label(f'Also covers: {names} — consider adding those rules first.') \
                                    .classes('text-xs text-amber-600')

                with ui.row().classes('justify-end mt-2'):
                    ui.button('Refresh', icon='refresh',
                              on_click=lambda: _run_suggestions(
                                  suggestions_state, suggestions_body,
                                  rule_table, vm, cfg)) \
                        .props('flat dense size=sm').classes('text-gray-400')

            suggestions_body()

    # ── Rules + Categories side by side ───────────────────────────────────────
    with ui.row().classes('w-full gap-4 items-start flex-wrap mt-4'):

        # ── Left: Rules list ─────────────────────────────────────────────────
        with ui.element('div').classes('card flex-1').style('min-width:400px'):
            with ui.row().classes('items-center justify-between mb-3'):
                ui.label('Classification rules').classes('section-title')
                ui.label('Checked in priority order — first match wins.').classes('text-xs text-muted')
                with ui.row().classes('gap-1'):
                    ui.button(icon='delete_sweep',
                              on_click=lambda: _clear_rules_dialog(cfg, rule_table)) \
                        .props('flat round dense').classes('text-red-400') \
                        .tooltip('Clear all rules')
                    ui.button(icon='add', on_click=lambda: _add_rule_dialog(cfg, rule_table)) \
                        .props('flat round dense').classes('text-gray-500')

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
                        ui.label(rule.pattern).classes('text-xs font-mono flex-1')
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

        # ── Right: Category list ─────────────────────────────────────────────
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


# ── Suggestions helpers ────────────────────────────────────────────────────────

def _run_suggestions(state: dict, body_fn, rule_table_fn, vm, cfg: CategoryConfig) -> None:
    fid = auth.current_family_id()
    state["clusters"] = get_uncategorized_clusters(fid)
    body_fn.refresh()


def _suggest_rule_dialog(
    cluster: dict,
    overlaps: list[dict],
    cfg: CategoryConfig,
    rule_table_fn,
    suggestions_state: dict,
    suggestions_body_fn,
    vm,
) -> None:
    pattern       = cluster["pattern"]
    auto_priority = _auto_priority(pattern)

    with ui.dialog() as dlg, ui.card().classes('w-96 gap-3'):
        ui.label('Add rule from suggestion').classes('text-base font-semibold')

        # Cluster summary
        cur = auth.current_currency_prefix() or ''
        with ui.row().classes('items-center gap-2 bg-gray-50 rounded px-3 py-2'):
            ui.label(pattern).classes('font-mono text-sm bg-gray-200 px-2 py-0.5 rounded')
            ui.label(f'{cluster["cnt"]} transactions · {cur}{cluster["total"]:,.2f}') \
                .classes('text-xs text-muted')

        # Example descriptions
        with ui.column().classes('gap-1'):
            ui.label('Example descriptions:').classes('text-xs text-muted')
            for ex in cluster["examples"]:
                ui.label(f'  {ex}').classes('text-xs font-mono text-gray-500')

        # Overlap warning inside the dialog
        if overlaps:
            with ui.row().classes('items-start gap-2 bg-amber-50 rounded px-3 py-2'):
                ui.icon('warning_amber').classes('text-amber-500 flex-shrink-0').style('font-size:16px')
                with ui.column().classes('gap-0.5'):
                    ui.label('This rule will also absorb:').classes('text-xs font-medium text-amber-700')
                    for o in overlaps:
                        ui.label(f'  "{o["pattern"]}" — {o["cnt"]} txn · {cur}{o["total"]:,.2f}') \
                            .classes('text-xs text-amber-600 font-mono')
                    ui.label('Consider adding those rules first so they can be assigned separately.') \
                        .classes('text-xs text-amber-600')

        ui.element('div').classes('divider')

        pattern_in = labeled_input('Pattern', value=pattern)

        # Category row with inline new-category form
        with ui.column().classes('gap-1 w-full'):
            ui.label('Category').classes('text-sm font-medium text-zinc-700')
            with ui.row().classes('items-center gap-2 w-full'):
                cat_in = ui.select(cfg.category_names()) \
                    .props('outlined dense').classes('flex-1')
                ui.button(icon='add',
                          on_click=lambda: _toggle_new_cat_form()) \
                    .props('flat round dense').classes('text-gray-500') \
                    .tooltip('Add a new category')

            new_cat_form = ui.column().classes('gap-2 bg-gray-50 rounded p-2 w-full')
            new_cat_form.set_visibility(False)
            with new_cat_form:
                new_cat_name_in = labeled_input('Name', placeholder='e.g. Pet Store', compact=True)
                new_cat_type_in = labeled_select('Cost type', COST_TYPES, value='variable', compact=True)
                with ui.row().classes('justify-end'):
                    def _add_new_cat():
                        name = new_cat_name_in.value.strip()
                        if not name or name in cfg.category_names():
                            return
                        cfg.categories.append(Category(name=name, cost_type=new_cat_type_in.value, color='#d1d5db'))
                        cat_in.options = cfg.category_names()
                        cat_in.value   = name
                        cat_in.update()
                        new_cat_form.set_visibility(False)
                    ui.button('Add category', on_click=_add_new_cat) \
                        .props('unelevated dense').classes('bg-gray-800 text-white text-xs')

        def _toggle_new_cat_form() -> None:
            new_cat_form.set_visibility(not new_cat_form.visible)

        priority_in = ui.number('Priority (lower = checked first)',
                                value=auto_priority, min=1, max=9999) \
            .props('outlined dense').classes('w-full')
        priority_hint = ui.label(f'Auto-suggested: {auto_priority} ({len(pattern.split())}-word pattern)') \
            .classes('text-xs text-muted -mt-2')

        def _update_priority(_=None) -> None:
            pat = pattern_in.value.strip()
            if not pat:
                return
            suggested = _auto_priority(pat)
            priority_in.value = suggested
            words = len(pat.split())
            priority_hint.set_text(f'Auto-suggested: {suggested} ({words}-word pattern)')

        pattern_in.on('keyup', _update_priority)

        with ui.row().classes('justify-end gap-2 w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat')

            def _ok():
                pat = pattern_in.value.strip()
                cat = cat_in.value
                if pat and cat:
                    cfg.rules.append(CategoryRule(
                        pattern=pat,
                        category=cat,
                        is_regex=False,
                        priority=int(priority_in.value),
                    ))
                    rule_table_fn.refresh()
                    _save_silent(vm, cfg)
                    _run_suggestions(suggestions_state, suggestions_body_fn, rule_table_fn, vm, cfg)
                    notify(f'Rule added for "{pat}" → {cat}', type='positive', position='top')
                dlg.close()

            ui.button('Add rule', on_click=_ok).props('unelevated').classes('bg-indigo-600 text-white')

    dlg.open()


def _save_silent(vm, cfg: CategoryConfig) -> None:
    """Save config and rebuild views without a success toast (used by suggestion flow)."""
    fid = auth.current_family_id()
    save_category_config(cfg, fid)
    try:
        vm.refresh()
    except Exception as e:
        notify(f'View rebuild failed: {e}', type='warning', position='top')


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


def _clear_rules_dialog(cfg: CategoryConfig, refresh_fn) -> None:
    with ui.dialog() as dlg, ui.card().classes('w-80 gap-3'):
        ui.label('Clear all rules?').classes('text-base font-semibold')
        ui.label('This removes all classification rules. You can add your own from scratch. '
                 'Remember to save after clearing.').classes('text-sm text-gray-500')
        with ui.row().classes('justify-end gap-2 w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat')
            def _ok():
                cfg.rules = []
                refresh_fn.refresh()
                dlg.close()
            ui.button('Clear all', on_click=_ok).props('unelevated').classes('bg-red-600 text-white')
    dlg.open()


def _save(vm, cfg: CategoryConfig) -> None:
    fid = auth.current_family_id()
    save_category_config(cfg, fid)
    try:
        vm.refresh()
        notify('Saved & views rebuilt.', type='positive', position='top')
    except Exception as e:
        notify(f'Saved but view rebuild failed: {e}', type='warning', position='top')

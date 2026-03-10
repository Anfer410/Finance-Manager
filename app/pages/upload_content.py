"""
upload_page.py  –  NiceGUI page with bank-rule editor
"""

from nicegui import ui, events
from services.bank_rules import load_rules, save_rules, BankRule, DEFAULT_RULES, _matcher
from services.view_manager import ViewManager
from services.handle_upload import handle_upload, DB_CONN, SCHEMA
from datetime import datetime
import services.auth as auth


# ─────────────────────────────────────────────────────────────────────────────
# Rule editor dialog
# ─────────────────────────────────────────────────────────────────────────────

def open_rule_dialog(rule: BankRule | None, rules: list[BankRule], on_save, on_delete=None):
    """Open a modal to create or edit a BankRule."""
    is_new = rule is None
    if is_new:
        rule = BankRule(bank_name="", prefix="", match_value="")

    with ui.dialog().props("persistent") as dlg, ui.card().classes("w-[480px] gap-0 p-0 overflow-hidden"):
        # Header bar
        with ui.row().classes("w-full items-center justify-between px-5 py-4 bg-gray-50 border-b border-gray-200"):
            ui.label("New rule" if is_new else "Edit rule").classes("font-semibold text-gray-800 text-base")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense").classes("text-gray-400")

        with ui.column().classes("w-full gap-4 px-5 py-5"):
            # Row 1: bank name + prefix
            with ui.row().classes("w-full gap-3"):
                bank_in   = ui.input("Bank name", value=rule.bank_name)\
                               .classes("flex-1").props("outlined dense")
                prefix_in = ui.input("Prefix", value=rule.prefix, placeholder="cap1")\
                               .classes("w-28").props("outlined dense")

            # Row 2: match type + match value
            with ui.row().classes("w-full gap-3 items-end"):
                match_type_sel = ui.select(
                    ["contains", "startswith", "endswith", "exact"],
                    value=rule.match_type,
                    label="Match type",
                ).classes("w-36").props("outlined dense")
                match_val_in = ui.input("Match value", value=rule.match_value,
                                        placeholder="transaction_download")\
                                  .classes("flex-1").props("outlined dense")

            ui.separator().classes("my-1")

            # Person override
            with ui.row().classes("w-full gap-3 items-center"):
                override_toggle = ui.switch(
                    "Override person",
                    value=rule.person_override is not None
                ).classes("text-sm")
                person_override_in = ui.input(
                    "Person value",
                    value=rule.person_override or "",
                    placeholder='mutual  (leave blank to omit)'
                ).classes("flex-1").props("outlined dense")

            def toggle_override(e):
                person_override_in.set_visibility(e.args)
            override_toggle.on("update:model-value", toggle_override)
            person_override_in.set_visibility(rule.person_override is not None)

            # Note
            note_in = ui.input("Note (optional)", value=rule.note)\
                         .classes("w-full").props("outlined dense")

        # Footer actions
        with ui.row().classes("w-full items-center justify-between px-5 py-4 bg-gray-50 border-t border-gray-200"):
            if not is_new and on_delete:
                ui.button("Delete rule", icon="delete_outline", on_click=lambda: (on_delete(), dlg.close()))\
                  .props("flat").classes("text-red-500 text-sm")
            else:
                ui.element("div")   # spacer

            with ui.row().classes("gap-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat").classes("text-gray-500 text-sm")

                def save():
                    rule.bank_name   = bank_in.value.strip()
                    rule.prefix      = prefix_in.value.strip()
                    rule.match_type  = match_type_sel.value
                    rule.match_value = match_val_in.value.strip()
                    rule.person_override    = (
                        person_override_in.value
                        if override_toggle.value
                        else None
                    )
                    rule.note = note_in.value.strip()
                    on_save(rule)
                    dlg.close()

                ui.button("Save rule", on_click=save, icon="check")\
                  .props("unelevated").classes("bg-gray-800 text-white text-sm px-4")

    dlg.open()


# ─────────────────────────────────────────────────────────────────────────────
# Rules panel (inline, collapsible)
# ─────────────────────────────────────────────────────────────────────────────

def _open_rules_dialog():
    """Opens a dialog with the file detection rules editor."""

    rules: list[BankRule] = load_rules()

    MATCH_CHIP_COLOR = {
        "contains":   "bg-blue-100 text-blue-700",
        "startswith": "bg-green-100 text-green-700",
        "endswith":   "bg-yellow-100 text-yellow-700",
        "exact":      "bg-purple-100 text-purple-700",
    }

    with ui.dialog() as dlg, \
         ui.card().classes("w-[680px] rounded-2xl p-0 gap-0 overflow-hidden"):

        # Header
        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("tune").classes("text-zinc-400 text-xl")
                ui.label("File detection rules").classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        # Scrollable rules list
        with ui.scroll_area().style("height: 60vh"):
            rules_container = ui.column().classes("w-full gap-2 px-6 py-4")

            def refresh_panel():
                nonlocal rules
                rules = load_rules()
                rules_container.clear()
                render_rules()

            def render_rules():
                with rules_container:
                    if not rules:
                        ui.label("No rules defined. Add one below.").classes("text-sm text-gray-400 py-2")

                    for idx, rule in enumerate(rules):
                        with ui.row().classes(
                            "w-full items-center gap-3 px-4 py-3 rounded-lg border border-gray-100 "
                            "bg-white hover:bg-gray-50 transition-colors cursor-default"
                        ):
                            ui.label(str(idx + 1)).classes(
                                "text-xs font-mono w-5 text-center text-gray-400 select-none"
                            )

                            with ui.column().classes("gap-0.5"):
                                def move_up(i=idx):
                                    if i > 0:
                                        rules[i], rules[i-1] = rules[i-1], rules[i]
                                        save_rules(rules)
                                        refresh_panel()
                                def move_down(i=idx):
                                    if i < len(rules) - 1:
                                        rules[i], rules[i+1] = rules[i+1], rules[i]
                                        save_rules(rules)
                                        refresh_panel()
                                ui.button(icon="arrow_drop_up",   on_click=move_up) \
                                  .props("flat round dense size=xs").classes("text-gray-300")
                                ui.button(icon="arrow_drop_down", on_click=move_down) \
                                  .props("flat round dense size=xs").classes("text-gray-300")

                            with ui.column().classes("gap-0 min-w-[120px]"):
                                ui.label(rule.bank_name or "—").classes("text-sm font-medium text-gray-800")
                                ui.label(f"prefix: {rule.prefix}").classes("text-xs text-gray-400 font-mono")

                            chip_cls = MATCH_CHIP_COLOR.get(rule.match_type, "bg-gray-100 text-gray-600")
                            with ui.row().classes("items-center gap-1 flex-1"):
                                ui.label(rule.match_type).classes(
                                    f"text-[11px] font-semibold px-1.5 py-0.5 rounded {chip_cls}"
                                )
                                ui.label(f'"{rule.match_value}"').classes("text-sm text-gray-600 font-mono")

                            with ui.row().classes("gap-1"):
                                if rule.person_override is not None:
                                    label = rule.person_override or "no-person"
                                    ui.label(f"person={label}").classes(
                                        "text-[10px] px-1.5 py-0.5 rounded bg-teal-50 text-teal-600 border border-teal-200"
                                    )

                            def edit(r=rule):
                                def on_save(updated: BankRule):
                                    save_rules(rules)
                                    refresh_panel()
                                def on_delete():
                                    rules.remove(r)
                                    save_rules(rules)
                                    refresh_panel()
                                open_rule_dialog(r, rules, on_save=on_save, on_delete=on_delete)

                            ui.button(icon="edit_note", on_click=edit) \
                              .props("flat round dense").classes("text-gray-400 hover:text-gray-700 ml-auto")

                    # Add + Reset row
                    with ui.row().classes("w-full items-center justify-between pt-2 border-t border-gray-100 mt-1"):
                        def add_rule():
                            def on_save(r: BankRule):
                                rules.append(r)
                                save_rules(rules)
                                refresh_panel()
                            open_rule_dialog(None, rules, on_save=on_save)

                        ui.button("Add rule", icon="add", on_click=add_rule) \
                          .props("flat").classes("text-gray-700 text-sm font-medium")

                        def reset_defaults():
                            nonlocal rules
                            rules = list(DEFAULT_RULES)
                            save_rules(rules)
                            refresh_panel()
                            ui.notify("Rules reset to defaults", type="info", position="top")

                        ui.button("Reset defaults", icon="restart_alt", on_click=reset_defaults) \
                          .props("flat").classes("text-gray-400 text-xs")

            render_rules()

        # Footer
        with ui.row().classes("items-center justify-end px-6 py-4 border-t border-zinc-100"):
            ui.button("Close", on_click=dlg.close) \
                .props("flat no-caps").classes("text-zinc-500")

    dlg.open()


# ─────────────────────────────────────────────────────────────────────────────
# Main page
# ─────────────────────────────────────────────────────────────────────────────

def content() -> None:
    person_ref = {"value": ""}   # set after auth check below

    # ── Header ──────────────────────────────────────────────────────────────
    with ui.row().classes("w-full items-center justify-between mb-2"):
        with ui.column().classes("gap-0"):
            ui.label("Data uploader").classes("page-title")
            ui.label(
                "Upload the latest data from your bank account and update your dashboard."
            ).classes("text-sm text-muted")
        ui.button(icon="settings", on_click=_open_rules_dialog) \
            .props("flat round").classes("text-zinc-400").tooltip("File detection rules")

    ui.element("div").classes("divider mb-4")

    # ── Person selector ──────────────────────────────────────────────────────
    # Build person options: admin sees all users, regular user sees only themselves
    if auth.is_admin():
        all_users = auth.get_all_users()
        person_options = [u.person_name for u in all_users if u.is_active]
        # deduplicate while preserving order
        seen = set()
        person_options = [p for p in person_options if not (p in seen or seen.add(p))]
    else:
        person_options = [auth.current_person_name()]

    default_person = auth.current_person_name() or (person_options[0] if person_options else "")
    person_ref["value"] = default_person

    with ui.row().classes("items-center gap-2 mb-4"):
        ui.label("Person:").classes("text-sm text-muted mr-2")
        p = ui.radio(person_options, value=default_person).classes("inline-flex items-center gap-2")
        p.on("update:model-value", lambda e: person_ref.update({"value": e.args}))

    # ── Uploader ─────────────────────────────────────────────────────────────
    with ui.row().classes("w-full mb-6"):
        with ui.column().classes("w-full mx-auto"):
            ui.upload(
                on_upload=lambda e: handle_upload(e, person_ref),
                auto_upload=False,
                multiple=True,
            ).classes("w-full")
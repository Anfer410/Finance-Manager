"""
pages/upload_content.py  —  bank sidebar + upload zone
"""
from __future__ import annotations

import re as _re
from nicegui import ui

import services.auth as auth
from data.bank_rules import BankRule, load_rules, save_rules
from data.bank_config import BankConfig, load_banks, save_banks
from services.transaction_config import load_config, save_config
from services.handle_upload import handle_upload
from services.notifications import notify
from data.db import get_engine, get_schema
from components.bank_wizard_component import (
    open_add_bank_wizard,
    ACCOUNT_COLORS,
    MATCH_TYPE_OPTIONS,
)


def _slugify(name: str) -> str:
    return _re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _bank_alias(rule: BankRule) -> str:
    """Return the human-readable account alias for a BankRule."""
    slug   = _slugify(rule.bank_name)
    prefix = rule.prefix
    if prefix.startswith(slug + "_"):
        return prefix[len(slug) + 1:].replace("_", " ").title()
    if prefix == slug:
        return ""
    return prefix.replace("_", " ").title()


# ─────────────────────────────────────────────────────────────────────────────
# Transaction search dialog  (used by edit dialog to browse raw_ tables)
# ─────────────────────────────────────────────────────────────────────────────

def _open_transaction_search_dialog(
    prefix: str,
    account_type: str = "credit",
    *,
    payment_cat_ref:  dict | None = None,
    payment_desc_ref: dict | None = None,
    checking_pat_ref: dict | None = None,
):
    from sqlalchemy import text

    schema     = get_schema()
    engine     = get_engine()
    tbl        = f"{schema}.transactions_{'credit' if account_type == 'credit' else 'debit'}"

    if account_type == "credit":
        DISPLAY_COLS = ["transaction_date", "description", "debit", "credit"]
    else:
        DISPLAY_COLS = ["transaction_date", "description", "amount"]

    COPY_TARGETS = {}
    if payment_cat_ref:
        COPY_TARGETS["Payment category"] = payment_cat_ref
    if payment_desc_ref:
        COPY_TARGETS["Payment description"] = payment_desc_ref
    if checking_pat_ref:
        COPY_TARGETS["Checking pattern"] = checking_pat_ref

    def _has_data() -> bool:
        try:
            with engine.connect() as conn:
                result = conn.execute(text(
                    f"SELECT 1 FROM {tbl} WHERE account_key = :k LIMIT 1"
                ), {"k": prefix}).fetchone()
                return result is not None
        except Exception:
            return False

    def _query(search: str, date_from: str, date_to: str, page: int, page_size: int = 50):
        try:
            with engine.connect() as conn:
                where_parts = ["account_key = :key"]
                params: dict = {"key": prefix, "offset": (page - 1) * page_size, "limit": page_size}

                if search:
                    where_parts.append("description ILIKE :search")
                    params["search"] = "%" + search + "%"
                if date_from:
                    where_parts.append("transaction_date >= :date_from")
                    params["date_from"] = date_from
                if date_to:
                    where_parts.append("transaction_date <= :date_to")
                    params["date_to"] = date_to

                col_list     = ", ".join(DISPLAY_COLS)
                where_clause = "WHERE " + " AND ".join(where_parts)

                rows = conn.execute(text(
                    f"SELECT {col_list} FROM {tbl} "
                    f"{where_clause} ORDER BY transaction_date DESC "
                    f"LIMIT :limit OFFSET :offset"
                ), params).fetchall()

                count = conn.execute(text(
                    f"SELECT COUNT(*) FROM {tbl} {where_clause}"
                ), {k: v for k, v in params.items() if k not in ("offset", "limit")}).fetchone()[0]

                return DISPLAY_COLS, [list(r) for r in rows], count
        except Exception:
            return [], [], 0

    with ui.dialog().props("maximized") as dlg, \
         ui.card().classes("w-full h-full rounded-none p-0 gap-0 overflow-hidden"):

        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100 shrink-0"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("manage_search").classes("text-zinc-400 text-xl")
                with ui.column().classes("gap-0"):
                    ui.label("Transaction browser").classes("text-base font-semibold text-zinc-800")
                    ui.label(f"Account: {prefix}").classes("text-xs text-zinc-400 font-mono")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        if not _has_data():
            with ui.column().classes("flex-1 items-center justify-center gap-3 py-20"):
                ui.icon("table_view").classes("text-zinc-200 text-6xl")
                ui.label("No data yet").classes("text-lg font-semibold text-zinc-400")
                ui.label(
                    "Upload a CSV for this account first, then come back to browse transactions."
                ).classes("text-sm text-zinc-400")
            dlg.open()
            return

        page_state = {"page": 1, "search": "", "date_from": "", "date_to": ""}

        with ui.row().classes("items-end gap-3 px-6 py-3 border-b border-zinc-100 bg-zinc-50 shrink-0 flex-wrap"):
            with ui.column().classes("gap-0.5"):
                ui.label("Search").classes("text-xs text-zinc-500")
                search_in = ui.input(placeholder="any column...") \
                    .classes("w-64").props("outlined dense clearable")
            with ui.column().classes("gap-0.5"):
                ui.label("From").classes("text-xs text-zinc-500")
                from_in = ui.input(placeholder="YYYY-MM-DD") \
                    .classes("w-36").props("outlined dense clearable")
            with ui.column().classes("gap-0.5"):
                ui.label("To").classes("text-xs text-zinc-500")
                to_in = ui.input(placeholder="YYYY-MM-DD") \
                    .classes("w-36").props("outlined dense clearable")
            ui.button("Search", icon="search", on_click=lambda: _refresh(reset=True)) \
                .props("unelevated dense no-caps") \
                .classes("bg-zinc-800 text-white rounded-lg px-3 self-end")

        if COPY_TARGETS:
            with ui.row().classes("items-center gap-2 px-6 py-2 bg-blue-50 border-b border-blue-100 shrink-0"):
                ui.icon("info_outline").classes("text-blue-400 text-base")
                ui.label(
                    "Click  ⊕  on any row to copy its description into a payment pattern field."
                ).classes("text-xs text-blue-600")

        table_container = ui.column().classes("flex-1 overflow-auto px-6 py-3 gap-0")
        pagination_row  = ui.row().classes("items-center gap-2 px-6 py-3 border-t border-zinc-100 shrink-0")

        @ui.refreshable
        def render_table():
            table_container.clear()
            pagination_row.clear()

            cols, rows, total = _query(
                page_state["search"], page_state["date_from"],
                page_state["date_to"], page_state["page"],
            )
            page_size   = 50
            total_pages = max(1, (total + page_size - 1) // page_size)

            with table_container:
                if not rows:
                    with ui.column().classes("items-center py-12 gap-2"):
                        ui.icon("search_off").classes("text-zinc-200 text-5xl")
                        ui.label("No rows match your filters.").classes("text-sm text-zinc-400")
                else:
                    with ui.scroll_area().classes("w-full"):
                        with ui.element("table").classes("w-full text-xs border-collapse font-mono"):
                            with ui.element("thead"):
                                with ui.element("tr"):
                                    if COPY_TARGETS:
                                        with ui.element("th").classes(
                                            "px-2 py-2 bg-zinc-50 border border-zinc-100 text-zinc-400 w-8"
                                        ):
                                            ui.label("")
                                    for col in cols:
                                        with ui.element("th").classes(
                                            "text-left px-2 py-2 bg-zinc-50 border "
                                            "border-zinc-100 text-zinc-500 font-semibold whitespace-nowrap"
                                        ):
                                            ui.label(col)

                            with ui.element("tbody"):
                                for row in rows:
                                    with ui.element("tr").classes("hover:bg-zinc-50"):
                                        if COPY_TARGETS:
                                            with ui.element("td").classes(
                                                "px-1 py-1 border border-zinc-100 text-center"
                                            ):
                                                desc_idx = next(
                                                    (i for i, c in enumerate(cols)
                                                     if "desc" in c.lower() or "memo" in c.lower()),
                                                    None
                                                )
                                                desc_val = str(row[desc_idx]) if desc_idx is not None else ""
                                                with ui.button_group().props("flat"):
                                                    ui.button(icon="add_circle_outline") \
                                                        .props("flat dense size=xs") \
                                                        .classes("text-zinc-400 hover:text-blue-500")
                                                    with ui.menu().props("auto-close"):
                                                        ui.label("Copy to field:").classes(
                                                            "text-xs text-zinc-400 px-3 pt-2 pb-1 font-semibold"
                                                        )
                                                        for target_label, ref in COPY_TARGETS.items():
                                                            def make_copy(lbl=target_label, r=ref, v=desc_val):
                                                                def _do():
                                                                    r["widget"].set_value(v)
                                                                    notify("Copied to " + lbl,
                                                                           type="positive", position="top")
                                                                return _do
                                                            ui.menu_item(
                                                                target_label + " ← " + (desc_val[:30] + "…" if len(desc_val) > 30 else desc_val),
                                                                on_click=make_copy(),
                                                            ).classes("text-xs")

                                        for cell in row:
                                            with ui.element("td").classes(
                                                "px-2 py-1 border border-zinc-100 text-zinc-600 "
                                                "whitespace-nowrap max-w-xs overflow-hidden text-ellipsis"
                                            ):
                                                ui.label(str(cell) if cell is not None else "")

            with pagination_row:
                ui.label(f"{total:,} rows").classes("text-xs text-zinc-400 mr-2")
                ui.button(icon="chevron_left", on_click=lambda: _page(-1)) \
                    .props("flat round dense size=sm") \
                    .classes("text-zinc-500").bind_enabled_from(
                        page_state, "page", lambda p: p > 1
                    )
                ui.label(f"Page {page_state['page']} of {total_pages}") \
                    .classes("text-xs text-zinc-600")
                ui.button(icon="chevron_right", on_click=lambda: _page(1)) \
                    .props("flat round dense size=sm") \
                    .classes("text-zinc-500").bind_enabled_from(
                        page_state, "page", lambda p: p < total_pages
                    )

        def _refresh(reset: bool = False):
            if reset:
                page_state["page"] = 1
            page_state["search"]    = search_in.value or ""
            page_state["date_from"] = from_in.value or ""
            page_state["date_to"]   = to_in.value or ""
            render_table.refresh()

        def _page(delta: int):
            page_state["page"] += delta
            render_table.refresh()

        render_table()

    dlg.open()


# ─────────────────────────────────────────────────────────────────────────────
# Edit / delete account dialog  (same as before, renamed "bank" → "account")
# ─────────────────────────────────────────────────────────────────────────────

def _open_edit_account_dialog(rule: BankRule, on_save, on_delete):
    """Edit an existing BankRule (account) or delete it."""
    all_users    = auth.get_all_users()
    active_users = [u for u in all_users if u.is_active]
    user_opt_map: dict[str, int] = {
        f"{u.display_name}": u.id
        for u in active_users
    }
    user_opt_labels = list(user_opt_map.keys())

    alias_rows: list[dict] = [
        {"raw_value": rv, "user_id": uid}
        for rv, uid in (rule.member_aliases or {}).items()
    ]

    alias_display = _bank_alias(rule)
    is_credit = rule.account_type == "credit"

    with ui.dialog().props("persistent") as dlg, \
         ui.card().classes("w-[580px] rounded-2xl p-0 gap-0 overflow-hidden"):

        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("tune").classes("text-zinc-400 text-xl")
                ui.label("Edit account").classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        with ui.scroll_area().style("max-height:70vh"):
          with ui.column().classes("px-6 py-5 gap-5 w-full"):

            ui.label("Account details") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            with ui.column().classes("w-full gap-1"):
                ui.label("Bank name").classes("text-sm font-medium text-zinc-700")
                ui.label("The institution name. Changing this does not rename the raw table.") \
                    .classes("text-xs text-zinc-400")
                bank_name_in = ui.input(value=rule.bank_name, placeholder="e.g. Citi") \
                    .classes("w-full").props("outlined dense")

            with ui.column().classes("w-full gap-1"):
                ui.label("Account alias").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "The alias is locked after creation — it forms part of the raw table name."
                ).classes("text-xs text-zinc-400")
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(alias_display or "—") \
                        .classes("flex-1 px-3 py-2 rounded border border-zinc-200 "
                                 "bg-zinc-50 text-zinc-500 text-sm font-mono")
                    ui.icon("lock").classes("text-zinc-300 text-base")

            ui.label("raw table: raw_" + rule.prefix) \
                .classes("text-xs font-mono text-zinc-400 bg-zinc-50 "
                         "border border-zinc-200 rounded px-2 py-1")

            ui.separator()
            ui.label("Filename detection") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            with ui.column().classes("w-full gap-1"):
                ui.label("Match type").classes("text-sm font-medium text-zinc-700")
                match_type_sel = ui.select(
                    MATCH_TYPE_OPTIONS, value=rule.match_type,
                ).classes("w-full").props("outlined dense")

            with ui.column().classes("w-full gap-1"):
                ui.label("Filename value").classes("text-sm font-medium text-zinc-700")
                ui.label("Matched against the uploaded filename without its extension.") \
                    .classes("text-xs text-zinc-400")
                match_val_in = ui.input(
                    value=rule.match_value, placeholder="e.g. transaction_download"
                ).classes("w-full").props("outlined dense")

            credit_col = ui.column().classes("w-full gap-4")
            with credit_col:
                ui.separator()
                with ui.row().classes("items-center justify-between"):
                    ui.label("Credit card settings") \
                        .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
                    ui.button(
                        icon="manage_search",
                        on_click=lambda r=rule: _open_transaction_search_dialog(
                            r.prefix, r.account_type,
                            payment_cat_ref={"widget": payment_cat_in},
                            payment_desc_ref={"widget": payment_desc_in},
                            checking_pat_ref={"widget": checking_pat_in},
                        )
                    ).props("flat round dense size=sm") \
                     .classes("text-zinc-400 hover:text-zinc-700") \
                     .tooltip("Browse transactions to find payment patterns")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Payment category value").classes("text-sm font-medium text-zinc-700")
                    payment_cat_in = ui.input(
                        value=rule.payment_category, placeholder="e.g. Payment/Credit"
                    ).classes("w-full").props("outlined dense")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Payment description pattern").classes("text-sm font-medium text-zinc-700")
                    payment_desc_in = ui.input(
                        value=rule.payment_description, placeholder="e.g. ONLINE PAYMENT"
                    ).classes("w-full").props("outlined dense")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Checking-side payment pattern").classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "Text in your checking account when paying this card. "
                        "Those rows are excluded from debit spend."
                    ).classes("text-xs text-zinc-400")
                    checking_pat_in = ui.input(
                        value=rule.checking_payment_pattern, placeholder="e.g. CAPITAL ONE"
                    ).classes("w-full").props("outlined dense")

            credit_col.set_visibility(is_credit)

            if rule.member_name_column:
                ui.separator()
                ui.label("Member name aliases") \
                    .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
                ui.label(
                    'Column "' + rule.member_name_column + '" stores the member name. '
                    "Map raw values to registered users."
                ).classes("text-xs text-zinc-400")

                aliases_container = ui.column().classes("w-full gap-1")

                @ui.refreshable
                def render_aliases():
                    aliases_container.clear()
                    with aliases_container:
                        if not alias_rows:
                            ui.label("No aliases yet.") \
                                .classes("text-xs text-zinc-400 italic py-1")
                            return
                        for i, row in enumerate(alias_rows):
                            user_label = next(
                                (lbl for lbl, uid in user_opt_map.items()
                                 if uid == row["user_id"]),
                                "user #" + str(row["user_id"])
                            )
                            with ui.row().classes(
                                "w-full items-center gap-2 px-3 py-2 "
                                "rounded-lg bg-zinc-50 border border-zinc-100"
                            ):
                                ui.label(row["raw_value"]) \
                                    .classes("font-mono text-sm text-zinc-700 flex-1")
                                ui.icon("arrow_forward").classes("text-zinc-300 text-base shrink-0")
                                ui.label(user_label).classes("text-sm text-zinc-600 flex-1")
                                ui.button(
                                    icon="close",
                                    on_click=lambda _, idx=i: (
                                        alias_rows.pop(idx),
                                        render_aliases.refresh()
                                    )
                                ).props("flat round dense size=xs").classes("text-zinc-400 shrink-0")

                render_aliases()

                with ui.row().classes("w-full items-end gap-2 mt-1"):
                    raw_val_in = ui.input(placeholder="e.g. JOHN") \
                        .classes("flex-1").props("outlined dense")
                    user_sel = ui.select(
                        user_opt_labels,
                        value=user_opt_labels[0] if user_opt_labels else None,
                    ).classes("flex-1").props("outlined dense")

                    def add_alias():
                        rv  = raw_val_in.value.strip().upper()
                        uid = user_opt_map.get(user_sel.value)
                        if not rv:
                            notify("Enter a raw member value.", type="warning", position="top")
                            return
                        if uid is None:
                            notify("Select a user.", type="warning", position="top")
                            return
                        if any(a["raw_value"] == rv for a in alias_rows):
                            notify("Alias for " + rv + " already exists.", type="warning", position="top")
                            return
                        alias_rows.append({"raw_value": rv, "user_id": uid})
                        raw_val_in.set_value("")
                        render_aliases.refresh()

                    ui.button("Add", icon="add", on_click=add_alias) \
                        .props("unelevated dense no-caps") \
                        .classes("bg-zinc-800 text-white rounded-lg px-3")

            ui.separator()
            with ui.column().classes("w-full gap-1"):
                ui.label("Person override (optional)").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "Pin every row from this account to specific people."
                ).classes("text-xs text-zinc-400")

            has_override = bool(rule.person_override)
            override_ids: set[int] = set(rule.person_override or [])
            override_sw = ui.switch("Enable person override", value=has_override).classes("text-sm")
            override_container = ui.column().classes("w-full gap-1 pl-1")
            override_container.set_visibility(has_override)
            override_sw.on("update:model-value",
                           lambda e: override_container.set_visibility(e.args))
            with override_container:
                for u in active_users:
                    chk = ui.checkbox(
                        f"{u.display_name}",
                        value=(u.id in override_ids),
                    )
                    chk.on("update:model-value",
                           lambda e, uid=u.id: (
                               override_ids.add(uid) if e.args else override_ids.discard(uid)
                           ))

        with ui.row().classes("items-center justify-between px-6 py-4 border-t border-zinc-100"):
            ui.button("Delete account", icon="delete_outline", on_click=lambda: _confirm_delete()) \
                .props("flat no-caps").classes("text-red-400 text-sm")
            with ui.row().classes("gap-2"):
                ui.button("Cancel", on_click=dlg.close) \
                    .props("flat no-caps").classes("text-zinc-500")
                ui.button("Save changes", icon="check", on_click=lambda: _save()) \
                    .props("unelevated no-caps") \
                    .classes("bg-zinc-800 text-white px-4 rounded-lg")

    def _save():
        bname = bank_name_in.value.strip()
        mval  = match_val_in.value.strip()
        if not bname or not mval:
            notify("Bank name and filename value are required.", type="warning", position="top")
            return
        rule.bank_name                = bname
        rule.match_type               = match_type_sel.value
        rule.match_value              = mval
        rule.payment_category         = payment_cat_in.value.strip()  if is_credit else ""
        rule.payment_description      = payment_desc_in.value.strip() if is_credit else ""
        rule.checking_payment_pattern = checking_pat_in.value.strip() if is_credit else ""
        rule.member_aliases           = {a["raw_value"]: a["user_id"] for a in alias_rows}
        rule.person_override          = sorted(override_ids) if override_sw.value and override_ids else None
        dlg.close()
        on_save(rule)

    def _confirm_delete():
        with ui.dialog() as confirm_dlg, \
             ui.card().classes("rounded-2xl p-0 gap-0 overflow-hidden w-80"):
            with ui.column().classes("px-6 py-5 gap-3"):
                ui.label("Delete account?").classes("text-base font-semibold text-zinc-800")
                ui.label(
                    'This removes the rule for "' + rule.bank_name + '". '
                    "Uploaded data in the raw table is not deleted."
                ).classes("text-sm text-zinc-500")
            with ui.row().classes("items-center justify-end gap-2 px-6 py-4 border-t border-zinc-100"):
                ui.button("Cancel", on_click=confirm_dlg.close) \
                    .props("flat no-caps").classes("text-zinc-500")
                ui.button(
                    "Delete", icon="delete",
                    on_click=lambda: (confirm_dlg.close(), dlg.close(), on_delete(rule))
                ).props("unelevated no-caps") \
                 .classes("bg-red-500 text-white px-4 rounded-lg")
        confirm_dlg.open()

    dlg.open()


# ─────────────────────────────────────────────────────────────────────────────
# Bank settings dialog  (name + transfer patterns)
# ─────────────────────────────────────────────────────────────────────────────

def _open_bank_settings_dialog(bank: BankConfig, on_save, on_delete, bank_rules: list | None = None):
    """Edit bank settings: name and family-wide transfer patterns."""
    fid = auth.current_family_id()
    txn_cfg = load_config(fid)
    patterns: list[str] = list(txn_cfg.transfer_patterns)

    with ui.dialog().props("persistent") as dlg, \
         ui.card().classes("w-[520px] rounded-2xl p-0 gap-0 overflow-hidden"):

        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("account_balance").classes("text-zinc-400 text-xl")
                ui.label("Bank settings").classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        with ui.scroll_area().style("max-height:70vh"):
          with ui.column().classes("px-6 py-5 gap-5 w-full"):

            ui.label("Bank details") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            with ui.column().classes("w-full gap-1"):
                ui.label("Bank name").classes("text-sm font-medium text-zinc-700")
                ui.label("Display name for this bank — does not affect table names.") \
                    .classes("text-xs text-zinc-400")
                name_in = ui.input(value=bank.name, placeholder="e.g. Capital One") \
                    .classes("w-full").props("outlined dense")

            ui.label("slug: " + bank.slug) \
                .classes("text-xs font-mono text-zinc-400 bg-zinc-50 "
                         "border border-zinc-200 rounded px-2 py-1")

            ui.separator()
            ui.label("Transfer exclusion patterns") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
            ui.label(
                "Transactions whose descriptions contain any of these patterns "
                "are excluded from spend and income totals across all accounts. "
                "These apply family-wide — not just to this bank."
            ).classes("text-xs text-zinc-400")

            chips_container = ui.row().classes("flex-wrap gap-2 min-h-8")
            new_pat_in = ui.input(placeholder="e.g. TRANSFER") \
                .classes("w-full").props("outlined dense")

            @ui.refreshable
            def render_chips():
                chips_container.clear()
                with chips_container:
                    if not patterns:
                        ui.label("No patterns yet.") \
                            .classes("text-xs text-zinc-400 italic py-1")
                        return
                    for i, pat in enumerate(patterns):
                        with ui.row().classes(
                            "items-center gap-1 px-2.5 py-1 rounded-full border "
                            "bg-zinc-50 border-zinc-200 text-zinc-700 text-xs"
                        ):
                            ui.label(pat).classes("font-mono")
                            ui.button(
                                icon="close",
                                on_click=lambda _, idx=i: (
                                    patterns.pop(idx),
                                    render_chips.refresh()
                                )
                            ).props("flat round dense size=xs").classes("text-zinc-400 -mr-1")

            render_chips()

            def add_pattern():
                val = new_pat_in.value.strip().upper()
                if not val:
                    return
                if val in patterns:
                    notify("Pattern already exists.", type="warning", position="top")
                    return
                patterns.append(val)
                new_pat_in.set_value("")
                render_chips.refresh()

            with ui.row().classes("w-full gap-2 items-end"):
                new_pat_in.on("keydown.enter", lambda _: add_pattern())
                ui.button("Add", icon="add", on_click=add_pattern) \
                    .props("unelevated dense no-caps") \
                    .classes("bg-zinc-800 text-white rounded-lg px-3")

        with ui.row().classes("items-center justify-between px-6 py-4 border-t border-zinc-100"):
            ui.button("Delete bank", icon="delete_outline", on_click=lambda: _confirm_delete()) \
                .props("flat no-caps").classes("text-red-400 text-sm")
            with ui.row().classes("gap-2"):
                ui.button("Cancel", on_click=dlg.close) \
                    .props("flat no-caps").classes("text-zinc-500")
                ui.button("Save", icon="check", on_click=lambda: _save()) \
                    .props("unelevated no-caps") \
                    .classes("bg-zinc-800 text-white px-4 rounded-lg")

    def _save():
        new_name = name_in.value.strip()
        if not new_name:
            notify("Bank name is required.", type="warning", position="top")
            return
        bank.name = new_name
        txn_cfg.transfer_patterns = list(patterns)
        save_config(txn_cfg, fid)
        dlg.close()
        on_save(bank)

    def _confirm_delete():
        affected = bank_rules or []
        with ui.dialog() as confirm_dlg, \
             ui.card().classes("rounded-2xl p-0 gap-0 overflow-hidden w-80"):
            with ui.column().classes("px-6 py-5 gap-3"):
                ui.label("Delete bank?").classes("text-base font-semibold text-zinc-800")
                if affected:
                    aliases = [_bank_alias(r) or r.prefix for r in affected]
                    acct_list = ", ".join(aliases)
                    ui.label(
                        f'"{bank.name}" has {len(affected)} account(s): {acct_list}. '
                        "Deleting the bank will also remove these account configurations. "
                        "Uploaded transaction data is not affected."
                    ).classes("text-sm text-zinc-500")
                else:
                    ui.label(
                        f'Remove the bank "{bank.name}"? '
                        "No accounts are configured under it."
                    ).classes("text-sm text-zinc-500")
            with ui.row().classes("items-center justify-end gap-2 px-6 py-4 border-t border-zinc-100"):
                ui.button("Cancel", on_click=confirm_dlg.close) \
                    .props("flat no-caps").classes("text-zinc-500")
                ui.button(
                    "Delete", icon="delete",
                    on_click=lambda: (confirm_dlg.close(), dlg.close(), on_delete(bank, affected))
                ).props("unelevated no-caps") \
                 .classes("bg-red-500 text-white px-4 rounded-lg")
        confirm_dlg.open()

    dlg.open()


# ─────────────────────────────────────────────────────────────────────────────
# Create bank dialog  (quick — just a name)
# ─────────────────────────────────────────────────────────────────────────────

def _open_create_bank_dialog(on_save):
    with ui.dialog().props("persistent") as dlg, \
         ui.card().classes("w-96 rounded-2xl p-0 gap-0 overflow-hidden"):

        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("add_card").classes("text-zinc-400 text-xl")
                ui.label("Add bank").classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        with ui.column().classes("px-6 py-5 gap-3 w-full"):
            ui.label("Bank name").classes("text-sm font-medium text-zinc-700")
            ui.label("The institution name, e.g. Chase, Capital One, Citi.") \
                .classes("text-xs text-zinc-400")
            name_in = ui.input(placeholder="e.g. Chase") \
                .classes("w-full").props("outlined dense autofocus")

        with ui.row().classes("items-center justify-end gap-2 px-6 py-4 border-t border-zinc-100"):
            ui.button("Cancel", on_click=dlg.close) \
                .props("flat no-caps").classes("text-zinc-500")
            ui.button("Create bank", icon="add", on_click=lambda: _create()) \
                .props("unelevated no-caps") \
                .classes("bg-zinc-800 text-white px-4 rounded-lg")

    def _create():
        name = name_in.value.strip()
        if not name:
            notify("Bank name is required.", type="warning", position="top")
            return
        banks = load_banks(auth.current_family_id())
        slug  = _slugify(name)
        if any(b.slug == slug for b in banks):
            notify(f'A bank named "{name}" already exists.', type="warning", position="top")
            return
        new_bank = BankConfig.from_name(name)
        banks.append(new_bank)
        save_banks(banks, auth.current_family_id())
        notify(f"Bank \"{name}\" created.", type="positive", position="top")
        dlg.close()
        on_save(new_bank)

    dlg.open()


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_bank_for_rule(rule: BankRule, banks: list[BankConfig]) -> BankConfig | None:
    """Find the BankConfig that owns this rule (matched by bank_name slug)."""
    rule_slug = _slugify(rule.bank_name)
    return next((b for b in banks if b.slug == rule_slug), None)


def _ensure_banks_for_rules(rules: list[BankRule]) -> list[BankConfig]:
    """Load banks; auto-create BankConfig entries for any banks implied by rules."""
    banks = load_banks(auth.current_family_id())
    changed = False
    for rule in rules:
        slug = _slugify(rule.bank_name)
        if not any(b.slug == slug for b in banks):
            banks.append(BankConfig.from_name(rule.bank_name))
            changed = True
    if changed:
        save_banks(banks, auth.current_family_id())
    return banks


# ─────────────────────────────────────────────────────────────────────────────
# Main page
# ─────────────────────────────────────────────────────────────────────────────

def content() -> None:
    person_ref   = {"value": None}
    selected_ref = {"value": "auto"}   # "auto" or rule.prefix

    if auth.is_family_head(): # display only users that are part of family
        import services.family_service as fam
        fam_id = auth.get_user_by_id(auth.current_user_id()).family_id
        fam_members = fam.get_family_members(fam_id)
        active_users = [auth.get_user_by_id(u.user_id) for u in fam_members if u.is_active]
    else:
        cur = auth.get_user_by_id(auth.current_user_id())
        active_users = [cur] if cur else []

    person_options  = {u.id: u.display_name for u in active_users}
    default_user_id = auth.current_user_id() or (active_users[0].id if active_users else None)
    person_ref["value"] = default_user_id

    with ui.row().classes("w-full items-center justify-between mb-2"):
        with ui.column().classes("gap-0"):
            ui.label("Banks").classes("page-title")
            ui.label(
                "Upload the latest data from your bank account and update your dashboard."
            ).classes("text-sm text-muted")

    ui.element("div").classes("divider mb-4")

    @ui.refreshable
    def page_body():
        rules = load_rules(auth.current_family_id()) or []
        banks = _ensure_banks_for_rules(rules)

        def _select(prefix: str):
            selected_ref["value"] = prefix
            page_body.refresh()

        # ── Empty state ──────────────────────────────────────────────────────
        if not banks:
            with ui.column().classes("w-full items-center justify-center py-24 gap-5"):
                ui.icon("account_balance").classes("text-zinc-200 text-7xl")
                ui.label("No banks configured yet") \
                    .classes("text-xl font-semibold text-zinc-400")
                ui.label("Add your first bank to start uploading transactions.") \
                    .classes("text-sm text-zinc-400")
                ui.button(
                    "Add bank", icon="add_card",
                    on_click=lambda: _open_create_bank_dialog(
                        on_save=lambda b: open_add_bank_wizard(
                            on_done=page_body.refresh,
                            preselected_bank_slug=b.slug,
                        )
                    ),
                ).props("unelevated no-caps") \
                 .classes("bg-zinc-800 text-white px-6 rounded-xl mt-2")
            return

        # ── Callbacks ────────────────────────────────────────────────────────
        def _edit_account(r: BankRule):
            def on_save(updated: BankRule):
                fid = auth.current_family_id()
                all_rules = load_rules(fid)
                idx = next(
                    (i for i, x in enumerate(all_rules) if x.prefix == r.prefix), None
                )
                if idx is not None:
                    all_rules[idx] = updated
                else:
                    all_rules.append(updated)
                save_rules(all_rules, fid)
                notify("Saved: " + updated.bank_name, type="positive", position="top")
                page_body.refresh()

            def on_delete(deleted: BankRule):
                fid = auth.current_family_id()
                all_rules = load_rules(fid)
                all_rules = [x for x in all_rules if x.prefix != deleted.prefix]
                save_rules(all_rules, fid)
                notify("Deleted: " + deleted.bank_name, type="info", position="top")
                page_body.refresh()

            _open_edit_account_dialog(r, on_save=on_save, on_delete=on_delete)

        def _edit_bank(b: BankConfig):
            b_rules = [r for r in rules if _slugify(r.bank_name) == b.slug]

            def on_save(updated: BankConfig):
                fid = auth.current_family_id()
                all_banks = load_banks(fid)
                idx = next((i for i, x in enumerate(all_banks) if x.slug == b.slug), None)
                if idx is not None:
                    all_banks[idx] = updated
                else:
                    all_banks.append(updated)
                save_banks(all_banks, fid)
                notify("Saved: " + updated.name, type="positive", position="top")
                page_body.refresh()

            def on_delete(deleted: BankConfig, affected_rules: list):
                fid = auth.current_family_id()
                all_banks = load_banks(fid)
                all_banks = [x for x in all_banks if x.slug != deleted.slug]
                save_banks(all_banks, fid)
                if affected_rules:
                    dead_prefixes = {r.prefix for r in affected_rules}
                    all_rules = load_rules(fid)
                    save_rules([r for r in all_rules if r.prefix not in dead_prefixes], fid)
                notify("Deleted: " + deleted.name, type="info", position="top")
                page_body.refresh()

            _open_bank_settings_dialog(b, on_save=on_save, on_delete=on_delete, bank_rules=b_rules)

        with ui.row().classes("w-full gap-5 items-start"):

            # ── Sidebar ──────────────────────────────────────────────────────
            with ui.column().classes(
                "gap-0 shrink-0 w-56 bg-zinc-50 rounded-xl border border-zinc-100 p-2"
            ):
                # Auto-detect entry
                is_auto = selected_ref["value"] == "auto"
                with ui.row().classes(
                    "w-full flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer "
                    "transition-colors border mb-1 " +
                    ("bg-zinc-800 border-zinc-700" if is_auto
                     else "bg-white border-zinc-100 hover:bg-zinc-50")
                ).on("click", lambda: _select("auto")):
                    ui.icon("auto_awesome").classes(
                        "text-xl " + ("text-white" if is_auto else "text-zinc-400")
                    )
                    ui.label("Auto-detect").classes(
                        "text-sm font-medium " +
                        ("text-white" if is_auto else "text-zinc-700")
                    )

                ui.separator().classes("mb-1")

                # Grouped by bank
                for bank in banks:
                    bank_rules = [r for r in rules if _slugify(r.bank_name) == bank.slug]

                    # Bank row
                    with ui.row().classes("w-full items-center gap-1 px-1 py-0.5"):
                        ui.label(bank.name).classes(
                            "text-[11px] font-semibold text-zinc-500 uppercase "
                            "tracking-wide flex-1 truncate px-1"
                        )
                        ui.button(
                            icon="settings",
                            on_click=lambda b=bank: _edit_bank(b)
                        ).props("flat round dense size=xs") \
                         .classes("text-zinc-300 hover:text-zinc-600 shrink-0")

                    # Account entries
                    for rule in bank_rules:
                        is_sel = selected_ref["value"] == rule.prefix
                        _, acct_icon = ACCOUNT_COLORS.get(rule.account_type, ("", "account_balance"))
                        alias = _bank_alias(rule)

                        with ui.row().classes(
                            "w-full items-center gap-2 px-2 py-2 rounded-lg cursor-pointer "
                            "transition-colors border min-w-0 pl-4 " +
                            ("bg-zinc-800 border-zinc-700" if is_sel
                             else "bg-white border-zinc-100 hover:bg-zinc-50")
                        ).on("click", lambda r=rule: _select(r.prefix)):
                            ui.icon(acct_icon).classes(
                                "text-base shrink-0 " +
                                ("text-white" if is_sel else "text-zinc-400")
                            )
                            ui.label(alias or rule.bank_name).classes(
                                "text-sm truncate flex-1 " +
                                ("text-white" if is_sel else "text-zinc-700")
                            )
                            ui.button(icon="settings") \
                             .props("flat round dense size=sm " +
                                    ("color=white" if is_sel else "color=grey-6")) \
                             .classes("shrink-0 opacity-60 hover:opacity-100") \
                             .on("click.stop", lambda e, r=rule: _edit_account(r))

                    # + Add Account
                    ui.button(
                        "Add account", icon="add",
                        on_click=lambda b=bank: open_add_bank_wizard(
                            on_done=page_body.refresh,
                            preselected_bank_slug=b.slug,
                        ),
                    ).props("flat no-caps dense") \
                     .classes("text-zinc-400 text-xs w-full justify-start pl-5 py-1")

                    if bank != banks[-1]:
                        ui.separator().classes("my-1")

                ui.separator().classes("my-1")

                # + Add Bank
                ui.button(
                    "Add bank", icon="add",
                    on_click=lambda: _open_create_bank_dialog(
                        on_save=lambda b: open_add_bank_wizard(
                            on_done=page_body.refresh,
                            preselected_bank_slug=b.slug,
                        )
                    ),
                ).props("flat no-caps dense") \
                 .classes("text-zinc-500 text-xs w-full justify-start px-3")

            # ── Upload area ──────────────────────────────────────────────────
            with ui.column().classes("flex-1 gap-4 min-w-0"):
                with ui.row().classes("items-center gap-3"):
                    ui.label("Person:").classes("text-sm text-zinc-500 shrink-0")
                    ui.radio(
                        person_options, value=person_ref["value"],
                        on_change=lambda e: person_ref.update({"value": int(e.value)}),
                    ).classes("inline-flex items-center gap-3")

                active_rule = next(
                    (r for r in rules if r.prefix == selected_ref["value"]), None
                )
                if active_rule:
                    acct_cls, acct_icon = ACCOUNT_COLORS.get(
                        active_rule.account_type, ("", "account_balance")
                    )
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        with ui.row().classes(
                            "items-center gap-2 px-3 py-1.5 rounded-full border text-xs " + acct_cls
                        ):
                            ui.icon(acct_icon).classes("text-base")
                            ui.label(active_rule.bank_name).classes("font-semibold")

                        with ui.row().classes(
                            "items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs "
                            "bg-zinc-50 border-zinc-200 text-zinc-600"
                        ):
                            ui.icon("search").classes("text-base text-zinc-400")
                            ui.label(
                                active_rule.match_type + ': "' + active_rule.match_value + '"'
                            ).classes("font-mono")

                        if active_rule.prefix:
                            with ui.row().classes(
                                "items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs "
                                "bg-zinc-50 border-zinc-200 text-zinc-600"
                            ):
                                ui.icon("tag").classes("text-base text-zinc-400")
                                ui.label(active_rule.prefix).classes("font-mono")

                        if active_rule.person_override is not None:
                            _uid_name = {u.id: u.display_name for u in active_users}
                            _names = ", ".join(
                                _uid_name.get(uid, f"#{uid}")
                                for uid in (active_rule.person_override or [])
                            ) or "—"
                            with ui.row().classes(
                                "items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs "
                                "bg-teal-50 border-teal-200 text-teal-700"
                            ):
                                ui.icon("people").classes("text-base")
                                ui.label(_names).classes("font-mono")
                else:
                    with ui.row().classes("items-center gap-2"):
                        with ui.row().classes(
                            "items-center gap-2 px-3 py-1.5 rounded-full border text-xs "
                            "bg-zinc-50 border-zinc-200 text-zinc-400"
                        ):
                            ui.icon("auto_awesome").classes("text-base text-zinc-300")
                            ui.label("Auto-detect")
                        ui.label("filename will be matched against all rules") \
                            .classes("text-xs text-zinc-400")

                with ui.element("div").classes(
                    "w-full rounded-xl border-2 border-dashed border-zinc-200 "
                    "bg-white hover:border-zinc-400 transition-colors p-1"
                ):
                    _rule_snap = active_rule
                    ui.upload(
                        on_upload=lambda e, r=_rule_snap: handle_upload(e, person_ref, bank_rule=r),
                        auto_upload=False,
                        multiple=True,
                        label="Drop CSV files here or click to browse",
                    ).classes("w-full").props("flat")

    page_body()

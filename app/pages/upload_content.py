"""
pages/upload_content.py  —  bank sidebar + upload zone
"""
from __future__ import annotations

import re as _re
from nicegui import ui

import re as _re2
from sqlalchemy import text

import services.auth as auth
from services.ui_inputs import labeled_input, labeled_select

def _cur() -> str:
    return auth.current_currency_prefix()
from data.bank_rules import BankRule, load_rules, save_rules
from data.bank_config import BankConfig, load_banks, save_banks
from services.transaction_config import (
    load_config, save_config, NamedTransferExclusion,
)
from services.handle_upload import handle_upload
from services.notifications import notify
from data.db import get_engine, get_schema
from components.bank_wizard_component import (
    open_add_bank_wizard,
    ACCOUNT_COLORS,
    MATCH_TYPE_OPTIONS,
    DATE_FORMAT_OPTIONS,
)
from data.currencies import CURRENCY_OPTIONS


# ─────────────────────────────────────────────────────────────────────────────
# Transfer review helpers
# ─────────────────────────────────────────────────────────────────────────────

_REF_RE = _re2.compile(
    r"\s+REF\s+#\S+|\s+ON\s+\d{2}/\d{2}/\d{2}|\s+\d{6,}\s*\S*$",
    _re2.IGNORECASE,
)


def _transfer_group_key(description: str) -> str:
    """Strip trailing reference numbers / dates so similar transfers group together."""
    return _REF_RE.sub("", description).strip()


def _extract_pattern_suggestion(description: str) -> str:
    """
    Try to pull a concise account fragment from a transfer description.
    e.g. 'ONLINE TRANSFER TO XXXXXX5045 REF ...' → 'XXXXXX5045'
    Falls back to the group key if no fragment found.
    """
    m = _re2.search(r"[Xx]{4,}\d{4}", description)
    if m:
        return m.group(0)
    return _transfer_group_key(description)


def _get_pending_transfers(fid: int, uid: int, is_head: bool) -> list[dict]:
    """Return all unreviewed potential_transfer flags with transaction detail."""
    engine = get_engine()
    schema = get_schema()
    person_filter = "" if is_head else "AND :uid = ANY(d.person)"
    sql = text(f"""
        SELECT f.id          AS flag_id,
               f.amount,
               f.detected_at,
               d.description,
               d.transaction_date,
               d.account_key,
               d.person
        FROM   {schema}.transaction_flags f
        JOIN   {schema}.transactions_debit d ON d.id = f.tx_id
        WHERE  f.family_id  = :fid
          AND  f.flag_type  = 'potential_transfer'
          AND  NOT f.user_kept
          {person_filter}
        ORDER BY d.transaction_date DESC
    """)
    params: dict = {"fid": fid}
    if not is_head:
        params["uid"] = uid
    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


def _count_pending_transfers(fid: int, uid: int, is_head: bool) -> int:
    engine = get_engine()
    schema = get_schema()
    person_filter = "" if is_head else "AND :uid = ANY(d.person)"
    sql = text(f"""
        SELECT COUNT(*)
        FROM   {schema}.transaction_flags f
        JOIN   {schema}.transactions_debit d ON d.id = f.tx_id
        WHERE  f.family_id  = :fid
          AND  f.flag_type  = 'potential_transfer'
          AND  NOT f.user_kept
          {person_filter}
    """)
    params: dict = {"fid": fid}
    if not is_head:
        params["uid"] = uid
    with engine.connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _set_flag_user_kept(flag_id: int, user_kept: bool) -> None:
    engine = get_engine()
    schema = get_schema()
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {schema}.transaction_flags SET user_kept = :k WHERE id = :id"),
            {"k": user_kept, "id": flag_id},
        )


def _get_reviewed_transfers(fid: int, uid: int, is_head: bool) -> list[dict]:
    """Return all user_kept=TRUE potential_transfer flags with transaction detail."""
    engine = get_engine()
    schema = get_schema()
    person_filter = "" if is_head else "AND :uid = ANY(d.person)"
    sql = text(f"""
        SELECT f.id          AS flag_id,
               f.amount,
               d.description,
               d.transaction_date,
               d.account_key
        FROM   {schema}.transaction_flags f
        JOIN   {schema}.transactions_debit d ON d.id = f.tx_id
        WHERE  f.family_id  = :fid
          AND  f.flag_type  = 'potential_transfer'
          AND  f.user_kept
          {person_filter}
        ORDER BY d.transaction_date DESC
    """)
    params: dict = {"fid": fid}
    if not is_head:
        params["uid"] = uid
    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


_TRANSFER_CANDIDATE_KEYWORDS = [
    "ZELLE", "VENMO", "CASHAPP", "CASH APP", "PAYPAL",
    "WIRE", "ACH", "XFER", "BILLPAY", "BILL PAY",
    "P2P", "SEND MONEY", "SQUARE CASH",
]


def _get_pattern_impact(patterns: list[str], fid: int) -> dict[str, tuple[int, int]]:
    """
    Return {pattern: (inflow_count, outflow_count)} for checking accounts.
    Counts transactions_debit rows whose description ILIKE '%pattern%'.
    """
    if not patterns:
        return {}
    engine = get_engine()
    schema = get_schema()
    rules = load_rules(fid)
    checking_keys = [r.prefix for r in rules if r.account_type == "checking"]
    if not checking_keys:
        return {p: (0, 0) for p in patterns}

    values_clause = ", ".join(f"(:p{i})" for i in range(len(patterns)))
    key_clause    = ", ".join(f":ck{j}" for j in range(len(checking_keys)))
    params: dict  = {"fid": fid}
    for i, p in enumerate(patterns):
        params[f"p{i}"] = p
    for j, k in enumerate(checking_keys):
        params[f"ck{j}"] = k

    sql = text(f"""
        SELECT
            unnested.pat,
            COUNT(*) FILTER (WHERE d.amount > 0) AS inflows,
            COUNT(*) FILTER (WHERE d.amount < 0) AS outflows
        FROM {schema}.transactions_debit d
        CROSS JOIN (VALUES {values_clause}) AS unnested(pat)
        WHERE d.family_id = :fid
          AND d.account_key IN ({key_clause})
          AND d.description ILIKE '%' || unnested.pat || '%'
        GROUP BY unnested.pat
    """)

    result = {p: (0, 0) for p in patterns}
    with engine.connect() as conn:
        for row in conn.execute(sql, params).mappings():
            result[row["pat"]] = (row["inflows"] or 0, row["outflows"] or 0)
    return result


def _get_pattern_suggestions(fid: int, existing: list[str]) -> list[str]:
    """
    Return keywords from _TRANSFER_CANDIDATE_KEYWORDS that appear in unflagged
    checking outflows but are not already in the configured transfer_patterns.
    """
    engine = get_engine()
    schema = get_schema()
    rules = load_rules(fid)
    checking_keys = [r.prefix for r in rules if r.account_type == "checking"]
    if not checking_keys:
        return []

    existing_upper = {p.upper() for p in existing}
    candidates = [c for c in _TRANSFER_CANDIDATE_KEYWORDS if c.upper() not in existing_upper]
    if not candidates:
        return []

    key_clause    = ", ".join(f":ck{j}" for j in range(len(checking_keys)))
    values_clause = ", ".join(f"(:c{i})" for i in range(len(candidates)))
    params: dict  = {"fid": fid}
    for j, k in enumerate(checking_keys):
        params[f"ck{j}"] = k
    for i, c in enumerate(candidates):
        params[f"c{i}"] = c

    sql = text(f"""
        SELECT cand.kw
        FROM (VALUES {values_clause}) AS cand(kw)
        WHERE EXISTS (
            SELECT 1
            FROM {schema}.transactions_debit d
            WHERE d.family_id = :fid
              AND d.account_key IN ({key_clause})
              AND d.amount < 0
              AND d.description ILIKE '%' || cand.kw || '%'
              AND d.id NOT IN (
                  SELECT tx_id
                  FROM {schema}.transaction_flags
                  WHERE family_id = :fid
                    AND tx_table  = 'debit'
                    AND flag_type IN ('internal_transfer', 'credit_payment', 'potential_transfer')
                    AND NOT user_kept
              )
        )
        ORDER BY cand.kw
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [r["kw"] for r in rows]



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
    payment_desc_ref: dict | None = None,
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
    if payment_desc_ref:
        COPY_TARGETS["Payment description"] = payment_desc_ref

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

        def _on_search_enter(e) -> None:
            key = (e.args.get('key') if isinstance(e.args, dict) else
                   e.args[0].get('key') if isinstance(e.args, list) else '')
            if key == 'Enter':
                _refresh(reset=True)

        with ui.row().classes("items-end gap-3 px-6 py-3 border-b border-zinc-100 bg-zinc-50 shrink-0 flex-wrap"):
            search_in = labeled_input('Search', placeholder="any column...", compact=True, classes='w-64') \
                .props('clearable').on('keydown', _on_search_enter)
            from_in = labeled_input('From', placeholder="YYYY-MM-DD", compact=True, classes='w-36') \
                .props('clearable').on('keydown', _on_search_enter)
            to_in = labeled_input('To', placeholder="YYYY-MM-DD", compact=True, classes='w-36') \
                .props('clearable').on('keydown', _on_search_enter)
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

def _open_date_migration_wizard(rule: BankRule, on_done):
    """
    Two-step dialog: pick source/target date format → preview → apply.
    Rewrites transaction_date for all rows in the account where the date was
    incorrectly parsed under a different format assumption.
    """
    from services.upload_manager import preview_redate, redate_account
    from services.view_manager import ViewManager

    # "parsed as" options: same list but the auto entry gets a clearer label
    PARSED_AS_OPTIONS = {
        "": 'Auto-detect (treated ambiguous dates as MM/DD/YYYY)',
        **{k: v for k, v in DATE_FORMAT_OPTIONS.items() if k != ""},
    }

    # Pre-fill "parsed as" from the current rule setting
    default_from = rule.date_format or ""

    preview_result: dict = {}

    with ui.dialog().props("persistent") as dlg, \
         ui.card().classes("w-[640px] rounded-2xl p-0 gap-0 overflow-hidden"):

        with ui.row().classes(
            "items-center justify-between px-6 py-4 border-b border-zinc-100"
        ):
            with ui.row().classes("items-center gap-3"):
                ui.icon("date_range").classes("text-zinc-400 text-xl")
                with ui.column().classes("gap-0"):
                    ui.label("Migrate existing dates").classes(
                        "text-base font-semibold text-zinc-800"
                    )
                    ui.label(rule.bank_name + "  ·  " + rule.prefix).classes(
                        "text-xs text-zinc-400 font-mono"
                    )
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        with ui.scroll_area().style("max-height:70vh"):
          with ui.column().classes("px-6 py-5 gap-5 w-full"):

            ui.label(
                "Dates in the consolidated table were stored using the wrong format. "
                "Select what format they were incorrectly parsed as and what they "
                "actually are. A preview shows sample corrections before you apply."
            ).classes("text-xs text-zinc-400")

            ui.separator()
            ui.label("Format mismatch") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            with ui.row().classes("w-full gap-4 items-end"):
                with ui.column().classes("flex-1 gap-1"):
                    from_sel = labeled_select(
                        'Stored / parsed as (wrong)',
                        PARSED_AS_OPTIONS,
                        value=default_from,
                    )

                ui.icon("arrow_forward").classes("text-zinc-400 text-xl mb-2 shrink-0")

                with ui.column().classes("flex-1 gap-1"):
                    to_sel = labeled_select(
                        'Actually in (correct)',
                        {k: v for k, v in DATE_FORMAT_OPTIONS.items() if k != ""},
                        value=None,
                    )

            ui.separator()
            ui.label("Preview") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            stats_row  = ui.row().classes("flex-wrap gap-4")
            preview_container = ui.column().classes("w-full gap-1")
            apply_btn_ref: dict = {"btn": None}

            @ui.refreshable
            def render_preview():
                stats_row.clear()
                preview_container.clear()

                if not preview_result:
                    with preview_container:
                        ui.label("Click Preview to see what would change.") \
                            .classes("text-xs text-zinc-400 italic")
                    if apply_btn_ref["btn"]:
                        apply_btn_ref["btn"].disable()
                    return

                p = preview_result
                with stats_row:
                    for label, val, color in [
                        ("Will update",     p["updatable"],          "text-green-600"),
                        ("No change / invalid", p["skipped_invalid"], "text-zinc-400"),
                        ("Cross-year (skipped)", p["skipped_cross_year"], "text-amber-500"),
                        ("Total rows",      p["total_rows"],         "text-zinc-600"),
                    ]:
                        with ui.column().classes("gap-0"):
                            ui.label(str(val)).classes(
                                f"text-lg font-semibold {color}"
                            )
                            ui.label(label).classes("text-xs text-zinc-400")

                with preview_container:
                    if p["samples"]:
                        with ui.element("table").classes(
                            "w-full text-xs font-mono border-collapse"
                        ):
                            with ui.element("thead"):
                                with ui.element("tr"):
                                    for hdr in ("Stored (wrong)", "Corrected"):
                                        with ui.element("th").classes(
                                            "text-left px-3 py-1.5 bg-zinc-50 border "
                                            "border-zinc-100 text-zinc-500 font-semibold"
                                        ):
                                            ui.label(hdr)
                            with ui.element("tbody"):
                                for s in p["samples"]:
                                    with ui.element("tr"):
                                        with ui.element("td").classes(
                                            "px-3 py-1 border border-zinc-100 text-red-400"
                                        ):
                                            ui.label(str(s["old"]))
                                        with ui.element("td").classes(
                                            "px-3 py-1 border border-zinc-100 text-green-600"
                                        ):
                                            ui.label(str(s["new"]))
                    else:
                        ui.label("No dates would change with this combination.") \
                            .classes("text-xs text-zinc-400 italic")

                if apply_btn_ref["btn"]:
                    if p["updatable"] > 0:
                        apply_btn_ref["btn"].enable()
                    else:
                        apply_btn_ref["btn"].disable()

            render_preview()

            def do_preview():
                fval = from_sel.value if from_sel.value is not None else ""
                tval = to_sel.value or ""
                if not tval:
                    notify("Select the correct format first.", type="warning", position="top")
                    return
                if fval == tval:
                    notify(
                        "The two formats are the same — nothing to migrate.",
                        type="warning", position="top",
                    )
                    return
                fid = auth.current_family_id()
                try:
                    result = preview_redate(rule.prefix, fval, tval, fid)
                    preview_result.clear()
                    preview_result.update(result)
                    render_preview.refresh()
                except Exception as ex:
                    notify(f"Preview failed: {ex}", type="warning", position="top")

            ui.button("Preview", icon="preview", on_click=do_preview) \
                .props("unelevated no-caps") \
                .classes("bg-zinc-100 text-zinc-700 rounded-lg px-4 self-start")

        with ui.row().classes(
            "items-center justify-between px-6 py-4 border-t border-zinc-100"
        ):
            ui.button("Cancel", on_click=dlg.close) \
                .props("flat no-caps").classes("text-zinc-500")

            apply_btn = ui.button(
                "Apply migration", icon="check",
                on_click=lambda: _do_apply(),
            ).props("unelevated no-caps") \
             .classes("bg-zinc-800 text-white px-4 rounded-lg")
            apply_btn.disable()
            apply_btn_ref["btn"] = apply_btn

    def _do_apply():
        fval = from_sel.value if from_sel.value is not None else ""
        tval = to_sel.value or ""
        fid  = auth.current_family_id()
        try:
            result = redate_account(rule.prefix, fval, tval, fid)
            ViewManager(get_engine(), get_schema()).refresh()
            parts = [f"{result['updated']} row(s) updated"]
            if result["skipped_conflict"]:
                parts.append(f"{result['skipped_conflict']} conflict(s) skipped")
            if result["skipped_cross_year"]:
                parts.append(f"{result['skipped_cross_year']} cross-year skipped")
            dlg.close()
            notify("Migration complete: " + ", ".join(parts) + ".",
                   type="positive", position="top")
            on_done()
        except Exception as ex:
            notify(f"Migration failed: {ex}", type="warning", position="top")

    dlg.open()


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

            bank_name_in = labeled_input(
                'Bank name',
                hint='The institution name. Changing this does not rename the raw table.',
                value=rule.bank_name, placeholder='e.g. Citi',
            )

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
                match_type_sel = labeled_select(
                    'Match type',
                    MATCH_TYPE_OPTIONS,
                    value=rule.match_type,
                )

            match_val_in = labeled_input(
                'Filename value',
                hint='Matched against the uploaded filename without its extension.',
                value=rule.match_value, placeholder='e.g. transaction_download',
            )

            ui.separator()
            ui.label("Date format") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
            with ui.column().classes("w-full gap-1"):
                ui.label("Date format").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "How dates are written in this bank's CSV. "
                    "Applies to future uploads only."
                ).classes("text-xs text-zinc-400")
                with ui.row().classes("items-center gap-3"):
                    date_fmt_sel = labeled_select(
                        'Date format',
                        DATE_FORMAT_OPTIONS,
                        value=rule.date_format or "",
                        classes='w-80',
                    )
                    ui.button(
                        "Migrate existing dates…", icon="date_range",
                        on_click=lambda: _open_date_migration_wizard(
                            rule, on_done=lambda: None
                        ),
                    ).props("flat no-caps").classes("text-zinc-500 text-sm")

            ui.separator()
            ui.label("Currency") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
            with ui.column().classes("w-full gap-1"):
                ui.label("Currency").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "ISO 4217 currency code. Changing this will backfill all existing "
                    "transactions for this account."
                ).classes("text-xs text-zinc-400")
                currency_sel = labeled_select(
                    'Currency',
                    CURRENCY_OPTIONS,
                    value=rule.currency or None,
                    with_input=True,
                    classes='w-72',
                ).props('clearable')

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
                            payment_desc_ref={"widget": payment_desc_in},
                        )
                    ).props("flat round dense size=sm") \
                     .classes("text-zinc-400 hover:text-zinc-700") \
                     .tooltip("Browse transactions to find payment description pattern")

                payment_desc_in = labeled_input(
                    'Payment description pattern',
                    hint=(
                        'Optional: only needed for banks that format payment rows as debit > 0 '
                        'instead of credit > 0 in their CSV.'
                    ),
                    value=rule.payment_description, placeholder='e.g. ONLINE PAYMENT',
                )

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
                    raw_val_in = labeled_input('Raw member value', placeholder='e.g. JOHN', compact=True, classes='flex-1')
                    user_sel = labeled_select(
                        'User',
                        user_opt_labels,
                        value=user_opt_labels[0] if user_opt_labels else None,
                        compact=True,
                        classes='flex-1',
                    )

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

        prev_currency = rule.currency or ""
        new_currency  = (currency_sel.value or "").strip().upper()

        rule.bank_name           = bname
        rule.match_type          = match_type_sel.value
        rule.match_value         = mval
        rule.date_format         = date_fmt_sel.value or ""
        rule.currency            = new_currency
        rule.payment_description = payment_desc_in.value.strip() if is_credit else ""
        rule.member_aliases      = {a["raw_value"]: a["user_id"] for a in alias_rows}
        rule.person_override     = sorted(override_ids) if override_sw.value and override_ids else None
        dlg.close()
        on_save(rule)

        if new_currency and new_currency != prev_currency:
            try:
                from services.upload_manager import backfill_currency
                from services.view_manager import ViewManager
                fid = auth.current_family_id()
                n = backfill_currency(rule.prefix, new_currency, fid)
                ViewManager(get_engine(), get_schema()).refresh()
                notify(f"Backfilled {n} row(s) to {new_currency}.", type="positive", position="top")
            except Exception as ex:
                notify(f"Currency backfill failed: {ex}", type="warning", position="top")

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
    """Edit bank settings: name only. Transfer detection patterns live on the Transfers tab."""
    with ui.dialog().props("persistent") as dlg, \
         ui.card().classes("w-[480px] rounded-2xl p-0 gap-0 overflow-hidden"):

        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("account_balance").classes("text-zinc-400 text-xl")
                ui.label("Bank settings").classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        with ui.column().classes("px-6 py-5 gap-5 w-full"):
            ui.label("Bank details") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            name_in = labeled_input(
                'Bank name',
                hint='Display name for this bank — does not affect table names.',
                value=bank.name, placeholder='e.g. Capital One',
            )

            ui.label("slug: " + bank.slug) \
                .classes("text-xs font-mono text-zinc-400 bg-zinc-50 "
                         "border border-zinc-200 rounded px-2 py-1")

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
            name_in = labeled_input(
                'Bank name',
                hint='The institution name, e.g. Chase, Capital One, Citi.',
                placeholder='e.g. Chase',
            ).props('autofocus')

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
# Pattern matches dialog
# ─────────────────────────────────────────────────────────────────────────────

def _open_pattern_matches_dialog(pattern: str, label: str, fid: int) -> None:
    """Show all debit outflows whose description contains *pattern*."""
    engine = get_engine()
    schema = get_schema()
    pat_escaped = pattern.replace("'", "''")
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT transaction_date, description, ABS(amount) AS amount, account_key
            FROM   {schema}.transactions_debit
            WHERE  family_id = :fid
              AND  amount < 0
              AND  description ILIKE '%{pat_escaped}%'
            ORDER BY transaction_date DESC
            LIMIT 200
        """), {"fid": fid}).mappings().all()
    rows = [dict(r) for r in rows]

    with ui.dialog() as dlg, \
         ui.card().classes("w-[820px] rounded-2xl p-0 gap-0 overflow-hidden"):

        with ui.row().classes(
            "items-center justify-between px-6 py-4 border-b border-zinc-100 shrink-0"
        ):
            with ui.column().classes("gap-1"):
                ui.label(f"Matches: {label}") \
                    .classes("text-base font-semibold text-zinc-800")
                with ui.row().classes(
                    "items-center gap-1.5 px-2 py-0.5 rounded "
                    "bg-zinc-50 border border-zinc-200 w-fit"
                ):
                    ui.icon("search").classes("text-sm text-zinc-400")
                    ui.label(pattern).classes("text-sm font-mono text-zinc-600")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        with ui.column().classes("px-6 py-4 gap-3").style("max-height: 70vh; overflow-y: auto"):
            if not rows:
                with ui.column().classes("items-center py-16 gap-3"):
                    ui.icon("search_off").classes("text-5xl text-zinc-200")
                    ui.label("No matching transactions found") \
                        .classes("text-sm text-zinc-400")
            else:
                ui.label(
                    f"{len(rows)} transaction(s) matched"
                    + (" (showing first 200)" if len(rows) == 200 else "")
                ).classes("text-sm text-zinc-400")
                with ui.element("table").classes("w-full border-collapse"):
                    with ui.element("thead"):
                        with ui.element("tr"):
                            for col, width in [
                                ("Date", "w-28"),
                                ("Description", ""),
                                ("Amount", "w-28"),
                                ("Account", "w-36"),
                            ]:
                                with ui.element("th").classes(
                                    f"text-left px-3 py-2 bg-zinc-50 border "
                                    f"border-zinc-100 text-zinc-500 text-sm font-semibold "
                                    f"whitespace-nowrap {width}"
                                ):
                                    ui.label(col)
                    with ui.element("tbody"):
                        for r in rows:
                            with ui.element("tr").classes("hover:bg-zinc-50"):
                                with ui.element("td").classes(
                                    "px-3 py-2 border border-zinc-100 "
                                    "text-sm text-zinc-500 whitespace-nowrap"
                                ):
                                    ui.label(str(r["transaction_date"]))
                                with ui.element("td").classes(
                                    "px-3 py-2 border border-zinc-100 text-sm text-zinc-700"
                                ):
                                    ui.label(r["description"])
                                with ui.element("td").classes(
                                    "px-3 py-2 border border-zinc-100 "
                                    "text-sm text-zinc-700 whitespace-nowrap text-right"
                                ):
                                    ui.label(f"{_cur()}{r['amount']:,.2f}")
                                with ui.element("td").classes(
                                    "px-3 py-2 border border-zinc-100 "
                                    "text-sm text-zinc-400 whitespace-nowrap font-mono"
                                ):
                                    ui.label(r["account_key"])
    dlg.open()


# ─────────────────────────────────────────────────────────────────────────────
# Main page
# ─────────────────────────────────────────────────────────────────────────────

def _transfers_tab_content(on_refresh: callable) -> None:
    """
    Transfers tab — single scrollable view:
      §1  Pending Review       : potential_transfer flags awaiting decision
      §2  Named Exclusion Patterns : user-defined permanent exclusion patterns
    """
    fid     = auth.current_family_id()
    uid     = auth.current_user_id()
    is_head = auth.is_family_head()

    # page_state / expansion_state hoisted outside refreshable so they survive refreshes
    page_state      = {"show": 25}
    expansion_state = {"open": False}

    # ── Helpers that close over pending_body / patterns_body (defined below) ──

    def _add_pattern_dialog(prefill_pat: str = "", prefill_lbl: str = "") -> None:
        pat_ref = {"value": prefill_pat}
        lbl_ref = {"value": prefill_lbl}
        with ui.dialog().props("persistent") as dlg, \
             ui.card().classes("w-96 rounded-2xl p-0 gap-0 overflow-hidden"):
            with ui.row().classes(
                "items-center justify-between px-6 py-4 border-b border-zinc-100"
            ):
                ui.label("Add exclusion pattern") \
                    .classes("text-base font-semibold text-zinc-800")
                ui.button(icon="close", on_click=dlg.close) \
                    .props("flat round dense").classes("text-zinc-400")
            with ui.column().classes("px-6 py-5 gap-4 w-full"):
                labeled_input(
                    'Label',
                    value=prefill_lbl,
                    placeholder='e.g. Jessica Savings',
                    on_change=lambda e: lbl_ref.update({'value': e.value}),
                )
                labeled_input(
                    'Pattern',
                    hint='Transactions containing this text are excluded from spend.',
                    value=prefill_pat,
                    placeholder='e.g. XXXXXX5045',
                    on_change=lambda e: pat_ref.update({'value': e.value}),
                )
            with ui.row().classes(
                "items-center justify-end gap-2 px-6 py-4 border-t border-zinc-100"
            ):
                ui.button("Cancel", on_click=dlg.close) \
                    .props("flat no-caps").classes("text-zinc-500")
                def _do_add(d=dlg):
                    pat = pat_ref["value"].strip()
                    lbl = lbl_ref["value"].strip()
                    if not pat:
                        notify("Pattern is required.", type="warning", position="top")
                        return
                    _c = load_config(fid)
                    if not any(e.pattern == pat for e in _c.named_transfer_exclusions):
                        _c.named_transfer_exclusions.append(
                            NamedTransferExclusion(pattern=pat, label=lbl or pat,
                                                   created_by=uid)
                        )
                        save_config(_c, fid)
                    try:
                        from services.transfer_detection_service import run_detection
                        from services.view_manager import default_view_manager
                        run_detection(fid, get_engine(), get_schema())
                        default_view_manager().refresh()
                    except Exception as ex:
                        notify(f"View refresh failed: {ex}", type="negative", position="top")
                    notify(f"Pattern added: {lbl or pat}", type="positive", position="top")
                    d.close()
                    patterns_body.refresh()
                    pending_body.refresh()
                    on_refresh()
                ui.button("Add", icon="add", on_click=_do_add) \
                    .props("unelevated no-caps") \
                    .classes("bg-zinc-800 text-white px-4 rounded-lg")
        dlg.open()

    def _edit_pattern_dialog(entry: NamedTransferExclusion) -> None:
        orig_pat = entry.pattern
        pat_ref  = {"value": entry.pattern}
        lbl_ref  = {"value": entry.label}
        with ui.dialog().props("persistent") as dlg, \
             ui.card().classes("w-96 rounded-2xl p-0 gap-0 overflow-hidden"):
            with ui.row().classes(
                "items-center justify-between px-6 py-4 border-b border-zinc-100"
            ):
                ui.label("Edit pattern") \
                    .classes("text-base font-semibold text-zinc-800")
                ui.button(icon="close", on_click=dlg.close) \
                    .props("flat round dense").classes("text-zinc-400")
            with ui.column().classes("px-6 py-5 gap-4 w-full"):
                labeled_input(
                    'Label',
                    value=entry.label,
                    on_change=lambda e: lbl_ref.update({'value': e.value}),
                )
                labeled_input(
                    'Pattern',
                    hint='Transactions containing this text are excluded from spend.',
                    value=entry.pattern,
                    on_change=lambda e: pat_ref.update({'value': e.value}),
                )
            with ui.row().classes(
                "items-center justify-end gap-2 px-6 py-4 border-t border-zinc-100"
            ):
                ui.button("Cancel", on_click=dlg.close) \
                    .props("flat no-caps").classes("text-zinc-500")
                def _do_save(d=dlg):
                    pat = pat_ref["value"].strip()
                    lbl = lbl_ref["value"].strip()
                    if not pat:
                        notify("Pattern is required.", type="warning", position="top")
                        return
                    _c = load_config(fid)
                    for _e in _c.named_transfer_exclusions:
                        if _e.pattern == orig_pat:
                            _e.pattern = pat
                            _e.label   = lbl or pat
                            break
                    save_config(_c, fid)
                    try:
                        from services.transfer_detection_service import run_detection
                        from services.view_manager import default_view_manager
                        run_detection(fid, get_engine(), get_schema())
                        default_view_manager().refresh()
                    except Exception as ex:
                        notify(f"View refresh failed: {ex}", type="negative", position="top")
                    notify("Pattern updated.", type="positive", position="top")
                    d.close()
                    patterns_body.refresh()
                    pending_body.refresh()
                ui.button("Save", icon="check", on_click=_do_save) \
                    .props("unelevated no-caps") \
                    .classes("bg-zinc-800 text-white px-4 rounded-lg")
        dlg.open()

    # ── §0  Detection Patterns ────────────────────────────────────────────────

    with ui.row().classes("items-center justify-between mb-2"):
        ui.label("Transfer Detection Patterns").classes("text-sm font-semibold text-zinc-700")

    ui.label(
        "Broad keywords that seed transfer detection. Outflows matching these are "
        "flagged for review below; inflows are excluded from income."
    ).classes("text-xs text-zinc-400 mb-3")

    @ui.refreshable
    def detection_chips():
        _cfg    = load_config(fid)
        impact  = _get_pattern_impact(_cfg.transfer_patterns, fid)
        with ui.row().classes("flex-wrap gap-2 min-h-8"):
            if _cfg.transfer_patterns:
                for pat in _cfg.transfer_patterns:
                    inflows, outflows = impact.get(pat, (0, 0))
                    has_warning = inflows > 0
                    chip_cls = (
                        "items-center gap-1 px-2.5 py-1 rounded-full border "
                        + ("bg-amber-50 border-amber-300 text-amber-800"
                           if has_warning
                           else "bg-zinc-50 border-zinc-200 text-zinc-700")
                    )
                    with ui.row().classes(chip_cls):
                        if has_warning:
                            ui.icon("warning", size="xs").classes("text-amber-500")
                        ui.label(pat).classes("text-sm font-mono")
                        badge_text = ""
                        if outflows or inflows:
                            parts = []
                            if outflows:
                                parts.append(f"{outflows}↓")
                            if inflows:
                                parts.append(f"{inflows}↑")
                            badge_text = " ".join(parts)
                        if badge_text:
                            tip = (
                                f"{outflows} outflow(s) flagged as transfers"
                                + (f", {inflows} inflow(s) excluded from income ⚠" if inflows else "")
                            )
                            ui.label(badge_text) \
                                .classes(
                                    "text-xs "
                                    + ("text-amber-600" if has_warning else "text-zinc-400")
                                ) \
                                .tooltip(tip)
                        if is_head:
                            def _remove(p=pat):
                                c = load_config(fid)
                                c.transfer_patterns = [x for x in c.transfer_patterns if x != p]
                                save_config(c, fid)
                                try:
                                    from services.transfer_detection_service import run_detection
                                    from services.view_manager import default_view_manager
                                    run_detection(fid, get_engine(), get_schema())
                                    default_view_manager().refresh()
                                except Exception:
                                    pass
                                detection_chips.refresh()
                                suggested_patterns.refresh()
                                pending_body.refresh()
                                on_refresh()
                            ui.button(icon="close", on_click=_remove) \
                                .props("flat round dense size=xs") \
                                .classes("text-zinc-400 -mr-1")
            else:
                ui.label("No patterns configured.") \
                    .classes("text-xs text-zinc-400 italic py-1")

    detection_chips()

    if is_head:
        new_pat_ref = {"value": ""}

        def _add_detection_pattern():
            val = new_pat_ref["value"].strip().upper()
            if not val:
                return
            c = load_config(fid)
            if val in c.transfer_patterns:
                notify("Pattern already exists.", type="warning", position="top")
                return
            c.transfer_patterns.append(val)
            save_config(c, fid)
            try:
                from services.transfer_detection_service import run_detection
                from services.view_manager import default_view_manager
                run_detection(fid, get_engine(), get_schema())
                default_view_manager().refresh()
            except Exception:
                pass
            new_pat_in.set_value("")
            new_pat_ref["value"] = ""
            detection_chips.refresh()
            suggested_patterns.refresh()
            pending_body.refresh()
            on_refresh()

        with ui.row().classes("gap-2 items-center mt-2"):
            new_pat_in = labeled_input('Pattern', placeholder='e.g. TRANSFER', compact=True, classes='w-48') \
                .on("change", lambda e: new_pat_ref.update({"value": e.args})) \
                .on("keydown.enter", lambda _: _add_detection_pattern())
            ui.button("Add", icon="add", on_click=_add_detection_pattern) \
                .props("unelevated dense no-caps") \
                .classes("bg-zinc-800 text-white rounded-lg px-3")

    @ui.refreshable
    def suggested_patterns():
        _cfg  = load_config(fid)
        suggs = _get_pattern_suggestions(fid, _cfg.transfer_patterns)
        if not suggs:
            return
        ui.label("Suggested — found in unflagged outflows:") \
            .classes("text-xs text-zinc-400 mt-3 mb-1")
        with ui.row().classes("flex-wrap gap-2"):
            for sug in suggs:
                def _add_suggestion(p=sug):
                    c = load_config(fid)
                    if p not in c.transfer_patterns:
                        c.transfer_patterns.append(p)
                        save_config(c, fid)
                        try:
                            from services.transfer_detection_service import run_detection
                            from services.view_manager import default_view_manager
                            run_detection(fid, get_engine(), get_schema())
                            default_view_manager().refresh()
                        except Exception:
                            pass
                    detection_chips.refresh()
                    suggested_patterns.refresh()
                    pending_body.refresh()
                    on_refresh()
                ui.button(f"+ {sug}", on_click=_add_suggestion) \
                    .props("flat dense no-caps") \
                    .classes(
                        "text-xs text-zinc-500 border border-dashed border-zinc-300 "
                        "rounded-full px-2.5 py-0.5 hover:bg-zinc-50"
                    )

    if is_head:
        suggested_patterns()


    ui.separator().classes("my-6")

    # ── §1  Pending Review ─────────────────────────────────────────────────────

    @ui.refreshable
    def pending_body():
        rows     = _get_pending_transfers(fid, uid, is_head)
        reviewed = _get_reviewed_transfers(fid, uid, is_head)
        cfg      = load_config(fid)

        named_pats = [e.pattern.lower() for e in cfg.named_transfer_exclusions]
        pending = [
            r for r in rows
            if not any(p in r["description"].lower() for p in named_pats)
        ]

        with ui.row().classes("items-center gap-3 mb-2"):
            ui.label("Pending Review").classes("text-sm font-semibold text-zinc-700")
            if pending:
                ui.label(str(len(pending))).classes(
                    "text-xs px-2 py-0.5 rounded-full font-medium "
                    "bg-amber-50 text-amber-600 border border-amber-200"
                )

        if pending:
            ui.label(
                f"{len(pending)} transaction(s) look like transfers to accounts not in "
                "your data. Excluded from spend by default — mark any that are real expenses."
            ).classes("text-xs text-zinc-400 mb-3")

            from collections import OrderedDict
            groups: OrderedDict[str, list[dict]] = OrderedDict()
            for r in pending:
                key = _transfer_group_key(r["description"])
                groups.setdefault(key, []).append(r)

            def _open_name_dialog(group_key: str, sample_desc: str, refresh_fn):
                suggested = _extract_pattern_suggestion(sample_desc)
                pat_ref   = {"value": suggested}
                lbl_ref   = {"value": ""}

                with ui.dialog().props("persistent") as dlg, \
                     ui.card().classes("w-96 rounded-2xl p-0 gap-0 overflow-hidden"):
                    with ui.row().classes(
                        "items-center justify-between px-6 py-4 border-b border-zinc-100"
                    ):
                        ui.label("Name this account") \
                            .classes("text-base font-semibold text-zinc-800")
                        ui.button(icon="close", on_click=dlg.close) \
                            .props("flat round dense").classes("text-zinc-400")

                    with ui.column().classes("px-6 py-5 gap-4 w-full"):
                        ui.label(
                            "Give this transfer destination a name. Future transactions "
                            "matching the pattern will be excluded automatically."
                        ).classes("text-xs text-zinc-400")

                        labeled_input(
                            'Label',
                            placeholder='e.g. Jessica Savings',
                            value='',
                            on_change=lambda e: lbl_ref.update({'value': e.value}),
                        )

                        labeled_input(
                            'Pattern',
                            hint=(
                                'Transactions whose description contains this text will '
                                'be excluded from spend.'
                            ),
                            value=pat_ref['value'],
                            on_change=lambda e: pat_ref.update({'value': e.value}),
                        )

                    with ui.row().classes(
                        "items-center justify-end gap-2 px-6 py-4 border-t border-zinc-100"
                    ):
                        ui.button("Cancel", on_click=dlg.close) \
                            .props("flat no-caps").classes("text-zinc-500")
                        def _save_pattern(d=dlg, p=pat_ref, l=lbl_ref):
                            pat = p["value"].strip()
                            lbl = l["value"].strip()
                            if not pat:
                                notify("Pattern is required.", type="warning", position="top")
                                return
                            _cfg = load_config(fid)
                            if not any(e.pattern == pat
                                       for e in _cfg.named_transfer_exclusions):
                                _cfg.named_transfer_exclusions.append(
                                    NamedTransferExclusion(
                                        pattern=pat, label=lbl or pat, created_by=uid,
                                    )
                                )
                                save_config(_cfg, fid)
                            try:
                                from services.transfer_detection_service import run_detection
                                from services.view_manager import default_view_manager
                                run_detection(fid, get_engine(), get_schema())
                                default_view_manager().refresh()
                            except Exception as ex:
                                notify(f"View refresh failed: {ex}",
                                       type="negative", position="top")
                            notify(f"Pattern saved: {lbl or pat}",
                                   type="positive", position="top")
                            d.close()
                            refresh_fn()
                            patterns_body.refresh()
                            on_refresh()
                        ui.button("Save & exclude", icon="check", on_click=_save_pattern) \
                            .props("unelevated no-caps") \
                            .classes("bg-zinc-800 text-white px-4 rounded-lg")
                dlg.open()

            shown_groups = list(groups.items())[:page_state["show"]]

            for group_key, group_rows in shown_groups:
                total_amt = sum(r["amount"] for r in group_rows)
                dates     = sorted(r["transaction_date"] for r in group_rows)

                with ui.card().classes(
                    "w-full rounded-xl border border-zinc-100 p-0 gap-0 "
                    "overflow-hidden shadow-none"
                ):
                    with ui.row().classes("w-full items-center gap-3 px-4 py-3 bg-white"):
                        with ui.column().classes("flex-1 min-w-0 gap-0.5"):
                            ui.label(group_key) \
                                .classes("text-sm font-medium text-zinc-800 truncate")
                            n = len(group_rows)
                            date_str = (
                                str(dates[0]) if n == 1
                                else f"{dates[0]} – {dates[-1]}"
                            )
                            ui.label(
                                f"{n} transaction{'s' if n > 1 else ''} · {date_str}"
                            ).classes("text-xs text-zinc-400")

                        ui.label(f"{_cur()}{total_amt:,.2f}") \
                            .classes("text-sm font-semibold text-zinc-700 shrink-0")

                        def _mark_group_keep(rows=group_rows):
                            for r in rows:
                                _set_flag_user_kept(r["flag_id"], True)
                            try:
                                from services.view_manager import default_view_manager
                                default_view_manager().refresh()
                            except Exception:
                                pass
                            notify("Marked as spend.", type="positive", position="top")
                            pending_body.refresh()
                            on_refresh()

                        ui.button(
                            "Keep as spend",
                            icon="shopping_cart",
                            on_click=_mark_group_keep,
                        ).props("flat no-caps dense") \
                         .classes("text-zinc-500 text-xs shrink-0")

                        ui.button(
                            "Name account",
                            icon="label",
                            on_click=lambda gk=group_key, sd=group_rows[0]["description"]: (
                                _open_name_dialog(gk, sd, pending_body.refresh)
                            ),
                        ).props("flat no-caps dense") \
                         .classes("text-teal-600 text-xs shrink-0")

                    if len(group_rows) > 1:
                        ui.separator()
                    for r in group_rows:
                        with ui.row().classes(
                            "w-full items-center gap-3 px-6 py-2 bg-zinc-50 "
                            "border-t border-zinc-100"
                        ):
                            ui.label(str(r["transaction_date"])) \
                                .classes("text-xs text-zinc-400 w-20 shrink-0")
                            ui.label(r["description"]) \
                                .classes("text-xs text-zinc-600 flex-1 truncate")
                            ui.label(f"{_cur()}{r['amount']:,.2f}") \
                                .classes("text-xs text-zinc-500 shrink-0")
                            def _keep_one(flag_id=r["flag_id"]):
                                _set_flag_user_kept(flag_id, True)
                                try:
                                    from services.view_manager import default_view_manager
                                    default_view_manager().refresh()
                                except Exception:
                                    pass
                                pending_body.refresh()
                                on_refresh()
                            ui.button(
                                icon="shopping_cart",
                                on_click=_keep_one,
                            ).props("flat round dense size=xs") \
                             .classes("text-zinc-300 hover:text-teal-600") \
                             .tooltip("Keep as spend")

            if len(groups) > page_state["show"]:
                remaining = len(groups) - page_state["show"]
                def _show_more():
                    page_state["show"] += 25
                    pending_body.refresh()
                ui.button(
                    f"Show {min(remaining, 25)} more",
                    icon="expand_more",
                    on_click=_show_more,
                ).props("flat no-caps").classes("text-zinc-500 text-sm mt-2")

        else:
            with ui.column().classes("w-full items-center py-10 gap-3"):
                ui.icon("check_circle_outline").classes("text-4xl text-zinc-200")
                ui.label("No pending transfers to review") \
                    .classes("text-zinc-400 text-sm")

        # Reviewed section
        if reviewed:
            with ui.expansion(
                f"Reviewed — {len(reviewed)} kept as spend",
                icon="history",
                value=expansion_state["open"],
                on_value_change=lambda e: expansion_state.update({"open": e.value}),
            ).classes("w-full mt-4 rounded-xl border border-zinc-100"):
                ui.label(
                    "These were marked as real expenses. "
                    "Use the undo button to re-exclude them."
                ).classes("text-xs text-zinc-400 mb-2 px-1")
                for r in reviewed:
                    with ui.row().classes(
                        "w-full items-center gap-3 py-2 "
                        "border-b border-zinc-50 last:border-0"
                    ):
                        ui.label(str(r["transaction_date"])) \
                            .classes("text-xs text-zinc-400 w-20 shrink-0")
                        ui.label(r["description"]) \
                            .classes("text-xs text-zinc-600 flex-1 truncate")
                        ui.label(f"{_cur()}{r['amount']:,.2f}") \
                            .classes("text-xs text-zinc-500 shrink-0")
                        def _unmark(flag_id=r["flag_id"]):
                            _set_flag_user_kept(flag_id, False)
                            try:
                                from services.view_manager import default_view_manager
                                default_view_manager().refresh()
                            except Exception:
                                pass
                            pending_body.refresh()
                            on_refresh()
                        ui.button(icon="undo", on_click=_unmark) \
                            .props("flat round dense size=xs") \
                            .classes("text-zinc-300 hover:text-amber-600") \
                            .tooltip("Re-exclude from spend")

    pending_body()
    ui.separator().classes("my-6")

    # ── §2  Named Exclusion Patterns ──────────────────────────────────────────

    with ui.row().classes("items-center justify-between mb-3"):
        ui.label("Named Exclusion Patterns").classes("text-sm font-semibold text-zinc-700")
        ui.button("Add pattern", icon="add", on_click=_add_pattern_dialog) \
            .props("unelevated no-caps") \
            .classes("bg-zinc-800 text-white px-4 rounded-lg")

    @ui.refreshable
    def patterns_body():
        _cfg = load_config(fid)

        visible = [
            e for e in _cfg.named_transfer_exclusions
            if is_head or e.created_by is None or e.created_by == uid
        ]

        ui.label(
            "Named patterns permanently exclude transactions from spend. "
            "They apply to all matching transactions, past and future."
        ).classes("text-xs text-zinc-400 mb-3")

        if not visible:
            with ui.column().classes("w-full items-center py-12 gap-3"):
                ui.icon("label_off").classes("text-4xl text-zinc-200")
                ui.label("No named patterns yet").classes("text-zinc-400 text-sm")
            return

        import services.family_service as _fam
        members     = _fam.get_family_members(fid)
        uid_to_name: dict[int, str] = {}
        for m in members:
            u = auth.get_user_by_id(m.user_id)
            if u:
                uid_to_name[m.user_id] = u.display_name

        with ui.element("div").classes(
            "w-full rounded-xl border border-zinc-100 overflow-hidden"
        ):
            with ui.row().classes(
                "w-full items-center gap-4 px-4 py-2 bg-zinc-50 border-b border-zinc-100"
            ):
                ui.label("Label").classes(
                    "text-xs font-semibold text-zinc-400 uppercase tracking-wide "
                    "w-40 shrink-0"
                )
                ui.label("Pattern").classes(
                    "text-xs font-semibold text-zinc-400 uppercase tracking-wide flex-1"
                )
                ui.label("Added by").classes(
                    "text-xs font-semibold text-zinc-400 uppercase tracking-wide "
                    "w-28 shrink-0"
                )
                ui.element("div").classes("w-24 shrink-0")

            for entry in visible:
                can_edit = is_head or entry.created_by == uid
                creator  = (
                    uid_to_name.get(entry.created_by, f"User #{entry.created_by}")
                    if entry.created_by is not None
                    else "Family"
                )

                with ui.row().classes(
                    "w-full items-center gap-4 px-4 py-3 bg-white "
                    "border-b border-zinc-50 last:border-0"
                ):
                    ui.label(entry.label or entry.pattern) \
                        .classes("text-sm text-zinc-800 w-40 shrink-0 truncate")

                    with ui.row().classes(
                        "items-center gap-1.5 px-2.5 py-1 rounded-full "
                        "bg-zinc-50 border border-zinc-200 flex-1 min-w-0"
                    ):
                        ui.icon("search").classes("text-xs text-zinc-400 shrink-0")
                        ui.label(entry.pattern) \
                            .classes("text-xs font-mono text-zinc-600 truncate")

                    ui.label(creator).classes(
                        "text-xs text-zinc-400 w-28 shrink-0 truncate"
                    )

                    with ui.row().classes("gap-1 shrink-0 w-24 justify-end"):
                        ui.button(
                            icon="table_view",
                            on_click=lambda p=entry.pattern, l=entry.label: (
                                _open_pattern_matches_dialog(p, l or p, fid)
                            ),
                        ).props("flat round dense size=xs") \
                         .classes("text-zinc-300 hover:text-blue-500") \
                         .tooltip("View matched transactions")

                        if can_edit:
                            ui.button(
                                icon="edit",
                                on_click=lambda e=entry: _edit_pattern_dialog(e),
                            ).props("flat round dense size=xs") \
                             .classes("text-zinc-300 hover:text-zinc-600") \
                             .tooltip("Edit pattern")

                        if can_edit:
                            def _delete(pat=entry.pattern):
                                _c = load_config(fid)
                                _c.named_transfer_exclusions = [
                                    e for e in _c.named_transfer_exclusions
                                    if e.pattern != pat
                                ]
                                save_config(_c, fid)
                                try:
                                    from services.view_manager import default_view_manager
                                    default_view_manager().refresh()
                                except Exception:
                                    pass
                                notify("Pattern removed.", type="info", position="top")
                                patterns_body.refresh()
                            ui.button(
                                icon="delete_outline",
                                on_click=_delete,
                            ).props("flat round dense size=xs") \
                             .classes("text-zinc-300 hover:text-red-400") \
                             .tooltip("Remove pattern")

    patterns_body()


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

    fid = auth.current_family_id()
    uid = auth.current_user_id()
    is_head = auth.is_family_head()

    with ui.row().classes("w-full items-center justify-between mb-2"):
        with ui.column().classes("gap-0"):
            ui.label("Banks").classes("page-title")
            ui.label(
                "Upload the latest data from your bank account and update your dashboard."
            ).classes("text-sm text-muted")

    ui.element("div").classes("divider mb-4")

    # ── Top-level tabs: Banks | Transfers ─────────────────────────────────────
    with ui.tabs().classes("w-full border-b border-zinc-100 mb-4") as main_tabs:
        banks_tab     = ui.tab("Banks",     icon="account_balance")
        transfers_tab = ui.tab("Transfers", icon="swap_horiz")

    @ui.refreshable
    def _pending_badge():
        # Apply the same named-pattern filter as pending_body so the count matches
        # what the user actually sees (raw DB count includes pattern-covered rows).
        rows = _get_pending_transfers(fid, uid, is_head)
        cfg  = load_config(fid)
        named_pats = [e.pattern.lower() for e in cfg.named_transfer_exclusions]
        cnt = sum(
            1 for r in rows
            if not any(p in r["description"].lower() for p in named_pats)
        )
        if cnt > 0:
            ui.badge(str(cnt), color="red").props("floating")

    with transfers_tab:
        _pending_badge()

    def _refresh_tabs():
        """Refresh the pending-count badge on the Transfers tab."""
        _pending_badge.refresh()

    with ui.tab_panels(main_tabs, value=banks_tab).classes("w-full"):

        with ui.tab_panel(banks_tab).classes("px-0"):

            @ui.refreshable
            def page_body():
                rules = load_rules(auth.current_family_id()) or []
                banks = _ensure_banks_for_rules(rules)

                def _select(prefix: str):
                    selected_ref["value"] = prefix
                    page_body.refresh()

                # ── Empty state ───────────────────────────────────────────────
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

                # ── Callbacks ─────────────────────────────────────────────────
                def _edit_account(r: BankRule):
                    def on_save(updated: BankRule):
                        _fid = auth.current_family_id()
                        all_rules = load_rules(_fid)
                        idx = next(
                            (i for i, x in enumerate(all_rules) if x.prefix == r.prefix), None
                        )
                        if idx is not None:
                            all_rules[idx] = updated
                        else:
                            all_rules.append(updated)
                        save_rules(all_rules, _fid)
                        notify("Saved: " + updated.bank_name, type="positive", position="top")
                        page_body.refresh()

                    def on_delete(deleted: BankRule):
                        _fid = auth.current_family_id()
                        all_rules = load_rules(_fid)
                        all_rules = [x for x in all_rules if x.prefix != deleted.prefix]
                        save_rules(all_rules, _fid)
                        notify("Deleted: " + deleted.bank_name, type="info", position="top")
                        page_body.refresh()

                    _open_edit_account_dialog(r, on_save=on_save, on_delete=on_delete)

                def _edit_bank(b: BankConfig):
                    b_rules = [r for r in rules if _slugify(r.bank_name) == b.slug]

                    def on_save(updated: BankConfig):
                        _fid = auth.current_family_id()
                        all_banks = load_banks(_fid)
                        idx = next((i for i, x in enumerate(all_banks) if x.slug == b.slug), None)
                        if idx is not None:
                            all_banks[idx] = updated
                        else:
                            all_banks.append(updated)
                        save_banks(all_banks, _fid)
                        notify("Saved: " + updated.name, type="positive", position="top")
                        page_body.refresh()

                    def on_delete(deleted: BankConfig, affected_rules: list):
                        _fid = auth.current_family_id()
                        all_banks = load_banks(_fid)
                        all_banks = [x for x in all_banks if x.slug != deleted.slug]
                        save_banks(all_banks, _fid)
                        if affected_rules:
                            dead_prefixes = {r.prefix for r in affected_rules}
                            all_rules = load_rules(_fid)
                            save_rules([r for r in all_rules if r.prefix not in dead_prefixes], _fid)
                        notify("Deleted: " + deleted.name, type="info", position="top")
                        page_body.refresh()

                    _open_bank_settings_dialog(b, on_save=on_save, on_delete=on_delete, bank_rules=b_rules)

                with ui.row().classes("w-full gap-5 items-start"):

                    # ── Sidebar ───────────────────────────────────────────────
                    with ui.column().classes(
                        "gap-0 shrink-0 w-56 bg-zinc-50 rounded-xl border border-zinc-100 p-2"
                    ):
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

                        for bank in banks:
                            bank_rules = [r for r in rules if _slugify(r.bank_name) == bank.slug]

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

                    # ── Upload area ───────────────────────────────────────────
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

        with ui.tab_panel(transfers_tab).classes("px-0"):
            _transfers_tab_content(on_refresh=_refresh_tabs)

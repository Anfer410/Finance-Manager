"""
pages/upload_content.py  —  bank sidebar + upload zone + onboarding wizard
"""
from __future__ import annotations
from pathlib import Path

from nicegui import ui, events

import services.auth as auth
from data.bank_rules import BankRule, load_rules, save_rules
from services.handle_upload import handle_upload
from services.upload_pipeline import (
    sniff, suggest_mapping, ColumnMapping,
    REQUIRED_ROLES, SniffResult,
)
from services.notifications import notify
from data.db import get_engine, get_schema


ACCOUNT_COLORS = {
    "credit":   ("bg-violet-50 text-violet-700 border-violet-200", "credit_card"),
    "checking": ("bg-sky-50 text-sky-700 border-sky-200",          "account_balance"),
}

ROLE_LABELS: dict[str, str] = {
    "date":        "Transaction date",
    "description": "Description / memo",
    "amount":      "Amount (single col)",
    "debit":       "Debit / charge column",
    "credit":      "Credit / payment column",
    "member_name": "Member / cardholder name",
}

MATCH_TYPE_OPTIONS = {
    "exact":      "Exact",
    "startswith": "Starts with",
    "endswith":   "Ends with",
    "contains":   "Contains  (* wildcards ok)",
}


# ─────────────────────────────────────────────────────────────────────────────
# Wizard
# ─────────────────────────────────────────────────────────────────────────────

def _open_add_bank_wizard(on_done):
    state: dict = {
        "step":         1,
        "raw":          None,
        "filename":     "",
        "sniff":        None,
        "mapping":      None,
        "account_type": "checking",
        "bank_details": {},
    }

    with ui.dialog().props("persistent") as dlg, \
         ui.card().classes("w-[660px] rounded-2xl p-0 gap-0 overflow-hidden"):

        # Header
        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("add_card").classes("text-zinc-400 text-xl")
                title_lbl = ui.label("Add bank — step 1 of 4") \
                    .classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        body = ui.column().classes("w-full")

        # Footer
        with ui.row().classes("items-center justify-between px-6 py-4 border-t border-zinc-100"):
            back_btn = ui.button("Back", icon="arrow_back") \
                .props("flat no-caps").classes("text-zinc-500")
            with ui.row().classes("gap-2"):
                skip_btn = ui.button("Skip", on_click=lambda: dlg.close()) \
                    .props("flat no-caps").classes("text-zinc-400")
                skip_btn.set_visibility(False)
                next_btn = ui.button("Next", icon="arrow_forward") \
                    .props("unelevated no-caps").classes("bg-zinc-800 text-white px-5 rounded-lg")

        # ── render_step ───────────────────────────────────────────────────────
        def render_step():
            body.clear()
            st = state["step"]
            title_lbl.set_text(f"Add bank — step {st} of 5")
            back_btn.set_visibility(st > 1)
            next_btn.set_text("Save bank" if st == 5 else "Next")
            next_btn.enable()
            next_btn._event_listeners.clear()
            back_btn._event_listeners.clear()
            # Step 4 (search) is skippable for non-credit or no data yet
            skip_btn.set_visibility(st == 4)
            back_btn.on("click", go_back)
            with body:
                if st == 1:   _step1()
                elif st == 2: _step2()
                elif st == 3: _step3()
                elif st == 4: _step4()
                elif st == 5: _step5()

        # ── Step 1: upload sample CSV ─────────────────────────────────────────
        def _step1():
            next_btn.disable()
            with ui.column().classes("px-6 py-5 gap-4 w-full"):
                ui.label("Upload a sample CSV from this bank.") \
                    .classes("text-sm text-zinc-500")
                ui.label(
                    "We'll inspect the column structure so you can map them next. "
                    "The file is read locally only — nothing is stored yet."
                ).classes("text-xs text-zinc-400")

                status_lbl = ui.label("").classes("text-sm")

                async def on_sample(e: events.UploadEventArguments):
                    raw = await e.file.read()
                    state["raw"]      = raw
                    state["filename"] = e.file.name
                    try:
                        result = sniff(raw)
                        state["sniff"]   = result
                        state["mapping"] = suggest_mapping(result, state["account_type"])
                        status_lbl.set_text(
                            f"✓  {len(result.norm_columns)} columns, {result.row_count} rows"
                            + ("  (no header)" if not result.has_header else "")
                        )
                        status_lbl.classes(replace="text-sm text-green-600")
                        next_btn.enable()
                    except Exception as ex:
                        status_lbl.set_text(f"Could not read file: {ex}")
                        status_lbl.classes(replace="text-sm text-red-500")

                ui.upload(
                    label="Drop your CSV here or click to browse",
                    on_upload=on_sample,
                    auto_upload=True,
                    max_files=1,
                ).props("accept=.csv").classes("w-full")

                if state["sniff"]:
                    sniff_r = state["sniff"]
                    status_lbl.set_text(
                        f"✓  {len(sniff_r.norm_columns)} columns — re-upload to change"
                    )
                    status_lbl.classes(replace="text-sm text-green-600")
                    next_btn.enable()

            next_btn.on("click", lambda: _advance(2))

        # ── Step 2: column mapping ────────────────────────────────────────────
        def _step2():
            sniff_res: SniffResult = state["sniff"]
            norm_cols  = sniff_res.norm_columns
            # NiceGUI select with dict: key=value stored, val=label shown
            col_opts   = {None: "— not mapped —", **{c: c for c in norm_cols}}
            selects: dict[str, ui.select] = {}

            with ui.column().classes("px-6 py-5 gap-4 w-full"):
                with ui.row().classes("items-center gap-3"):
                    ui.label("Account type:").classes("text-sm font-medium text-zinc-700")
                    acct_sel = ui.toggle(
                        {"checking": "Checking / savings", "credit": "Credit card"},
                        value=state["account_type"],
                    ).props("no-caps")

                ui.separator()

                @ui.refreshable
                def mapping_rows():
                    selects.clear()
                    acct     = acct_sel.value
                    required = set(REQUIRED_ROLES[acct])
                    for role, label in ROLE_LABELS.items():
                        if role == "amount" and acct != "checking":
                            continue
                        if role in ("debit", "credit") and acct != "credit":
                            continue
                        current = getattr(state["mapping"], role, None)
                        is_req  = role in required
                        with ui.row().classes("w-full items-center gap-3"):
                            ui.label("●" if is_req else "○").classes(
                                "text-xs w-3 " +
                                ("text-red-400" if is_req else "text-zinc-300")
                            )
                            ui.label(label).classes(
                                "text-sm w-48 shrink-0 " +
                                ("font-medium text-zinc-700" if is_req else "text-zinc-400")
                            )
                            sel = ui.select(col_opts, value=current) \
                                .classes("flex-1").props("outlined dense")
                            selects[role] = sel

                mapping_rows()

                def on_acct_change(_=None):
                    state["account_type"] = acct_sel.value
                    state["mapping"] = suggest_mapping(sniff_res, acct_sel.value)
                    mapping_rows.refresh()

                acct_sel.on("update:model-value", on_acct_change)

                ui.separator()
                ui.label("Column preview (first 3 rows):") \
                    .classes("text-xs text-zinc-400 font-semibold uppercase tracking-wide")
                with ui.scroll_area().style("max-height:130px"):
                    with ui.element("table").classes("w-full text-xs font-mono border-collapse"):
                        with ui.element("thead"):
                            with ui.element("tr"):
                                for col in sniff_res.raw_columns:
                                    with ui.element("th").classes(
                                        "text-left px-2 py-1 bg-zinc-50 border "
                                        "border-zinc-100 text-zinc-500 font-semibold whitespace-nowrap"
                                    ):
                                        ui.label(col)
                        with ui.element("tbody"):
                            for row in sniff_res.sample_rows[:3]:
                                with ui.element("tr"):
                                    for cell in row:
                                        with ui.element("td").classes(
                                            "px-2 py-1 border border-zinc-100 text-zinc-600"
                                        ):
                                            ui.label(str(cell)[:24])

            def advance_step2():
                acct = acct_sel.value
                state["account_type"] = acct
                m = state["mapping"]
                for role, sel in selects.items():
                    setattr(m, role, sel.value)
                missing = m.missing_required(acct)
                if missing:
                    notify(
                        "Map required columns: " + ", ".join(missing),
                        type="warning", position="top",
                    )
                    return
                _advance(3)

            next_btn.on("click", advance_step2)

        # ── Step 3: bank details + member aliases ─────────────────────────────
        def _step3():
            import re as _re
            uploaded_stem = Path(state["filename"]).stem if state["filename"] else ""

            all_users    = auth.get_all_users()
            active_users = [u for u in all_users if u.is_active]
            user_opt_map: dict[str, int] = {
                f"{u.display_name}  ({u.person_name})": u.id
                for u in active_users
            }
            user_opt_labels = list(user_opt_map.keys())

            alias_rows: list[dict] = []
            member_col = (state["mapping"].member_name or "") if state["mapping"] else ""

            # helpers for live table name preview
            def _to_slug(text: str) -> str:
                return _re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")

            def _table_name(bank: str, alias: str) -> str:
                """Returns the slug used as rule.prefix, e.g. 'wells_fargo_mutual'.
                The actual DB table is always raw_{slug}."""
                parts = [p for p in [_to_slug(bank), _to_slug(alias)] if p]
                return "_".join(parts) if parts else ""

            with ui.scroll_area().style("max-height:62vh"):
              with ui.column().classes("px-6 py-5 gap-5 w-full"):

                # ── Bank name + account alias ──────────────────────────────────
                ui.label("Bank details") \
                    .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Bank name") \
                        .classes("text-sm font-medium text-zinc-700")
                    ui.label("The institution name, e.g. Citi, Wells Fargo, Capital One.") \
                        .classes("text-xs text-zinc-400")
                    bank_name_in = ui.input(placeholder="e.g. Citi") \
                        .classes("w-full").props("outlined dense")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Account alias") \
                        .classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "A short name for this specific account, e.g. Daily Spending, Rewards, Joint. "
                        "Together with the bank name this becomes the raw data table name."
                    ).classes("text-xs text-zinc-400")
                    prefix_in = ui.input(placeholder="e.g. Daily Spending") \
                        .classes("w-full").props("outlined dense")

                # live table name preview
                table_preview = ui.label("raw_table: raw_") \
                    .classes("text-xs font-mono text-zinc-400 bg-zinc-50 "
                             "border border-zinc-200 rounded px-2 py-1")

                def update_table_preview(_=None):
                    slug = _table_name(bank_name_in.value, prefix_in.value)
                    table_preview.set_text("raw table: raw_" + slug if slug else "raw table: raw_")

                bank_name_in.on("update:model-value", update_table_preview)
                prefix_in.on("update:model-value", update_table_preview)

                # ── Filename detection ─────────────────────────────────────────
                ui.separator()
                ui.label("Filename detection") \
                    .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Match type") \
                        .classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "How to compare the uploaded filename against the value below. "
                        'Exact is pre-selected from your sample. Switch to Contains and use * for wildcards.'
                    ).classes("text-xs text-zinc-400")
                    match_type_sel = ui.select(
                        MATCH_TYPE_OPTIONS, value="exact",
                    ).classes("w-full").props("outlined dense")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Filename value") \
                        .classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "Matched against the uploaded filename without its extension. "
                        "Pre-filled from your sample file."
                    ).classes("text-xs text-zinc-400")
                    match_val_in = ui.input(
                        value=uploaded_stem,
                        placeholder="e.g. transaction_download",
                    ).classes("w-full").props("outlined dense")

                # ── Credit-only settings ───────────────────────────────────────
                credit_col = ui.column().classes("w-full gap-4")
                with credit_col:
                    ui.separator()
                    ui.label("Credit card settings") \
                        .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

                    with ui.column().classes("w-full gap-1"):
                        ui.label("Payment category value") \
                            .classes("text-sm font-medium text-zinc-700")
                        ui.label(
                            "The category string in the bank CSV that marks a payment or credit row, "
                            "e.g. Payment/Credit. Used to separate payments from purchases."
                        ).classes("text-xs text-zinc-400")
                        payment_cat_in = ui.input(placeholder="e.g. Payment/Credit") \
                            .classes("w-full").props("outlined dense")

                    with ui.column().classes("w-full gap-1"):
                        ui.label("Payment description pattern") \
                            .classes("text-sm font-medium text-zinc-700")
                        ui.label(
                            "A substring that appears in the description of payment rows, "
                            "e.g. ONLINE PAYMENT. Used as a secondary filter."
                        ).classes("text-xs text-zinc-400")
                        payment_desc_in = ui.input(placeholder="e.g. ONLINE PAYMENT") \
                            .classes("w-full").props("outlined dense")

                    with ui.column().classes("w-full gap-1"):
                        ui.label("Checking-side payment pattern") \
                            .classes("text-sm font-medium text-zinc-700")
                        ui.label(
                            "When you pay this credit card from a checking account, what text appears "
                            "in that checking transaction? e.g. CAPITAL ONE. Those rows are excluded "
                            "from debit spend so the payment is not double-counted."
                        ).classes("text-xs text-zinc-400")
                        checking_pat_in = ui.input(placeholder="e.g. CAPITAL ONE") \
                            .classes("w-full").props("outlined dense")

                credit_col.set_visibility(state["account_type"] == "credit")

                # ── Member name aliases (only if member col was mapped) ─────────
                if member_col:
                    ui.separator()
                    ui.label("Member name aliases") \
                        .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
                    ui.label(
                        "Column \"" + member_col + "\" was mapped as the member name. "
                        "Map each raw value found in that column to a registered user. "
                        "Aliases are stored by user ID, so renaming a user never breaks old data."
                    ).classes("text-xs text-zinc-400")

                    aliases_container = ui.column().classes("w-full gap-1")

                    @ui.refreshable
                    def render_aliases():
                        aliases_container.clear()
                        with aliases_container:
                            if not alias_rows:
                                ui.label("No aliases yet — add one below.") \
                                    .classes("text-xs text-zinc-400 italic py-1")
                                return
                            for i, row in enumerate(alias_rows):
                                user_label = next(
                                    (lbl for lbl, uid in user_opt_map.items()
                                     if uid == row["user_id"]),
                                    "unknown"
                                )
                                with ui.row().classes(
                                    "w-full items-center gap-2 px-3 py-2 "
                                    "rounded-lg bg-zinc-50 border border-zinc-100"
                                ):
                                    ui.label(row["raw_value"]) \
                                        .classes("font-mono text-sm text-zinc-700 flex-1")
                                    ui.icon("arrow_forward") \
                                        .classes("text-zinc-300 text-base shrink-0")
                                    ui.label(user_label) \
                                        .classes("text-sm text-zinc-600 flex-1")
                                    ui.button(
                                        icon="close",
                                        on_click=lambda _, idx=i: (
                                            alias_rows.pop(idx),
                                            render_aliases.refresh()
                                        )
                                    ).props("flat round dense size=xs").classes("text-zinc-400 shrink-0")

                    render_aliases()

                    # Add-alias row
                    with ui.column().classes("w-full gap-1 mt-1"):
                        ui.label("Add alias") \
                            .classes("text-sm font-medium text-zinc-700")
                        ui.label(
                            "Enter the exact value as it appears in the member column "
                            "(the app will uppercase it), then pick the matching user."
                        ).classes("text-xs text-zinc-400")
                    with ui.row().classes("w-full items-end gap-2"):
                        raw_val_in = ui.input(placeholder="e.g. JOHN") \
                            .classes("flex-1").props("outlined dense")
                        user_sel = ui.select(
                            user_opt_labels, label=None,
                            value=user_opt_labels[0] if user_opt_labels else None,
                        ).classes("flex-1").props("outlined dense")

                        def add_alias():
                            rv  = raw_val_in.value.strip().upper()
                            uid = user_opt_map.get(user_sel.value)
                            if not rv:
                                notify("Enter a raw member value.", type="warning", position="top")
                                return
                            if uid is None:
                                notify("Select a user to map to.", type="warning", position="top")
                                return
                            if any(a["raw_value"] == rv for a in alias_rows):
                                notify(
                                    "An alias for " + rv + " already exists.",
                                    type="warning", position="top",
                                )
                                return
                            alias_rows.append({"raw_value": rv, "user_id": uid})
                            raw_val_in.set_value("")
                            render_aliases.refresh()

                        ui.button("Add", icon="add", on_click=add_alias) \
                            .props("unelevated dense no-caps") \
                            .classes("bg-zinc-800 text-white rounded-lg px-3")

                # ── Person override ────────────────────────────────────────────
                ui.separator()
                with ui.column().classes("w-full gap-1"):
                    ui.label("Person override (optional)") \
                        .classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "Force every row from this bank to be assigned to one person. "
                        "Useful for joint or shared accounts where the file never contains per-person data."
                    ).classes("text-xs text-zinc-400")
                with ui.row().classes("w-full gap-3 items-center"):
                    override_sw = ui.switch("Enable person override") \
                        .classes("text-sm shrink-0")
                    person_override_in = ui.input(placeholder="e.g. mutual") \
                        .classes("flex-1").props("outlined dense")
                    person_override_in.set_visibility(False)
                    override_sw.on(
                        "update:model-value",
                        lambda e: person_override_in.set_visibility(e.args)
                    )

            def advance_step3():
                bname = bank_name_in.value.strip()
                pfx   = _table_name(bank_name_in.value, prefix_in.value)
                mval  = match_val_in.value.strip()
                if not bname:
                    notify("Bank name is required.", type="warning", position="top")
                    return
                if not prefix_in.value.strip():
                    notify("Account alias is required.", type="warning", position="top")
                    return
                if not pfx:
                    notify("Could not generate a table name — check bank name and alias.", type="warning", position="top")
                    return
                if not mval:
                    notify("Filename value is required.", type="warning", position="top")
                    return
                is_credit = state["account_type"] == "credit"
                state["bank_details"] = dict(
                    bank_name                = bname,
                    prefix                   = pfx,
                    match_type               = match_type_sel.value,
                    match_value              = mval,
                    account_type             = state["account_type"],
                    payment_category         = payment_cat_in.value.strip()  if is_credit else "",
                    payment_description      = payment_desc_in.value.strip() if is_credit else "",
                    checking_payment_pattern = checking_pat_in.value.strip() if is_credit else "",
                    member_name_column       = member_col,
                    member_aliases           = {a["raw_value"]: a["user_id"] for a in alias_rows},
                    person_override          = person_override_in.value.strip() if override_sw.value else None,
                )
                _advance(4)

            next_btn.on("click", advance_step3)

        # ── Step 4: confirm & save ────────────────────────────────────────────
        # ── Step 4: transaction browser + payment patterns ──────────────────
        def _step4():
            d         = state["bank_details"]
            prefix    = d.get("prefix", "")
            is_credit = state.get("account_type", "") == "credit"

            skip_btn.set_text("Skip")

            with ui.column().classes("w-full gap-0"):
                # ── Header strip ──────────────────────────────────────────────
                with ui.column().classes("px-6 py-4 gap-1"):
                    ui.label("Payment patterns").classes("text-base font-semibold text-zinc-800")
                    if is_credit:
                        ui.label(
                            "Search your transactions to find the exact text that identifies "
                            "payment rows. Click ⊕ on any row to copy its description into "
                            "the fields below. Highlighted rows already match."
                        ).classes("text-sm text-zinc-500")
                    else:
                        ui.label(
                            "No payment patterns needed for checking accounts. "
                            "Click Next to review and save."
                        ).classes("text-sm text-zinc-500")

                if not is_credit:
                    next_btn.on("click", lambda: _advance(5))
                    return

                # ── Pattern fields ─────────────────────────────────────────────
                with ui.row().classes("px-6 gap-3 flex-wrap"):
                    with ui.column().classes("gap-0.5 flex-1 min-w-36"):
                        ui.label("Payment category").classes("text-xs text-zinc-500")
                        pat_cat_in = ui.input(
                            value=d.get("payment_category", ""),
                            placeholder="e.g. Payment/Credit",
                        ).classes("w-full").props("outlined dense")
                    with ui.column().classes("gap-0.5 flex-1 min-w-36"):
                        ui.label("Payment description").classes("text-xs text-zinc-500")
                        pat_desc_in = ui.input(
                            value=d.get("payment_description", ""),
                            placeholder="e.g. ONLINE PAYMENT",
                        ).classes("w-full").props("outlined dense")
                    with ui.column().classes("gap-0.5 flex-1 min-w-36"):
                        ui.label("Checking-side pattern").classes("text-xs text-zinc-500")
                        pat_chk_in = ui.input(
                            value=d.get("checking_payment_pattern", ""),
                            placeholder="e.g. CAPITAL ONE",
                        ).classes("w-full").props("outlined dense")

                ui.separator().classes("mx-6")

                # ── Inline transaction table ───────────────────────────────────
                from sqlalchemy import text as _text

                schema     = get_schema()
                engine     = get_engine()
                table_name = "raw_" + prefix

                def _table_exists():
                    try:
                        with engine.connect() as conn:
                            r = conn.execute(_text(
                                "SELECT 1 FROM information_schema.tables "
                                "WHERE table_schema=:s AND table_name=:t"
                            ), {"s": schema, "t": table_name}).fetchone()
                            return r is not None
                    except Exception:
                        return False

                tbl_page = {"n": 1, "search": ""}

                def _query_rows(search: str, page: int, page_size: int = 40):
                    try:
                        with engine.connect() as conn:
                            cols_r = conn.execute(_text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema=:s AND table_name=:t "
                                "ORDER BY ordinal_position"
                            ), {"s": schema, "t": table_name}).fetchall()
                            cols = [r[0] for r in cols_r]

                            params: dict = {
                                "offset": (page - 1) * page_size,
                                "limit":  page_size,
                            }
                            where = ""
                            if search:
                                text_cols = [c for c in cols if c != "id"][:8]
                                like_parts = " OR ".join(
                                    f'CAST("{c}" AS TEXT) ILIKE :search'
                                    for c in text_cols
                                )
                                where = "WHERE (" + like_parts + ")"
                                params["search"] = "%" + search + "%"

                            date_col = next((c for c in cols if "date" in c.lower()), None)
                            order    = f'ORDER BY "{date_col}" DESC' if date_col else ""

                            rows = conn.execute(_text(
                                f'SELECT * FROM "{schema}"."{table_name}" '
                                f'{where} {order} LIMIT :limit OFFSET :offset'
                            ), params).fetchall()
                            total = conn.execute(_text(
                                f'SELECT COUNT(*) FROM "{schema}"."{table_name}" {where}'
                            ), {k: v for k, v in params.items()
                               if k not in ("offset", "limit")}).fetchone()[0]
                            return cols, [list(r) for r in rows], total
                    except Exception:
                        return [], [], 0

                def _matches_pattern(val: str) -> bool:
                    """True if value matches any of the currently-entered patterns."""
                    v = str(val).upper()
                    checks = [
                        pat_cat_in.value.strip().upper(),
                        pat_desc_in.value.strip().upper(),
                    ]
                    return any(c and c in v for c in checks)

                if not _table_exists():
                    with ui.column().classes("px-6 py-8 items-center gap-2"):
                        ui.icon("table_view").classes("text-zinc-200 text-5xl")
                        ui.label("No data yet for this bank.").classes("text-sm text-zinc-400")
                        ui.label(
                            "You can still save and come back to set patterns after uploading."
                        ).classes("text-xs text-zinc-400")
                    def advance_no_data():
                        d["payment_category"]         = pat_cat_in.value.strip()
                        d["payment_description"]      = pat_desc_in.value.strip()
                        d["checking_payment_pattern"] = pat_chk_in.value.strip()
                        _advance(5)
                    next_btn.on("click", advance_no_data)
                    return

                # Search bar
                with ui.row().classes("px-6 items-end gap-2"):
                    search_in = ui.input(placeholder="Search descriptions...") \
                        .classes("flex-1").props("outlined dense clearable")
                    ui.button("Search", icon="search",
                              on_click=lambda: (
                                  tbl_page.update({"n": 1, "search": search_in.value or ""}),
                                  render_rows.refresh()
                              )).props("unelevated dense no-caps") \
                       .classes("bg-zinc-700 text-white rounded-lg px-3")

                tbl_wrap = ui.column().classes("px-6 w-full gap-0")
                page_row  = ui.row().classes("px-6 items-center gap-2 py-2")

                @ui.refreshable
                def render_rows():
                    tbl_wrap.clear()
                    page_row.clear()
                    cols, rows, total = _query_rows(tbl_page["search"], tbl_page["n"])
                    PAGE_SIZE = 40
                    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

                    with tbl_wrap:
                        with ui.scroll_area().style("max-height:340px"):
                            with ui.element("table").classes(
                                "w-full text-xs border-collapse font-mono"
                            ):
                                with ui.element("thead"):
                                    with ui.element("tr"):
                                        # copy button col
                                        with ui.element("th").classes(
                                            "px-2 py-2 bg-zinc-50 border border-zinc-100 w-6"
                                        ):
                                            ui.label("")
                                        for col in cols:
                                            with ui.element("th").classes(
                                                "text-left px-2 py-2 bg-zinc-50 border "
                                                "border-zinc-100 text-zinc-500 font-semibold "
                                                "whitespace-nowrap"
                                            ):
                                                ui.label(col)

                                desc_idx = next(
                                    (i for i, c in enumerate(cols)
                                     if "desc" in c.lower() or "memo" in c.lower()),
                                    None
                                )

                                with ui.element("tbody"):
                                    for row in rows:
                                        # Check if any cell matches current patterns
                                        is_match = any(_matches_pattern(cell) for cell in row)
                                        row_bg = (
                                            "bg-amber-50 hover:bg-amber-100"
                                            if is_match else
                                            "hover:bg-zinc-50"
                                        )
                                        with ui.element("tr").classes(row_bg):
                                            # Copy button
                                            with ui.element("td").classes(
                                                "px-1 py-1 border border-zinc-100 text-center"
                                            ):
                                                desc_val = str(row[desc_idx]) if desc_idx is not None else ""
                                                copy_btn = ui.button(icon="add_circle_outline") \
                                                    .props("flat round dense size=xs") \
                                                    .classes("text-zinc-400 hover:text-blue-500")
                                                with ui.menu().props("auto-close"):
                                                    ui.label("Copy to field:").classes(
                                                        "text-xs text-zinc-400 px-3 pt-2 font-semibold"
                                                    )
                                                    for lbl, widget in [
                                                        ("Payment category",    pat_cat_in),
                                                        ("Payment description", pat_desc_in),
                                                        ("Checking pattern",    pat_chk_in),
                                                    ]:
                                                        preview = desc_val[:28] + ("…" if len(desc_val) > 28 else "")
                                                        def make_copy(w=widget, v=desc_val, l=lbl):
                                                            def _do():
                                                                w.set_value(v)
                                                                notify("Copied to " + l, type="positive", position="top")
                                                                render_rows.refresh()
                                                            return _do
                                                        ui.menu_item(
                                                            lbl + " ← " + preview,
                                                            on_click=make_copy(),
                                                        ).classes("text-xs")

                                            for cell in row:
                                                cell_str = str(cell) if cell is not None else ""
                                                is_cell_match = _matches_pattern(cell_str)
                                                with ui.element("td").classes(
                                                    "px-2 py-1 border border-zinc-100 "
                                                    "whitespace-nowrap max-w-xs overflow-hidden "
                                                    + ("text-amber-800 font-semibold" if is_cell_match
                                                       else "text-zinc-600")
                                                ):
                                                    ui.label(cell_str)

                    with page_row:
                        ui.label(f"{total:,} rows").classes("text-xs text-zinc-400 mr-1")
                        ui.button(icon="chevron_left",
                                  on_click=lambda: (tbl_page.update({"n": tbl_page["n"] - 1}), render_rows.refresh())) \
                            .props("flat round dense size=sm").classes("text-zinc-500") \
                            .bind_enabled_from(tbl_page, "n", lambda p: p > 1)
                        ui.label(f"p.{tbl_page['n']} / {total_pages}").classes("text-xs text-zinc-600")
                        ui.button(icon="chevron_right",
                                  on_click=lambda: (tbl_page.update({"n": tbl_page["n"] + 1}), render_rows.refresh())) \
                            .props("flat round dense size=sm").classes("text-zinc-500") \
                            .bind_enabled_from(tbl_page, "n", lambda p: p < total_pages)

                render_rows()

            def advance_step4():
                d["payment_category"]         = pat_cat_in.value.strip()
                d["payment_description"]      = pat_desc_in.value.strip()
                d["checking_payment_pattern"] = pat_chk_in.value.strip()
                _advance(5)

            next_btn.on("click", advance_step4)

        # ── Step 5: confirm + save ────────────────────────────────────────────
        def _step5():
            d    = state["bank_details"]
            m    = state["mapping"]
            acct = state["account_type"]
            _, acct_icon = ACCOUNT_COLORS.get(acct, ("", "account_balance"))

            with ui.column().classes("px-6 py-5 gap-4 w-full"):
                ui.label("Review and save.").classes("text-sm text-zinc-500")

                with ui.element("div").classes(
                    "w-full rounded-xl border border-zinc-200 bg-zinc-50 px-5 py-4 flex flex-col gap-3"
                ):
                    with ui.row().classes("items-center gap-3"):
                        ui.icon(acct_icon).classes("text-zinc-500 text-2xl")
                        with ui.column().classes("gap-0"):
                            ui.label(d["bank_name"]).classes("text-base font-semibold text-zinc-800")
                            ui.label("table: " + d["prefix"]).classes("text-xs text-zinc-400 font-mono")

                    chips = [
                        d["match_type"] + ': "' + d["match_value"] + '"'  ,
                        d["account_type"],
                    ]
                    if d.get("payment_description"):
                        chips.append("payment: " + d["payment_description"])
                    if d.get("member_name_column"):
                        chips.append("member col: " + d["member_name_column"])
                    if d.get("member_aliases"):
                        chips.append(str(len(d["member_aliases"])) + " member alias(es)")
                    if d.get("person_override") is not None:
                        chips.append("person: " + str(d["person_override"]))

                    with ui.row().classes("flex-wrap gap-2"):
                        for chip in chips:
                            ui.label(chip).classes(
                                "text-[11px] px-2 py-0.5 rounded-full border "
                                "bg-white border-zinc-200 text-zinc-600"
                            )

                    ui.separator()
                    ui.label("Column mapping:") \
                        .classes("text-xs text-zinc-400 font-semibold uppercase tracking-wide")
                    with ui.row().classes("flex-wrap gap-x-6 gap-y-1"):
                        for role, actual in m.for_account_type(acct).items():
                            if actual:
                                ui.label(role + " → " + actual).classes("text-xs font-mono text-zinc-600")

                    if d.get("member_aliases"):
                        ui.separator()
                        ui.label("Member aliases:") \
                            .classes("text-xs text-zinc-400 font-semibold uppercase tracking-wide")
                        all_users   = auth.get_all_users()
                        uid_to_label = {u.id: f"{u.display_name} ({u.person_name})" for u in all_users}
                        with ui.column().classes("gap-0.5"):
                            for raw_val, uid in d["member_aliases"].items():
                                ui.label(
                                    raw_val + " → " + uid_to_label.get(uid, "user #" + str(uid))
                                ).classes("text-xs font-mono text-zinc-600")

            def save_bank():
                print(f"[Wizard] saving column_map: {m.to_dict()}")
                print(f"[Wizard] mapping object: date={m.date} amount={m.amount} description={m.description} debit={m.debit} credit={m.credit}")
                rule = BankRule(
                    bank_name                = d["bank_name"],
                    prefix                   = d["prefix"],
                    match_type               = d["match_type"],
                    match_value              = d["match_value"],
                    account_type             = d["account_type"],
                    payment_category         = d.get("payment_category", ""),
                    payment_description      = d.get("payment_description", ""),
                    checking_payment_pattern = d.get("checking_payment_pattern", ""),
                    member_name_column       = d.get("member_name_column", ""),
                    member_aliases           = d.get("member_aliases", {}),
                    person_override          = d.get("person_override"),
                    column_map               = m.to_dict(),
                    dedup_columns            = m.dedup_columns(d["account_type"]),
                )
                rules = load_rules()
                if any(r.prefix == rule.prefix for r in rules):
                    notify(
                        'A bank with alias "' + rule.prefix + '" already exists. '
                        "Choose a different account alias.",
                        type="warning", position="top",
                    )
                    return
                rules.append(rule)
                save_rules(rules)
                notify("Added: " + rule.bank_name, type="positive", position="top")
                dlg.close()
                on_done()

            next_btn.on("click", save_bank)

        # ── navigation ────────────────────────────────────────────────────────
        def _advance(step: int):
            state["step"] = step
            render_step()

        def go_back():
            if state["step"] > 1:
                state["step"] -= 1
                render_step()

        render_step()

    dlg.open()



# ─────────────────────────────────────────────────────────────────────────────
# Transaction search dialog  (shared by wizard step 5 + edit dialog)
# ─────────────────────────────────────────────────────────────────────────────

def _open_transaction_search_dialog(
    prefix: str,
    *,
    payment_cat_ref:  dict | None = None,
    payment_desc_ref: dict | None = None,
    checking_pat_ref: dict | None = None,
):
    """
    Browse raw_{prefix} rows.  For credit accounts, each row has a
    copy-to button that writes into the provided input refs.

    Refs are dicts with a "widget" key holding the ui.input instance,
    e.g. {"widget": payment_desc_in}.  Pass None to hide that target.
    """
    from datetime import date, timedelta
    import pandas as pd

    table_name = "raw_" + prefix
    schema     = get_schema()
    engine     = get_engine()

    COPY_TARGETS = {}
    if payment_cat_ref:
        COPY_TARGETS["Payment category"] = payment_cat_ref
    if payment_desc_ref:
        COPY_TARGETS["Payment description"] = payment_desc_ref
    if checking_pat_ref:
        COPY_TARGETS["Checking pattern"] = checking_pat_ref

    # ── Check table exists ────────────────────────────────────────────────────
    def _table_exists() -> bool:
        try:
            with engine.connect() as conn:
                from sqlalchemy import text
                result = conn.execute(text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = :t"
                ), {"s": schema, "t": table_name}).fetchone()
                return result is not None
        except Exception:
            return False

    # ── Query ─────────────────────────────────────────────────────────────────
    def _query(search: str, date_from: str, date_to: str, page: int, page_size: int = 50):
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                # Get columns first
                cols_result = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
                ), {"s": schema, "t": table_name}).fetchall()
                cols = [r[0] for r in cols_result]

                where_parts = []
                params: dict = {"schema": schema, "offset": (page - 1) * page_size, "limit": page_size}

                if search:
                    # Search across all text columns
                    text_cols = [c for c in cols if c not in ("id",)]
                    if text_cols:
                        like_parts = " OR ".join(
                            f"CAST({chr(34)}{c}{chr(34)} AS TEXT) ILIKE :search"
                            for c in text_cols[:8]  # cap to avoid huge query
                        )
                        where_parts.append("(" + like_parts + ")")
                        params["search"] = "%" + search + "%"

                if date_from:
                    # Find date column
                    date_col = next((c for c in cols if "date" in c.lower()), None)
                    if date_col:
                        where_parts.append(f'"{date_col}" >= :date_from')
                        params["date_from"] = date_from

                if date_to:
                    date_col = next((c for c in cols if "date" in c.lower()), None)
                    if date_col:
                        where_parts.append(f'"{date_col}" <= :date_to')
                        params["date_to"] = date_to

                where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
                date_col = next((c for c in cols if "date" in c.lower()), None)
                order_clause = f'ORDER BY "{date_col}" DESC' if date_col else ""

                rows = conn.execute(text(
                    f'SELECT * FROM "{schema}"."{table_name}" '
                    f"{where_clause} {order_clause} "
                    f"LIMIT :limit OFFSET :offset"
                ), params).fetchall()

                count = conn.execute(text(
                    f'SELECT COUNT(*) FROM "{schema}"."{table_name}" {where_clause}'
                ), {k: v for k, v in params.items() if k not in ("offset", "limit")}).fetchone()[0]

                return cols, [list(r) for r in rows], count
        except Exception as ex:
            return [], [], 0

    # ── Dialog ────────────────────────────────────────────────────────────────
    with ui.dialog().props("maximized") as dlg, \
         ui.card().classes("w-full h-full rounded-none p-0 gap-0 overflow-hidden"):

        # Header
        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100 shrink-0"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("manage_search").classes("text-zinc-400 text-xl")
                with ui.column().classes("gap-0"):
                    ui.label("Transaction browser").classes("text-base font-semibold text-zinc-800")
                    ui.label("Table: " + table_name).classes("text-xs text-zinc-400 font-mono")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        if not _table_exists():
            with ui.column().classes("flex-1 items-center justify-center gap-3 py-20"):
                ui.icon("table_view").classes("text-zinc-200 text-6xl")
                ui.label("No data yet").classes("text-lg font-semibold text-zinc-400")
                ui.label(
                    "Upload a CSV for this bank first, then come back to browse transactions."
                ).classes("text-sm text-zinc-400")
            dlg.open()
            return

        # Filter bar
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

        # Table area
        table_container = ui.column().classes("flex-1 overflow-auto px-6 py-3 gap-0")
        pagination_row  = ui.row().classes("items-center gap-2 px-6 py-3 border-t border-zinc-100 shrink-0")

        @ui.refreshable
        def render_table():
            table_container.clear()
            pagination_row.clear()

            cols, rows, total = _query(
                page_state["search"],
                page_state["date_from"],
                page_state["date_to"],
                page_state["page"],
            )

            page_size = 50
            total_pages = max(1, (total + page_size - 1) // page_size)

            with table_container:
                if not rows:
                    with ui.column().classes("items-center py-12 gap-2"):
                        ui.icon("search_off").classes("text-zinc-200 text-5xl")
                        ui.label("No rows match your filters.").classes("text-sm text-zinc-400")
                else:
                    with ui.scroll_area().classes("w-full"):
                        with ui.element("table").classes(
                            "w-full text-xs border-collapse font-mono"
                        ):
                            # Header
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
                                            "border-zinc-100 text-zinc-500 font-semibold "
                                            "whitespace-nowrap"
                                        ):
                                            ui.label(col)

                            # Body
                            with ui.element("tbody"):
                                for row in rows:
                                    with ui.element("tr").classes("hover:bg-zinc-50"):
                                        if COPY_TARGETS:
                                            with ui.element("td").classes(
                                                "px-1 py-1 border border-zinc-100 text-center"
                                            ):
                                                # Find description-ish value
                                                desc_idx = next(
                                                    (i for i, c in enumerate(cols)
                                                     if "desc" in c.lower() or "memo" in c.lower()),
                                                    None
                                                )
                                                desc_val = str(row[desc_idx]) if desc_idx is not None else ""

                                                with ui.button_group().props("flat"):
                                                    copy_menu_btn = ui.button(
                                                        icon="add_circle_outline"
                                                    ).props("flat dense size=xs").classes("text-zinc-400 hover:text-blue-500")
                                                    with ui.menu().props("auto-close") as copy_menu:
                                                        ui.label("Copy to field:").classes(
                                                            "text-xs text-zinc-400 px-3 pt-2 pb-1 font-semibold"
                                                        )
                                                        for target_label, ref in COPY_TARGETS.items():
                                                            def make_copy(lbl=target_label, r=ref, v=desc_val):
                                                                def _do():
                                                                    r["widget"].set_value(v)
                                                                    notify(
                                                                        "Copied to " + lbl,
                                                                        type="positive", position="top"
                                                                    )
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

            # Pagination
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
# Edit / delete bank dialog
# ─────────────────────────────────────────────────────────────────────────────

def _open_edit_bank_dialog(rule: BankRule, on_save, on_delete):
    """Edit an existing BankRule or delete it."""
    import re as _re

    all_users    = auth.get_all_users()
    active_users = [u for u in all_users if u.is_active]
    user_opt_map: dict[str, int] = {
        f"{u.display_name}  ({u.person_name})": u.id
        for u in active_users
    }
    user_opt_labels = list(user_opt_map.keys())

    # Work on a mutable alias list pre-populated from the rule
    alias_rows: list[dict] = [
        {"raw_value": rv, "user_id": uid}
        for rv, uid in (rule.member_aliases or {}).items()
    ]

    def _to_slug(text: str) -> str:
        return _re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")

    def _table_name(bank: str, alias: str) -> str:
        """Returns slug only — actual table is raw_{slug}."""
        parts = [p for p in [_to_slug(bank), _to_slug(alias)] if p]
        return "_".join(parts) if parts else ""

    # Reverse-engineer display alias from stored prefix
    # prefix is "wells_fargo_mutual" → strip bank slug to get alias part
    bank_slug = _to_slug(rule.bank_name)
    stored_prefix = rule.prefix  # e.g. "wells_fargo_mutual"
    if stored_prefix.startswith(bank_slug + "_"):
        alias_display = stored_prefix[len(bank_slug) + 1:].replace("_", " ").title()
    elif stored_prefix == bank_slug:
        alias_display = ""
    else:
        alias_display = stored_prefix.replace("_", " ").title()

    is_credit = rule.account_type == "credit"

    with ui.dialog().props("persistent") as dlg, \
         ui.card().classes("w-[580px] rounded-2xl p-0 gap-0 overflow-hidden"):

        # Header
        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("tune").classes("text-zinc-400 text-xl")
                ui.label("Edit bank").classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=dlg.close) \
                .props("flat round dense").classes("text-zinc-400")

        with ui.scroll_area().style("max-height:70vh"):
          with ui.column().classes("px-6 py-5 gap-5 w-full"):

            # ── Bank name + account alias ──────────────────────────────────────
            ui.label("Bank details") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            with ui.column().classes("w-full gap-1"):
                ui.label("Bank name").classes("text-sm font-medium text-zinc-700")
                ui.label("The institution name, e.g. Citi, Wells Fargo, Capital One.") \
                    .classes("text-xs text-zinc-400")
                bank_name_in = ui.input(value=rule.bank_name, placeholder="e.g. Citi") \
                    .classes("w-full").props("outlined dense")

            with ui.column().classes("w-full gap-1"):
                ui.label("Account alias").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "The alias is locked after creation — it forms part of the raw table name "
                    "which cannot be renamed."
                ).classes("text-xs text-zinc-400")
                # Read-only display — alias cannot change after the table is created
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(alias_display or "—") \
                        .classes("flex-1 px-3 py-2 rounded border border-zinc-200 "
                                 "bg-zinc-50 text-zinc-500 text-sm font-mono")
                    ui.icon("lock").classes("text-zinc-300 text-base")
                # Hidden input to hold the value for _save()
                prefix_in = ui.input(value=alias_display).props("hidden")

            # Show the locked raw table name
            ui.label("raw table: raw_" + rule.prefix) \
                .classes("text-xs font-mono text-zinc-400 bg-zinc-50 "
                         "border border-zinc-200 rounded px-2 py-1")

            # ── Filename detection ─────────────────────────────────────────────
            ui.separator()
            ui.label("Filename detection") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            with ui.column().classes("w-full gap-1"):
                ui.label("Match type").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    'How the filename is compared. Use Contains with * for wildcards.'
                ).classes("text-xs text-zinc-400")
                match_type_sel = ui.select(
                    MATCH_TYPE_OPTIONS, value=rule.match_type,
                ).classes("w-full").props("outlined dense")

            with ui.column().classes("w-full gap-1"):
                ui.label("Filename value").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "Matched against the uploaded filename without its extension."
                ).classes("text-xs text-zinc-400")
                match_val_in = ui.input(
                    value=rule.match_value, placeholder="e.g. transaction_download"
                ).classes("w-full").props("outlined dense")

            # ── Credit-only settings ───────────────────────────────────────────
            credit_col = ui.column().classes("w-full gap-4")
            with credit_col:
                ui.separator()
                with ui.row().classes("items-center justify-between"):
                    ui.label("Credit card settings") \
                        .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
                    ui.button(
                        icon="manage_search",
                        on_click=lambda: _open_transaction_search_dialog(
                            rule.prefix,
                            payment_cat_ref={"widget": payment_cat_in},
                            payment_desc_ref={"widget": payment_desc_in},
                            checking_pat_ref={"widget": checking_pat_in},
                        )
                    ).props("flat round dense size=sm") \
                     .classes("text-zinc-400 hover:text-zinc-700") \
                     .tooltip("Browse transactions to find payment patterns")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Payment category value").classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "Category value in CSV that marks a payment/credit row, e.g. Payment/Credit."
                    ).classes("text-xs text-zinc-400")
                    payment_cat_in = ui.input(
                        value=rule.payment_category, placeholder="e.g. Payment/Credit"
                    ).classes("w-full").props("outlined dense")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Payment description pattern").classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "Substring in the description of payment rows, e.g. ONLINE PAYMENT."
                    ).classes("text-xs text-zinc-400")
                    payment_desc_in = ui.input(
                        value=rule.payment_description, placeholder="e.g. ONLINE PAYMENT"
                    ).classes("w-full").props("outlined dense")

                with ui.column().classes("w-full gap-1"):
                    ui.label("Checking-side payment pattern").classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "Text in your checking account when paying this card, e.g. CAPITAL ONE. "
                        "Those rows are excluded from debit spend."
                    ).classes("text-xs text-zinc-400")
                    checking_pat_in = ui.input(
                        value=rule.checking_payment_pattern, placeholder="e.g. CAPITAL ONE"
                    ).classes("w-full").props("outlined dense")

            credit_col.set_visibility(is_credit)

            # ── Member name aliases ────────────────────────────────────────────
            if rule.member_name_column:
                ui.separator()
                ui.label("Member name aliases") \
                    .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")
                ui.label(
                    "Column \"" + rule.member_name_column + "\" stores the member name. "
                    "Map raw values to registered users. Stored by user ID so renames never break data."
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

                with ui.column().classes("w-full gap-1 mt-1"):
                    ui.label("Add alias").classes("text-sm font-medium text-zinc-700")
                    ui.label(
                        "Enter the value as it appears in the member column (uppercased automatically), "
                        "then pick the matching user."
                    ).classes("text-xs text-zinc-400")
                with ui.row().classes("w-full items-end gap-2"):
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

            # ── Person override ────────────────────────────────────────────────
            ui.separator()
            with ui.column().classes("w-full gap-1"):
                ui.label("Person override (optional)").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "Force every row from this bank to one person. "
                    "Useful for shared accounts."
                ).classes("text-xs text-zinc-400")
            has_override = rule.person_override is not None
            with ui.row().classes("w-full gap-3 items-center"):
                override_sw = ui.switch("Enable person override", value=has_override) \
                    .classes("text-sm shrink-0")
                person_override_in = ui.input(
                    value=rule.person_override or "",
                    placeholder="e.g. mutual",
                ).classes("flex-1").props("outlined dense")
                person_override_in.set_visibility(has_override)
                override_sw.on(
                    "update:model-value",
                    lambda e: person_override_in.set_visibility(e.args)
                )

        # Footer
        with ui.row().classes("items-center justify-between px-6 py-4 border-t border-zinc-100"):
            ui.button("Delete bank", icon="delete_outline", on_click=lambda: _confirm_delete()) \
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
        # prefix is intentionally NOT updated — raw table name is immutable
        rule.match_type               = match_type_sel.value
        rule.match_value              = mval
        rule.payment_category         = payment_cat_in.value.strip()  if is_credit else ""
        rule.payment_description      = payment_desc_in.value.strip() if is_credit else ""
        rule.checking_payment_pattern = checking_pat_in.value.strip() if is_credit else ""
        rule.member_aliases           = {a["raw_value"]: a["user_id"] for a in alias_rows}
        rule.person_override          = person_override_in.value.strip() if override_sw.value else None
        dlg.close()
        on_save(rule)

    def _confirm_delete():
        with ui.dialog() as confirm_dlg, \
             ui.card().classes("rounded-2xl p-0 gap-0 overflow-hidden w-80"):
            with ui.column().classes("px-6 py-5 gap-3"):
                ui.label("Delete bank?").classes("text-base font-semibold text-zinc-800")
                ui.label(
                    "This removes the rule for \"" + rule.bank_name + "\". "
                    "Uploaded data in the raw table is not deleted."
                ).classes("text-sm text-zinc-500")
            with ui.row().classes("items-center justify-end gap-2 px-6 py-4 border-t border-zinc-100"):
                ui.button("Cancel", on_click=confirm_dlg.close) \
                    .props("flat no-caps").classes("text-zinc-500")
                ui.button("Delete", icon="delete", on_click=lambda: (confirm_dlg.close(), dlg.close(), on_delete(rule))) \
                    .props("unelevated no-caps") \
                    .classes("bg-red-500 text-white px-4 rounded-lg")
        confirm_dlg.open()

    dlg.open()


# ─────────────────────────────────────────────────────────────────────────────
# Bank sidebar card
# ─────────────────────────────────────────────────────────────────────────────

def _bank_card(rule: BankRule, selected_ref: dict, on_select, on_edit) -> None:
    is_sel = selected_ref["value"] == rule.prefix
    _, acct_icon = ACCOUNT_COLORS.get(rule.account_type, ("", "account_balance"))
    # Outer row: NOT clickable — contains the selectable area + gear button side by side
    with ui.row().classes("w-full items-center gap-1"):
        # Selectable area takes all available space
        with ui.row().classes(
            "flex-1 items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer "
            "transition-colors border min-w-0 " +
            ("bg-zinc-800 border-zinc-700" if is_sel
             else "bg-white border-zinc-100 hover:bg-zinc-50")
        ).on("click", lambda r=rule: on_select(r.prefix)):
            ui.icon(acct_icon).classes(
                "text-xl " + ("text-white" if is_sel else "text-zinc-400")
            )
            with ui.column().classes("gap-0 flex-1 min-w-0"):
                ui.label(rule.bank_name).classes(
                    "text-sm font-medium truncate " +
                    ("text-white" if is_sel else "text-zinc-800")
                )
                ui.label(rule.account_type).classes(
                    "text-[11px] " + ("text-zinc-300" if is_sel else "text-zinc-400")
                )
        # Gear sits outside the clickable row — no propagation issue
        ui.button(icon="settings", on_click=lambda r=rule: on_edit(r)) \
            .props("flat round dense") \
            .classes("text-zinc-400 hover:text-zinc-700 shrink-0")


# ─────────────────────────────────────────────────────────────────────────────
# Main page
# ─────────────────────────────────────────────────────────────────────────────

def content() -> None:
    person_ref   = {"value": ""}
    selected_ref = {"value": "auto"}   # "auto" or rule.prefix

    if auth.is_admin():
        all_users = auth.get_all_users()
        seen: set = set()
        person_options = [
            u.person_name for u in all_users
            if u.is_active and not (u.person_name in seen or seen.add(u.person_name))
        ]
    else:
        person_options = [auth.current_person_name()]

    default_person      = auth.current_person_name() or (person_options[0] if person_options else "")
    person_ref["value"] = default_person

    with ui.row().classes("w-full items-center justify-between mb-2"):
        with ui.column().classes("gap-0"):
            ui.label("Data uploader").classes("page-title")
            ui.label(
                "Upload the latest data from your bank account and update your dashboard."
            ).classes("text-sm text-muted")

    ui.element("div").classes("divider mb-4")

    @ui.refreshable
    def page_body():
        rules = load_rules()

        if not rules:
            with ui.column().classes("w-full items-center justify-center py-24 gap-5"):
                ui.icon("account_balance").classes("text-zinc-200 text-7xl")
                ui.label("No banks configured yet") \
                    .classes("text-xl font-semibold text-zinc-400")
                ui.label("Add your first bank to start uploading transactions.") \
                    .classes("text-sm text-zinc-400")
                ui.button(
                    "Add your first bank", icon="add_card",
                    on_click=lambda: _open_add_bank_wizard(on_done=page_body.refresh),
                ).props("unelevated no-caps") \
                 .classes("bg-zinc-800 text-white px-6 rounded-xl mt-2")
            return

        def _select(prefix: str):
            selected_ref["value"] = prefix
            page_body.refresh()

        with ui.row().classes("w-full gap-5 items-start"):

            # Sidebar
            with ui.column().classes(
                "gap-1 shrink-0 w-52 bg-zinc-50 rounded-xl border border-zinc-100 p-2"
            ):
                ui.label("Banks").classes(
                    "text-[11px] font-semibold text-zinc-400 uppercase "
                    "tracking-wide px-2 pt-1 pb-0.5"
                )
                is_auto = selected_ref["value"] == "auto"
                with ui.row().classes(
                    "w-full flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer "
                    "transition-colors border " +
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

                ui.separator().classes("my-1")
                def _edit(r: BankRule):
                    def on_save(updated: BankRule):
                        all_rules = load_rules()
                        idx = next((i for i, x in enumerate(all_rules) if x.bank_name == updated.bank_name or x.prefix == r.prefix), None)
                        if idx is not None:
                            all_rules[idx] = updated
                        else:
                            all_rules.append(updated)
                        save_rules(all_rules)
                        notify("Saved: " + updated.bank_name, type="positive", position="top")
                        page_body.refresh()

                    def on_delete(deleted: BankRule):
                        all_rules = load_rules()
                        all_rules = [x for x in all_rules if x.prefix != deleted.prefix]
                        save_rules(all_rules)
                        notify("Deleted: " + deleted.bank_name, type="info", position="top")
                        page_body.refresh()

                    _open_edit_bank_dialog(r, on_save=on_save, on_delete=on_delete)

                for rule in rules:
                    _bank_card(rule, selected_ref, _select, on_edit=_edit)
                ui.separator().classes("my-1")

                ui.button(
                    "Add bank", icon="add",
                    on_click=lambda: _open_add_bank_wizard(on_done=page_body.refresh),
                ).props("flat no-caps dense") \
                 .classes("text-zinc-500 text-xs w-full justify-start px-3")

            # Upload area
            with ui.column().classes("flex-1 gap-4 min-w-0"):
                with ui.row().classes("items-center gap-3"):
                    ui.label("Person:").classes("text-sm text-zinc-500 shrink-0")
                    radio = ui.radio(
                        {p: p for p in person_options}, value=default_person
                    ).classes("inline-flex items-center gap-3")
                    radio.on(
                        "update:model-value",
                        lambda e: person_ref.update({"value": e.args})
                    )

                active_rule = next(
                    (r for r in rules if r.prefix == selected_ref["value"]), None
                )
                if active_rule:
                    acct_cls, acct_icon = ACCOUNT_COLORS.get(active_rule.account_type, ("", "account_balance"))
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        # Bank identity pill
                        with ui.row().classes(
                            "items-center gap-2 px-3 py-1.5 rounded-full border text-xs " + acct_cls
                        ):
                            ui.icon(acct_icon).classes("text-base")
                            ui.label(active_rule.bank_name).classes("font-semibold")

                        # Match pill
                        with ui.row().classes(
                            "items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs "
                            "bg-zinc-50 border-zinc-200 text-zinc-600"
                        ):
                            ui.icon("search").classes("text-base text-zinc-400")
                            ui.label(
                                active_rule.match_type + ': "' + active_rule.match_value + '"'
                            ).classes("font-mono")

                        # Alias pill
                        if active_rule.prefix:
                            with ui.row().classes(
                                "items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs "
                                "bg-zinc-50 border-zinc-200 text-zinc-600"
                            ):
                                ui.icon("tag").classes("text-base text-zinc-400")
                                ui.label(active_rule.prefix).classes("font-mono")

                        # Person override pill
                        if active_rule.person_override is not None:
                            with ui.row().classes(
                                "items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs "
                                "bg-teal-50 border-teal-200 text-teal-700"
                            ):
                                ui.icon("person").classes("text-base")
                                ui.label(active_rule.person_override or "—").classes("font-mono")
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
                    # active_rule is the bank currently selected in the sidebar.
                    # Captured into the lambda so each upload uses the rule at the
                    # moment the file is dropped — no filename matching needed.
                    _rule_snap = active_rule  # snapshot in closure
                    ui.upload(
                        on_upload=lambda e, r=_rule_snap: handle_upload(e, person_ref, bank_rule=r),
                        auto_upload=False,
                        multiple=True,
                        label="Drop CSV files here or click to browse",
                    ).classes("w-full").props("flat")

    page_body()
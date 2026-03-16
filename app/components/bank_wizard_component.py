"""
pages/bank_wizard_component.py — Add-bank wizard (5 steps)

Step 1 — Upload sample CSV
Step 2 — Map columns  →  stages CSV data into a temp DB table
Step 3 — Bank details (name, alias, filename matching, member aliases)
Step 4 — Payment patterns (credit only); browse staged data (credit) or
          existing debit transactions (debit) via a toggle switch
Step 5 — Confirm + save; pushes staged data to consolidated tables
"""
from __future__ import annotations

import io
import re as _re
import uuid
from pathlib import Path

import pandas as pd
from nicegui import ui, events
from sqlalchemy import text as sa_text

import services.auth as auth
from data.bank_rules import BankRule, load_rules, save_rules
from services.notifications import notify
from services.upload_pipeline import (
    sniff, suggest_mapping, ColumnMapping,
    REQUIRED_ROLES, SniffResult,
)
from data.db import get_engine, get_schema


# ── Constants ──────────────────────────────────────────────────────────────────

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

ACCOUNT_COLORS = {
    "credit":   ("bg-violet-50 text-violet-700 border-violet-200", "credit_card"),
    "checking": ("bg-sky-50 text-sky-700 border-sky-200",          "account_balance"),
}


# ── Staging table helpers ──────────────────────────────────────────────────────

def _drop_staging_table(temp_table: str) -> None:
    engine = get_engine()
    schema = get_schema()
    try:
        with engine.begin() as conn:
            conn.execute(sa_text(
                f'DROP TABLE IF EXISTS "{schema}"."{temp_table}"'
            ))
    except Exception as ex:
        print(f"[WizardStage] drop failed for {temp_table}: {ex}")


def _create_staging_table(state: dict) -> bool:
    """
    Parse state["raw"] using state["mapping"] and write normalised rows
    into a staging table (schema.wizard_stage_<id>).
    Also stores the parsed DataFrame in state["staged_df"] for the final push.
    Returns True on success.
    """
    engine       = get_engine()
    schema       = get_schema()
    raw          = state["raw"]
    mapping: ColumnMapping = state["mapping"]
    account_type = state["account_type"]
    temp_table   = state["temp_table"]
    is_credit    = account_type == "credit"

    # ── Parse CSV ──────────────────────────────────────────────────────────────
    sniff_result: SniffResult | None = state.get("sniff")
    try:
        text_data = raw.decode("utf-8", errors="replace")
        try:
            sep = pd.io.parsers.readers.csv.Sniffer().sniff(text_data[:4096]).delimiter
        except Exception:
            sep = ","
        if sniff_result and not sniff_result.has_header:
            df = pd.read_csv(io.BytesIO(raw), sep=sep, header=None, dtype=str)
            df.columns = [f"col_{i}" for i in range(len(df.columns))]
        else:
            df = pd.read_csv(io.BytesIO(raw), sep=sep, dtype=str)
    except Exception as ex:
        print(f"[WizardStage] CSV parse failed: {ex}")
        return False

    state["staged_df"] = df

    # ── Build normalised rows ──────────────────────────────────────────────────
    date_col = mapping.date or ""
    desc_col = mapping.description or ""
    rows: list[dict] = []

    for _, row in df.iterrows():
        raw_date = row.get(date_col)
        if pd.isna(raw_date) or str(raw_date).strip() in ("", "NaN", "nan"):
            continue
        try:
            txn_date = pd.to_datetime(str(raw_date), dayfirst=False).date()
        except Exception:
            continue
        desc = str(row.get(desc_col, "")).strip()

        if is_credit:
            debit_col  = mapping.debit  or ""
            credit_col = mapping.credit or ""
            try:
                dbt = float(str(row.get(debit_col, 0) or 0).replace(",", ""))
                if pd.isna(dbt): dbt = 0.0
            except ValueError:
                dbt = 0.0
            try:
                crd = float(str(row.get(credit_col, 0) or 0).replace(",", ""))
                if pd.isna(crd): crd = 0.0
            except ValueError:
                crd = 0.0
            rows.append({"date": txn_date, "description": desc,
                         "debit": abs(dbt), "credit": abs(crd)})
        else:
            amount_col = mapping.amount or ""
            try:
                amt = float(str(row.get(amount_col, 0) or 0).replace(",", ""))
            except ValueError:
                amt = 0.0
            rows.append({"date": txn_date, "description": desc, "amount": amt})

    if not rows:
        print("[WizardStage] no valid rows after parsing")
        return False

    # ── Write staging table ────────────────────────────────────────────────────
    try:
        with engine.begin() as conn:
            conn.execute(sa_text(
                f'DROP TABLE IF EXISTS "{schema}"."{temp_table}"'
            ))
            if is_credit:
                conn.execute(sa_text(f"""
                    CREATE TABLE "{schema}"."{temp_table}" (
                        id          SERIAL PRIMARY KEY,
                        date        DATE,
                        description TEXT,
                        debit       NUMERIC,
                        credit      NUMERIC
                    )
                """))
                conn.execute(sa_text(f"""
                    INSERT INTO "{schema}"."{temp_table}"
                        (date, description, debit, credit)
                    VALUES (:date, :description, :debit, :credit)
                """), rows)
            else:
                conn.execute(sa_text(f"""
                    CREATE TABLE "{schema}"."{temp_table}" (
                        id          SERIAL PRIMARY KEY,
                        date        DATE,
                        description TEXT,
                        amount      NUMERIC
                    )
                """))
                conn.execute(sa_text(f"""
                    INSERT INTO "{schema}"."{temp_table}"
                        (date, description, amount)
                    VALUES (:date, :description, :amount)
                """), rows)
        print(f"[WizardStage] {len(rows)} rows staged → {schema}.{temp_table}")
        return True
    except Exception as ex:
        print(f"[WizardStage] DB write failed: {ex}")
        return False


# ── Wizard ─────────────────────────────────────────────────────────────────────

def open_add_bank_wizard(on_done) -> None:
    """Open the 5-step add-bank wizard dialog."""

    state: dict = {
        "step":         1,
        "raw":          None,
        "filename":     "",
        "sniff":        None,
        "mapping":      None,
        "account_type": "checking",
        "bank_details": {},
        "staged_df":    None,
        "temp_table":   f"wizard_stage_{uuid.uuid4().hex[:12]}",
    }

    with ui.dialog().props("persistent") as dlg, \
         ui.card().classes("w-[660px] rounded-2xl p-0 gap-0 overflow-hidden"):

        # Header
        with ui.row().classes("items-center justify-between px-6 py-4 border-b border-zinc-100"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("add_card").classes("text-zinc-400 text-xl")
                title_lbl = ui.label("Add bank — step 1 of 5") \
                    .classes("text-base font-semibold text-zinc-800")
            ui.button(icon="close", on_click=lambda: _close_wizard()) \
                .props("flat round dense").classes("text-zinc-400")

        body = ui.column().classes("w-full")

        # Footer
        with ui.row().classes("items-center justify-between px-6 py-4 border-t border-zinc-100"):
            back_btn = ui.button("Back", icon="arrow_back") \
                .props("flat no-caps").classes("text-zinc-500")
            with ui.row().classes("gap-2"):
                skip_btn = ui.button("Skip") \
                    .props("flat no-caps").classes("text-zinc-400")
                skip_btn.set_visibility(False)
                next_btn = ui.button("Next", icon="arrow_forward") \
                    .props("unelevated no-caps") \
                    .classes("bg-zinc-800 text-white px-5 rounded-lg")

    def _close_wizard():
        _drop_staging_table(state["temp_table"])
        dlg.close()

    # ── render_step ─────────────────────────────────────────────────────────────
    def render_step():
        body.clear()
        st = state["step"]
        title_lbl.set_text(f"Add bank — step {st} of 5")
        back_btn.set_visibility(st > 1)
        next_btn.set_text("Save bank" if st == 5 else "Next")
        next_btn.enable()
        next_btn._event_listeners.clear()
        back_btn._event_listeners.clear()
        skip_btn.set_visibility(st == 4)
        back_btn.on("click", go_back)
        with body:
            if st == 1:   _step1()
            elif st == 2: _step2()
            elif st == 3: _step3()
            elif st == 4: _step4()
            elif st == 5: _step5()

    # ── Step 1: upload sample CSV ──────────────────────────────────────────────
    def _step1():
        next_btn.disable()
        with ui.column().classes("px-6 py-5 gap-4 w-full"):
            ui.label("Upload a sample CSV from this bank.") \
                .classes("text-sm text-zinc-500")
            ui.label(
                "We'll inspect the column structure so you can map them next. "
                "The data will be staged and committed when you save in the final step."
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

    # ── Step 2: column mapping → stages CSV on advance ─────────────────────────
    def _step2():
        sniff_res: SniffResult = state["sniff"]
        norm_cols = sniff_res.norm_columns
        dragging: dict = {"col": None}

        with ui.column().classes("px-6 py-5 gap-4 w-full"):
            with ui.row().classes("items-center gap-3"):
                ui.label("Account type:").classes("text-sm font-medium text-zinc-700")
                acct_sel = ui.toggle(
                    {"checking": "Checking / savings", "credit": "Credit card"},
                    value=state["account_type"],
                ).props("no-caps")

            ui.separator()

            @ui.refreshable
            def mapping_ui():
                acct     = state["account_type"]
                m        = state["mapping"]
                assigned = {v for v in m.to_dict().values() if v}
                required = set(REQUIRED_ROLES[acct])

                with ui.row().classes("w-full gap-5 items-start"):

                    # ── Column palette ──────────────────────────────────────────
                    with ui.column().classes("gap-1.5 w-40 shrink-0"):
                        ui.label("CSV columns").classes(
                            "text-xs font-semibold text-zinc-400 uppercase tracking-wide"
                        )
                        for col in norm_cols:
                            used = col in assigned
                            chip = ui.element("div").classes(
                                "px-2.5 py-1.5 rounded-lg border text-xs font-mono "
                                "select-none transition-colors "
                                + ("bg-zinc-50 border-zinc-100 text-zinc-300"
                                   if used else
                                   "bg-white border-zinc-300 text-zinc-700 "
                                   "cursor-grab hover:border-blue-300 hover:bg-blue-50")
                            ).props(f'draggable={"true" if not used else "false"}')
                            with chip:
                                ui.label(col)
                            if not used:
                                chip.on("dragstart",
                                        lambda e, c=col: dragging.update({"col": c}))

                    # ── Role drop zones ─────────────────────────────────────────
                    with ui.column().classes("flex-1 gap-1.5"):
                        ui.label("Field mapping").classes(
                            "text-xs font-semibold text-zinc-400 uppercase tracking-wide"
                        )
                        for role, label in ROLE_LABELS.items():
                            if role == "amount" and acct != "checking":
                                continue
                            if role in ("debit", "credit") and acct != "credit":
                                continue
                            current = getattr(m, role, None)
                            is_req  = role in required

                            with ui.row().classes("w-full items-center gap-2"):
                                ui.label("●" if is_req else "○").classes(
                                    "text-xs w-3 shrink-0 "
                                    + ("text-red-400" if is_req else "text-zinc-300")
                                )
                                ui.label(label).classes(
                                    "text-sm w-44 shrink-0 "
                                    + ("font-medium text-zinc-700" if is_req
                                       else "text-zinc-400")
                                )
                                zone = ui.element("div").classes(
                                    "flex-1 min-h-[34px] rounded-lg border-2 border-dashed "
                                    "px-3 py-1 flex items-center justify-between gap-2 "
                                    + ("border-blue-200 bg-blue-50"
                                       if current else
                                       "border-zinc-200 bg-zinc-50 hover:border-zinc-300")
                                ).props(
                                    'ondragover="event.preventDefault()" '
                                    'ondragenter="event.preventDefault()"'
                                )
                                with zone:
                                    if current:
                                        ui.label(current).classes(
                                            "text-xs font-mono text-blue-700 flex-1 truncate"
                                        )
                                        def _clear(r=role):
                                            setattr(state["mapping"], r, None)
                                            mapping_ui.refresh()
                                        ui.button(icon="close", on_click=_clear) \
                                            .props("flat round dense size=xs") \
                                            .classes("text-zinc-400 shrink-0")
                                    else:
                                        ui.label("drop here").classes(
                                            "text-xs text-zinc-300 pointer-events-none"
                                        )

                                def _drop(e, r=role):
                                    col = dragging.get("col")
                                    if col:
                                        setattr(state["mapping"], r, col)
                                        dragging["col"] = None
                                        mapping_ui.refresh()

                                zone.on("drop", _drop)

            mapping_ui()

            def on_acct_change(_=None):
                state["account_type"] = acct_sel.value
                state["mapping"] = suggest_mapping(sniff_res, acct_sel.value)
                mapping_ui.refresh()

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
            missing = m.missing_required(acct)
            if missing:
                notify(
                    "Map required columns: " + ", ".join(missing),
                    type="warning", position="top",
                )
                return
            # Stage CSV data now that mapping is confirmed
            next_btn.disable()
            next_btn.set_text("Staging…")
            ok = _create_staging_table(state)
            next_btn.set_text("Next")
            next_btn.enable()
            if not ok:
                notify(
                    "Could not stage CSV data — check column mapping.",
                    type="warning", position="top",
                )
                return
            _advance(3)

        next_btn.on("click", advance_step2)

    # ── Step 3: bank details + member aliases ──────────────────────────────────
    def _step3():
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

        def _to_slug(text: str) -> str:
            return _re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")

        def _table_name(bank: str, alias: str) -> str:
            parts = [p for p in [_to_slug(bank), _to_slug(alias)] if p]
            return "_".join(parts) if parts else ""

        with ui.scroll_area().style("max-height:62vh"):
          with ui.column().classes("px-6 py-5 gap-5 w-full"):

            # ── Bank name + account alias ──────────────────────────────────────
            ui.label("Bank details") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            with ui.column().classes("w-full gap-1"):
                ui.label("Bank name").classes("text-sm font-medium text-zinc-700")
                ui.label("The institution name, e.g. Citi, Wells Fargo, Capital One.") \
                    .classes("text-xs text-zinc-400")
                bank_name_in = ui.input(placeholder="e.g. Citi") \
                    .classes("w-full").props("outlined dense")

            with ui.column().classes("w-full gap-1"):
                ui.label("Account alias").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "A short name for this specific account, e.g. Daily Spending, Rewards, Joint. "
                    "Together with the bank name this becomes the raw data table name."
                ).classes("text-xs text-zinc-400")
                prefix_in = ui.input(placeholder="e.g. Daily Spending") \
                    .classes("w-full").props("outlined dense")

            table_preview = ui.label("raw table: raw_") \
                .classes("text-xs font-mono text-zinc-400 bg-zinc-50 "
                         "border border-zinc-200 rounded px-2 py-1")

            def update_table_preview(_=None):
                slug = _table_name(bank_name_in.value, prefix_in.value)
                table_preview.set_text("raw table: raw_" + slug if slug else "raw table: raw_")

            bank_name_in.on("update:model-value", update_table_preview)
            prefix_in.on("update:model-value", update_table_preview)

            # ── Filename detection ─────────────────────────────────────────────
            ui.separator()
            ui.label("Filename detection") \
                .classes("text-xs font-semibold text-zinc-400 uppercase tracking-wide")

            with ui.column().classes("w-full gap-1"):
                ui.label("Match type").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "How to compare the uploaded filename against the value below. "
                    'Exact is pre-selected from your sample. Switch to Contains and use * for wildcards.'
                ).classes("text-xs text-zinc-400")
                match_type_sel = ui.select(
                    MATCH_TYPE_OPTIONS, value="exact",
                ).classes("w-full").props("outlined dense")

            with ui.column().classes("w-full gap-1"):
                ui.label("Filename value").classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "Matched against the uploaded filename without its extension. "
                    "Pre-filled from your sample file."
                ).classes("text-xs text-zinc-400")
                match_val_in = ui.input(
                    value=uploaded_stem,
                    placeholder="e.g. transaction_download",
                ).classes("w-full").props("outlined dense")

            # ── Member name aliases (only if member col was mapped) ────────────
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

                with ui.column().classes("w-full gap-1 mt-1"):
                    ui.label("Add alias").classes("text-sm font-medium text-zinc-700")
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

            # ── Person override ────────────────────────────────────────────────
            ui.separator()
            with ui.column().classes("w-full gap-1"):
                ui.label("Person override (optional)") \
                    .classes("text-sm font-medium text-zinc-700")
                ui.label(
                    "Pin every row from this bank to specific people. "
                    "Select multiple for a shared/mutual account."
                ).classes("text-xs text-zinc-400")

            override_ids: set[int] = set()
            override_sw = ui.switch("Enable person override").classes("text-sm")
            override_container = ui.column().classes("w-full gap-1 pl-1")
            override_container.set_visibility(False)
            override_sw.on(
                "update:model-value",
                lambda e: override_container.set_visibility(e.args)
            )
            with override_container:
                for u in active_users:
                    chk = ui.checkbox(f"{u.person_name}  ({u.display_name})", value=False)
                    chk.on(
                        "update:model-value",
                        lambda e, uid=u.id: (
                            override_ids.add(uid) if e.args else override_ids.discard(uid)
                        ),
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
                notify(
                    "Could not generate a table name — check bank name and alias.",
                    type="warning", position="top",
                )
                return
            if not mval:
                notify("Filename value is required.", type="warning", position="top")
                return
            state["bank_details"] = dict(
                bank_name                = bname,
                prefix                   = pfx,
                match_type               = match_type_sel.value,
                match_value              = mval,
                account_type             = state["account_type"],
                member_name_column       = member_col,
                member_aliases           = {a["raw_value"]: a["user_id"] for a in alias_rows},
                person_override          = sorted(override_ids) if override_sw.value and override_ids else None,
            )
            _advance(4)

        next_btn.on("click", advance_step3)

    # ── Step 4: payment patterns + dual-source transaction browser ─────────────
    def _step4():
        d         = state["bank_details"]
        is_credit = state.get("account_type", "") == "credit"

        skip_btn.set_text("Skip")

        with ui.column().classes("w-full gap-0"):

            # Header
            with ui.column().classes("px-6 py-4 gap-1"):
                ui.label("Payment patterns").classes("text-base font-semibold text-zinc-800")
                if is_credit:
                    ui.label(
                        "Search transactions to identify payment rows and the checking-side pattern. "
                        "Use the Credit tab to browse your uploaded CSV data, "
                        "or the Debit tab to find how this card's payment appears in your checking account."
                    ).classes("text-sm text-zinc-500")
                else:
                    ui.label(
                        "No payment patterns needed for checking accounts. "
                        "Click Next to review and save."
                    ).classes("text-sm text-zinc-500")

            if not is_credit:
                next_btn.on("click", lambda: _advance(5))
                return

            # Pattern fields
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

            # Source toggle + search bar
            with ui.row().classes("px-6 items-center gap-4 py-2 flex-wrap"):
                source_toggle = ui.toggle(
                    {"credit": "Credit (uploaded data)", "debit": "Debit transactions"},
                    value="credit",
                ).props("no-caps dense")
                search_in = ui.input(placeholder="Search descriptions…") \
                    .classes("flex-1 min-w-32").props("outlined dense clearable")
                ui.button(
                    "Search", icon="search",
                    on_click=lambda: (
                        tbl_page.update({"n": 1, "search": search_in.value or ""}),
                        render_rows.refresh()
                    )
                ).props("unelevated dense no-caps") \
                 .classes("bg-zinc-700 text-white rounded-lg px-3")

            tbl_wrap = ui.column().classes("px-6 w-full gap-0")
            page_row = ui.row().classes("px-6 items-center gap-2 py-2")

            engine     = get_engine()
            schema     = get_schema()
            temp_table = state["temp_table"]
            tbl_page   = {"n": 1, "search": ""}
            PAGE_SIZE  = 40

            # ── Query helpers ──────────────────────────────────────────────────

            def _query_credit(search: str, page: int):
                """Query staged credit data from temp table."""
                try:
                    with engine.connect() as conn:
                        params: dict = {
                            "offset": (page - 1) * PAGE_SIZE,
                            "limit":  PAGE_SIZE,
                        }
                        where = ""
                        if search:
                            where = "WHERE description ILIKE :search"
                            params["search"] = "%" + search + "%"
                        rows = conn.execute(sa_text(
                            f'SELECT date, description, debit, credit '
                            f'FROM "{schema}"."{temp_table}" '
                            f'{where} ORDER BY date DESC '
                            f'LIMIT :limit OFFSET :offset'
                        ), params).fetchall()
                        count = conn.execute(sa_text(
                            f'SELECT COUNT(*) FROM "{schema}"."{temp_table}" {where}'
                        ), {k: v for k, v in params.items()
                           if k not in ("offset", "limit")}).fetchone()[0]
                        return ["date", "description", "debit", "credit"], [list(r) for r in rows], count
                except Exception:
                    return ["date", "description", "debit", "credit"], [], 0

            def _query_debit(search: str, page: int):
                """Query existing transactions_debit rows."""
                try:
                    with engine.connect() as conn:
                        params: dict = {
                            "offset": (page - 1) * PAGE_SIZE,
                            "limit":  PAGE_SIZE,
                        }
                        where = ""
                        if search:
                            where = "WHERE description ILIKE :search"
                            params["search"] = "%" + search + "%"
                        rows = conn.execute(sa_text(
                            f'SELECT transaction_date, description, amount, account_key '
                            f'FROM "{schema}".transactions_debit '
                            f'{where} ORDER BY transaction_date DESC '
                            f'LIMIT :limit OFFSET :offset'
                        ), params).fetchall()
                        count = conn.execute(sa_text(
                            f'SELECT COUNT(*) FROM "{schema}".transactions_debit {where}'
                        ), {k: v for k, v in params.items()
                           if k not in ("offset", "limit")}).fetchone()[0]
                        return ["date", "description", "amount", "account_key"], [list(r) for r in rows], count
                except Exception:
                    return ["date", "description", "amount", "account_key"], [], 0

            def _matches_pattern(val: str) -> bool:
                v = str(val).upper()
                checks = [
                    pat_cat_in.value.strip().upper(),
                    pat_desc_in.value.strip().upper(),
                ]
                return any(c and c in v for c in checks)

            # ── Table renderer ─────────────────────────────────────────────────

            @ui.refreshable
            def render_rows():
                tbl_wrap.clear()
                page_row.clear()

                mode = source_toggle.value
                if mode == "credit":
                    cols, rows, total = _query_credit(tbl_page["search"], tbl_page["n"])
                    copy_targets = [
                        ("Payment category",    pat_cat_in),
                        ("Payment description", pat_desc_in),
                    ]
                else:
                    cols, rows, total = _query_debit(tbl_page["search"], tbl_page["n"])
                    copy_targets = [("Checking pattern", pat_chk_in)]

                total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                desc_idx    = 1  # description is always the second column

                with tbl_wrap:
                    if not rows:
                        with ui.column().classes("items-center py-8 gap-2"):
                            ui.icon("search_off").classes("text-zinc-200 text-4xl")
                            ui.label("No rows to display.").classes("text-sm text-zinc-400")
                    else:
                        with ui.scroll_area().style("max-height:300px"):
                            with ui.element("table").classes(
                                "w-full text-xs border-collapse font-mono"
                            ):
                                with ui.element("thead"):
                                    with ui.element("tr"):
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

                                with ui.element("tbody"):
                                    for row in rows:
                                        is_match = any(_matches_pattern(str(cell)) for cell in row)
                                        row_bg   = (
                                            "bg-amber-50 hover:bg-amber-100"
                                            if is_match else "hover:bg-zinc-50"
                                        )
                                        with ui.element("tr").classes(row_bg):
                                            desc_val = str(row[desc_idx]) if row else ""
                                            with ui.element("td").classes(
                                                "px-1 py-1 border border-zinc-100 text-center"
                                            ):
                                                ui.button(icon="add_circle_outline") \
                                                    .props("flat round dense size=xs") \
                                                    .classes("text-zinc-400 hover:text-blue-500")
                                                with ui.menu().props("auto-close"):
                                                    ui.label("Copy to field:").classes(
                                                        "text-xs text-zinc-400 px-3 pt-2 font-semibold"
                                                    )
                                                    for lbl, widget in copy_targets:
                                                        preview = desc_val[:28] + ("…" if len(desc_val) > 28 else "")
                                                        def make_copy(w=widget, v=desc_val, l=lbl):
                                                            def _do():
                                                                w.set_value(v)
                                                                notify(
                                                                    "Copied to " + l,
                                                                    type="positive", position="top",
                                                                )
                                                                render_rows.refresh()
                                                            return _do
                                                        ui.menu_item(
                                                            lbl + " ← " + preview,
                                                            on_click=make_copy(),
                                                        ).classes("text-xs")

                                            for cell in row:
                                                cell_str      = str(cell) if cell is not None else ""
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
                    ui.button(
                        icon="chevron_left",
                        on_click=lambda: (
                            tbl_page.update({"n": tbl_page["n"] - 1}),
                            render_rows.refresh()
                        )
                    ).props("flat round dense size=sm").classes("text-zinc-500") \
                     .bind_enabled_from(tbl_page, "n", lambda p: p > 1)
                    ui.label(f"p.{tbl_page['n']} / {total_pages}").classes("text-xs text-zinc-600")
                    ui.button(
                        icon="chevron_right",
                        on_click=lambda: (
                            tbl_page.update({"n": tbl_page["n"] + 1}),
                            render_rows.refresh()
                        )
                    ).props("flat round dense size=sm").classes("text-zinc-500") \
                     .bind_enabled_from(tbl_page, "n", lambda p: p < total_pages)

            # Reset page + search when switching tabs
            source_toggle.on("update:model-value", lambda _: (
                tbl_page.update({"n": 1, "search": ""}),
                render_rows.refresh()
            ))

            render_rows()

        def advance_step4():
            d["payment_category"]         = pat_cat_in.value.strip()
            d["payment_description"]      = pat_desc_in.value.strip()
            d["checking_payment_pattern"] = pat_chk_in.value.strip()
            _advance(5)

        next_btn.on("click", advance_step4)

    # ── Step 5: confirm + save ─────────────────────────────────────────────────
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
                    d["match_type"] + ': "' + d["match_value"] + '"',
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
                    all_users    = auth.get_all_users()
                    uid_to_label = {u.id: f"{u.display_name} ({u.person_name})" for u in all_users}
                    with ui.column().classes("gap-0.5"):
                        for raw_val, uid in d["member_aliases"].items():
                            ui.label(
                                raw_val + " → " + uid_to_label.get(uid, "user #" + str(uid))
                            ).classes("text-xs font-mono text-zinc-600")

            # Show how many rows will be committed
            staged_count = len(state["staged_df"]) if state.get("staged_df") is not None else 0
            if staged_count:
                ui.label(
                    f"Saving will also commit {staged_count:,} staged rows "
                    f"to transactions_{acct}."
                ).classes("text-xs text-zinc-400")

        def save_bank():
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

            # Push staged CSV data to consolidated tables
            if state.get("staged_df") is not None:
                from services.upload_pipeline import write_to_consolidated
                from services.view_manager import default_view_manager
                person = d.get("person_override") or auth.current_person_name() or ""
                try:
                    inserted = write_to_consolidated(
                        df          = state["staged_df"],
                        rule        = rule,
                        mapping     = m,
                        person      = person,
                        source_file = state["filename"],
                    )
                    print(f"[Wizard] committed {inserted} rows from staged data")
                    default_view_manager().refresh()
                except Exception as ex:
                    print(f"[Wizard] staged push failed: {ex}")

            _drop_staging_table(state["temp_table"])
            notify("Added: " + rule.bank_name, type="positive", position="top")
            dlg.close()
            on_done()

        next_btn.on("click", save_bank)

    # ── Navigation ─────────────────────────────────────────────────────────────
    def _advance(step: int):
        state["step"] = step
        render_step()

    def go_back():
        if state["step"] > 1:
            state["step"] -= 1
            render_step()

    render_step()
    dlg.open()

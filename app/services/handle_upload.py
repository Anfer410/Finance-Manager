"""
Example: wiring RawTableManager into handle_upload
"""

# import io
# import pandas as pd
from nicegui import events
from services.bank_rules import _matcher
from services.raw_table_manager import RawTableManager, parse_csv
from services.helpers import read_secrets
from services.view_manager import ViewManager
from datetime import datetime
from pathlib import Path
from services.helpers import read_secrets
from services.notifications import notify
import re


# ── Config ────────────────────────────────────────────────────────────────────

secrets=read_secrets()

DB_CONN = (
    secrets["DB_USER"],
    secrets["DB_PASSWORD"],
    secrets["DB_HOST"],
    secrets["DB_PORT"],
    secrets["DB_NAME"])
SCHEMA  = secrets["DB_SCHEMA"]


# Dedup columns per bank prefix — adjust to match each bank's CSV structure
DEDUP_COLUMNS: dict[str, list[str]] = {
    "cap1": ["transaction_date", "debit",  "credit", "description"],
    "wf":   ["transaction_date", "amount", "description"],
    "citi": ["transaction_date", "debit",  "credit", "description"],
}
DEFAULT_DEDUP = ["transaction_date", "amount", "description"]


# ── Upload handler ─────────────────────────────────────────────────────────────

async def archive_upload(contents: str, filename: str, person: str) -> None:
    """
    Archive uploaded files to a local folder.
    """
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = Path(f".archive/{filename}_{now}_{person}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(contents)
    notify(f"✓ Uploaded: {filename}", type="positive", position="top")



async def handle_upload(e: events.UploadEventArguments, person_ref: dict) -> None:
    b      = await e.file.read()
    name   = e.file.name
    person = person_ref["value"].lower()

    if person == "none":
        notify("No person selected!", type="negative", position="top")
        return

    result = _matcher.match(name, person)
    if result is None:
        notify(f"No rule matched: {name}", type="warning", position="top")
        return

    bank_name, output_name, person = result   # person may be overridden by the rule
    prefix = output_name.split("_")[0]   # e.g. "cap1"

    # Parse CSV using the bank-specific parser (handles headerless formats like WF)
    try:
        df = parse_csv(b, prefix=prefix)
    except Exception as ex:
        notify(f"Could not parse CSV: {ex}", type="negative", position="top")
        return

    # Write to Postgres
    mgr        = RawTableManager(DB_CONN, schema=SCHEMA)
    views      = ViewManager(DB_CONN, schema=SCHEMA)
    dedup_cols = DEDUP_COLUMNS.get(prefix, DEFAULT_DEDUP)

    try:
        inserted = mgr.upsert(df, bank_name=bank_name, dedup_columns=dedup_cols, person=person)
        views.refresh()
        notify(
            f"✓ {bank_name}: {inserted} new rows added ({len(df) - inserted} duplicates skipped)",
            type="positive",
            position="top",
        )
    except Exception as ex:
        notify(f"DB error: {ex}", type="negative", position="top")
        raise

    
    date = datetime.now().strftime('%Y%m%d')
    # Detect date in filename and use it as the date for the CSV file
    if match := re.search(r"\d{4}\d{2}\d{2}", name):
        date = match.group(0)
    
    secrets = read_secrets()
    
    path = Path(f"{secrets['ARCHIVE_PATH']}/{prefix}_{date}_{person}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b)
    # Write to archive folder
    print(f"[HandleUpload]Wrote {path.name}")
    notify(f"✓ Uploaded: {path.name}", type="info", position="bottom")

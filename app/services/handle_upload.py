"""
services/handle_upload.py

Thin NiceGUI adapter — all logic lives in services/upload_pipeline.py.
"""

from nicegui import events

from services.upload_pipeline import pipeline
from services.notifications import notify


async def handle_upload(
    e: events.UploadEventArguments,
    person_ref: dict,
    bank_rule=None,          # BankRule | None — if set, skips filename matching
) -> None:
    raw    = await e.file.read()
    name   = e.file.name
    person = person_ref["value"]

    if not person:
        notify("No person selected!", type="negative", position="top")
        return

    result = pipeline.run(raw=raw, filename=name, person=person, bank_rule=bank_rule)

    if not result.success:
        notify(
            result.error,
            type="negative" if "DB error" in result.error else "warning",
            position="top",
        )
        return

    notify(
        f"✓ {result.bank_name}: {result.inserted} new rows "
        f"({result.skipped} duplicates skipped)",
        type="positive",
        position="top",
    )
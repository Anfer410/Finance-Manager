---
name: Bank/Account hierarchy refactor
description: Ongoing refactor introducing Bank as a first-class entity above BankRule (account)
type: project
---

Split the flat BankRule list into a two-level hierarchy:

- **Bank** (`data/bank_config.py` → `BankConfig` dataclass) — name, slug, transfer_patterns. Stored in `app_config_banks` DB table via `config_repo.load_banks()` / `save_banks()`.
- **Account** (existing `BankRule`) — unchanged, still stored in `app_config_bank_rules`.

**Why:** Transfer patterns should be per-bank (not global), and the upload page UI needed a grouped hierarchy showing banks → accounts.

**What was done (2026-03-18):**
1. `data/bank_config.py` — new `BankConfig` dataclass + `load_banks()` / `save_banks()`
2. `services/config_repo.py` — added `load_banks()` / `save_banks()` functions
3. `db_migration.py` — `app_config_banks` table added to the auto-created config tables
4. `pages/upload_content.py` — sidebar now groups accounts under banks; new "Bank settings" gear dialog (name + transfer patterns); "Create bank" dialog; "+ Add Account" per bank pre-selects the bank in wizard
5. `components/bank_wizard_component.py` — full step reorder: Step 1=Account details (bank selector + alias), Step 2=Upload CSV + filename detection pre-populated, Step 3=Column mapping, Step 4=Member aliases (conditional), Step 5=Payment patterns (conditional credit only), Step 6=Review & save. `open_add_bank_wizard()` accepts optional `preselected_bank_slug`.

**How to apply:** When touching upload/bank flow, be aware of the Bank→Account two-level model. `_ensure_banks_for_rules()` auto-creates BankConfig entries from existing BankRule.bank_name values.

# Changelog

## 2026-03-13

app/services/loan_service.py

Added monthly_insurance: float = 0.0 to LoanRecord
compute_amortization: uses monthly_payment - monthly_insurance as the P&I payment — insurance is excluded from principal/interest math
compute_stats: same adjustment for the interest_paid back-calculation
load_loans / save_loan / _row_to_loan: all include the new column
app/db_migration.py

ALTER TABLE ... ADD COLUMN IF NOT EXISTS monthly_insurance for existing databases
Column added to the CREATE TABLE for fresh installs
app/pages/loans_content.py

Dialog: added "Homeowner insurance $/mo" input field alongside the monthly payment field
Summary panel: when insurance > 0, shows a P&I / Insurance breakdown under the monthly payment row

## 2026-03-13

### Features

#### Loans module
- Added `app_loans` table (DDL in `db_migration.py`) supporting fixed/ARM loans with full amortisation metadata: `loan_type`, `rate_type`, `interest_rate`, `original_principal`, `term_months`, `monthly_payment`, `current_balance`, `balance_as_of`, `arm_*` cap fields, `payment_description_pattern`, and `payment_account_key`.
- Added `/loans` and `/loan-planning` routes (`pages/loans_content.py`, `pages/loan_planning_content.py`) and registered them in `main.py`.
- Added **Loans** and **Loan Planning** nav entries to the admin sidebar in `header.py`.

#### Consolidated transaction tables
- Introduced `transactions_debit` and `transactions_credit` as year-partitioned PostgreSQL tables (`PARTITION BY RANGE (transaction_date)`). Partitions are pre-created ±5/+2 years from today at startup, and dynamically created by `ensure_partition_for_year()` when an uploaded file spans a new year.
- Each table carries `(account_key, transaction_date, description, amount|debit+credit, person, source_file, inserted_at)` with unique dedup indexes per partition.
- `ViewManager` now reads exclusively from these consolidated tables instead of introspecting raw `raw_*` tables.

#### `services/upload_pipeline.py` (new)
- Pure, NiceGUI-free upload pipeline with three public stages: `sniff()`, `suggest_mapping()`, and `UploadPipeline.run()`.
- `sniff()` detects CSV delimiter, header presence, and returns column samples.
- `suggest_mapping()` auto-maps CSV columns to standard roles using keyword matching.
- `write_to_consolidated()` normalises and bulk-inserts rows into `transactions_debit`/`transactions_credit` using `INSERT … ON CONFLICT DO NOTHING`.
- `_resolve_person()` resolves `member_name` column values to `person_name` via `BankRule.member_aliases`.

#### `data.db.py` (new)
- Single source of truth for DB connectivity: `get_engine()` (LRU-cached), `get_schema()`, `get_conn_tuple()`, `get_url()`, `get_psycopg_dsn()`.
- `ArchiveConfig` dataclass and `get_archive_cfg()` with priority: DB (`app_settings` table) → env vars → defaults.

#### `services/config_repo.py` (new)
- Unified config persistence layer replacing `data.db_config.py`. Provides `load_bank_rules` / `save_bank_rules`, `load_categories` / `save_categories`, `load_transaction_cfg` / `save_transaction_cfg`, `load_app_settings` / `save_app_settings` / `patch_app_settings`.
- Backward-compat shims retained for transitional callers.

#### Add-bank wizard (upload page)
- Replaced the flat rule editor dialog with a 5-step guided wizard covering: CSV upload + sniff, column role mapping, bank details & member aliases, payment pattern refinement with live transaction browser, and a confirmation/save step.
- Upload page now renders a **bank sidebar** (with an "Auto-detect" option) instead of a hidden settings button.
- Zero-state empty page shown with CTA when no banks are configured.

#### Finance data export/import in Settings (admin)
- `_finance_data_export_section()`: CSV download for `transactions_debit`, `transactions_credit`, and `app_loans`.
- `_finance_data_import_section()`: auto-detects CSV type from headers and imports rows with `ON CONFLICT DO NOTHING` dedup.
- `_raw_export_section()`: per-bank CSV downloads of all `raw_*` tables.

#### Year-over-year dashboard data
- `get_year_over_year_monthly_spend_series()`: monthly spend + income from N years back to current month, with rolling surplus.

---

### Improvements

#### DB migration refactor
- Split into **startup migration** (`run_migrations()`, called via `app.on_startup`) and **CLI admin creation** (`create_admin()`).
- `run_migrations()` is fully idempotent — creates missing tables/partitions/indexes only; never mutates existing data.
- `--full-setup` CLI flag added; `--admin-username` is now optional.

#### `BankRule` dataclass additions
- New fields: `member_aliases`, `column_map`, `dedup_columns`.

#### `RawTableManager`
- `_normalize_columns()` handles empty/whitespace column names and duplicate name collisions.
- `parse_csv()` accepts optional `column_map`; falls back to registered bank parsers or generic read.
- New `default_manager()`, `list_banks()`, and `export_csv()` methods.

#### `ViewManager` rewrite
- Views built from consolidated tables using fixed column names — no per-table column introspection.
- `_person_case_for_rule()` resolves `member_aliases` via subquery to `app_users.person_name` (stable across display-name renames).

#### DB connectivity consolidation
- `auth.py`, `finance_dashboard_data.py`, `handle_upload.py`, `bank_rules.py`, `category_rules.py`, `transaction_config.py` all migrated to `data.db.get_engine()` / `get_schema()`.

#### `handle_upload.py` simplification
- Reduced to a thin NiceGUI adapter; all logic delegated to `upload_pipeline.pipeline.run()`.

#### Config export/import versioning
- Export format bumped to `_version: 2`; key renamed `bank_rules` → `banks`. Import accepts both v1 and v2.

#### Settings: password minimum length raised from 4 to 6 characters.

---

### Bug Fixes

- `app_settings` table creation was missing from `_create_app_tables()` — now always created at startup.
- `RuleMatcher._matches()`: removed `fnmatch`-based fallback for `contains` type that caused false matches on `.csv`-suffixed filenames.
- `view_manager.py`: removed debug `print` calls leaking raw table lists.
- `handle_upload.py`: person value now properly lowercased; empty/`"none"` guard extended.

---

### Removals

- `pages/design_system_content.py` and `pages/icons_content.py` routes removed from `main.py`.
- `_aliases_section()` call commented out in Settings.
- `data.db_config.py` superseded by `services/config_repo.py` (shims retained for transition).
- Hardcoded `DEDUP_COLUMNS` dicts and `archive_upload()` removed from `handle_upload.py`.

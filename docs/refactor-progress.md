# Family Hierarchy Refactor — Progress

> Companion to: `docs/family-hierarchy-design.md`
> Last updated: 2026-03-18

---

## Status Summary

| Phase | Description | Status |
|-------|-------------|--------|
| Infra | Test infrastructure (docker-compose + conftest) | ✅ Done |
| 1 | DB schema — new tables + column additions | ✅ Done |
| 2 | Data migration | ⏭ Skipped (alpha — drop & recreate) |
| 3 | Auth layer | ✅ Done |
| 4 | Config scoping | ✅ Done |
| 5 | Transaction scoping | ✅ Done |
| 6 | UI pages (family mgmt, user mgmt) | ✅ Done |
| 7 | Email / invite flows | ⬜ Deferred |

---

## Test Infrastructure

**Files:**
- `tests/docker-compose.yml` — throwaway Postgres 18 on port 5434 (tmpfs, in-memory)
- `tests/conftest.py` — session-scoped container lifecycle, per-test rollback isolation
- `tests/test_infra.py` — 5 smoke tests (connection, tables, partitions, isolation)

**Run:** `cd app && .venv/bin/pytest ../tests/ -v`

**Key decisions:**
- Uses `docker compose` (not testcontainers) — avoids Rancher Desktop Ryuk issues
- `data.db` and `db_migration` loaded fresh from disk in fixture to bypass unit-test mocks
- Per-test rollback via `conn.begin()` / `trans.rollback()` — no re-running migrations between tests

---

## Phase 1 — DB Schema

**Approach:** Alpha app — drop existing tables and recreate with new schema.
No data migration. `run_migrations()` creates the complete new schema from scratch.

### New tables added

| Table | Purpose |
|-------|---------|
| `families` | One row per family/household |
| `family_memberships` | User↔family membership with role + join/leave timestamps |
| `user_bank_permissions` | Per-user upload permissions within a family |
| `password_reset_tokens` | Time-limited tokens for self-service password reset |
| `invitations` | Family Head invite flow (email-based) |
| `dashboard_templates` | Instance-level reusable dashboard layouts (no data) |
| `dashboard_template_widgets` | Widget rows for a template |

### Columns added to existing tables

**`app_users`:**
- `email TEXT UNIQUE` — required for invite/reset flows; nullable for now
- `must_change_password BOOLEAN NOT NULL DEFAULT FALSE` — force reset on next login
- `is_instance_admin BOOLEAN NOT NULL DEFAULT FALSE` — replaces `role = 'admin'`

**`transactions_debit` + `transactions_credit`:**
- `family_id INTEGER REFERENCES families(id)` — immutable family context at upload time
- `uploaded_by INTEGER REFERENCES app_users(id)` — who pressed upload

**`app_config_*` (bank_rules, banks, categories, transaction):**
- `family_id INTEGER NOT NULL REFERENCES families(id)` — replaces singleton `id=1`
- `PRIMARY KEY (family_id)` — one config row per family

### Default family seeding
On first startup (empty DB), a default family `(id=1, name='Default Family')` is created
and config tables are seeded for `family_id=1`.

---

## Phase 3 — Auth Layer (planned)

**Changes to `services/auth.py`:**
- `AuthUser` dataclass: add `is_instance_admin`, `family_id`, `family_role`
- Session keys: add `auth_family_id`, `auth_family_role`, `auth_is_instance_admin`
- New helpers: `is_instance_admin()`, `is_family_head()`, `current_family_id()`
- Replace `is_admin()` → keep as alias for `is_instance_admin()` during transition
- Update `require_admin` → `require_instance_admin`
- Update all `get_user_by_*` to JOIN `family_memberships`
- Update `create_user` → also inserts `family_memberships` row

---

## Phase 4 — Config Scoping ✅

**Data layer:**
- `services/config_repo.py` — all family-scoped functions require `family_id: int`
- `data/bank_config.py` — `load_banks(family_id)`, `save_banks(banks, family_id)`
- `data/bank_rules.py` — `load_rules(family_id)`, `save_rules(rules, family_id)`, `RuleMatcher(rules)` now takes explicit rules (removed module-level singleton `_matcher`)
- `data/category_rules.py` — `load_category_config(family_id)`, `save_category_config(cfg, family_id)`
- `services/transaction_config.py` — `load_config(family_id)`, `save_config(cfg, family_id)`

**Service layer:**
- `services/view_manager.py` — `ViewManager.refresh(family_id)` — passes `family_id` to all config loads
- `services/upload_pipeline.py` — `UploadPipeline.run(..., family_id)` — instantiates `RuleMatcher(load_rules(family_id))` inline; passes `family_id` to view refresh
- `services/handle_upload.py` — gets `auth.current_family_id()`, passes to `pipeline.run()`

**Pages / components** — all calls to config load/save now pass `auth.current_family_id()`:
- `pages/categories_content.py`
- `pages/settings_content.py`
- `pages/upload_content.py`
- `components/bank_wizard_component.py`
- `data/finance_dashboard_data.py`
- `components/finance_charts.py`

**Tests:**
- `tests/test_config_repo.py` — 17 integration tests covering load/save roundtrip + family isolation for all config types

---

## Phase 5 — Transaction Scoping ✅

**Upload stamping:**
- `write_to_consolidated()` adds `family_id: int = 1` and `uploaded_by: int = 0` params; stamps both on every row dict; includes in INSERT statements
- `UploadPipeline.run()` adds `uploaded_by: int = 0`; passes both to `write_to_consolidated()`
- `handle_upload.py` passes `uploaded_by=auth.current_user_id() or 0`

**Views — family_id passthrough (not per-family views):**
- All four view builders (`_build_credit_payments_view`, `_build_credit_spend_view`, `_build_debit_spend_view`, `_build_income_view`) include `family_id` as a SELECT column
- `v_all_spend` column list includes `family_id`
- Cross-view dedup subquery gains `AND t.family_id = cp.family_id` to prevent cross-family payment matching

**Dashboard queries (`finance_dashboard_data.py`):**
- `_family_filter()` helper mirrors `_persons_filter()` — returns `("AND family_id = :_fid", {"_fid": auth.current_family_id()})`
- Applied to all 11 query functions: `get_years`, `_spend_income_kpi`, `get_monthly_spend_series`, `get_year_over_year_monthly_spend_series`, `get_spend_per_bank_series`, `get_employer_income_series`, `get_spend_by_category`, `get_category_trend`, `get_fixed_vs_variable`, `get_persons`, `get_persons_with_ids`, `get_spend_by_person_monthly` (both date-range and full-year branches), `get_filter_options`, `get_weekly_transactions`, `gettransactions_table`

**Tests:**
- `tests/test_transaction_scoping.py` — integration tests covering:
  - `family_id` and `uploaded_by` stamped on `transactions_debit` / `transactions_credit` rows
  - Dashboard query isolation: `get_years`, `gettransactions_table`, `get_weekly_transactions` each verified to scope results to the current family

---

## Phase 6 — UI Pages ✅

**New files:**
- `services/family_service.py` — DB operations: `get_family`, `get_family_members`, `get_all_families`, `create_family`, `rename_family`, `update_member_role`, `remove_member`, `add_user_to_family`, `get_users_without_family`
- `pages/family_content.py` — `/family` route, accessible to Family Head + Instance Admin
- `pages/users_content.py` — `/users` route, Instance Admin only

**Family Management (`/family`):**
- Shows current family name (Instance Admin can rename)
- Lists all active members with role badges, joined date, edit button
- Edit dialog: change family role (member ↔ head), remove member (with confirmation + cleanup of `user_bank_permissions`)
- Instance Admin: can add any unassigned user to the family
- Instance Admin: All Families section — lists all families with member counts, create new family (auto-seeds config from Default Family)

**User Management (`/users`):**
- Lists all users with family, role, active status
- Create user dialog: username, display name, person name, password, family assignment, role
- Edit user dialog: display name, person name, active toggle, instance admin toggle, set temp password (`must_change_password=true`), move to different family / change role

**Routing + Nav:**
- `main.py`: added `/family` (requires family head or admin) and `/users` (requires admin) sub-page handlers
- `header.py`: added "Family" nav item (Family Head + Admin), "Users" nav item (Admin only)

---

## Notes

- `app_users.role` column kept for backward compat during transition; removed in Phase 3 cleanup
- `app_config_*` old `id=1` rows (if any) are left in place; new family-scoped rows use `family_id`
- `create_admin` CLI creates default family + adds admin as family head + sets `is_instance_admin=TRUE`

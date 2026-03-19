---
name: Bug — app_loans missing family_id
description: app_loans table has no family_id column, so loans are global not per-family — breaks multi-tenancy
type: project
---

`app_loans` has no `family_id` column. `load_loans()` in `loan_service.py` queries all active loans with no family scoping, meaning all families see all loans.

**Why:** Loans were added before or alongside the multi-tenancy refactor and the column was never added.

**How to apply:** Fix requires a migration to add `family_id INTEGER REFERENCES finance.families(id)` to `app_loans`, update `load_loans()` / `save_loan()` / `delete_loan()` to accept and filter by `family_id`, update `loans_content.py` and `loan_planning_content.py` to pass the current family_id, and seed existing loans to family 1 (default family) in the migration.

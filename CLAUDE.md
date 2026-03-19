# Finance Manager — Claude Code Guide

## Project Overview

A personal finance management web app built with **NiceGUI** (Python) and **PostgreSQL**. Runs via Docker Compose. Supports transaction upload/categorization, loan tracking, loan planning, and a finance dashboard.

- **App**: NiceGUI + FastAPI, port 8080
- **DB**: PostgreSQL 18, port 5432
- **Auth**: session-based, bcrypt passwords, `app.storage.user` for session keys
- **Multi-tenancy**: family-based — all transaction queries scoped by `family_id`

## Running the App

```bash
# Start all services
docker compose up

# Start in background
docker compose up -d

# Rebuild after dependency changes
docker compose up --build

# Local dev (no Docker, requires local postgres)
cd app && python main.py
```

## Database

```bash
# Connect to the database
docker exec -it finance-manager-db-1 psql -U postgres -d finance-manager

# Run a query
docker exec finance-manager-db-1 psql -U postgres -d finance-manager -c "SELECT * FROM finance.app_users;"
```

Migrations run automatically on startup in prod (`APP_ENV=prod`). In dev, migrations do NOT auto-run — trigger manually via `run_migrations()` in [app/db_migration.py](app/db_migration.py).

**Alpha DB policy**: clean DB assumed — no backward-compat migration shims needed.

## Project Structure

```
app/
  main.py                 # Entry point: routing, layout decorator, ui.run()
  config.json             # App name, version, port
  db_migration.py         # Migration runner
  header.py               # Shared sidebar/header frame
  footer.py
  pages/                  # One file per page, each exports content()
    finance_dashboard_content.py
    upload_content.py
    categories_content.py       # Admin-only
    loans_content.py
    loan_planning_content.py
    settings_content.py         # Tabbed: Personal / Uploads / Data / Users / Family
    login_content.py
  services/               # Business logic, helpers
    auth.py               # AuthUser dataclass, session helpers, route guards
    config_repo.py        # Per-family config stored in DB (JSONB)
    loan_service.py
    upload_pipeline.py    # CSV ingestion: parse → consolidated tables → raw archive → views
    raw_table_manager.py  # Archive tables raw_<account_key>; one per bank account
    view_manager.py       # Builds Postgres views from consolidated transaction tables
    transaction_config.py # TransactionConfig + EmployerPattern dataclasses
    dashboard_config.py   # Dashboard + widget CRUD; default layout seeding
    dashboard_grid_layout.py  # Grid compaction, move/resize helpers
    family_service.py     # Family + membership CRUD
    upload_manager.py     # Upload batch management: list / reassign person / delete
    helpers.py            # env(), misc utils
    notifications.py
  components/             # Reusable UI components
    widgets/              # Dashboard widget system
      registry.py         # All widget definitions + REGISTRY / REGISTRY_BY_ID
      base.py             # Widget, RenderContext, ConfigField base classes
      kpi.py              # KPIWidget base
      echart.py           # Chart widget bases (Bar, Line, Mixed, Donut, etc.)
      table_widget.py
      settings_ui.py      # Widget settings dialog
      custom_chart_widget.py
    dashboard_registry.py # Compat shim — re-exports from widgets/
    dashboard_txn_table.py
    finance_charts.py
    bank_wizard_component.py
  styles/                 # Shared style helpers
  assets/                 # Static files (CSS, images, icons)
  data/                   # DB connection, Category/bank matching rules
    bank_rules.py         # BankRule dataclass, load_rules(), save_rules()
    category_rules.py
    db.py
    finance_dashboard_data.py   # All dashboard queries; scoped by family_id
```

## Key Architectural Concepts

### Multi-tenancy
- Every transaction row carries `family_id` (FK to `families` table)
- All dashboard queries use `_family_filter()` → `("AND family_id = :_fid", {"_fid": ...})`
- Users belong to one active family via `family_memberships` (partial unique index on `user_id WHERE left_at IS NULL`)

### Roles
- `is_instance_admin` — full access, counts as family head everywhere
- `family_role = 'head'` — manages family settings, members, uploads
- `family_role = 'member'` — sees own data only
- `is_family_head()` returns `True` for both heads and instance admins

### Transaction Storage
Two consolidated tables partitioned by year:
- `transactions_debit` — checking/savings: `(id, account_key, transaction_date, description, amount, person INTEGER[], source_file, family_id, uploaded_by, inserted_at)`
- `transactions_credit` — credit cards: same but `debit` + `credit` instead of `amount`

Raw archive: `raw_<account_key>` — one table per bank account, original CSV columns, `person` stored as JSON string. Dedup constraint named `uq_raw_<account_key>_dedup`.

Views rebuilt by `ViewManager.refresh(family_id)`: `v_all_spend`, `v_credit_spend`, `v_debit_spend`, `v_income`, `v_transactions`.

### Dashboard System
- `app_dashboards` + `app_dashboard_widgets` tables
- Default layout seeded from `_DEFAULT_LAYOUT` in `dashboard_config.py` (snapshot of reference dashboard)
- Widget `default_row_span`: KPI widgets = 1, all other widgets = 2
- `REGISTRY` in `components/widgets/registry.py` — all widget singleton instances

### Settings Page Tabs
| Tab | Who sees it | Content |
|-----|-------------|---------|
| Personal | All | Profile, employer patterns |
| Uploads | Head+ | Upload batch manager (reassign person, delete) |
| Data | Head+ | Export/import, config backup/restore, refresh views |
| Users | Head+ | Family members; admin also sees all-user management |
| Family | Admin only | All families overview, create/rename |

### EmployerPattern ownership
`EmployerPattern(pattern, added_by)` — `added_by=None` means head-owned (protected); `added_by=<user_id>` means member-owned (only that member or a head can remove it).

## Page Pattern

Each page file lives in `app/pages/` and exports a single `content()` function called from `main.py`. Pages should not import from each other.

### Stale session guard
`with_base_layout` in `main.py` checks `auth.get_user_by_id(auth.current_user_id())` on every render — redirects to `/login` if the user no longer exists in the DB.

## Environment / Secrets

Secrets are loaded via `services/helpers.py:env(key, default)`. Set `APP_ENV=prod` for production mode (enables auto-migrations, disables reload).

## Syntax Check

```bash
python -m py_compile app/pages/<file>.py
python -m py_compile app/services/<file>.py
```

## Testing

Integration tests live in `tests/`. They spin up a throwaway Postgres container on port **5434** (no collision with dev on 5432), run full migrations, then roll back each test in a transaction.

```bash
# Run all tests (must be run from app/ so the venv and app imports resolve)
cd app && .venv/bin/pytest ../tests/ -v

# Run a single file
cd app && .venv/bin/pytest ../tests/test_transaction_scoping.py -v
```

**Infrastructure** (`tests/conftest.py`):
- `pg_engine` (session-scoped) — starts `tests/docker-compose.yml`, runs migrations, yields engine; tears down on session end
- `db_conn` (function-scoped) — connection inside an open transaction, auto-rolled-back after each test
- `schema` fixture returns `"finance"`

**Test files**:
| File | What it covers |
|------|----------------|
| `test_infra.py` | DB connection + schema sanity |
| `test_migrations.py` | All migration functions run cleanly |
| `test_auth.py` | User creation, login, session helpers |
| `test_config_repo.py` | Per-family config read/write |
| `test_transaction_scoping.py` | family_id stamping, view passthrough, dashboard isolation |
| `test_upload_pipeline.py` | CSV ingestion into consolidated + raw tables |
| `test_finance_dashboard_data.py` | Dashboard query functions (mocked `_q`) |
| `test_finance_dashboard_data_db.py` | Dashboard query functions against real DB (get_years, KPI, monthly series) |
| `test_custom_chart_query.py` | Unit tests for custom chart query builder (no DB — mocked) |
| `test_custom_chart_query_db.py` | Custom chart query execution against real DB + views |
| `test_custom_chart_renderer.py` | Unit tests for custom chart renderer (no DB — NiceGUI mocked) |
| `test_family_service.py` | Family + member CRUD, role management, users-without-family |
| `test_loan_service.py` | Loan amortization unit tests + DB CRUD |
| `test_dashboard_config.py` | Dashboard + widget CRUD, auto-positioning, find_free_position |
| `test_dashboard_grid_layout.py` | Grid layout: compact, cascade, move, resize (DB mocked) |
| `test_upload_manager.py` | Upload batch list/reassign/delete + _sanitize/_raw_join_clause |
| `test_upload_pipeline_run.py` | UploadPipeline.run() against real DB |

## Key Dependencies

- `nicegui` — UI framework (replaces HTML/JS)
- `nicegui-echart` — ECharts integration for dashboard charts
- `psycopg` / `SQLAlchemy` — PostgreSQL
- `pandas` — data processing in upload pipeline
- `bcrypt` — password hashing

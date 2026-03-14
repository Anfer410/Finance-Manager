# Finance Manager — Claude Code Guide

## Project Overview

A personal finance management web app built with **NiceGUI** (Python) and **PostgreSQL**. Runs via Docker Compose. Supports transaction upload/categorization, loan tracking, loan planning, and a finance dashboard.

- **App**: NiceGUI + FastAPI, port 8080
- **DB**: PostgreSQL 18, port 5432
- **Auth**: session-based with admin role support

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
docker exec finance-manager-db-1 psql -U postgres -d finance-manager -c "SELECT * FROM users;"
```

Migrations run automatically on startup in prod (`APP_ENV=prod`). In dev, migrations do NOT auto-run — trigger manually via `run_migrations()` in [app/db_migration.py](app/db_migration.py).

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
    categories_content.py   # Admin-only
    loans_content.py
    loan_planning_content.py
    settings_content.py
    login_content.py
  services/               # Business logic, helpers
    auth.py               # is_authenticated(), is_admin()
    config_repo.py        # User config/settings
    loan_service.py
    upload_pipeline.py    # CSV ingestion pipeline
    raw_table_manager.py  # Hisorically all data was saved to separate raw_ tables, now it is used as archive
    view_manager.py       # Builds Postgres views from the two consolidated transaction tables
    helpers.py            # read_secrets(), misc utils
    transaction_config.py
  components/             # Reusable UI components
  styles/                 # Shared style helpers
  assets/                 # Static files (CSS, images, icons)
  data/                   # DB connection, Category/bank matching rules
    bank_rules.py
    category_rules.py
    db.py
    finance_dashboard_data.py
```

## Page Pattern

Each page file lives in `app/pages/` and exports a single `content()` function called from `main.py`. Pages should not import from each other.

## Environment / Secrets

Secrets are loaded via `services/helpers.py:env(key, default)`. Set `APP_ENV=prod` for production mode (enables auto-migrations, disables reload).

## Syntax Check

```bash
python -m py_compile app/pages/<file>.py
```

## Key Dependencies

- `nicegui` — UI framework (replaces HTML/JS)
- `nicegui-highcharts` — charts
- `psycopg` / `SQLAlchemy` — PostgreSQL
- `pandas` — data processing
- `bcrypt` — password hashing

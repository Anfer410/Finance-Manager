# Finance Manager

A personal finance management web app built with **NiceGUI** (Python) and **PostgreSQL**. Supports transaction upload and categorization, loan tracking, loan planning, and a customizable finance dashboard.

## Features

- **Finance Dashboard** — configurable grid of KPI cards, bar/line/donut charts, tables, and custom SQL charts
- **Transaction Upload** — CSV ingestion with bank rule matching and automatic categorization
- **Loan Tracking** — amortization schedules and loan CRUD
- **Loan Planning** — what-if planning for future loans
- **Multi-family tenancy** — each family sees only their own data
- **Role-based access** — instance admin, family head, and family member roles

## Requirements

- Docker and Docker Compose

## Quick Start

1. Create a `.env` file in the project root:

```env
POSTGRES_PASSWORD=your_db_password
STORAGE_SECRET=your_session_secret
# Optional overrides (defaults shown):
# POSTGRES_DB=finance-manager
# POSTGRES_USER=postgres
# TZ=UTC
```

2. Start the app:

```bash
docker compose up -d
```

3. Open [http://localhost:8080](http://localhost:8080) in your browser.

To rebuild after dependency changes:

```bash
docker compose up --build
```

## Architecture

| Component | Technology | Port |
|-----------|-----------|------|
| App | NiceGUI + FastAPI (Python) | 8080 |
| Database | PostgreSQL 18 | 5432 |

### Multi-tenancy

All data is scoped to a **family**. Users belong to one active family, and all transaction queries are filtered by `family_id`.

### Roles

| Role | Description |
|------|-------------|
| Instance admin | Full access across all families |
| Family head | Manages family members, uploads, and settings |
| Family member | Views own data only |

### Transaction Storage

Transactions are stored in two consolidated tables (`transactions_debit`, `transactions_credit`) and raw per-account archive tables (`raw_<account_key>`). Postgres views (`v_all_spend`, `v_credit_spend`, `v_debit_spend`, `v_income`, `v_transactions`) are rebuilt after each upload.

### Dashboard

Dashboards are per-family and fully configurable. Widgets are arranged on a drag-and-drop grid. A default layout is seeded for new families.

## Local Development (no Docker)

Requires a local PostgreSQL instance.

```bash
cd app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Migrations do **not** run automatically in dev mode — call `run_migrations()` from `app/db_migration.py` manually if needed.

### Database access

```bash
# Connect to the running container's DB
docker exec -it finance-manager-db-1 psql -U postgres -d finance-manager
```

## Testing

Integration tests spin up a throwaway Postgres container on port **5434** and roll back each test in a transaction.

```bash
cd app && .venv/bin/pytest ../tests/ -v
```

## Project Structure

```
app/
  main.py                 # Entry point: routing, layout, ui.run()
  pages/                  # One file per page, each exports content()
  services/               # Business logic (auth, uploads, loans, dashboard, etc.)
  components/             # Reusable UI components and dashboard widgets
  data/                   # DB connection, bank/category matching rules
  assets/                 # Static files (CSS, images, icons)
```

## Key Dependencies

- [NiceGUI](https://nicegui.io) — Python UI framework
- [FryCodeLab](https://github.com/frycodelab/nicegui-component-based/tree/main) - Bootstrap Template
- [nicegui-echart](https://github.com/nicegui-community/nicegui-echart) — ECharts integration
- [SQLAlchemy](https://www.sqlalchemy.org) + [psycopg](https://www.psycopg.org) — PostgreSQL
- [pandas](https://pandas.pydata.org) — CSV processing
- [bcrypt](https://pypi.org/project/bcrypt/) — password hashing

## License

This project is licensed under the [MIT License](LICENSE).

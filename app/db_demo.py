"""
db_demo.py

Provisions (or removes) demo data for Finance Manager.

Usage
─────
    # Create demo data (idempotent — safe to run twice)
    python db_demo.py

    # Destroy all demo data
    python db_demo.py --destroy

    # Same thing
    python db_demo.py --clean

Sentinel
────────
A key "demo_installed" is stored in app_settings with metadata about what
was created. Destroy reads this sentinel to know what to delete.

Demo layout
───────────
Family 1 — "Demo Family 1" (healthy finances, DTI ~27 %)
    demo_admin   — family head, instance admin
    demo_user_1  — family member (spouse)
    demo_user_2  — family member (kid, no income)

    Accounts:
        demo1_checking  (checking)  — shared account_key demo1_checking
        demo1_savings   (checking)  — shared account_key demo1_savings
        demo1_credit    (credit)    — shared account_key demo1_credit

    Income:  ~$10 000/mo split between admin ($6 500) and user_1 ($3 500)
    Loans:   Home $400k @5.5 % (360 mo) + Car $20k @6.5 % (60 mo)
    DTI:     ~27 %

Family 2 — "Demo Family 2" (stressed finances, DTI ~42 %)
    demo_user_3  — family head
    demo_user_4  — family member (spouse)
    demo_user_5  — family member (kid)

    Accounts:
        demo2_checking  (checking)  — account_key demo2_checking
        demo2_credit1   (credit)    — account_key demo2_credit1
        demo2_credit2   (credit)    — account_key demo2_credit2
        demo2_credit3   (credit)    — account_key demo2_credit3

    Income:  ~$9 000/mo split between user_3 ($5 500) and user_4 ($3 500)
    Loans:   Home $380k @7.2 % (360 mo) + Car1 $35k @8.9 % (60 mo) +
             Car2 $28k @9.5 % (60 mo)
    DTI:     ~42 %

Transaction date range: 2023-01-01 — 2025-12-31 (3 full years)

Sample CSVs (written to demo/sample_csvs/):
    demo1_checking_2026q1.csv   — Family 1 checking Jan–Feb 2026
    demo1_savings_2026q1.csv    — Family 1 savings Jan–Feb 2026
    demo1_credit_2026q1.csv     — Family 1 credit Jan–Feb 2026
    demo2_checking_2026q1.csv   — Family 2 checking Jan–Feb 2026
    demo2_credit1_2026q1.csv    — Family 2 credit1 Jan–Feb 2026
    demo2_credit2_2026q1.csv    — Family 2 credit2 Jan–Feb 2026
    demo2_credit3_2026q1.csv    — Family 2 credit3 Jan–Feb 2026
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

from data.db import get_engine, get_schema
from services.auth import hash_password

# ── Constants ─────────────────────────────────────────────────────────────────

SENTINEL_KEY = "demo_installed"
DEMO_PASSWORD = "demo"

# Date range for generated history
START_DATE = date(2023, 1, 1)
END_DATE   = date(2025, 12, 31)

# Sample CSV date range
CSV_START = date(2026, 1, 1)
CSV_END   = date(2026, 2, 28)

# reproducible random seed
RNG = random.Random(42)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _engine():
    return get_engine()

def _schema() -> str:
    return get_schema()


def _months(start: date, end: date):
    """Yield the first day of each month in [start, end]."""
    m = start.replace(day=1)
    while m <= end:
        yield m
        # advance one month
        if m.month == 12:
            m = date(m.year + 1, 1, 1)
        else:
            m = date(m.year, m.month + 1, 1)


def _last_day(d: date) -> int:
    import calendar
    return calendar.monthrange(d.year, d.month)[1]


def _rand_date(month_start: date) -> date:
    return month_start + timedelta(days=RNG.randint(0, _last_day(month_start) - 1))


def _jitter(base: float, pct: float = 0.12) -> float:
    """±pct random jitter around base."""
    return round(base * (1 + RNG.uniform(-pct, pct)), 2)


def _upsert_setting(conn, schema: str, key: str, value) -> None:
    conn.execute(text(f"""
        INSERT INTO {schema}.app_settings (key, value, updated_at)
        VALUES (:k, CAST(:v AS jsonb), NOW())
        ON CONFLICT (key) DO UPDATE
            SET value = CAST(:v AS jsonb), updated_at = NOW()
    """), {"k": key, "v": json.dumps(value)})


# ── Main entry points ─────────────────────────────────────────────────────────

def create_demo(force: bool = False) -> None:
    engine = _engine()
    schema = _schema()

    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT value FROM {schema}.app_settings WHERE key = :k"),
            {"k": SENTINEL_KEY},
        ).fetchone()
        if row and not force:
            print("[demo] Demo data already installed. Use --force to reinstall or --destroy to remove.")
            return

    # Force re-provision: destroy existing demo data first
    if force:
        destroy_demo(force=True)

    print("[demo] Provisioning demo data …")

    with engine.begin() as conn:
        # 1. Users
        user_ids = _create_users(conn, schema)
        uid_admin  = user_ids["demo_admin"]
        uid_user1  = user_ids["demo_user_1"]
        uid_user2  = user_ids["demo_user_2"]
        uid_user3  = user_ids["demo_user_3"]
        uid_user4  = user_ids["demo_user_4"]
        uid_user5  = user_ids["demo_user_5"]

        # 2. Families
        fid1 = _create_family(conn, schema, "Demo Family 1", uid_admin)
        fid2 = _create_family(conn, schema, "Demo Family 2", uid_user3)

        # 3. Memberships
        _add_member(conn, schema, fid1, uid_admin,  "head")
        _add_member(conn, schema, fid1, uid_user1,  "member")
        _add_member(conn, schema, fid1, uid_user2,  "member")
        _add_member(conn, schema, fid2, uid_user3,  "head")
        _add_member(conn, schema, fid2, uid_user4,  "member")
        _add_member(conn, schema, fid2, uid_user5,  "member")

        # 4. Bank rules + config (copy from family 1 defaults, then customise)
        _setup_family_config(conn, schema, fid1, _fam1_bank_rules(uid_admin, uid_user1, uid_user2))
        _setup_family_config(conn, schema, fid2, _fam2_bank_rules(uid_user3, uid_user4, uid_user5))

        # 5. Archive toggle (enabled for both demo families)
        for fid in (fid1, fid2):
            conn.execute(text(f"""
                INSERT INTO {schema}.app_config_archive (family_id, archive_enabled, updated_at)
                VALUES (:fid, TRUE, NOW())
                ON CONFLICT (family_id) DO UPDATE SET archive_enabled = TRUE, updated_at = NOW()
            """), {"fid": fid})

        # 6. Transactions
        print("[demo]   inserting transactions for Family 1 …")
        _insert_fam1_transactions(conn, schema, fid1, uid_admin, uid_user1, uid_user2)
        print("[demo]   inserting transactions for Family 2 …")
        _insert_fam2_transactions(conn, schema, fid2, uid_user3, uid_user4, uid_user5)

        # 7. Loans
        _insert_loans(conn, schema, fid1, fid2)

        # 8. Dashboard seeding (handled by get_or_create_default on first login)

        # 9. Sentinel
        _upsert_setting(conn, schema, SENTINEL_KEY, {
            "installed_at": date.today().isoformat(),
            "family_ids": [fid1, fid2],
            "user_ids": list(user_ids.values()),
            "usernames": list(user_ids.keys()),
        })

        print(f"[demo]   Family 1 id={fid1}  Family 2 id={fid2}")
        for uname, uid in user_ids.items():
            print(f"[demo]   user {uname!r:20s} id={uid}")

    # 10. Rebuild views — fam2 first, then fam1 so admin sees correct data on login
    print("[demo]   rebuilding views …")
    from services.view_manager import default_view_manager
    vm = default_view_manager()
    vm.refresh(fid2)
    vm.refresh(fid1)

    # 11. Write sample CSVs
    print("[demo]   writing sample CSVs …")
    _write_sample_csvs(user_ids)

    print("[demo] Done. Log in with demo_admin / demo")


def destroy_demo(force: bool = False) -> None:
    engine = _engine()
    schema = _schema()

    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT value FROM {schema}.app_settings WHERE key = :k"),
            {"k": SENTINEL_KEY},
        ).fetchone()

    if not row:
        print("[demo] No demo data found (sentinel missing). Nothing to do.")
        return

    sentinel = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    family_ids = sentinel.get("family_ids", [])
    user_ids   = sentinel.get("user_ids", [])

    if not force:
        print(f"[demo] About to remove demo families {family_ids} and users {user_ids}.")
        ans = input("Continue? [y/N] ").strip().lower()
        if ans != "y":
            print("[demo] Aborted.")
            return

    print("[demo] Removing demo data …")
    with engine.begin() as conn:
        for fid in family_ids:
            # transactions
            for tbl in ("transactions_debit", "transactions_credit"):
                conn.execute(text(f"DELETE FROM {schema}.{tbl} WHERE family_id = :fid"), {"fid": fid})
            # loans
            conn.execute(text(f"DELETE FROM {schema}.app_loans WHERE family_id = :fid"), {"fid": fid})
            # config tables
            for cfg in ("bank_rules", "banks", "categories", "transaction", "archive"):
                conn.execute(text(f"DELETE FROM {schema}.app_config_{cfg} WHERE family_id = :fid"), {"fid": fid})

        # dashboards (cascade → widgets)
        if user_ids:
            uid_list = ",".join(str(u) for u in user_ids)
            conn.execute(text(f"DELETE FROM {schema}.app_dashboards WHERE user_id IN ({uid_list})"))
            conn.execute(text(f"DELETE FROM {schema}.app_user_prefs WHERE user_id IN ({uid_list})"))

        # memberships
        for fid in family_ids:
            conn.execute(text(f"DELETE FROM {schema}.family_memberships WHERE family_id = :fid"), {"fid": fid})
            conn.execute(text(f"DELETE FROM {schema}.families WHERE id = :fid"), {"fid": fid})

        # users
        if user_ids:
            uid_list = ",".join(str(u) for u in user_ids)
            conn.execute(text(f"DELETE FROM {schema}.app_users WHERE id IN ({uid_list})"))

        # sentinel
        conn.execute(text(f"DELETE FROM {schema}.app_settings WHERE key = :k"), {"k": SENTINEL_KEY})

    # Rebuild views with remaining data (family 1 = Default Family)
    print("[demo]   rebuilding views …")
    from services.view_manager import default_view_manager
    vm = default_view_manager()
    try:
        vm.refresh(1)
    except Exception as e:
        print(f"[demo]   view refresh warning: {e}")

    # Remove sample CSVs
    csv_dir = Path(__file__).parent.parent / "demo" / "sample_csvs"
    if csv_dir.exists():
        import shutil
        shutil.rmtree(csv_dir)
        print("[demo]   removed demo/sample_csvs/")

    print("[demo] Demo data removed.")


# ── User creation ─────────────────────────────────────────────────────────────

def _create_users(conn, schema: str) -> dict[str, int]:
    pw_hash = hash_password(DEMO_PASSWORD)
    users = [
        # username, display_name, person_name, is_instance_admin
        ("demo_admin",  "Demo Admin",   "Admin",  True),
        ("demo_user_1", "Demo User 1",  "User1",  False),
        ("demo_user_2", "Demo User 2",  "User2",  False),
        ("demo_user_3", "Demo User 3",  "User3",  False),
        ("demo_user_4", "Demo User 4",  "User4",  False),
        ("demo_user_5", "Demo User 5",  "User5",  False),
    ]
    ids: dict[str, int] = {}
    for uname, dname, pname, is_admin in users:
        # upsert — if already exists (from prior force run), reuse
        existing = conn.execute(
            text(f"SELECT id FROM {schema}.app_users WHERE username = :u"),
            {"u": uname},
        ).fetchone()
        if existing:
            uid = existing[0]
        else:
            row = conn.execute(text(f"""
                INSERT INTO {schema}.app_users
                    (username, password_hash, display_name, person_name,
                     role, is_active, is_instance_admin)
                VALUES (:u, :ph, :dn, :pn, :role, TRUE, :admin)
                RETURNING id
            """), {
                "u": uname, "ph": pw_hash, "dn": dname, "pn": pname,
                "role": "admin" if is_admin else "user",
                "admin": is_admin,
            }).fetchone()
            uid = row[0]
            conn.execute(text(f"""
                INSERT INTO {schema}.app_user_prefs (user_id, selected_persons)
                VALUES (:uid, '[]') ON CONFLICT DO NOTHING
            """), {"uid": uid})
        ids[uname] = uid
    return ids


# ── Family helpers ─────────────────────────────────────────────────────────────

def _create_family(conn, schema: str, name: str, created_by: int) -> int:
    row = conn.execute(text(f"""
        INSERT INTO {schema}.families (name, created_by, created_at)
        VALUES (:name, :uid, NOW())
        RETURNING id
    """), {"name": name, "uid": created_by}).fetchone()
    return row[0]


def _add_member(conn, schema: str, family_id: int, user_id: int, role: str) -> None:
    # Check for existing active membership first (partial unique index can't be used with ON CONFLICT)
    existing = conn.execute(text(f"""
        SELECT id FROM {schema}.family_memberships
        WHERE user_id = :uid AND left_at IS NULL
    """), {"uid": user_id}).fetchone()
    if existing:
        return
    conn.execute(text(f"""
        INSERT INTO {schema}.family_memberships (family_id, user_id, family_role, joined_at)
        VALUES (:fid, :uid, :role, NOW())
    """), {"fid": family_id, "uid": user_id, "role": role})


# ── Config ────────────────────────────────────────────────────────────────────

def _setup_family_config(conn, schema: str, family_id: int, bank_rules: list[dict]) -> None:
    """Save bank rules + copy category/transaction config from family 1 defaults."""
    import json as _json
    from data.category_rules import DEFAULT_CATEGORIES, DEFAULT_RULES
    from services.transaction_config import TransactionConfig

    def _upsert_config(table_suffix: str, data: dict) -> None:
        conn.execute(text(f"""
            INSERT INTO {schema}.app_config_{table_suffix} (family_id, data, updated_at)
            VALUES (:fid, CAST(:data AS jsonb), NOW())
            ON CONFLICT (family_id) DO UPDATE
                SET data = CAST(:data AS jsonb), updated_at = NOW()
        """), {"fid": family_id, "data": _json.dumps(data)})

    _upsert_config("bank_rules", {"rules": bank_rules})
    _upsert_config("banks",      {"banks": [r["bank_name"] for r in bank_rules]})
    _upsert_config("categories", {"categories": DEFAULT_CATEGORIES, "rules": DEFAULT_RULES})
    _upsert_config("transaction", TransactionConfig().to_dict())


def _fam1_bank_rules(uid_admin: int, uid_user1: int, uid_user2: int) -> list[dict]:
    """
    Family 1: one checking, one savings, one credit card.
    Files must start with the account_key prefix (match_type=startswith).
    """
    shared = [uid_admin, uid_user1, uid_user2]
    return [
        {
            "bank_name": "Demo Bank",
            "prefix": "demo1_checking",
            "match_field": "filename",
            "match_type": "startswith",
            "match_value": "demo1_checking",
            "account_type": "checking",
            "payment_category": "",
            "payment_description": "",
            "checking_payment_pattern": "",
            "member_name_column": "",
            "member_aliases": {},
            "column_map": {
                "date": "Date",
                "description": "Description",
                "amount": "Amount",
            },
            "dedup_columns": [],
            "person_override": shared,
            "note": "Demo Family 1 — primary checking",
        },
        {
            "bank_name": "Demo Bank",
            "prefix": "demo1_savings",
            "match_field": "filename",
            "match_type": "startswith",
            "match_value": "demo1_savings",
            "account_type": "checking",
            "payment_category": "",
            "payment_description": "",
            "checking_payment_pattern": "",
            "member_name_column": "",
            "member_aliases": {},
            "column_map": {
                "date": "Date",
                "description": "Description",
                "amount": "Amount",
            },
            "dedup_columns": [],
            "person_override": shared,
            "note": "Demo Family 1 — savings",
        },
        {
            "bank_name": "Demo Credit",
            "prefix": "demo1_credit",
            "match_field": "filename",
            "match_type": "startswith",
            "match_value": "demo1_credit",
            "account_type": "credit",
            "payment_category": "Payment",
            "payment_description": "ONLINE PAYMENT",
            "checking_payment_pattern": "DEMO CREDIT",
            "member_name_column": "",
            "member_aliases": {},
            "column_map": {
                "date": "Transaction Date",
                "description": "Description",
                "debit": "Debit",
                "credit": "Credit",
            },
            "dedup_columns": [],
            "person_override": shared,
            "note": "Demo Family 1 — credit card",
        },
    ]


def _fam2_bank_rules(uid_user3: int, uid_user4: int, uid_user5: int) -> list[dict]:
    """
    Family 2: one checking, three credit cards.
    """
    shared = [uid_user3, uid_user4, uid_user5]
    rules = [
        {
            "bank_name": "Demo Bank",
            "prefix": "demo2_checking",
            "match_field": "filename",
            "match_type": "startswith",
            "match_value": "demo2_checking",
            "account_type": "checking",
            "payment_category": "",
            "payment_description": "",
            "checking_payment_pattern": "",
            "member_name_column": "",
            "member_aliases": {},
            "column_map": {
                "date": "Date",
                "description": "Description",
                "amount": "Amount",
            },
            "dedup_columns": [],
            "person_override": shared,
            "note": "Demo Family 2 — primary checking",
        },
    ]
    for i in range(1, 4):
        rules.append({
            "bank_name": f"Demo Credit {i}",
            "prefix": f"demo2_credit{i}",
            "match_field": "filename",
            "match_type": "startswith",
            "match_value": f"demo2_credit{i}",
            "account_type": "credit",
            "payment_category": "Payment",
            "payment_description": "ONLINE PAYMENT",
            "checking_payment_pattern": f"DEMO CREDIT {i}",
            "member_name_column": "",
            "member_aliases": {},
            "column_map": {
                "date": "Transaction Date",
                "description": "Description",
                "debit": "Debit",
                "credit": "Credit",
            },
            "dedup_columns": [],
            "person_override": shared,
            "note": f"Demo Family 2 — credit card {i}",
        })
    return rules


# ── Transaction generation ─────────────────────────────────────────────────────

# Shared spend categories
_GROCERIES    = ["WHOLE FOODS", "TRADER JOES", "KROGER", "SAFEWAY", "ALDI", "COSTCO GROCERIES"]
_RESTAURANTS  = ["CHIPOTLE", "MCDONALDS", "STARBUCKS", "PANERA BREAD", "LOCAL BISTRO", "PIZZA HUT", "SUBWAY"]
_GAS          = ["SHELL GAS", "CHEVRON", "BP GAS STATION", "MOBIL"]
_UTILITIES    = ["ELECTRIC BILL", "WATER BILL", "INTERNET SERVICE", "GAS UTILITY"]
_SUBSCRIPTIONS= ["NETFLIX", "SPOTIFY", "AMAZON PRIME", "APPLE ICLOUD", "HULU"]
_SHOPPING     = ["AMAZON PURCHASE", "TARGET", "WALMART", "HOME DEPOT", "BEST BUY"]
_HEALTHCARE   = ["PHARMACY CVS", "DOCTOR COPAY", "DENTAL CARE", "VISION CENTER"]
_KIDS         = ["SCHOOL SUPPLIES", "SPORTS EQUIPMENT", "TOY STORE", "KIDS CLOTHING"]
_TRANSFER_IN  = "TRANSFER FROM SAVINGS"
_TRANSFER_OUT = "TRANSFER TO CHECKING"


def _debit_rows(
    account_key: str,
    family_id: int,
    uploaded_by: int,
    person_ids: list[int],
    start: date,
    end: date,
    income_entries: list[tuple[str, float]],  # (description, monthly_amount)
    spending_profile: dict,  # category → monthly_amount
) -> list[dict]:
    """Generate checking/savings rows for a date range."""
    rows: list[dict] = []
    for month in _months(start, end):
        # Income deposits
        for desc, amt in income_entries:
            d = _rand_date(month)
            d = min(d, date(month.year, month.month, _last_day(month)))
            rows.append({
                "account_key": account_key,
                "transaction_date": d,
                "description": desc,
                "amount": _jitter(amt, 0.03),
                "person": person_ids,
                "source_file": f"demo_seed",
                "family_id": family_id,
                "uploaded_by": uploaded_by,
            })

        # Expenses (negative = outflow)
        for cat, cat_entries in spending_profile.items():
            for desc, monthly_amt in cat_entries:
                if RNG.random() < 0.85:  # 85 % chance this expense appears this month
                    d = _rand_date(month)
                    rows.append({
                        "account_key": account_key,
                        "transaction_date": d,
                        "description": desc,
                        "amount": -abs(_jitter(monthly_amt)),
                        "person": person_ids,
                        "source_file": "demo_seed",
                        "family_id": family_id,
                        "uploaded_by": uploaded_by,
                    })

    return rows


def _credit_rows(
    account_key: str,
    family_id: int,
    uploaded_by: int,
    person_ids: list[int],
    start: date,
    end: date,
    spend_entries: list[tuple[str, float]],  # (description, monthly_amount)
    payment_amount: float,
) -> list[dict]:
    """Generate credit card rows for a date range."""
    rows: list[dict] = []
    for month in _months(start, end):
        # Monthly payment (credit > 0)
        payment_day = _rand_date(month)
        rows.append({
            "account_key": account_key,
            "transaction_date": payment_day,
            "description": "ONLINE PAYMENT",
            "debit": 0.0,
            "credit": _jitter(payment_amount, 0.05),
            "person": person_ids,
            "source_file": "demo_seed",
            "family_id": family_id,
            "uploaded_by": uploaded_by,
        })

        # Purchases (debit > 0)
        for desc, monthly_amt in spend_entries:
            if RNG.random() < 0.80:
                d = _rand_date(month)
                rows.append({
                    "account_key": account_key,
                    "transaction_date": d,
                    "description": desc,
                    "debit": abs(_jitter(monthly_amt)),
                    "credit": 0.0,
                    "person": person_ids,
                    "source_file": "demo_seed",
                    "family_id": family_id,
                    "uploaded_by": uploaded_by,
                })

    return rows


def _bulk_insert_debit(conn, schema: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    for r in rows:
        try:
            conn.execute(text(f"""
                INSERT INTO {schema}.transactions_debit
                    (account_key, transaction_date, description, amount,
                     person, source_file, family_id, uploaded_by)
                VALUES
                    (:account_key, :transaction_date, :description, :amount,
                     :person, :source_file, :family_id, :uploaded_by)
                ON CONFLICT DO NOTHING
            """), {**r, "person": r["person"]})
            inserted += 1
        except Exception as e:
            print(f"[demo] debit insert warning: {e}")
    return inserted


def _bulk_insert_credit(conn, schema: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    for r in rows:
        try:
            conn.execute(text(f"""
                INSERT INTO {schema}.transactions_credit
                    (account_key, transaction_date, description, debit, credit,
                     person, source_file, family_id, uploaded_by)
                VALUES
                    (:account_key, :transaction_date, :description, :debit, :credit,
                     :person, :source_file, :family_id, :uploaded_by)
                ON CONFLICT DO NOTHING
            """), {**r, "person": r["person"]})
            inserted += 1
        except Exception as e:
            print(f"[demo] credit insert warning: {e}")
    return inserted


def _insert_fam1_transactions(
    conn, schema: str, fid: int,
    uid_admin: int, uid_user1: int, uid_user2: int,
) -> None:
    """
    Family 1 — healthy finances
    Income: Admin $6 500/mo (employer payroll), User1 $3 500/mo
    Monthly debt: ~$2 270 (home $2 148 + car $388)
    DTI ≈ 27%
    """
    persons = [uid_admin, uid_user1, uid_user2]
    uploader = uid_admin

    # ── Checking ──────────────────────────────────────────────────────────────
    income_checking = [
        ("ACME CORP PAYROLL",    6500.0),
        ("EMPLOYER DIRECT DEP",  3500.0),
    ]
    checking_spend = {
        "mortgage":   [("DEMO HOME LOAN PAYMENT", 2148.0)],
        "car":        [("DEMO AUTO LOAN PAYMENT",  388.0)],
        "utilities":  [(u, 120.0) for u in _UTILITIES],
        "gas":        [(g,  60.0) for g in _GAS],
        "savings_xfr": [(_TRANSFER_OUT, 500.0)],
        "credit_pmt": [("DEMO CREDIT PAYMENT",    1200.0)],
    }
    rows = _debit_rows("demo1_checking", fid, uploader, persons,
                       START_DATE, END_DATE, income_checking, checking_spend)
    cnt = _bulk_insert_debit(conn, schema, rows)
    print(f"[demo]     demo1_checking: {cnt} rows")

    # ── Savings ───────────────────────────────────────────────────────────────
    income_savings = [
        (_TRANSFER_IN, 500.0),
    ]
    savings_spend: dict = {}
    rows = _debit_rows("demo1_savings", fid, uploader, persons,
                       START_DATE, END_DATE, income_savings, savings_spend)
    cnt = _bulk_insert_debit(conn, schema, rows)
    print(f"[demo]     demo1_savings: {cnt} rows")

    # ── Credit card ───────────────────────────────────────────────────────────
    credit_spend = [
        (RNG.choice(_GROCERIES),     250.0),
        (RNG.choice(_GROCERIES),     180.0),
        (RNG.choice(_RESTAURANTS),    60.0),
        (RNG.choice(_RESTAURANTS),    45.0),
        (RNG.choice(_RESTAURANTS),    35.0),
        (RNG.choice(_SHOPPING),       80.0),
        (RNG.choice(_SUBSCRIPTIONS),  15.0),
        (RNG.choice(_SUBSCRIPTIONS),  12.0),
        (RNG.choice(_HEALTHCARE),     40.0),
        (RNG.choice(_KIDS),           30.0),
    ]
    rows = _credit_rows("demo1_credit", fid, uploader, persons,
                        START_DATE, END_DATE, credit_spend, payment_amount=1100.0)
    cnt = _bulk_insert_credit(conn, schema, rows)
    print(f"[demo]     demo1_credit: {cnt} rows")


def _insert_fam2_transactions(
    conn, schema: str, fid: int,
    uid_user3: int, uid_user4: int, uid_user5: int,
) -> None:
    """
    Family 2 — stressed finances
    Income: User3 $5 500/mo, User4 $3 500/mo = $9 000 total
    Monthly debt: ~$3 800 (home $2 700 + car1 $726 + car2 $591)
    DTI ≈ 42%
    """
    persons = [uid_user3, uid_user4, uid_user5]
    uploader = uid_user3

    # ── Checking ──────────────────────────────────────────────────────────────
    income_checking = [
        ("CITYWIDE PAYROLL",      5500.0),
        ("EMPLOYER DIRECT DEP",   3500.0),
    ]
    checking_spend = {
        "mortgage":    [("DEMO HOME LOAN PAYMENT",   2700.0)],
        "car1":        [("DEMO AUTO LOAN 1 PAYMENT",  726.0)],
        "car2":        [("DEMO AUTO LOAN 2 PAYMENT",  591.0)],
        "utilities":   [(u, 130.0) for u in _UTILITIES],
        "gas":         [(g,  70.0) for g in _GAS],
        "credit_pmts": [
            ("DEMO CREDIT 1 PAYMENT", 600.0),
            ("DEMO CREDIT 2 PAYMENT", 450.0),
            ("DEMO CREDIT 3 PAYMENT", 350.0),
        ],
    }
    rows = _debit_rows("demo2_checking", fid, uploader, persons,
                       START_DATE, END_DATE, income_checking, checking_spend)
    cnt = _bulk_insert_debit(conn, schema, rows)
    print(f"[demo]     demo2_checking: {cnt} rows")

    # ── Credit 1 (general groceries + dining) ─────────────────────────────────
    credit1_spend = [
        (RNG.choice(_GROCERIES),     280.0),
        (RNG.choice(_GROCERIES),     200.0),
        (RNG.choice(_RESTAURANTS),    75.0),
        (RNG.choice(_RESTAURANTS),    55.0),
        (RNG.choice(_RESTAURANTS),    40.0),
        (RNG.choice(_SHOPPING),      120.0),
    ]
    rows = _credit_rows("demo2_credit1", fid, uploader, persons,
                        START_DATE, END_DATE, credit1_spend, payment_amount=600.0)
    cnt = _bulk_insert_credit(conn, schema, rows)
    print(f"[demo]     demo2_credit1: {cnt} rows")

    # ── Credit 2 (shopping heavy) ─────────────────────────────────────────────
    credit2_spend = [
        (RNG.choice(_SHOPPING),      150.0),
        (RNG.choice(_SHOPPING),      100.0),
        (RNG.choice(_SUBSCRIPTIONS),  18.0),
        (RNG.choice(_SUBSCRIPTIONS),  15.0),
        (RNG.choice(_HEALTHCARE),     55.0),
    ]
    rows = _credit_rows("demo2_credit2", fid, uploader, persons,
                        START_DATE, END_DATE, credit2_spend, payment_amount=450.0)
    cnt = _bulk_insert_credit(conn, schema, rows)
    print(f"[demo]     demo2_credit2: {cnt} rows")

    # ── Credit 3 (kids + misc) ────────────────────────────────────────────────
    credit3_spend = [
        (RNG.choice(_KIDS),           60.0),
        (RNG.choice(_KIDS),           45.0),
        (RNG.choice(_RESTAURANTS),    50.0),
        (RNG.choice(_SHOPPING),       80.0),
    ]
    rows = _credit_rows("demo2_credit3", fid, uploader, persons,
                        START_DATE, END_DATE, credit3_spend, payment_amount=350.0)
    cnt = _bulk_insert_credit(conn, schema, rows)
    print(f"[demo]     demo2_credit3: {cnt} rows")


# ── Loans ─────────────────────────────────────────────────────────────────────

def _insert_loans(conn, schema: str, fid1: int, fid2: int) -> None:
    """Insert demo loans directly (bypasses loan_service to avoid session dependency)."""

    def _ins(data: dict) -> None:
        conn.execute(text(f"""
            INSERT INTO {schema}.app_loans
                (name, loan_type, rate_type, interest_rate,
                 original_principal, term_months, start_date,
                 monthly_payment, monthly_insurance,
                 current_balance, balance_as_of,
                 payment_description_pattern, payment_account_key,
                 lender, notes, is_active, family_id, updated_at)
            VALUES
                (:name, :loan_type, :rate_type, :interest_rate,
                 :original_principal, :term_months, :start_date,
                 :monthly_payment, :monthly_insurance,
                 :current_balance, :balance_as_of,
                 :payment_description_pattern, :payment_account_key,
                 :lender, :notes, TRUE, :family_id, NOW())
        """), data)

    # Family 1 — home loan
    _ins({
        "name": "Demo Home Loan",
        "loan_type": "mortgage",
        "rate_type": "fixed",
        "interest_rate": 5.5,
        "original_principal": 400000.0,
        "term_months": 360,
        "start_date": date(2021, 6, 1),
        "monthly_payment": 2271.16,
        "monthly_insurance": 125.0,
        "current_balance": 368000.0,
        "balance_as_of": date(2025, 1, 1),
        "payment_description_pattern": "DEMO HOME LOAN",
        "payment_account_key": "demo1_checking",
        "lender": "Demo Mortgage Co",
        "notes": "30-year fixed, bought the house in 2021",
        "family_id": fid1,
    })

    # Family 1 — car loan
    _ins({
        "name": "Demo Car Loan",
        "loan_type": "auto",
        "rate_type": "fixed",
        "interest_rate": 6.5,
        "original_principal": 20000.0,
        "term_months": 60,
        "start_date": date(2023, 3, 1),
        "monthly_payment": 391.32,
        "monthly_insurance": 0.0,
        "current_balance": 14500.0,
        "balance_as_of": date(2025, 1, 1),
        "payment_description_pattern": "DEMO AUTO LOAN",
        "payment_account_key": "demo1_checking",
        "lender": "Demo Auto Finance",
        "notes": "60-month auto loan",
        "family_id": fid1,
    })

    # Family 2 — home loan
    _ins({
        "name": "Demo Home Loan",
        "loan_type": "mortgage",
        "rate_type": "fixed",
        "interest_rate": 7.2,
        "original_principal": 380000.0,
        "term_months": 360,
        "start_date": date(2022, 9, 1),
        "monthly_payment": 2577.77,
        "monthly_insurance": 130.0,
        "current_balance": 365000.0,
        "balance_as_of": date(2025, 1, 1),
        "payment_description_pattern": "DEMO HOME LOAN",
        "payment_account_key": "demo2_checking",
        "lender": "Demo Mortgage Co",
        "notes": "30-year fixed, bought in 2022 at higher rates",
        "family_id": fid2,
    })

    # Family 2 — car 1
    _ins({
        "name": "Demo Car Loan 1",
        "loan_type": "auto",
        "rate_type": "fixed",
        "interest_rate": 8.9,
        "original_principal": 35000.0,
        "term_months": 60,
        "start_date": date(2022, 5, 1),
        "monthly_payment": 725.56,
        "monthly_insurance": 0.0,
        "current_balance": 17000.0,
        "balance_as_of": date(2025, 1, 1),
        "payment_description_pattern": "DEMO AUTO LOAN 1",
        "payment_account_key": "demo2_checking",
        "lender": "Demo Auto Finance",
        "notes": "60-month, higher rate",
        "family_id": fid2,
    })

    # Family 2 — car 2
    _ins({
        "name": "Demo Car Loan 2",
        "loan_type": "auto",
        "rate_type": "fixed",
        "interest_rate": 9.5,
        "original_principal": 28000.0,
        "term_months": 60,
        "start_date": date(2023, 8, 1),
        "monthly_payment": 591.47,
        "monthly_insurance": 0.0,
        "current_balance": 23000.0,
        "balance_as_of": date(2025, 1, 1),
        "payment_description_pattern": "DEMO AUTO LOAN 2",
        "payment_account_key": "demo2_checking",
        "lender": "Demo Auto Finance",
        "notes": "Second car, 9.5% rate",
        "family_id": fid2,
    })

    print("[demo]   inserted 5 loans (2 for Family 1, 3 for Family 2)")


# ── Sample CSV generation ─────────────────────────────────────────────────────

def _write_sample_csvs(user_ids: dict[str, int]) -> None:
    """Write 2026 Q1 sample CSV files to demo/sample_csvs/."""
    csv_dir = Path(__file__).parent.parent / "demo" / "sample_csvs"
    csv_dir.mkdir(parents=True, exist_ok=True)

    # Reset RNG for reproducible CSVs
    csv_rng = random.Random(99)

    def rand_date_in_range(start: date, end: date) -> date:
        delta = (end - start).days
        return start + timedelta(days=csv_rng.randint(0, delta))

    def jitter(v: float, p: float = 0.15) -> float:
        return round(v * (1 + csv_rng.uniform(-p, p)), 2)

    # ── Family 1 — checking ───────────────────────────────────────────────────
    rows = [("Date", "Description", "Amount", "Balance")]
    balance = 4200.0
    entries = [
        ("2026-01-15", "ACME CORP PAYROLL",        6500.0),
        ("2026-01-16", "EMPLOYER DIRECT DEP",       3500.0),
        ("2026-01-05", "DEMO HOME LOAN PAYMENT",  -2271.0),
        ("2026-01-10", "DEMO AUTO LOAN PAYMENT",   -391.0),
        ("2026-01-20", "ELECTRIC BILL",            -115.0),
        ("2026-01-22", "INTERNET SERVICE",          -75.0),
        ("2026-01-18", "SHELL GAS",                 -55.0),
        ("2026-01-28", "DEMO CREDIT PAYMENT",      -1100.0),
        ("2026-01-29", "TRANSFER TO CHECKING",     -500.0),
        ("2026-02-15", "ACME CORP PAYROLL",         6500.0),
        ("2026-02-16", "EMPLOYER DIRECT DEP",       3500.0),
        ("2026-02-05", "DEMO HOME LOAN PAYMENT",  -2271.0),
        ("2026-02-10", "DEMO AUTO LOAN PAYMENT",   -391.0),
        ("2026-02-20", "ELECTRIC BILL",            -118.0),
        ("2026-02-22", "INTERNET SERVICE",          -75.0),
        ("2026-02-19", "CHEVRON",                   -62.0),
        ("2026-02-25", "DEMO CREDIT PAYMENT",      -1100.0),
        ("2026-02-26", "TRANSFER TO CHECKING",     -500.0),
    ]
    for dt, desc, amt in entries:
        balance = round(balance + amt, 2)
        rows.append((dt, desc, str(amt), str(balance)))
    _write_csv(csv_dir / "demo1_checking_2026q1.csv", rows)

    # ── Family 1 — savings ────────────────────────────────────────────────────
    rows = [("Date", "Description", "Amount", "Balance")]
    balance = 12000.0
    for dt, amt in [
        ("2026-01-29", 500.0),
        ("2026-02-26", 500.0),
    ]:
        balance = round(balance + amt, 2)
        rows.append((dt, "TRANSFER FROM SAVINGS", str(amt), str(balance)))
    _write_csv(csv_dir / "demo1_savings_2026q1.csv", rows)

    # ── Family 1 — credit ─────────────────────────────────────────────────────
    rows = [("Transaction Date", "Description", "Debit", "Credit")]
    for dt, desc, debit, credit in [
        ("2026-01-03", "WHOLE FOODS",       "182.50", ""),
        ("2026-01-07", "STARBUCKS",          "14.80", ""),
        ("2026-01-09", "AMAZON PURCHASE",    "67.99", ""),
        ("2026-01-12", "CHIPOTLE",           "38.50", ""),
        ("2026-01-15", "NETFLIX",            "15.99", ""),
        ("2026-01-18", "TRADER JOES",       "143.20", ""),
        ("2026-01-22", "DOCTOR COPAY",       "40.00", ""),
        ("2026-01-25", "TARGET",             "92.15", ""),
        ("2026-01-28", "ONLINE PAYMENT",        "",  "1100.00"),
        ("2026-02-02", "WHOLE FOODS",       "196.30", ""),
        ("2026-02-06", "PANERA BREAD",       "22.40", ""),
        ("2026-02-11", "AMAZON PURCHASE",    "54.99", ""),
        ("2026-02-14", "SPOTIFY",            "11.99", ""),
        ("2026-02-17", "TRADER JOES",       "158.90", ""),
        ("2026-02-20", "SCHOOL SUPPLIES",    "35.00", ""),
        ("2026-02-24", "PHARMACY CVS",       "28.50", ""),
        ("2026-02-25", "ONLINE PAYMENT",        "",  "1100.00"),
    ]:
        rows.append((dt, desc, debit, credit))
    _write_csv(csv_dir / "demo1_credit_2026q1.csv", rows)

    # ── Family 2 — checking ───────────────────────────────────────────────────
    rows = [("Date", "Description", "Amount", "Balance")]
    balance = 1800.0
    for dt, desc, amt in [
        ("2026-01-15", "CITYWIDE PAYROLL",          5500.0),
        ("2026-01-16", "EMPLOYER DIRECT DEP",        3500.0),
        ("2026-01-03", "DEMO HOME LOAN PAYMENT",   -2700.0),
        ("2026-01-08", "DEMO AUTO LOAN 1 PAYMENT",  -726.0),
        ("2026-01-08", "DEMO AUTO LOAN 2 PAYMENT",  -591.0),
        ("2026-01-18", "ELECTRIC BILL",             -128.0),
        ("2026-01-20", "WATER BILL",                 -60.0),
        ("2026-01-22", "INTERNET SERVICE",           -80.0),
        ("2026-01-19", "SHELL GAS",                  -65.0),
        ("2026-01-21", "BP GAS STATION",             -72.0),
        ("2026-01-26", "DEMO CREDIT 1 PAYMENT",     -600.0),
        ("2026-01-27", "DEMO CREDIT 2 PAYMENT",     -450.0),
        ("2026-01-28", "DEMO CREDIT 3 PAYMENT",     -350.0),
        ("2026-02-15", "CITYWIDE PAYROLL",           5500.0),
        ("2026-02-16", "EMPLOYER DIRECT DEP",        3500.0),
        ("2026-02-03", "DEMO HOME LOAN PAYMENT",   -2700.0),
        ("2026-02-08", "DEMO AUTO LOAN 1 PAYMENT",  -726.0),
        ("2026-02-08", "DEMO AUTO LOAN 2 PAYMENT",  -591.0),
        ("2026-02-18", "ELECTRIC BILL",             -132.0),
        ("2026-02-20", "WATER BILL",                 -58.0),
        ("2026-02-22", "INTERNET SERVICE",           -80.0),
        ("2026-02-19", "CHEVRON",                    -68.0),
        ("2026-02-23", "MOBIL",                      -75.0),
        ("2026-02-25", "DEMO CREDIT 1 PAYMENT",     -600.0),
        ("2026-02-26", "DEMO CREDIT 2 PAYMENT",     -450.0),
        ("2026-02-27", "DEMO CREDIT 3 PAYMENT",     -350.0),
    ]:
        balance = round(balance + amt, 2)
        rows.append((dt, desc, str(amt), str(balance)))
    _write_csv(csv_dir / "demo2_checking_2026q1.csv", rows)

    # ── Family 2 — credit 1 ───────────────────────────────────────────────────
    rows = [("Transaction Date", "Description", "Debit", "Credit")]
    for dt, desc, debit, credit in [
        ("2026-01-04", "KROGER",            "215.60", ""),
        ("2026-01-08", "MCDONALDS",          "18.40", ""),
        ("2026-01-10", "WALMART",           "142.90", ""),
        ("2026-01-14", "CHIPOTLE",           "42.50", ""),
        ("2026-01-18", "ALDI",              "189.20", ""),
        ("2026-01-22", "PIZZA HUT",          "35.00", ""),
        ("2026-01-26", "ONLINE PAYMENT",         "",  "600.00"),
        ("2026-02-03", "KROGER",            "228.40", ""),
        ("2026-02-07", "MCDONALDS",          "21.60", ""),
        ("2026-02-11", "WALMART",           "156.30", ""),
        ("2026-02-15", "SUBWAY",             "28.90", ""),
        ("2026-02-19", "ALDI",              "202.80", ""),
        ("2026-02-24", "CHIPOTLE",           "47.20", ""),
        ("2026-02-25", "ONLINE PAYMENT",         "",  "600.00"),
    ]:
        rows.append((dt, desc, debit, credit))
    _write_csv(csv_dir / "demo2_credit1_2026q1.csv", rows)

    # ── Family 2 — credit 2 ───────────────────────────────────────────────────
    rows = [("Transaction Date", "Description", "Debit", "Credit")]
    for dt, desc, debit, credit in [
        ("2026-01-06", "AMAZON PURCHASE",   "138.50", ""),
        ("2026-01-10", "BEST BUY",          "249.99", ""),
        ("2026-01-14", "NETFLIX",            "17.99", ""),
        ("2026-01-18", "DOCTOR COPAY",       "55.00", ""),
        ("2026-01-22", "HOME DEPOT",         "87.40", ""),
        ("2026-01-27", "ONLINE PAYMENT",         "",  "450.00"),
        ("2026-02-05", "AMAZON PURCHASE",   "114.20", ""),
        ("2026-02-09", "TARGET",            "165.80", ""),
        ("2026-02-13", "HULU",               "13.99", ""),
        ("2026-02-17", "DENTAL CARE",        "75.00", ""),
        ("2026-02-21", "HOME DEPOT",         "62.30", ""),
        ("2026-02-26", "ONLINE PAYMENT",         "",  "450.00"),
    ]:
        rows.append((dt, desc, debit, credit))
    _write_csv(csv_dir / "demo2_credit2_2026q1.csv", rows)

    # ── Family 2 — credit 3 ───────────────────────────────────────────────────
    rows = [("Transaction Date", "Description", "Debit", "Credit")]
    for dt, desc, debit, credit in [
        ("2026-01-07", "TOY STORE",          "68.90", ""),
        ("2026-01-11", "SPORTS EQUIPMENT",   "92.50", ""),
        ("2026-01-16", "STARBUCKS",          "16.40", ""),
        ("2026-01-20", "KIDS CLOTHING",      "54.20", ""),
        ("2026-01-28", "ONLINE PAYMENT",         "",  "350.00"),
        ("2026-02-04", "SCHOOL SUPPLIES",    "44.80", ""),
        ("2026-02-09", "TOY STORE",          "71.30", ""),
        ("2026-02-13", "LOCAL BISTRO",       "62.00", ""),
        ("2026-02-20", "KIDS CLOTHING",      "48.60", ""),
        ("2026-02-27", "ONLINE PAYMENT",         "",  "350.00"),
    ]:
        rows.append((dt, desc, debit, credit))
    _write_csv(csv_dir / "demo2_credit3_2026q1.csv", rows)

    files = sorted(csv_dir.glob("*.csv"))
    print(f"[demo]   wrote {len(files)} sample CSVs to demo/sample_csvs/")
    for f in files:
        print(f"[demo]     {f.name}")


def _write_csv(path: Path, rows: list[tuple]) -> None:
    import csv
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision or remove Finance Manager demo data."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--destroy", "--clean",
        action="store_true",
        dest="destroy",
        help="Remove all demo data (prompts for confirmation unless --force)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt; also re-provisions if already installed",
    )
    args = parser.parse_args()

    if args.destroy:
        destroy_demo(force=args.force)
    else:
        create_demo(force=args.force)


if __name__ == "__main__":
    main()

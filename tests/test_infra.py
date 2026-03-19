"""
tests/test_infra.py

Smoke tests that verify the test infrastructure is wired correctly.
These do NOT test any app logic — they just confirm:
  1. The throwaway container starts and is reachable
  2. Migrations ran and the core tables exist
  3. The per-test rollback isolation works (data from test A doesn't bleed into test B)
"""

from sqlalchemy import text


def _tables(db_conn, schema: str) -> set[str]:
    rows = db_conn.execute(text("""
        SELECT table_name
        FROM   information_schema.tables
        WHERE  table_schema = :schema
          AND  table_type   = 'BASE TABLE'
    """), {"schema": schema}).fetchall()
    return {r[0] for r in rows}


class TestContainerAndMigrations:
    def test_connection_works(self, db_conn):
        row = db_conn.execute(text("SELECT 1 AS n")).fetchone()
        assert row[0] == 1

    def test_core_tables_exist(self, db_conn, schema):
        tables = _tables(db_conn, schema)
        for expected in (
            "app_users",
            "app_user_prefs",
            "app_config_bank_rules",
            "app_config_banks",
            "app_config_categories",
            "app_config_transaction",
            "app_settings",
            "app_dashboards",
            "app_dashboard_widgets",
            "app_loans",
            "transactions_debit",
            "transactions_credit",
        ):
            assert expected in tables, f"Expected table '{expected}' not found after migrations"

    def test_transaction_tables_are_partitioned(self, db_conn, schema):
        """Verify at least one year partition exists for each transaction table."""
        rows = db_conn.execute(text("""
            SELECT inhrelid::regclass::text
            FROM   pg_inherits
            JOIN   pg_class parent ON parent.oid = inhparent
            WHERE  parent.relname IN ('transactions_debit', 'transactions_credit')
        """)).fetchall()
        assert len(rows) > 0, "No partitions found for transaction tables"


class TestRollbackIsolation:
    """Two tests that each insert a row — neither should see the other's data."""

    def test_insert_user_a(self, db_conn, schema):
        db_conn.execute(text(f"""
            INSERT INTO {schema}.app_users
                (username, password_hash, display_name, person_name, role)
            VALUES ('isolation_user_a', 'x', 'A', 'A', 'user')
        """))
        row = db_conn.execute(text(
            f"SELECT username FROM {schema}.app_users WHERE username = 'isolation_user_a'"
        )).fetchone()
        assert row is not None

    def test_user_a_not_visible(self, db_conn, schema):
        """isolation_user_a was rolled back — must not appear here."""
        row = db_conn.execute(text(
            f"SELECT username FROM {schema}.app_users WHERE username = 'isolation_user_a'"
        )).fetchone()
        assert row is None, "Rollback isolation failed — previous test's data leaked"

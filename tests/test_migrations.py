"""
tests/test_migrations.py

Integration tests verifying Phase 1 DB schema.
All tests use the `db_conn` fixture (real DB, rolled back after each test).
"""

from sqlalchemy import text


def _columns(db_conn, schema: str, table: str) -> set[str]:
    rows = db_conn.execute(text("""
        SELECT column_name
        FROM   information_schema.columns
        WHERE  table_schema = :schema AND table_name = :table
    """), {"schema": schema, "table": table}).fetchall()
    return {r[0] for r in rows}


def _tables(db_conn, schema: str) -> set[str]:
    rows = db_conn.execute(text("""
        SELECT table_name
        FROM   information_schema.tables
        WHERE  table_schema = :schema AND table_type = 'BASE TABLE'
    """), {"schema": schema}).fetchall()
    return {r[0] for r in rows}


def _constraints(db_conn, schema: str, table: str) -> set[str]:
    rows = db_conn.execute(text("""
        SELECT constraint_name
        FROM   information_schema.table_constraints
        WHERE  table_schema = :schema AND table_name = :table
    """), {"schema": schema, "table": table}).fetchall()
    return {r[0] for r in rows}


# ── New family tables ──────────────────────────────────────────────────────────

class TestFamilyTables:
    def test_families_exists(self, db_conn, schema):
        assert "families" in _tables(db_conn, schema)

    def test_families_columns(self, db_conn, schema):
        cols = _columns(db_conn, schema, "families")
        assert {"id", "name", "created_by", "created_at"} <= cols

    def test_family_memberships_exists(self, db_conn, schema):
        assert "family_memberships" in _tables(db_conn, schema)

    def test_family_memberships_columns(self, db_conn, schema):
        cols = _columns(db_conn, schema, "family_memberships")
        assert {"id", "family_id", "user_id", "family_role", "joined_at", "left_at"} <= cols

    def test_family_memberships_role_check(self, db_conn, schema):
        """family_role must be 'member' or 'head' — anything else raises."""
        db_conn.execute(text(f"""
            INSERT INTO {schema}.families (id, name) VALUES (999, 'Test')
            ON CONFLICT (id) DO NOTHING
        """))
        import pytest
        with pytest.raises(Exception):
            db_conn.execute(text(f"""
                INSERT INTO {schema}.family_memberships (family_id, user_id, family_role)
                VALUES (999, 0, 'superadmin')
            """))

    def test_user_bank_permissions_exists(self, db_conn, schema):
        assert "user_bank_permissions" in _tables(db_conn, schema)

    def test_user_bank_permissions_columns(self, db_conn, schema):
        cols = _columns(db_conn, schema, "user_bank_permissions")
        assert {"user_id", "family_id", "account_key", "can_upload"} <= cols


# ── Auth / invite tables ───────────────────────────────────────────────────────

class TestAuthTables:
    def test_password_reset_tokens_exists(self, db_conn, schema):
        assert "password_reset_tokens" in _tables(db_conn, schema)

    def test_password_reset_tokens_columns(self, db_conn, schema):
        cols = _columns(db_conn, schema, "password_reset_tokens")
        assert {"id", "user_id", "token_hash", "expires_at", "used_at"} <= cols

    def test_invitations_exists(self, db_conn, schema):
        assert "invitations" in _tables(db_conn, schema)

    def test_invitations_columns(self, db_conn, schema):
        cols = _columns(db_conn, schema, "invitations")
        assert {"id", "token_hash", "invited_by", "family_id", "family_role",
                "email", "expires_at", "accepted_at"} <= cols


# ── Dashboard template tables ──────────────────────────────────────────────────

class TestDashboardTemplateTables:
    def test_dashboard_templates_exists(self, db_conn, schema):
        assert "dashboard_templates" in _tables(db_conn, schema)

    def test_dashboard_templates_columns(self, db_conn, schema):
        cols = _columns(db_conn, schema, "dashboard_templates")
        assert {"id", "name", "description", "created_by",
                "is_published", "created_at", "updated_at"} <= cols

    def test_dashboard_template_widgets_exists(self, db_conn, schema):
        assert "dashboard_template_widgets" in _tables(db_conn, schema)

    def test_dashboard_template_widgets_columns(self, db_conn, schema):
        cols = _columns(db_conn, schema, "dashboard_template_widgets")
        assert {"id", "template_id", "chart_id", "col_start", "row_start",
                "col_span", "row_span", "config"} <= cols


# ── app_users new columns ─────────────────────────────────────────────────────

class TestAppUsersColumns:
    def test_email_column(self, db_conn, schema):
        assert "email" in _columns(db_conn, schema, "app_users")

    def test_must_change_password_column(self, db_conn, schema):
        assert "must_change_password" in _columns(db_conn, schema, "app_users")

    def test_is_instance_admin_column(self, db_conn, schema):
        assert "is_instance_admin" in _columns(db_conn, schema, "app_users")

    def test_is_instance_admin_defaults_false(self, db_conn, schema):
        db_conn.execute(text(f"""
            INSERT INTO {schema}.app_users
                (username, password_hash, display_name, person_name)
            VALUES ('test_admin_default', 'x', 'T', 'T')
        """))
        row = db_conn.execute(text(f"""
            SELECT is_instance_admin FROM {schema}.app_users
            WHERE username = 'test_admin_default'
        """)).fetchone()
        assert row[0] is False

    def test_must_change_password_defaults_false(self, db_conn, schema):
        db_conn.execute(text(f"""
            INSERT INTO {schema}.app_users
                (username, password_hash, display_name, person_name)
            VALUES ('test_mcp_default', 'x', 'T', 'T')
        """))
        row = db_conn.execute(text(f"""
            SELECT must_change_password FROM {schema}.app_users
            WHERE username = 'test_mcp_default'
        """)).fetchone()
        assert row[0] is False


# ── Transaction tables new columns ────────────────────────────────────────────

class TestTransactionColumns:
    def test_debit_family_id_column(self, db_conn, schema):
        assert "family_id" in _columns(db_conn, schema, "transactions_debit")

    def test_debit_uploaded_by_column(self, db_conn, schema):
        assert "uploaded_by" in _columns(db_conn, schema, "transactions_debit")

    def test_credit_family_id_column(self, db_conn, schema):
        assert "family_id" in _columns(db_conn, schema, "transactions_credit")

    def test_credit_uploaded_by_column(self, db_conn, schema):
        assert "uploaded_by" in _columns(db_conn, schema, "transactions_credit")


# ── app_config_* family scoping ───────────────────────────────────────────────

class TestConfigFamilyScoping:
    def test_config_tables_have_family_id(self, db_conn, schema):
        for cfg in ("bank_rules", "banks", "categories", "transaction"):
            cols = _columns(db_conn, schema, f"app_config_{cfg}")
            assert "family_id" in cols, f"app_config_{cfg} missing family_id column"

    def test_config_scoped_to_family(self, db_conn, schema):
        """Two families can have independent config rows."""
        db_conn.execute(text(f"""
            INSERT INTO {schema}.families (id, name) VALUES (901, 'Family A'), (902, 'Family B')
            ON CONFLICT (id) DO NOTHING
        """))
        db_conn.execute(text(f"""
            INSERT INTO {schema}.app_config_bank_rules (family_id, data)
            VALUES (901, '{{"rules": []}}'), (902, '{{"rules": [1]}}')
        """))
        row = db_conn.execute(text(f"""
            SELECT data FROM {schema}.app_config_bank_rules WHERE family_id = 902
        """)).fetchone()
        assert row is not None
        assert row[0].get("rules") == [1]

    def test_config_family_uniqueness(self, db_conn, schema):
        """Can't insert two config rows for the same family."""
        import pytest
        db_conn.execute(text(f"""
            INSERT INTO {schema}.families (id, name) VALUES (903, 'Dup Test')
            ON CONFLICT (id) DO NOTHING
        """))
        db_conn.execute(text(f"""
            INSERT INTO {schema}.app_config_bank_rules (family_id, data)
            VALUES (903, '{{"rules": []}}')
        """))
        with pytest.raises(Exception):
            db_conn.execute(text(f"""
                INSERT INTO {schema}.app_config_bank_rules (family_id, data)
                VALUES (903, '{{"rules": [999]}}')
            """))


# ── Default family seeding ────────────────────────────────────────────────────

class TestDefaultFamilySeeding:
    def test_default_family_exists(self, db_conn, schema):
        """Migrations seed family id=1 via config seeding."""
        row = db_conn.execute(text(
            f"SELECT name FROM {schema}.families WHERE id = 1"
        )).fetchone()
        assert row is not None
        assert row[0] == "Default Family"

    def test_default_config_seeded(self, db_conn, schema):
        """Config tables are seeded for family_id=1."""
        for cfg in ("bank_rules", "categories", "transaction"):
            row = db_conn.execute(text(
                f"SELECT 1 FROM {schema}.app_config_{cfg} WHERE family_id = 1"
            )).fetchone()
            assert row is not None, f"app_config_{cfg} not seeded for default family"

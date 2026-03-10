"""
view_manager.py

Builds four Postgres views from raw_* tables:

  v_credit_payments   — payment rows received on credit cards (used for reconciliation)
  v_credit_spend      — actual purchases on credit cards (excludes payments)
  v_debit_spend       — checking outflows, minus transfers/zelle/employer patterns,
                        minus any outflow that matches a credit card payment by amount+date
  v_income            — checking inflows matching employer patterns
  v_all_spend         — UNION ALL of v_credit_spend + v_debit_spend

Each view is rebuilt from the live schema on every refresh() call, so adding
a new bank is handled automatically once its raw table exists and its BankRule
has account_type and payment_indicator fields set.
"""

from __future__ import annotations
from sqlalchemy import create_engine, text, inspect
from services.bank_rules import load_rules, BankRule
from services.transaction_config import load_config
from services.category_rules import load_category_config, CategoryRule


def _sqlalchemy_url(conn: tuple) -> str:
    user, password, host, port, db = conn
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"

def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def _esc(s: str) -> str:
    """Escape single quotes for embedding in SQL string literals."""
    return s.replace("'", "''")


# Column name candidates — first match wins
DATE_CANDIDATES = ["transaction_date", "date"]
DESC_CANDIDATES = ["description", "memo", "transaction_description"]
AMOUNT_CANDIDATES = ["amount"]
DEBIT_CANDIDATES  = ["debit"]
CREDIT_CANDIDATES = ["credit"]


def _first_match(cols: set[str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in cols:
            return c
    return None


class ViewManager:
    def __init__(self, db_connection_string: tuple, schema: str = "public"):
        self.conn_tuple = db_connection_string
        self.schema     = schema
        self.engine     = create_engine(_sqlalchemy_url(db_connection_string))

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Rebuild all views in dependency order."""
        # Drop all views first in reverse-dependency order so stale schemas
        # never block recreation (e.g. column renames, reordering).
        with self.engine.begin() as conn:
            for v in ("v_transactions", "v_all_spend",
                      "v_income", "v_debit_spend",
                      "v_credit_spend", "v_credit_payments"):
                conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.{v} CASCADE"))

        rules      = load_rules()
        cfg        = load_config()
        rule_map   = self._build_rule_map(rules)
        from services.category_rules import load_category_config
        cfg_cat    = load_category_config()
        all_tables = self._raw_tables()
        print(f"[ViewManager] raw tables: {all_tables}")

        self._build_credit_payments_view(all_tables, rule_map)
        self._build_credit_spend_view(all_tables, rule_map, cfg_cat)
        self._build_debit_spend_view(all_tables, rule_map, cfg, cfg_cat)
        self._build_income_view(all_tables, rule_map, cfg)
        self._build_all_spend_view()
        # Keep legacy v_transactions for any existing queries
        self._build_legacy_transactions_view(all_tables, rule_map)
        print("[ViewManager] views refreshed: v_credit_payments, v_credit_spend, v_debit_spend, v_income, v_all_spend")

    # ── Rule map ──────────────────────────────────────────────────────────────

    def _build_rule_map(self, rules: list[BankRule]) -> dict[str, BankRule]:
        """
        Map sanitized table name → BankRule.
        Builds two indexes stored on self for use by _rule_for():
          - exact:     raw_{sanitize(bank_name)} → rule
          - substring: sanitize(bank_name) → rule  (fallback)
        """
        import re
        def sanitize(s: str) -> str:
            return re.sub(r"[^a-z0-9_]", "", s.lower().replace(" ", "_").replace("-", "_"))

        self._exact_rule_map     = {}
        self._substring_rule_map = {}
        for rule in rules:
            key = f"raw_{sanitize(rule.bank_name)}"
            self._exact_rule_map[key]                  = rule
            self._substring_rule_map[sanitize(rule.bank_name)] = rule

        return self._exact_rule_map

    def _rule_for(self, table: str, rule_map: dict[str, BankRule] | None = None) -> BankRule | None:
        # Exact match
        exact = getattr(self, "_exact_rule_map", {})
        if table in exact:
            return exact[table]
        # Substring fallback
        for key, rule in getattr(self, "_substring_rule_map", {}).items():
            if key in table:
                return rule
        return None

    # ── Column introspection ──────────────────────────────────────────────────

    def _cols(self, table: str) -> set[str]:
        return {c["name"] for c in inspect(self.engine).get_columns(table, schema=self.schema)}

    def _amount_col(self, cols: set[str], rule: BankRule | None) -> str | None:
        """Resolve which column holds the monetary value for this table."""
        return _first_match(cols, AMOUNT_CANDIDATES) or _first_match(cols, DEBIT_CANDIDATES)

    # ── Payment indicator WHERE fragment ─────────────────────────────────────

    def _payment_filter(self, rule: BankRule | None, cols: set[str]) -> str:
        """
        Returns a WHERE fragment that is TRUE for payment rows.
        A row is a payment if EITHER condition matches.
        Having only one configured is fine — the other is simply skipped.
        """
        if not rule:
            return ""
        parts = []

        if rule.payment_description:
            desc_col = _first_match(cols, DESC_CANDIDATES)
            if desc_col:
                parts.append(f"{_q(desc_col)} ILIKE '%{_esc(rule.payment_description)}%'")

        if rule.payment_category:
            cat_col = _first_match(cols, [
                "category", "transaction_category", "transaction_type",
                "type", "category_1", "trans_category",
            ])
            if cat_col:
                parts.append(f"{_q(cat_col)} ILIKE '%{_esc(rule.payment_category)}%'")

        if not parts:
            return ""
        return "(" + " OR ".join(parts) + ")"

    # ── Person expression ────────────────────────────────────────────────────

    def _person_expr(self, cols: set[str], rule: BankRule | None, cfg) -> str:
        """
        Build a SQL expression for the person column.
        If the rule has a member_name_column and cfg has member_aliases,
        generates a CASE WHEN ... THEN ... ELSE person END expression.
        Falls back to the raw person column if no mapping configured.
        """
        if (rule and rule.member_name_column
                and rule.member_name_column in cols
                and cfg.member_aliases):
            mc = _q(rule.member_name_column)
            cases = "\n            ".join(
                f"WHEN {mc} ILIKE '%{_esc(name)}%' THEN '{_esc(alias)}'"
                for name, alias in cfg.member_aliases.items()
            )
            return f"CASE {cases} ELSE person END"
        return "person"

    # ── Category expression ──────────────────────────────────────────────────

    def _category_case_expr(self, desc_col: str, cfg_cat) -> tuple[str, str]:
        """
        Build SQL CASE WHEN expressions for category and cost_type.
        Rules are sorted by priority (lower = checked first).
        Never falls back to the bank's own category column — unmatched rows
        always resolve to 'Other' / 'variable' to avoid duplicates and
        uncolored categories from raw bank data.
        Returns (category_expr, cost_type_expr).
        """
        cost_map = {c.name: c.cost_type for c in cfg_cat.categories}
        lines    = []

        for rule in cfg_cat.sorted_rules():
            cat   = _esc(rule.category)
            ctype = _esc(cost_map.get(rule.category, "variable"))
            p     = _esc(rule.pattern)
            if rule.is_regex:
                cond = f"{_q(desc_col)} ~* '{p}'"
            else:
                cond = f"{_q(desc_col)} ILIKE '%{p}%'"
            lines.append((cond, cat, ctype))

        if lines:
            cat_cases   = "\n            ".join(f"WHEN {c} THEN '{v}'" for c, v, _ in lines)
            ctype_cases = "\n            ".join(f"WHEN {c} THEN '{t}'" for c, _, t in lines)
        else:
            cat_cases   = ""
            ctype_cases = ""

        cat_expr   = f"CASE {cat_cases} ELSE 'Other' END" if cat_cases else "'Other'"
        ctype_expr = f"CASE {ctype_cases} ELSE 'variable' END" if ctype_cases else "'variable'"

        return cat_expr, ctype_expr

    # ── NULL/NaN guard ────────────────────────────────────────────────────────

    def _valid_amount(self, col: str) -> str:
        return (f"{_q(col)} IS NOT NULL "
                f"AND {_q(col)}::TEXT <> '' "
                f"AND {_q(col)}::TEXT <> 'NaN'")

    # ── SELECT branch builders ────────────────────────────────────────────────

    def _base_select(self, table: str, cols: set[str],
                     amount_col: str, amount_expr: str,
                     bank_label: str, rule: BankRule | None = None,
                     cfg=None, cfg_cat=None) -> str:
        date_col    = _first_match(cols, DATE_CANDIDATES)
        desc_col    = _first_match(cols, DESC_CANDIDATES)
        if not date_col or not desc_col:
            return ""
        person_expr = self._person_expr(cols, rule, cfg) if cfg else "person"

        if cfg_cat and desc_col:
            cat_expr, ctype_expr = self._category_case_expr(desc_col, cfg_cat)
            cat_cols = (
                f"        ({cat_expr}) AS category,\n"
                f"        ({ctype_expr}) AS cost_type,\n"
            )
        else:
            cat_cols = (
                f"        'Other'::TEXT AS category,\n"
                f"        'variable'::TEXT AS cost_type,\n"
            )

        return (
            f"    SELECT\n"
            f"        {person_expr} AS person,\n"
            f"        {_q(date_col)}::DATE AS transaction_date,\n"
            f"        {_q(desc_col)}      AS description,\n"
            f"        {amount_expr}       AS amount,\n"
            f"        '{bank_label}'::TEXT AS bank,\n"
            f"{cat_cols}"
            f"        '{bank_label}'::TEXT AS source_bank\n"
            f"    FROM {self.schema}.{_q(table)}\n"
            f"    WHERE {self._valid_amount(amount_col)}"
        )

    # ── v_credit_payments ────────────────────────────────────────────────────

    def _build_credit_payments_view(self, tables: list[str], rule_map: dict) -> None:
        """
        Payment rows received on credit cards.
        credit account_type → always has separate debit/credit columns.
        Payments = credit column > 0, filtered by payment_description/category.
        Used downstream to reconcile and exclude matching debit outflows from checking.
        """
        branches = []
        for table in tables:
            rule = self._rule_for(table)
            if not rule or rule.account_type != "credit":
                continue
            cols       = self._cols(table)
            credit_col = _first_match(cols, CREDIT_CANDIDATES)
            date_col   = _first_match(cols, DATE_CANDIDATES)
            desc_col   = _first_match(cols, DESC_CANDIDATES)
            if not date_col or not desc_col:
                continue

            pay_filter = self._payment_filter(rule, cols)
            if not pay_filter:
                continue

            bank_label  = table.replace("raw_", "").replace("_", " ").title()
            from services.transaction_config import load_config as _lc
            cfg_ref     = _lc()
            person_expr = self._person_expr(cols, rule, cfg_ref)

            if credit_col:
                branches.append(
                    f"    SELECT {person_expr} AS person, {_q(date_col)}::DATE AS transaction_date,"
                    f" {_q(desc_col)} AS description,"
                    f" ABS({_q(credit_col)}) AS amount, '{bank_label}'::TEXT AS bank"
                    f" FROM {self.schema}.{_q(table)}"
                    f" WHERE {self._valid_amount(credit_col)}"
                    f" AND {pay_filter}"
                )

        self._create_view("v_credit_payments", branches,
                          fallback="SELECT NULL::DATE AS transaction_date, "
                                   "NULL::TEXT AS description, "
                                   "NULL::NUMERIC AS amount, "
                                   "NULL::TEXT AS bank, "
                                   "NULL::TEXT AS person WHERE FALSE")

    # ── v_credit_spend ───────────────────────────────────────────────────────

    def _build_credit_spend_view(self, tables: list[str], rule_map: dict, cfg_cat=None) -> None:
        """
        Real purchases on credit cards.
        credit account_type → always has separate debit/credit columns.
        Spend = debit column > 0. Excludes payment rows via payment_filter.
        """
        branches = []
        for table in tables:
            rule = self._rule_for(table)
            if not rule or rule.account_type != "credit":
                continue
            cols      = self._cols(table)
            debit_col = _first_match(cols, DEBIT_CANDIDATES)
            date_col  = _first_match(cols, DATE_CANDIDATES)
            desc_col  = _first_match(cols, DESC_CANDIDATES)
            if not debit_col or not date_col or not desc_col:
                continue

            pay_filter  = self._payment_filter(rule, cols)
            bank_label  = table.replace("raw_", "").replace("_", " ").title()
            from services.transaction_config import load_config as _lc
            cfg_ref     = _lc()

            branch = (
                self._base_select(table, cols, debit_col, _q(debit_col),
                                  bank_label, rule, cfg_ref, cfg_cat)
                + f"\n      AND {_q(debit_col)} > 0"
            )
            if pay_filter:
                branch += f"\n      AND NOT {pay_filter}"
            branches.append(branch)

        self._create_view("v_credit_spend", branches, fallback="SELECT NULL::TEXT AS person, NULL::DATE AS transaction_date, NULL::TEXT AS description, NULL::NUMERIC AS amount, NULL::TEXT AS bank, NULL::TEXT AS category, NULL::TEXT AS cost_type, NULL::TEXT AS source_bank WHERE FALSE")

    # ── v_debit_spend ─────────────────────────────────────────────────────────

    def _build_debit_spend_view(self, tables: list[str], rule_map: dict, cfg, cfg_cat=None) -> None:
        """
        Checking outflows, with four layers of exclusion:
        1. User-configured transfer/zelle patterns
        2. Employer income patterns (those are income, not spend)
        3. Description patterns collected from all credit BankRules
           (payment_description fields — e.g. "CAPITAL ONE MOBILE PMT", "CITI CARD")
        4. Amount+date reconciliation against v_credit_payments (when populated)
        """
        # Collect payment description patterns from all credit rules
        all_rules = load_rules()
        credit_payment_patterns = [
            r.payment_description
            for r in all_rules
            if r.account_type == "credit" and r.payment_description
        ]

        branches = []
        for table in tables:
            rule = self._rule_for(table)
            if not rule or rule.account_type != "checking":
                continue
            cols       = self._cols(table)
            amount_col = self._amount_col(cols, rule)
            desc_col   = _first_match(cols, DESC_CANDIDATES)
            date_col   = _first_match(cols, DATE_CANDIDATES)
            if not amount_col or not desc_col or not date_col:
                continue

            bank_label = table.replace("raw_", "").replace("_", " ").title()

            # Layer 1: user transfer patterns
            transfer_excl = "\n      ".join(
                f"AND {_q(desc_col)} NOT ILIKE '%{_esc(p)}%'"
                for p in cfg.transfer_patterns
            ) if cfg.transfer_patterns else ""

            # Layer 2: employer patterns
            employer_excl = "\n      ".join(
                f"AND {_q(desc_col)} NOT ILIKE '%{_esc(p)}%'"
                for p in cfg.employer_patterns
            ) if cfg.employer_patterns else ""

            # Layer 4: two-pronged credit payment exclusion.
            #
            # A) checking_payment_pattern (explicit, per BankRule):
            #    Each credit BankRule can define what its payment looks like on
            #    the checking side. e.g. Capital One → "CAPITAL ONE", Citi → "CITI CARD".
            #    Adding a new bank = set checking_payment_pattern in its BankRule. Done.
            #
            # B) Amount+date reconciliation (fallback):
            #    Catches payments for banks that have no checking_payment_pattern yet.
            #    Window ±3 days, amounts > $50 to avoid coincidental matches.
            #
            # A row is excluded if EITHER condition matches.
            checking_desc_patterns = [
                r.checking_payment_pattern
                for r in all_rules
                if r.account_type == "credit" and r.checking_payment_pattern
            ]
            if checking_desc_patterns:
                desc_or = " OR ".join(
                    f"{_q(desc_col)} ILIKE '%{_esc(p)}%'"
                    for p in checking_desc_patterns
                )
                cond_a = f"({desc_or})"
            else:
                cond_a = "FALSE"

            t = f"{self.schema}.{_q(table)}"
            reconcile_excl = (
                f"AND {_q(amount_col)} < 0\n"
                f"      AND NOT (\n"
                f"          {cond_a}\n"
                f"          OR EXISTS (\n"
                f"              SELECT 1 FROM {self.schema}.v_credit_payments cp\n"
                f"              WHERE ABS({t}.{_q(amount_col)}) = cp.amount\n"
                f"                AND ABS({t}.{_q(amount_col)}) > 50\n"
                f"                AND {t}.{_q(date_col)}::DATE\n"
                f"                    BETWEEN cp.transaction_date - 3\n"
                f"                        AND cp.transaction_date + 3\n"
                f"          )\n"
                f"      )"
            )

            branch = self._base_select(table, cols, amount_col,
                                  f"ABS({_q(amount_col)})", bank_label, rule, cfg, cfg_cat)
            for excl in [transfer_excl, employer_excl, reconcile_excl]:
                if excl:
                    branch += f"\n      {excl}"

            branches.append(branch)

        self._create_view("v_debit_spend", branches, fallback="SELECT NULL::TEXT AS person, NULL::DATE AS transaction_date, NULL::TEXT AS description, NULL::NUMERIC AS amount, NULL::TEXT AS bank, NULL::TEXT AS category, NULL::TEXT AS cost_type, NULL::TEXT AS source_bank WHERE FALSE")

    # ── v_income ─────────────────────────────────────────────────────────────

    def _build_income_view(self, tables: list[str], rule_map: dict, cfg) -> None:
        """
        All positive inflows into checking accounts, minus transfer patterns.
        Employer patterns optionally tag rows as payroll but are not a filter gate —
        all positive non-transfer transactions are included regardless.
        """
        branches = []

        for table in tables:
            rule = self._rule_for(table)
            if not rule or rule.account_type != "checking":
                continue
            cols       = self._cols(table)
            amount_col = self._amount_col(cols, rule)
            desc_col   = _first_match(cols, DESC_CANDIDATES)
            if not amount_col or not desc_col:
                continue

            bank_label = table.replace("raw_", "").replace("_", " ").title()

            # Exclude transfer patterns (same as debit spend)
            transfer_excl = "\n      ".join(
                f"AND {_q(desc_col)} NOT ILIKE '%{_esc(p)}%'"
                for p in cfg.transfer_patterns
            ) if cfg.transfer_patterns else ""

            branch = (
                self._base_select(table, cols, amount_col, _q(amount_col), bank_label, rule, cfg)
                + f"\n      AND {_q(amount_col)} > 0"
            )
            if transfer_excl:
                branch += f"\n      {transfer_excl}"

            branches.append(branch)

        self._create_view("v_income", branches, fallback="SELECT NULL::TEXT AS person, NULL::DATE AS transaction_date, NULL::TEXT AS description, NULL::NUMERIC AS amount, NULL::TEXT AS bank WHERE FALSE")

    # ── v_all_spend ───────────────────────────────────────────────────────────

    def _build_all_spend_view(self) -> None:
        """Union of credit + debit spend with a source tag."""
        cols = "person, transaction_date, description, amount, bank, category, cost_type, source_bank"
        sql = (
            f"    SELECT {cols}, 'credit'::TEXT AS source FROM {self.schema}.v_credit_spend\n"
            f"    UNION ALL\n"
            f"    SELECT {cols}, 'debit'::TEXT  AS source FROM {self.schema}.v_debit_spend\n"
        )
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.v_all_spend CASCADE"))
            conn.execute(text(f"CREATE VIEW {self.schema}.v_all_spend AS\n{sql}"))

    # ── v_transactions (legacy) ───────────────────────────────────────────────

    def _build_legacy_transactions_view(self, tables: list[str], rule_map: dict) -> None:
        """Kept for backward compatibility — mirrors v_all_spend."""
        sql = (
            f"    SELECT person, transaction_date, description, amount, bank\n"
            f"    FROM {self.schema}.v_all_spend\n"
        )
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.v_transactions CASCADE"))
            conn.execute(text(f"CREATE VIEW {self.schema}.v_transactions AS\n{sql}"))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _create_view(self, view_name: str, branches: list[str],
                     fallback: str | None = None) -> None:
        if not branches:
            if fallback:
                body = fallback
            else:
                return
        else:
            body = "\n    UNION ALL\n".join(branches)

        with self.engine.begin() as conn:
            conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.{view_name} CASCADE"))
            conn.execute(text(f"CREATE VIEW {self.schema}.{view_name} AS\n{body}\n"))
        print(f"[ViewManager] {view_name} — {len(branches)} branch(es)")

    def _raw_tables(self) -> list[str]:
        return sorted(
            t for t in inspect(self.engine).get_table_names(schema=self.schema)
            if t.startswith("raw_")
        )

    def _ensure_schema(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self.schema}"))
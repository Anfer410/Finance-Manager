"""
view_manager.py

Builds Postgres views from the two consolidated transaction tables:

  transactions_debit   — normalised checking/savings rows (partitioned by year)
  transactions_credit  — normalised credit card rows (partitioned by year)

Views produced:
  v_credit_payments   — payment rows on credit cards (credit > 0 + payment filter)
  v_credit_spend      — purchases on credit (debit > 0, payment rows excluded)
  v_debit_spend       — checking outflows (negative amount, transfers/payments excluded)
  v_income            — checking inflows (positive amount, transfers excluded)
  v_all_spend         — UNION ALL of v_credit_spend + v_debit_spend

Because all rows are already normalised at upload time (account_key, date,
description, debit/credit or amount, person), the views are simple WHERE
filters + CASE expressions — no per-bank column sniffing needed.

Raw tables remain in the DB as archive; this file no longer touches them.
"""

from __future__ import annotations

from sqlalchemy import Engine, text
from data.bank_rules import load_rules, BankRule
from services.transaction_config import load_config
from data.category_rules import load_category_config

def _esc(s: str) -> str:
    return s.replace("'", "''")


class ViewManager:
    def __init__(self, engine: Engine , schema: str = "public"):
        self.schema     = schema
        self.engine     = engine

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Rebuild all views."""
        with self.engine.begin() as conn:
            for v in ("v_transactions", "v_all_spend",
                      "v_income", "v_debit_spend",
                      "v_credit_spend", "v_credit_payments"):
                conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.{v} CASCADE"))

        rules   = load_rules()
        cfg     = load_config()
        cfg_cat = load_category_config()

        self._build_credit_payments_view(rules)
        self._build_credit_spend_view(rules, cfg_cat)
        self._build_debit_spend_view(rules, cfg, cfg_cat)
        self._build_income_view(rules, cfg)
        self._build_all_spend_view()
        self._build_legacy_transactions_view()
        print("[ViewManager] views refreshed from consolidated tables")

    # ── Category / cost_type expressions ──────────────────────────────────────

    def _category_case_expr(self, cfg_cat) -> tuple[str, str]:
        """
        Build CASE WHEN expressions for category + cost_type against
        the 'description' column (already normalised at upload time).
        """
        cost_map = {c.name: c.cost_type for c in cfg_cat.categories}
        lines = []

        for rule in cfg_cat.sorted_rules():
            cat   = _esc(rule.category)
            ctype = _esc(cost_map.get(rule.category, "variable"))
            p     = _esc(rule.pattern)
            cond  = (
                f'description ~* \'{p}\''
                if rule.is_regex else
                f'description ILIKE \'%{p}%\''
            )
            lines.append((cond, cat, ctype))

        if lines:
            cat_cases   = "\n        ".join(f"WHEN {c} THEN '{v}'" for c, v, _ in lines)
            ctype_cases = "\n        ".join(f"WHEN {c} THEN '{t}'" for c, _, t in lines)
            cat_expr    = f"CASE {cat_cases} ELSE 'Other' END"
            ctype_expr  = f"CASE {ctype_cases} ELSE 'variable' END"
        else:
            cat_expr    = "'Other'"
            ctype_expr  = "'variable'"

        return cat_expr, ctype_expr

    # ── Payment filter ─────────────────────────────────────────────────────────

    def _payment_where(self, rule: BankRule) -> str:
        """
        WHERE fragment that is TRUE for payment rows (on credit cards).
        Uses description pattern and/or category value.
        """
        parts = []
        if rule.payment_description:
            parts.append(f"description ILIKE '%{_esc(rule.payment_description)}%'")
        if rule.payment_category:
            # category column doesn't exist in transactions_credit —
            # payment_category is the raw bank category value which is
            # only in raw tables.  We rely on description pattern here.
            # If users need category matching they should set payment_description.
            pass
        if not parts:
            return ""
        return "(" + " OR ".join(parts) + ")"

    # ── View builders ──────────────────────────────────────────────────────────

    def _build_credit_payments_view(self, rules: list[BankRule]) -> None:
        """
        Payment rows received on credit cards.
        credit > 0 AND description matches payment_description.
        """
        branches = []
        for rule in rules:
            if rule.account_type != "credit":
                continue
            pay_filter = self._payment_where(rule)
            if not pay_filter:
                continue

            ak = _esc(rule.prefix)
            branches.append(
                f"    SELECT person, transaction_date, description,\n"
                f"           credit AS amount,\n"
                f"           '{ak}'::TEXT AS bank\n"
                f"    FROM {self.schema}.transactions_credit\n"
                f"    WHERE account_key = '{ak}'\n"
                f"      AND credit > 0\n"
                f"      AND credit != 'NaN'::numeric\n"
                f"      AND {pay_filter}"
            )

        self._create_view(
            "v_credit_payments", branches,
            fallback=(
                "SELECT NULL::INTEGER[] AS person, NULL::DATE AS transaction_date, "
                "NULL::TEXT AS description, NULL::NUMERIC AS amount, "
                "NULL::TEXT AS bank WHERE FALSE"
            ),
        )

    def _build_credit_spend_view(self, rules: list[BankRule], cfg_cat) -> None:
        """
        Real purchases on credit cards (debit > 0, payment rows excluded).
        """
        cat_expr, ctype_expr = self._category_case_expr(cfg_cat)
        branches = []

        for rule in rules:
            if rule.account_type != "credit":
                continue

            ak         = _esc(rule.prefix)
            pay_filter = self._payment_where(rule)
            bank_label = rule.bank_name.replace("'", "''")

            excl = f"\n      AND NOT {pay_filter}" if pay_filter else ""
            branches.append(
                f"    SELECT person,\n"
                f"           transaction_date,\n"
                f"           description,\n"
                f"           debit AS amount,\n"
                f"           '{bank_label}'::TEXT AS bank,\n"
                f"           ({cat_expr}) AS category,\n"
                f"           ({ctype_expr}) AS cost_type,\n"
                f"           account_key AS source_bank\n"
                f"    FROM {self.schema}.transactions_credit\n"
                f"    WHERE account_key = '{ak}'\n"
                f"      AND debit > 0\n"
                f"      AND debit != 'NaN'::numeric"
                f"{excl}"
            )

        self._create_view(
            "v_credit_spend", branches,
            fallback=(
                "SELECT NULL::INTEGER[] AS person, NULL::DATE AS transaction_date, "
                "NULL::TEXT AS description, NULL::NUMERIC AS amount, "
                "NULL::TEXT AS bank, NULL::TEXT AS category, "
                "NULL::TEXT AS cost_type, NULL::TEXT AS source_bank WHERE FALSE"
            ),
        )

    def _build_debit_spend_view(
        self, rules: list[BankRule], cfg, cfg_cat
    ) -> None:
        """
        Checking outflows:
          amount < 0 (negative = outflow in checking convention)
          EXCLUDING:
            1. Transfer patterns
            2. Employer income patterns
            3. Checking-side credit payment patterns (e.g. 'CAPITAL ONE')
            4. Amount+date reconciliation against v_credit_payments
        """
        cat_expr, ctype_expr = self._category_case_expr(cfg_cat)

        # Collect checking_payment_pattern from all credit rules
        credit_rules = [r for r in rules if r.account_type == "credit"]
        checking_payment_patterns = [
            r.checking_payment_pattern
            for r in credit_rules
            if r.checking_payment_pattern
        ]

        branches = []
        for rule in rules:
            if rule.account_type != "checking":
                continue

            ak         = _esc(rule.prefix)
            bank_label = rule.bank_name.replace("'", "''")

            # Build exclusion clauses
            excls = []

            # 1. Transfer patterns
            for p in cfg.transfer_patterns:
                excls.append(f"      AND description NOT ILIKE '%{_esc(p)}%'")

            # 2. Employer patterns
            for p in cfg.employer_patterns:
                excls.append(f"      AND description NOT ILIKE '%{_esc(p)}%'")

            # 3. Checking-side credit payment patterns
            if checking_payment_patterns:
                desc_or = " OR ".join(
                    f"description ILIKE '%{_esc(p)}%'"
                    for p in checking_payment_patterns
                )
                excls.append(f"      AND NOT ({desc_or})")

            # 4. Amount+date reconciliation against v_credit_payments
            excls.append(
                f"      AND NOT EXISTS (\n"
                f"          SELECT 1 FROM {self.schema}.v_credit_payments cp\n"
                f"          WHERE ABS(t.amount) = cp.amount\n"
                f"            AND ABS(t.amount) > 50\n"
                f"            AND t.transaction_date\n"
                f"                BETWEEN cp.transaction_date - 3\n"
                f"                    AND cp.transaction_date + 3\n"
                f"      )"
            )

            excl_sql = "\n".join(excls)

            branches.append(
                f"    SELECT person,\n"
                f"           transaction_date,\n"
                f"           description,\n"
                f"           ABS(amount) AS amount,\n"
                f"           '{bank_label}'::TEXT AS bank,\n"
                f"           ({cat_expr}) AS category,\n"
                f"           ({ctype_expr}) AS cost_type,\n"
                f"           account_key AS source_bank\n"
                f"    FROM {self.schema}.transactions_debit t\n"
                f"    WHERE account_key = '{ak}'\n"
                f"      AND amount < 0\n"
                f"{excl_sql}"
            )

        self._create_view(
            "v_debit_spend", branches,
            fallback=(
                "SELECT NULL::INTEGER[] AS person, NULL::DATE AS transaction_date, "
                "NULL::TEXT AS description, NULL::NUMERIC AS amount, "
                "NULL::TEXT AS bank, NULL::TEXT AS category, "
                "NULL::TEXT AS cost_type, NULL::TEXT AS source_bank WHERE FALSE"
            ),
        )

    def _build_income_view(self, rules: list[BankRule], cfg) -> None:
        """Positive checking inflows, transfer patterns excluded."""
        branches = []
        for rule in rules:
            if rule.account_type != "checking":
                continue

            ak         = _esc(rule.prefix)
            bank_label = rule.bank_name.replace("'", "''")

            transfer_excl = "\n".join(
                f"      AND description NOT ILIKE '%{_esc(p)}%'"
                for p in cfg.transfer_patterns
            )

            branches.append(
                f"    SELECT person,\n"
                f"           transaction_date,\n"
                f"           description,\n"
                f"           amount,\n"
                f"           '{bank_label}'::TEXT AS bank\n"
                f"    FROM {self.schema}.transactions_debit\n"
                f"    WHERE account_key = '{ak}'\n"
                f"      AND amount > 0\n"
                + (f"\n{transfer_excl}" if transfer_excl else "")
            )

        self._create_view(
            "v_income", branches,
            fallback=(
                "SELECT NULL::INTEGER[] AS person, NULL::DATE AS transaction_date, "
                "NULL::TEXT AS description, NULL::NUMERIC AS amount, "
                "NULL::TEXT AS bank WHERE FALSE"
            ),
        )

    def _build_all_spend_view(self) -> None:
        cols = "person, transaction_date, description, amount, bank, category, cost_type, source_bank"
        sql  = (
            f"    SELECT {cols}, 'credit'::TEXT AS source FROM {self.schema}.v_credit_spend\n"
            f"    UNION ALL\n"
            f"    SELECT {cols}, 'debit'::TEXT  AS source FROM {self.schema}.v_debit_spend\n"
        )
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.v_all_spend CASCADE"))
            conn.execute(text(f"CREATE VIEW {self.schema}.v_all_spend AS\n{sql}"))

    def _build_legacy_transactions_view(self) -> None:
        sql = (
            f"    SELECT person, transaction_date, description, amount, bank\n"
            f"    FROM {self.schema}.v_all_spend\n"
        )
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.v_transactions CASCADE"))
            conn.execute(text(f"CREATE VIEW {self.schema}.v_transactions AS\n{sql}"))

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _create_view(self, name: str, branches: list[str], fallback: str | None = None) -> None:
        body = ("\n    UNION ALL\n".join(branches)) if branches else (fallback or "")
        if not body:
            return
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.{name} CASCADE"))
            conn.execute(text(f"CREATE VIEW {self.schema}.{name} AS\n{body}\n"))
        print(f"[ViewManager] {name} — {len(branches)} branch(es)")

    def _ensure_schema(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self.schema}"))


def default_view_manager(schema: str | None = None) -> ViewManager:
    from data.db import get_conn_tuple, get_schema
    return ViewManager(get_conn_tuple(), schema=schema or get_schema())
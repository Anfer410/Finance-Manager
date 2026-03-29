"""
view_manager.py

Builds Postgres views from the two consolidated transaction tables:

  transactions_debit   — normalised checking/savings rows (partitioned by year)
  transactions_credit  — normalised credit card rows (partitioned by year)

Views produced:
  v_credit_spend      — purchases on credit (debit > 0, payment rows excluded)
  v_debit_spend       — checking outflows (negative amount, employer patterns + flags excluded)
  v_income            — checking inflows (positive amount, transfer patterns excluded)
  v_all_spend         — UNION ALL of v_credit_spend + v_debit_spend

Multi-family design
───────────────────
Views are global SQL objects that cover ALL families in one pass.  Each
account_key branch uses that family's own category rules and transaction
config, so categorisation is always correct per-family.

Call ViewManager.refresh() — no family_id argument — after any change that
affects view output: bank rule edits, category edits, person reassignment,
upload, delete, or import.  The method loads every family that has bank
rules configured and rebuilds the combined views in one transaction.

Raw tables remain in the DB as archive; this file no longer touches them.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text

from data.bank_rules import load_rules, BankRule
from services.transaction_config import load_config, TransactionConfig
from data.category_rules import load_category_config, CategoryConfig


def _esc(s: str) -> str:
    return s.replace("'", "''")


@dataclass
class _FamilyViewData:
    """All config needed to build view branches for one family."""
    family_id: int
    rules:     list[BankRule]
    cfg:       TransactionConfig
    cfg_cat:   CategoryConfig


class ViewManager:
    def __init__(self, engine: Engine, schema: str = "public"):
        self.schema = schema
        self.engine = engine

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """
        Rebuild all views covering every family that has bank rules configured.

        Loads rules + configs for all families in one pass so every family's
        account_keys appear in the view UNION ALL branches, with per-family
        category expressions.  No family_id argument needed.
        """
        family_data = self._load_all_family_data()

        with self.engine.begin() as conn:
            for v in ("v_transactions", "v_all_spend",
                      "v_income", "v_debit_spend",
                      "v_credit_spend", "v_credit_payments"):
                conn.execute(text(f"DROP VIEW IF EXISTS {self.schema}.{v} CASCADE"))

        self._build_credit_spend_view(family_data)
        self._build_debit_spend_view(family_data)
        self._build_income_view(family_data)
        self._build_all_spend_view()
        self._build_legacy_transactions_view()

        fam_count   = len(family_data)
        branch_count = sum(len(fd.rules) for fd in family_data)
        print(f"[ViewManager] views refreshed — {fam_count} familie(s), {branch_count} account_key(s)")

    # ── Internal data loading ──────────────────────────────────────────────────

    def _load_all_family_ids(self) -> list[int]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT id FROM {self.schema}.families ORDER BY id")
            ).fetchall()
        return [r[0] for r in rows]

    def _load_all_family_data(self) -> list[_FamilyViewData]:
        """Load rules + configs for every family that has bank rules configured."""
        result: list[_FamilyViewData] = []
        for fid in self._load_all_family_ids():
            rules = load_rules(fid)
            if not rules:
                continue  # no bank rules → nothing to put in views
            result.append(_FamilyViewData(
                family_id = fid,
                rules     = rules,
                cfg       = load_config(fid),
                cfg_cat   = load_category_config(fid),
            ))
        return result

    # ── Category / cost_type expressions ──────────────────────────────────────

    def _category_case_expr(self, cfg_cat: CategoryConfig) -> tuple[str, str]:
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
        if not parts:
            return ""
        return "(" + " OR ".join(parts) + ")"

    # ── View builders ──────────────────────────────────────────────────────────

    def _build_credit_spend_view(self, family_data: list[_FamilyViewData]) -> None:
        """
        Real purchases on credit cards (debit > 0, payment rows excluded).
        Each family's branches use that family's category rules.
        """
        branches = []
        for fd in family_data:
            cat_expr, ctype_expr = self._category_case_expr(fd.cfg_cat)
            for rule in fd.rules:
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
                    f"           account_key AS source_bank,\n"
                    f"           family_id,\n"
                    f"           currency\n"
                    f"    FROM {self.schema}.transactions_credit\n"
                    f"    WHERE account_key = '{ak}'\n"
                    f"      AND family_id = {fd.family_id}\n"
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
                "NULL::TEXT AS cost_type, NULL::TEXT AS source_bank, "
                "NULL::INTEGER AS family_id, NULL::TEXT AS currency WHERE FALSE"
            ),
        )

    def _build_debit_spend_view(self, family_data: list[_FamilyViewData]) -> None:
        """
        Checking outflows (amount < 0), per-family exclusions:
          1. Employer income patterns (payroll excluded from spend)
          2. Named transfer exclusions (user-confirmed external account patterns)
          3. Automated flags: internal_transfer, credit_payment, potential_transfer
        """
        branches = []
        for fd in family_data:
            cat_expr, ctype_expr = self._category_case_expr(fd.cfg_cat)

            for rule in fd.rules:
                if rule.account_type != "checking":
                    continue

                ak         = _esc(rule.prefix)
                bank_label = rule.bank_name.replace("'", "''")

                excls = []

                # 1. Employer patterns
                for p in fd.cfg.employer_pattern_strings:
                    excls.append(f"      AND description NOT ILIKE '%{_esc(p)}%'")

                # 2. Named transfer exclusions — user-confirmed external accounts.
                #    These are always excluded regardless of flag state, because the
                #    user has explicitly named them as non-spend destinations.
                for p in fd.cfg.named_exclusion_patterns:
                    excls.append(f"      AND description NOT ILIKE '%{_esc(p)}%'")

                # 3. Automated flags (includes potential_transfer pending user review)
                excls.append(
                    f"      AND t.id NOT IN (\n"
                    f"          SELECT tx_id FROM {self.schema}.transaction_flags\n"
                    f"          WHERE flag_type IN ('internal_transfer', 'credit_payment', 'potential_transfer')\n"
                    f"            AND tx_table  = 'debit'\n"
                    f"            AND family_id = {fd.family_id}\n"
                    f"            AND NOT user_kept\n"
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
                    f"           account_key AS source_bank,\n"
                    f"           t.family_id,\n"
                    f"           t.currency\n"
                    f"    FROM {self.schema}.transactions_debit t\n"
                    f"    WHERE account_key = '{ak}'\n"
                    f"      AND t.family_id = {fd.family_id}\n"
                    f"      AND amount < 0\n"
                    f"{excl_sql}"
                )

        self._create_view(
            "v_debit_spend", branches,
            fallback=(
                "SELECT NULL::INTEGER[] AS person, NULL::DATE AS transaction_date, "
                "NULL::TEXT AS description, NULL::NUMERIC AS amount, "
                "NULL::TEXT AS bank, NULL::TEXT AS category, "
                "NULL::TEXT AS cost_type, NULL::TEXT AS source_bank, "
                "NULL::INTEGER AS family_id, NULL::TEXT AS currency WHERE FALSE"
            ),
        )

    def _build_income_view(self, family_data: list[_FamilyViewData]) -> None:
        """Positive checking inflows, each family's transfer patterns excluded."""
        branches = []
        for fd in family_data:
            for rule in fd.rules:
                if rule.account_type != "checking":
                    continue

                ak         = _esc(rule.prefix)
                bank_label = rule.bank_name.replace("'", "''")

                transfer_excl = "\n".join(
                    f"      AND description NOT ILIKE '%{_esc(p)}%'"
                    for p in fd.cfg.transfer_patterns
                )

                flag_excl = (
                    f"      AND id NOT IN (\n"
                    f"          SELECT tx_id FROM {self.schema}.transaction_flags\n"
                    f"          WHERE flag_type = 'internal_transfer'\n"
                    f"            AND tx_table  = 'debit'\n"
                    f"            AND family_id = {fd.family_id}\n"
                    f"            AND NOT user_kept\n"
                    f"      )"
                )

                branches.append(
                    f"    SELECT person,\n"
                    f"           transaction_date,\n"
                    f"           description,\n"
                    f"           amount,\n"
                    f"           '{bank_label}'::TEXT AS bank,\n"
                    f"           family_id,\n"
                    f"           currency\n"
                    f"    FROM {self.schema}.transactions_debit\n"
                    f"    WHERE account_key = '{ak}'\n"
                    f"      AND family_id = {fd.family_id}\n"
                    f"      AND amount > 0\n"
                    + (f"\n{transfer_excl}" if transfer_excl else "")
                    + f"\n{flag_excl}"
                )

        self._create_view(
            "v_income", branches,
            fallback=(
                "SELECT NULL::INTEGER[] AS person, NULL::DATE AS transaction_date, "
                "NULL::TEXT AS description, NULL::NUMERIC AS amount, "
                "NULL::TEXT AS bank, NULL::INTEGER AS family_id, "
                "NULL::TEXT AS currency WHERE FALSE"
            ),
        )

    def _build_all_spend_view(self) -> None:
        cols = "person, transaction_date, description, amount, bank, category, cost_type, source_bank, family_id, currency"
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
    from data.db import get_engine, get_schema
    return ViewManager(get_engine(), schema=schema or get_schema())

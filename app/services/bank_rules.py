"""
Bank detection rules engine.
Rules are stored as dicts (easily serializable to JSON/DB) and matched dynamically.
"""

from dataclasses import dataclass, field, asdict
from typing import Literal, Optional
import json
from pathlib import Path
from datetime import datetime


MatchField  = Literal["filename"]
MatchType   = Literal["contains", "startswith", "endswith", "exact"]
AccountType = Literal["checking", "credit"]


@dataclass
class BankRule:
    """A single detection rule for one bank."""
    bank_name:   str
    prefix:      str
    match_field: MatchField  = "filename"
    match_type:  MatchType   = "contains"
    match_value: str         = ""

    # ── Account classification ─────────────────────────────────────────────
    account_type:        AccountType = "checking"
    payment_category:    str         = ""   # e.g. "Payment/Credit"
    payment_description: str         = ""   # e.g. "ONLINE PAYMENT"
    # ── Credit payment checking-side pattern ─────────────────────────────
    # When this credit card payment appears as an outflow in checking,
    # what substring appears in the checking description?
    # e.g. Capital One → "CAPITAL ONE", Citi → "CITI CARD"
    # Used by view_manager to exclude these rows from v_debit_spend
    # without relying solely on amount+date matching.
    checking_payment_pattern: str = ""

    # ── Member name resolution ────────────────────────────────────────────
    # If the bank stores member/cardholder name in a non-standard column,
    # set this to that column name. The global member_aliases map in
    # TransactionConfig will be used to resolve it to a person alias.
    # e.g. "member_name" for Citi
    member_name_column: str = ""

    # ── Filename config ────────────────────────────────────────────────────
    person_override: Optional[str] = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "BankRule":
        known = {f for f in BankRule.__dataclass_fields__}
        return BankRule(**{k: v for k, v in d.items() if k in known})


# ── Default rules ─────────────────────────────────────────────────────────────

DEFAULT_RULES: list[BankRule] = [
    BankRule(
        bank_name="Capital One",
        prefix="cap1",
        match_type="contains",
        match_value="transaction_download",
        account_type="credit",
        payment_category="Payment/Credit",
        payment_description="MOBILE PYMT",
        checking_payment_pattern="CAPITAL ONE",
        note="e.g. 2024-01-15_transaction_download.csv",
    ),
    BankRule(
        bank_name="Wells Fargo Checking",
        prefix="wf",
        match_type="contains",
        match_value="Checking",
        account_type="checking",
        note="e.g. Checking1234.csv",
    ),
    BankRule(
        bank_name="Wells Fargo Savings",
        prefix="wf",
        match_type="contains",
        match_value="Savings",
        account_type="checking",
        person_override="mutual",
        note="e.g. Savings5678.csv",
    ),
    BankRule(
        bank_name="Citi",
        prefix="citi",
        match_type="contains",
        match_value="citi",
        account_type="credit",
        payment_description="ONLINE PAYMENT",
        checking_payment_pattern="CITI CARD",
        member_name_column="member_name",
        person_override="",
        note="e.g. Citi_export.csv",
    ),
]


# ── Persistent rule store ─────────────────────────────────────────────────────

RULES_FILE = Path("bank_rules_config.json")  # kept for fallback only


def load_rules() -> list[BankRule]:
    try:
        from services.db_config import load_bank_rules_data
        raw = load_bank_rules_data()
        if raw:
            rules = [BankRule.from_dict(r) for r in raw]
            default_by_name = {r.bank_name: r for r in DEFAULT_RULES}
            for rule in rules:
                default = default_by_name.get(rule.bank_name)
                if not default:
                    continue
                if not rule.account_type:
                    rule.account_type = default.account_type
                if not rule.payment_description:
                    rule.payment_description = default.payment_description
                if not rule.payment_category:
                    rule.payment_category = default.payment_category
            return rules
    except Exception as e:
        print(f"[bank_rules] DB load failed ({e}), falling back to file/defaults")

    if RULES_FILE.exists():
        data = json.loads(RULES_FILE.read_text())
        return [BankRule.from_dict(r) for r in data]
    return list(DEFAULT_RULES)


def save_rules(rules: list[BankRule]) -> None:
    try:
        from services.db_config import save_bank_rules_data
        save_bank_rules_data([r.to_dict() for r in rules])
        return
    except Exception as e:
        print(f"[bank_rules] DB save failed ({e}), falling back to file")
    RULES_FILE.write_text(json.dumps([r.to_dict() for r in rules], indent=2))


# ── Matcher ───────────────────────────────────────────────────────────────────

class RuleMatcher:
    def __init__(self, rules: Optional[list[BankRule]] = None):
        self._rules = rules

    def _get_rules(self) -> list[BankRule]:
        return self._rules if self._rules is not None else load_rules()

    def _matches(self, rule: BankRule, filename: str) -> bool:
        v, p = filename.lower(), rule.match_value.lower()
        if rule.match_type == "contains":   return p in v
        if rule.match_type == "startswith": return v.startswith(p)
        if rule.match_type == "endswith":   return v.endswith(p)
        if rule.match_type == "exact":      return v == p
        return False

    def match(self, filename: str, person: str) -> Optional[tuple[str, str, str]]:
        """Returns (bank_name, output_filename, resolved_person) or None."""
        for rule in self._get_rules():
            if self._matches(rule, filename):
                date       = datetime.now().strftime("%Y%m%d")
                person_seg = rule.person_override if rule.person_override is not None else person
                parts      = [rule.prefix, date] + ([person_seg] if person_seg else [])
                return rule.bank_name, "_".join(parts) + ".csv", person_seg
        return None


_matcher = RuleMatcher()
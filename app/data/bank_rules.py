"""
Bank detection rules engine.
Rules are stored as dicts (easily serializable to JSON/DB) and matched dynamically.
"""

from dataclasses import dataclass, field, asdict
from typing import Literal, Optional
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
    # Optional safety filter for credit cards whose CSV formats payment rows
    # as debit > 0 rather than credit > 0.  Used by view_manager to exclude
    # those rows from v_credit_spend.  Leave blank for standard bank CSVs.
    payment_description: str         = ""   # e.g. "ONLINE PAYMENT"

    # ── Member name resolution ────────────────────────────────────────────
    # If the bank stores member/cardholder name in a non-standard column,
    # set this to that column name. The global member_aliases map in
    # TransactionConfig will be used to resolve it to a person alias.
    # e.g. "member_name" for Citi
    member_name_column: str = ""

    # ── Per-rule member name aliases ─────────────────────────────────────────
    # Maps raw member_name value → user_id (stable across display_name changes).
    # e.g. {"JOHN": 1, "ANNA": 2}
    # Resolved to user IDs at upload time via app_users table.
    member_aliases: dict = field(default_factory=dict)

    # ── Currency ───────────────────────────────────────────────────────────
    # ISO 4217 code for the currency of this account, e.g. "PLN", "USD", "EUR".
    # Empty string = not configured (single-currency families can leave this unset).
    currency: str = ""

    # ── Date format ────────────────────────────────────────────────────────
    # strptime format string for the date column, e.g. "%Y-%m-%d", "%d/%m/%Y".
    # Empty string = auto-detect (pandas inference with dayfirst=False).
    date_format: str = ""

    # ── Column mapping (set by wizard, used by UploadPipeline) ──────────────
    # Maps logical role → actual normalised column name in this bank's CSV.
    # e.g. {"date": "trans_date", "amount": "transaction_amount"}
    column_map:    dict = field(default_factory=dict)
    # Explicit dedup columns stored after wizard; [] means auto-detect
    dedup_columns: list = field(default_factory=list)

    # ── Filename config ────────────────────────────────────────────────────
    # List of user IDs. For a single-person account use [user_id].
    # For shared/mutual accounts list all owner IDs, e.g. [1, 2].
    person_override: Optional[list] = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "BankRule":
        known = {f for f in BankRule.__dataclass_fields__}
        return BankRule(**{k: v for k, v in d.items() if k in known})


# ── Default rules ─────────────────────────────────────────────────────────────

DEFAULT_RULES: list[BankRule] = []


# ── Persistent rule store ─────────────────────────────────────────────────────
def load_rules(family_id: int) -> list[BankRule]:
    try:
        from services.config_repo import load_bank_rules
        raw = load_bank_rules(family_id)
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
            return rules
    except Exception as e:
        print(f"[bank_rules] DB load failed ({e})")
    return []


def save_rules(rules: list[BankRule], family_id: int) -> None:
    try:
        from services.config_repo import save_bank_rules
        save_bank_rules([r.to_dict() for r in rules], family_id)
        return
    except Exception as e:
        print(f"[bank_rules] DB save failed ({e})")
    


# ── Matcher ───────────────────────────────────────────────────────────────────

class RuleMatcher:
    def __init__(self, rules: list[BankRule]):
        self._rules = rules

    def _get_rules(self) -> list[BankRule]:
        return self._rules

    def _matches(self, rule: BankRule, filename: str) -> bool:
        v, p = filename.lower(), rule.match_value.lower()
        if rule.match_type == "contains":   return p in v
        if rule.match_type == "startswith": return v.startswith(p)
        if rule.match_type == "endswith":   return v.endswith(p)
        if rule.match_type == "exact":      return v == p
        return False

    def match(self, filename: str, person: int) -> Optional[tuple[BankRule, str, list[int] | int]]:
        """Returns (matched_rule, output_filename, resolved_person) or None.
        resolved_person is list[int] (from person_override) or int (the caller's user ID)."""
        for rule in self._get_rules():
            if self._matches(rule, filename):
                date        = datetime.now().strftime("%Y%m%d")
                person_seg  = rule.person_override if rule.person_override is not None else person
                person_part = str(person_seg[0] if isinstance(person_seg, list) else person_seg)
                parts       = [rule.prefix, date, person_part]
                return rule, "_".join(parts) + ".csv", person_seg
        return None



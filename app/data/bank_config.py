"""
data/bank_config.py — Bank entity (name, slug, transfer patterns).

A Bank groups one or more BankRule accounts (e.g. "Capital One" → Checking, Savings).
Transfer patterns are bank-wide: they apply to every account under the bank.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


@dataclass
class BankConfig:
    name: str
    slug: str
    # Patterns that identify inter-account transfers — applied to all accounts
    # under this bank.  Transactions matching any pattern are excluded from
    # spend/income totals in view_manager.
    transfer_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "BankConfig":
        known = {f for f in BankConfig.__dataclass_fields__}
        return BankConfig(**{k: v for k, v in d.items() if k in known})

    @staticmethod
    def from_name(name: str) -> "BankConfig":
        return BankConfig(name=name, slug=_slugify(name))


def load_banks(family_id: int) -> list[BankConfig]:
    try:
        from services.config_repo import load_banks as _load
        raw = _load(family_id)
        if raw:
            return [BankConfig.from_dict(b) for b in raw]
    except Exception as e:
        print(f"[bank_config] load failed ({e})")
    return []


def save_banks(banks: list[BankConfig], family_id: int) -> None:
    try:
        from services.config_repo import save_banks as _save
        _save([b.to_dict() for b in banks], family_id)
    except Exception as e:
        print(f"[bank_config] save failed ({e})")

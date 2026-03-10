"""
services/transaction_config.py
"""

from __future__ import annotations
import json
from pathlib import Path
from dataclasses import dataclass, asdict, field


CONFIG_FILE = Path("transaction_config.json")


@dataclass
class TransactionConfig:
    # Inter-account transfer exclusions
    transfer_patterns: list[str] = field(default_factory=lambda: [
        "ONLINE PAYMENT",
        "AUTOPAY",
        "AUTOMATIC PAYMENT",
        "TRANSFER",
        "ZELLE",
        "CAPITAL ONE MOBILE PMT",
        "CAPITAL ONE ONLINE PMT",
        "CITI CARD",
    ])

    # Employer / payroll description patterns
    employer_patterns: list[str] = field(default_factory=list)

    # Global member name → person alias map.
    # Used by view_manager to resolve person from member_name columns.
    # Key: substring to match in the member_name column (case-insensitive)
    # Value: person alias (e.g. "andy", "jess")
    # Example: {"ANDRZEJ": "andy", "JESSICA": "jess"}
    member_aliases: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TransactionConfig":
        known = {f for f in TransactionConfig.__dataclass_fields__}
        return TransactionConfig(**{k: v for k, v in d.items() if k in known})


def load_config() -> TransactionConfig:
    try:
        from services.db_config import load_transaction_config_data
        data = load_transaction_config_data()
        if data:
            return TransactionConfig.from_dict(data)
    except Exception as e:
        print(f"[transaction_config] DB load failed ({e}), falling back to file/defaults")

    if CONFIG_FILE.exists():
        return TransactionConfig.from_dict(json.loads(CONFIG_FILE.read_text()))
    return TransactionConfig()


def save_config(cfg: TransactionConfig) -> None:
    try:
        from services.db_config import save_transaction_config_data
        save_transaction_config_data(cfg.to_dict())
        return
    except Exception as e:
        print(f"[transaction_config] DB save failed ({e}), falling back to file")
    CONFIG_FILE.write_text(json.dumps(cfg.to_dict(), indent=2))
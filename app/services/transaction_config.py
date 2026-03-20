"""
services/transaction_config.py
"""

from __future__ import annotations
import json
from pathlib import Path
from dataclasses import dataclass, asdict, field


CONFIG_FILE = Path("transaction_config.json")


@dataclass
class EmployerPattern:
    pattern:  str
    added_by: int | None = None   # None = added by a Family Head (protected from members)


@dataclass
class TransactionConfig:
    # Inter-account transfer exclusions
    transfer_patterns: list[str] = field(default_factory=list)

    # Employer / payroll description patterns — each carries who added it.
    # added_by=None  → Family Head entry, members cannot edit/remove it.
    # added_by=<id>  → Member-owned entry, only that member (or a Head) can remove it.
    employer_patterns: list[EmployerPattern] = field(default_factory=list)

    # Global member name → person alias map.
    member_aliases: dict[str, str] = field(default_factory=dict)

    @property
    def employer_pattern_strings(self) -> list[str]:
        """Plain list of pattern strings — used by view_manager and dashboard queries."""
        return [ep.pattern for ep in self.employer_patterns]

    def to_dict(self) -> dict:
        return {
            "transfer_patterns": self.transfer_patterns,
            "employer_patterns": [
                {"pattern": ep.pattern, "added_by": ep.added_by}
                for ep in self.employer_patterns
            ],
            "member_aliases": self.member_aliases,
        }

    @staticmethod
    def from_dict(d: dict) -> "TransactionConfig":
        raw_ep = d.get("employer_patterns", [])
        employer_patterns = []
        for item in raw_ep:
            if isinstance(item, str):
                # Backward compat: plain string → Head-owned
                employer_patterns.append(EmployerPattern(pattern=item, added_by=None))
            elif isinstance(item, dict):
                employer_patterns.append(EmployerPattern(
                    pattern=item.get("pattern", ""),
                    added_by=item.get("added_by"),
                ))
        return TransactionConfig(
            transfer_patterns=d.get("transfer_patterns", []),
            employer_patterns=employer_patterns,
            member_aliases=d.get("member_aliases", {}),
        )


def load_config(family_id: int) -> TransactionConfig:
    try:
        from services.config_repo import load_transaction_cfg
        data = load_transaction_cfg(family_id)
        if data:
            return TransactionConfig.from_dict(data)
    except Exception as e:
        print(f"[transaction_config] DB load failed ({e}), falling back to file/defaults")

    if CONFIG_FILE.exists():
        return TransactionConfig.from_dict(json.loads(CONFIG_FILE.read_text()))
    return TransactionConfig()


def save_config(cfg: TransactionConfig, family_id: int) -> None:
    try:
        from services.config_repo import save_transaction_cfg
        save_transaction_cfg(cfg.to_dict(), family_id)
        return
    except Exception as e:
        print(f"[transaction_config] DB save failed ({e}), falling back to file")
    CONFIG_FILE.write_text(json.dumps(cfg.to_dict(), indent=2))
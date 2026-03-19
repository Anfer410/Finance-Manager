"""
tests/test_config_repo.py

Integration tests for services/config_repo.py — family-scoped config storage.

Covers:
  - load / save bank_rules
  - load / save banks
  - load / save categories
  - load / save transaction_cfg
  - load / save app_settings (instance-wide)
  - Family isolation: family_id=1 data doesn't leak to family_id=2
  - Default seeding: load_categories returns defaults when no DB row
"""
import pytest
from sqlalchemy import text


# ── Helpers ───────────────────────────────────────────────────────────────────

def _repo(pg_engine):
    """Return a fresh config_repo module bound to the test engine."""
    import importlib
    import sys
    import importlib.util
    from pathlib import Path

    repo_path = Path(__file__).parent.parent / "app" / "services" / "config_repo.py"
    spec = importlib.util.spec_from_file_location("services.config_repo", repo_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Bank rules ────────────────────────────────────────────────────────────────

class TestBankRules:
    def test_load_empty_returns_empty_list(self, pg_engine):
        repo = _repo(pg_engine)
        result = repo.load_bank_rules(family_id=99)
        assert result == []

    def test_save_and_load_roundtrip(self, pg_engine):
        repo = _repo(pg_engine)
        rules = [
            {"bank_name": "TestBank", "prefix": "tb_checking",
             "match_type": "contains", "match_value": "testbank",
             "account_type": "checking"},
        ]
        repo.save_bank_rules(rules, family_id=1)
        loaded = repo.load_bank_rules(family_id=1)
        assert len(loaded) == 1
        assert loaded[0]["bank_name"] == "TestBank"
        assert loaded[0]["prefix"] == "tb_checking"

    def test_family_isolation(self, pg_engine):
        repo = _repo(pg_engine)
        rules_f1 = [{"bank_name": "FamilyOne", "prefix": "f1_check"}]
        rules_f2 = [{"bank_name": "FamilyTwo", "prefix": "f2_check"}]
        repo.save_bank_rules(rules_f1, family_id=1)
        repo.save_bank_rules(rules_f2, family_id=2)

        loaded_f1 = repo.load_bank_rules(family_id=1)
        loaded_f2 = repo.load_bank_rules(family_id=2)

        assert any(r["bank_name"] == "FamilyOne" for r in loaded_f1)
        assert not any(r["bank_name"] == "FamilyTwo" for r in loaded_f1)
        assert any(r["bank_name"] == "FamilyTwo" for r in loaded_f2)
        assert not any(r["bank_name"] == "FamilyOne" for r in loaded_f2)

    def test_upsert_overwrites(self, pg_engine):
        repo = _repo(pg_engine)
        repo.save_bank_rules([{"bank_name": "Old"}], family_id=1)
        repo.save_bank_rules([{"bank_name": "New"}], family_id=1)
        loaded = repo.load_bank_rules(family_id=1)
        assert len(loaded) == 1
        assert loaded[0]["bank_name"] == "New"


# ── Banks ─────────────────────────────────────────────────────────────────────

class TestBanks:
    def test_load_empty_returns_empty_list(self, pg_engine):
        repo = _repo(pg_engine)
        result = repo.load_banks(family_id=99)
        assert result == []

    def test_save_and_load_roundtrip(self, pg_engine):
        repo = _repo(pg_engine)
        banks = [
            {"name": "Wells Fargo", "slug": "wells_fargo", "transfer_patterns": ["TRANSFER"]},
        ]
        repo.save_banks(banks, family_id=1)
        loaded = repo.load_banks(family_id=1)
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Wells Fargo"
        assert loaded[0]["transfer_patterns"] == ["TRANSFER"]

    def test_family_isolation(self, pg_engine):
        repo = _repo(pg_engine)
        repo.save_banks([{"name": "BankA", "slug": "bank_a"}], family_id=1)
        repo.save_banks([{"name": "BankB", "slug": "bank_b"}], family_id=2)

        assert any(b["name"] == "BankA" for b in repo.load_banks(family_id=1))
        assert not any(b["name"] == "BankB" for b in repo.load_banks(family_id=1))
        assert any(b["name"] == "BankB" for b in repo.load_banks(family_id=2))


# ── Categories ────────────────────────────────────────────────────────────────

class TestCategories:
    def test_load_returns_defaults_when_no_row(self, pg_engine):
        repo = _repo(pg_engine)
        # family_id=99 has no row — should return defaults
        data = repo.load_categories(family_id=99)
        assert "categories" in data
        assert "rules" in data
        assert len(data["categories"]) > 0
        assert any(c["name"] == "Groceries" for c in data["categories"])

    def test_save_and_load_roundtrip(self, pg_engine):
        repo = _repo(pg_engine)
        payload = {
            "categories": [
                {"name": "Food", "cost_type": "variable", "color": "#ff0000"},
                {"name": "Rent", "cost_type": "fixed", "color": "#0000ff"},
            ],
            "rules": [
                {"pattern": "KROGER", "is_regex": False, "category": "Food", "priority": 10},
            ],
        }
        repo.save_categories(payload, family_id=1)
        loaded = repo.load_categories(family_id=1)
        assert len(loaded["categories"]) == 2
        assert loaded["categories"][0]["name"] == "Food"
        assert len(loaded["rules"]) == 1
        assert loaded["rules"][0]["pattern"] == "KROGER"

    def test_family_isolation(self, pg_engine):
        repo = _repo(pg_engine)
        repo.save_categories(
            {"categories": [{"name": "F1Cat"}], "rules": []}, family_id=1
        )
        repo.save_categories(
            {"categories": [{"name": "F2Cat"}], "rules": []}, family_id=2
        )
        f1 = repo.load_categories(family_id=1)
        f2 = repo.load_categories(family_id=2)
        assert any(c["name"] == "F1Cat" for c in f1["categories"])
        assert not any(c["name"] == "F2Cat" for c in f1["categories"])
        assert any(c["name"] == "F2Cat" for c in f2["categories"])


# ── Transaction config ────────────────────────────────────────────────────────

class TestTransactionConfig:
    def test_load_returns_defaults_when_no_row(self, pg_engine):
        repo = _repo(pg_engine)
        data = repo.load_transaction_cfg(family_id=99)
        assert "transfer_patterns" in data
        assert isinstance(data["transfer_patterns"], list)

    def test_save_and_load_roundtrip(self, pg_engine):
        repo = _repo(pg_engine)
        payload = {
            "transfer_patterns": ["ZELLE", "TRANSFER"],
            "employer_patterns": ["ACME CORP"],
            "member_aliases": {"JOHN": "andy"},
        }
        repo.save_transaction_cfg(payload, family_id=1)
        loaded = repo.load_transaction_cfg(family_id=1)
        assert "ZELLE" in loaded["transfer_patterns"]
        assert loaded["employer_patterns"] == ["ACME CORP"]
        assert loaded["member_aliases"]["JOHN"] == "andy"

    def test_family_isolation(self, pg_engine):
        repo = _repo(pg_engine)
        repo.save_transaction_cfg(
            {"transfer_patterns": ["F1_TRANSFER"], "employer_patterns": [], "member_aliases": {}},
            family_id=1
        )
        repo.save_transaction_cfg(
            {"transfer_patterns": ["F2_TRANSFER"], "employer_patterns": [], "member_aliases": {}},
            family_id=2
        )
        f1 = repo.load_transaction_cfg(family_id=1)
        f2 = repo.load_transaction_cfg(family_id=2)
        assert "F1_TRANSFER" in f1["transfer_patterns"]
        assert "F2_TRANSFER" not in f1["transfer_patterns"]
        assert "F2_TRANSFER" in f2["transfer_patterns"]


# ── App settings (instance-wide) ─────────────────────────────────────────────

class TestAppSettings:
    def test_load_empty_returns_dict(self, pg_engine):
        repo = _repo(pg_engine)
        data = repo.load_app_settings()
        assert isinstance(data, dict)

    def test_save_and_load_roundtrip(self, pg_engine):
        repo = _repo(pg_engine)
        repo.save_app_settings({"smtp_host": "mail.example.com", "smtp_port": 587})
        loaded = repo.load_app_settings()
        assert loaded["smtp_host"] == "mail.example.com"
        assert loaded["smtp_port"] == 587

    def test_patch_updates_individual_keys(self, pg_engine):
        repo = _repo(pg_engine)
        repo.save_app_settings({"key_a": "old_value", "key_b": "unchanged"})
        repo.patch_app_settings(key_a="new_value")
        loaded = repo.load_app_settings()
        assert loaded["key_a"] == "new_value"
        assert loaded["key_b"] == "unchanged"

"""
category_rules.py

Manages spend categories and pattern-based classification rules.

Storage: category_rules.json
  {
    "categories": [
      {"name": "Groceries", "cost_type": "variable", "color": "#4ade80"},
      ...
    ],
    "rules": [
      {"pattern": "KROGER", "is_regex": false, "category": "Groceries", "priority": 10},
      ...
    ]
  }

Resolution at view time: view_manager builds a CASE WHEN SQL expression.
User rules (ordered by priority asc = higher priority first) are checked before
the bank's own category column. Falls back to "Other" if nothing matches.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Literal

CostType = Literal["fixed", "variable"]

RULES_FILE = Path("category_rules.json")

# ── Default categories ────────────────────────────────────────────────────────

DEFAULT_CATEGORIES: list[dict] = [
    {"name": "Groceries",       "cost_type": "variable", "color": "#4ade80"},
    {"name": "Restaurants",     "cost_type": "variable", "color": "#fb923c"},
    {"name": "Gas/Automotive",  "cost_type": "variable", "color": "#f87171"},
    {"name": "Rideshare",       "cost_type": "variable", "color": "#e879f9"},
    {"name": "Health",          "cost_type": "variable", "color": "#34d399"},
    {"name": "Merchandise",     "cost_type": "variable", "color": "#a78bfa"},
    {"name": "Lodging",         "cost_type": "variable", "color": "#38bdf8"},
    {"name": "Personal Care",   "cost_type": "variable", "color": "#f9a8d4"},
    {"name": "Amazon",          "cost_type": "variable", "color": "#fbbf24"},
    {"name": "Home",            "cost_type": "fixed",    "color": "#60a5fa"},
    {"name": "Utilities",       "cost_type": "fixed",    "color": "#818cf8"},
    {"name": "Childcare",       "cost_type": "fixed",    "color": "#6ee7b7"},
    {"name": "Investments",     "cost_type": "fixed",    "color": "#fde68a"},
    {"name": "Other",           "cost_type": "variable", "color": "#d1d5db"},
]

# ── Default rules (seeded from the examples provided) ─────────────────────────

DEFAULT_RULES: list[dict] = [
    # Amazon
    {"pattern": "AMAZON",           "is_regex": False, "category": "Amazon",         "priority": 10},
    # Groceries
    {"pattern": "KROGER",           "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "PUBLIX",           "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "COSTCO WHSE",      "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "TRADER JOE",       "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "WAL-MART",         "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "TARGET",           "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "WHOLE FOODS",      "is_regex": False, "category": "Groceries",      "priority": 20},
    # Gas / Automotive
    {"pattern": "COSTCO GAS",       "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "KROGER FUEL",      "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "RACEWAY",          "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "RACETRAC",         "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "PILOT_",           "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "QT ",              "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "EMISSIONS",        "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "PARKING",          "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "SPI\\*GA NATGAS",  "is_regex": True,  "category": "Gas/Automotive", "priority": 30},
    # Rideshare
    {"pattern": "LYFT",             "is_regex": False, "category": "Rideshare",      "priority": 40},
    {"pattern": "UBER.*TRIP",       "is_regex": True,  "category": "Rideshare",      "priority": 40},
    {"pattern": "UBER.*EATS",       "is_regex": True,  "category": "Rideshare",      "priority": 40},
    # Health
    {"pattern": "CVS",              "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "WALGREENS",        "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "PHARMACY",         "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "MEDICAL",          "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "CLINIC",           "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "DENTAL",           "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "OPTIQUE",          "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "WELLSTAR",         "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "TEAMHEALTH",       "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "VET ",             "is_regex": False, "category": "Health",         "priority": 50},
    # Home
    {"pattern": "CITIZENS MTG",     "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "GEICO",            "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "SYNCHRONY BANK",   "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "PENTAGON FEDERAL", "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "BANK OF AMERICA",  "is_regex": False, "category": "Home",           "priority": 60},
    # Utilities
    {"pattern": "ATT\\*BILL",       "is_regex": True,  "category": "Utilities",      "priority": 70},
    {"pattern": "SAWNEE ELECTRIC",  "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "WATER.*SEWER",     "is_regex": True,  "category": "Utilities",      "priority": 70},
    {"pattern": "WM\\.COM",         "is_regex": True,  "category": "Utilities",      "priority": 70},
    {"pattern": "VENMO",            "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "SITEGROUND",       "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "PORKBUN",          "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "IVEY RIDGE",       "is_regex": False, "category": "Utilities",      "priority": 70},
    # Childcare
    {"pattern": "PRESCOLAIRE",      "is_regex": False, "category": "Childcare",      "priority": 80},
    {"pattern": "ADORA ELA",        "is_regex": False, "category": "Childcare",      "priority": 80},
    # Lodging
    {"pattern": "HAMPTON INN",      "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "HOLIDAY INN",      "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "SPRINGHILL SUITE", "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "BOOKING\\.COM",    "is_regex": True,  "category": "Lodging",        "priority": 90},
    {"pattern": "MARRIOTT",         "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "HILTON",           "is_regex": False, "category": "Lodging",        "priority": 90},
    # Restaurants
    {"pattern": "CHICK-FIL-A",      "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "MCDONALD",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "STARBUCKS",        "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "PANERA",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "PAPA JOHN",        "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "DOORDASH",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "GRUBHUB",          "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "TAVERN",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "SUSHI",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "BAKERY",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "CAFE",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "TAQUERIA",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "KITCHEN",          "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "RESTAURANT",       "is_regex": False, "category": "Restaurants",    "priority": 100},
    # Merchandise
    {"pattern": "STEAM",            "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "SEPHORA",          "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "PAYPAL",           "is_regex": False, "category": "Merchandise",    "priority": 110},
]


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Category:
    name:      str
    cost_type: CostType = "variable"
    color:     str      = "#d1d5db"

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Category":
        known = {f for f in Category.__dataclass_fields__}
        return Category(**{k: v for k, v in d.items() if k in known})


@dataclass
class CategoryRule:
    pattern:  str
    category: str
    is_regex: bool = False
    priority: int  = 100   # lower number = checked first

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CategoryRule":
        known = {f for f in CategoryRule.__dataclass_fields__}
        return CategoryRule(**{k: v for k, v in d.items() if k in known})

    def matches(self, text: str) -> bool:
        """Test this rule against a description string (Python-side, for preview)."""
        try:
            if self.is_regex:
                return bool(re.search(self.pattern, text, re.IGNORECASE))
            return self.pattern.upper() in text.upper()
        except re.error:
            return False


@dataclass
class CategoryConfig:
    categories: list[Category]     = field(default_factory=list)
    rules:      list[CategoryRule] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "categories": [c.to_dict() for c in self.categories],
            "rules":      [r.to_dict() for r in self.rules],
        }

    @staticmethod
    def from_dict(d: dict) -> "CategoryConfig":
        return CategoryConfig(
            categories=[Category.from_dict(c) for c in d.get("categories", [])],
            rules=[CategoryRule.from_dict(r) for r in d.get("rules", [])],
        )

    def category_names(self) -> list[str]:
        return [c.name for c in self.categories]

    def category_map(self) -> dict[str, Category]:
        return {c.name: c for c in self.categories}

    def sorted_rules(self) -> list[CategoryRule]:
        return sorted(self.rules, key=lambda r: r.priority)

    def resolve(self, description: str, bank_category: str | None = None) -> str:
        """Resolve category for a description (Python-side, used in preview)."""
        for rule in self.sorted_rules():
            if rule.matches(description):
                return rule.category
        if bank_category and bank_category.strip():
            return bank_category.strip()
        return "Other"


# ── Persistence ───────────────────────────────────────────────────────────────

def load_category_config() -> CategoryConfig:
    try:
        from services.db_config import load_categories_data
        data = load_categories_data()
        if data:
            cfg = CategoryConfig.from_dict(data)
            # Backfill any missing default categories
            existing_names = {c.name for c in cfg.categories}
            for d in DEFAULT_CATEGORIES:
                if d["name"] not in existing_names:
                    cfg.categories.append(Category.from_dict(d))
            return cfg
    except Exception as e:
        print(f"[category_rules] DB load failed ({e}), falling back to file/defaults")

    # File fallback
    if RULES_FILE.exists():
        data = json.loads(RULES_FILE.read_text())
        return CategoryConfig.from_dict(data)
    return CategoryConfig(
        categories=[Category.from_dict(d) for d in DEFAULT_CATEGORIES],
        rules=[CategoryRule.from_dict(r) for r in DEFAULT_RULES],
    )


def save_category_config(cfg: CategoryConfig) -> None:
    try:
        from services.db_config import save_categories_data
        save_categories_data(cfg.to_dict())
        return
    except Exception as e:
        print(f"[category_rules] DB save failed ({e}), falling back to file")
    RULES_FILE.write_text(json.dumps(cfg.to_dict(), indent=2))
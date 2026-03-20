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
    {"name": "Travel",          "cost_type": "variable", "color": "#2dd4bf"},
    {"name": "Entertainment",   "cost_type": "variable", "color": "#c084fc"},
    {"name": "Personal Care",   "cost_type": "variable", "color": "#f9a8d4"},
    {"name": "Amazon",          "cost_type": "variable", "color": "#fbbf24"},
    {"name": "Home",            "cost_type": "fixed",    "color": "#60a5fa"},
    {"name": "Utilities",       "cost_type": "fixed",    "color": "#818cf8"},
    {"name": "Childcare",       "cost_type": "fixed",    "color": "#6ee7b7"},
    {"name": "Investments",     "cost_type": "fixed",    "color": "#fde68a"},
    {"name": "Other",           "cost_type": "variable", "color": "#d1d5db"},
]

# ── Default rules ─────────────────────────────────────────────────────────────
# Lower priority number = checked first (higher precedence).
# More-specific patterns (e.g. "COSTCO GAS") are placed before broader ones
# (e.g. "COSTCO") so they win when the description contains both substrings.

DEFAULT_RULES: list[dict] = [
    # ── Amazon (priority 10) ──────────────────────────────────────────────────
    {"pattern": "AMAZON",              "is_regex": False, "category": "Amazon",         "priority": 10},
    {"pattern": "AMZN",                "is_regex": False, "category": "Amazon",         "priority": 10},

    # ── Gas / Automotive — specific overrides BEFORE groceries (priority 15) ─
    # These must come before any grocery store rules that share a prefix
    # (e.g. "COSTCO GAS" before "COSTCO WHSE", "KROGER FUEL" before "KROGER").
    {"pattern": "COSTCO GAS",          "is_regex": False, "category": "Gas/Automotive", "priority": 15},
    {"pattern": "KROGER FUEL",         "is_regex": False, "category": "Gas/Automotive", "priority": 15},
    {"pattern": "WALMART FUEL",        "is_regex": False, "category": "Gas/Automotive", "priority": 15},
    {"pattern": "SAM'S FUEL",          "is_regex": False, "category": "Gas/Automotive", "priority": 15},

    # ── Groceries (priority 20) ───────────────────────────────────────────────
    {"pattern": "KROGER",              "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "PUBLIX",              "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "COSTCO WHSE",         "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "TRADER JOE",          "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "WHOLE FOODS",         "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "ALDI",                "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "LIDL",                "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "SPROUTS",             "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "HEB",                 "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "H-E-B",               "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "SAFEWAY",             "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "MEIJER",              "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "FOOD LION",           "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "WINN-DIXIE",          "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "WINNDIXIE",           "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "WEGMANS",             "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "GIANT",               "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "STOP & SHOP",         "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "FRESH MARKET",        "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "SMART & FINAL",       "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "HARRIS TEETER",       "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "MARKET BASKET",       "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "WAL-MART",            "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "WALMART",             "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "TARGET",              "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "SAM'S CLUB",          "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "SAMS CLUB",           "is_regex": False, "category": "Groceries",      "priority": 20},
    {"pattern": "INSTACART",           "is_regex": False, "category": "Groceries",      "priority": 20},

    # ── Gas / Automotive (priority 30) ────────────────────────────────────────
    {"pattern": "SHELL",               "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "EXXON",               "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "MOBIL",               "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "BP",                  "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "CHEVRON",             "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "MARATHON",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "SUNOCO",              "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "SPEEDWAY",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "CASEY'S",             "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "CIRCLE K",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "WAWA",                "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "QUIKTRIP",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "QT ",                 "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "RACETRAC",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "RACEWAY",             "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "PILOT",               "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "LOVE'S",              "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "KWIK TRIP",           "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "KWIKTRIP",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "MURPHY USA",          "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "HOLIDAY STATIONSTORES","is_regex": False,"category": "Gas/Automotive", "priority": 30},
    {"pattern": "SHEETZ",              "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "KWIKIE MART",         "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "NATGAS",              "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "EMISSIONS",           "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "PARKING",             "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "JIFFY LUBE",          "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "FIRESTONE",           "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "GOODYEAR",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "AUTOZONE",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "ADVANCE AUTO",        "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "O'REILLY AUTO",       "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "OREILLY AUTO",        "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "VALVOLINE",           "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "PEP BOYS",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "MIDAS",               "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "MEINEKE",             "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "NAPA AUTO",           "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "CAR WASH",            "is_regex": False, "category": "Gas/Automotive", "priority": 30},
    {"pattern": "DMV",                 "is_regex": False, "category": "Gas/Automotive", "priority": 30},

    # ── Rideshare (priority 40) ───────────────────────────────────────────────
    {"pattern": "LYFT",                "is_regex": False, "category": "Rideshare",      "priority": 40},
    {"pattern": "UBER.*TRIP",          "is_regex": True,  "category": "Rideshare",      "priority": 40},
    {"pattern": "WAYMO",               "is_regex": False, "category": "Rideshare",      "priority": 40},
    {"pattern": "TURO",                "is_regex": False, "category": "Rideshare",      "priority": 40},

    # ── Health (priority 50) ──────────────────────────────────────────────────
    {"pattern": "CVS",                 "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "WALGREENS",           "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "RITE AID",            "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "PHARMACY",            "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "MEDICAL",             "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "CLINIC",              "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "DENTAL",              "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "ORTHODONT",           "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "HOSPITAL",            "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "URGENT CARE",         "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "OPTOMETRY",           "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "OPTIQUE",             "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "VISION CENTER",       "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "LENSCRAFTERS",        "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "DERMATOL",            "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "LABCORP",             "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "QUEST DIAGNOSTICS",   "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "WELLSTAR",            "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "TEAMHEALTH",          "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "KAISER",              "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "PLANNED PARENTHOOD",  "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "VET ",                "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "VETERINARY",          "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "ANIMAL HOSPITAL",     "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "PETVET",              "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "BANFIELD",            "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "HEALTH INS",          "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "HUMANA",              "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "CIGNA",               "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "AETNA",               "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "BLUE CROSS",          "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "BLUECROSS",           "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "UNITED HEALTH",       "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "RXBIRCH",             "is_regex": False, "category": "Health",         "priority": 50},
    {"pattern": "GOODRX",              "is_regex": False, "category": "Health",         "priority": 50},

    # ── Home (priority 60) ────────────────────────────────────────────────────
    {"pattern": "MORTGAGE",            "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "MTG PMT",             "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "HOA",                 "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "GEICO",               "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "STATE FARM",          "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "ALLSTATE",            "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "PROGRESSIVE",         "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "LIBERTY MUTUAL",      "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "FARMERS INS",         "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "USAA",                "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "HOME DEPOT",          "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "LOWE'S",              "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "LOWES",               "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "MENARDS",             "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "ACE HARDWARE",        "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "TRUE VALUE",          "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "IKEA",                "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "WAYFAIR",             "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "POTTERY BARN",        "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "CRATE AND BARREL",    "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "BED BATH",            "is_regex": False, "category": "Home",           "priority": 60},
    {"pattern": "RENT ",               "is_regex": False, "category": "Home",           "priority": 60},

    # ── Utilities (priority 70) ───────────────────────────────────────────────
    {"pattern": "AT&T",                "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "ATT*",                "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "VERIZON",             "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "T-MOBILE",            "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "TMOBILE",             "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "COMCAST",             "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "XFINITY",             "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "SPECTRUM",            "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "COX COMM",            "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "OPTIMUM",             "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "DIRECTV",             "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "DISH NETWORK",        "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "ELECTRIC",            "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "SAWNEE",              "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "GEORGIA POWER",       "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "DUKE ENERGY",         "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "DOMINION ENERGY",     "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "CON EDISON",          "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "WATER",               "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "SEWER",               "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "WASTE MANAGEMENT",    "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "WM\\.COM",            "is_regex": True,  "category": "Utilities",      "priority": 70},
    {"pattern": "REPUBLIC SERVICES",   "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "GOOGLE.*STORAGE",     "is_regex": True,  "category": "Utilities",      "priority": 70},
    {"pattern": "ICLOUD",              "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "DROPBOX",             "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "MICROSOFT 365",       "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "ADOBE",               "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "GITHUB",              "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "SITEGROUND",          "is_regex": False, "category": "Utilities",      "priority": 70},
    {"pattern": "PORKBUN",             "is_regex": False, "category": "Utilities",      "priority": 70},

    # ── Childcare (priority 80) ───────────────────────────────────────────────
    {"pattern": "DAYCARE",             "is_regex": False, "category": "Childcare",      "priority": 80},
    {"pattern": "PRESCHOOL",           "is_regex": False, "category": "Childcare",      "priority": 80},
    {"pattern": "PRESCOLAIRE",         "is_regex": False, "category": "Childcare",      "priority": 80},
    {"pattern": "CHILDCARE",           "is_regex": False, "category": "Childcare",      "priority": 80},
    {"pattern": "BRIGHT HORIZONS",     "is_regex": False, "category": "Childcare",      "priority": 80},
    {"pattern": "KINDERCARE",          "is_regex": False, "category": "Childcare",      "priority": 80},
    {"pattern": "TUITION",             "is_regex": False, "category": "Childcare",      "priority": 80},
    {"pattern": "YMCA",                "is_regex": False, "category": "Childcare",      "priority": 80},

    # ── Lodging (priority 90) ─────────────────────────────────────────────────
    {"pattern": "MARRIOTT",            "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "HILTON",              "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "HYATT",               "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "IHG",                 "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "HOLIDAY INN",         "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "HAMPTON INN",         "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "COURTYARD",           "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "RESIDENCE INN",       "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "SHERATON",            "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "WESTIN",              "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "DOUBLETREE",          "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "ALOFT",               "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "SPRINGHILL",          "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "BEST WESTERN",        "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "MOTEL 6",             "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "HAMPTON BY HILTON",   "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "AIRBNB",              "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "VRBO",                "is_regex": False, "category": "Lodging",        "priority": 90},
    {"pattern": "BOOKING\\.COM",       "is_regex": True,  "category": "Lodging",        "priority": 90},
    {"pattern": "HOTELS\\.COM",        "is_regex": True,  "category": "Lodging",        "priority": 90},

    # ── Travel (priority 95) ──────────────────────────────────────────────────
    {"pattern": "DELTA AIR",           "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "AMERICAN AIRLINES",   "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "UNITED AIRLINES",     "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "SOUTHWEST",           "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "JETBLUE",             "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "SPIRIT AIRLINES",     "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "FRONTIER AIRLINES",   "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "EXPEDIA",             "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "PRICELINE",           "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "KAYAK",               "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "ENTERPRISE RENT",     "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "HERTZ",               "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "AVIS",                "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "BUDGET CAR",          "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "NATIONAL CAR",        "is_regex": False, "category": "Travel",         "priority": 95},
    {"pattern": "ALAMO",               "is_regex": False, "category": "Travel",         "priority": 95},

    # ── Restaurants / Food delivery (priority 100) ────────────────────────────
    {"pattern": "CHICK-FIL-A",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "CHICKFILA",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "MCDONALD",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "WENDY'S",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "WENDYS",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "BURGER KING",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "TACO BELL",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "CHIPOTLE",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "STARBUCKS",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "DUNKIN",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "SUBWAY",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "PANERA",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "DOMINO",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "PIZZA HUT",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "PAPA JOHN",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "LITTLE CAESARS",      "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "OLIVE GARDEN",        "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "APPLEBEE",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "RED ROBIN",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "CHILI'S",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "CHILIS",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "DENNY'S",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "DENNYS",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "IHOP",                "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "WAFFLE HOUSE",        "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "CRACKER BARREL",      "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "OUTBACK",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "LONGHORN",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "TEXAS ROADHOUSE",     "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "BUFFALO WILD WINGS",  "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "PANDA EXPRESS",       "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "FIVE GUYS",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "SHAKE SHACK",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "SONIC DRIVE",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "ARBY'S",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "ARBYS",               "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "POPEYES",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "KFC",                 "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "JIMMY JOHN",          "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "JERSEY MIKE",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "FIREHOUSE SUBS",      "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "JASON'S DELI",        "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "WHATABURGER",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "COOKOUT",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "BOJANGLES",           "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "RAISING CANE",        "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "WINGSTOP",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "DOORDASH",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "GRUBHUB",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "UBER.*EATS",          "is_regex": True,  "category": "Restaurants",    "priority": 100},
    {"pattern": "SEAMLESS",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "RESTAURANT",         "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "TAVERN",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "STEAKHOUSE",          "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "SUSHI",               "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "TAQUERIA",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "PIZZERIA",            "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "BAKERY",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "CAFE",                "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "COFFEE",              "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "DINER",               "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "KITCHEN",             "is_regex": False, "category": "Restaurants",    "priority": 100},
    {"pattern": "GRILL",               "is_regex": False, "category": "Restaurants",    "priority": 100},

    # ── Entertainment (priority 105) ──────────────────────────────────────────
    {"pattern": "NETFLIX",             "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "SPOTIFY",             "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "HULU",                "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "DISNEY",              "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "HBO",                 "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "PARAMOUNT",           "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "PEACOCK",             "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "YOUTUBE",             "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "APPLE.*TV",           "is_regex": True,  "category": "Entertainment",  "priority": 105},
    {"pattern": "AMAZON.*PRIME",       "is_regex": True,  "category": "Entertainment",  "priority": 105},
    {"pattern": "STEAM",               "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "PLAYSTATION",         "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "XBOX",                "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "NINTENDO",            "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "GAMESTOP",            "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "AMC THEATRE",         "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "REGAL CINEMA",        "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "CINEMARK",            "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "FANDANGO",            "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "TICKETMASTER",        "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "STUBHUB",             "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "EVENTBRITE",          "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "APPLE ARCADE",        "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "TIDAL",               "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "PANDORA",             "is_regex": False, "category": "Entertainment",  "priority": 105},
    {"pattern": "SIRIUS",              "is_regex": False, "category": "Entertainment",  "priority": 105},

    # ── Merchandise / Shopping (priority 110) ─────────────────────────────────
    {"pattern": "BEST BUY",            "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "APPLE STORE",         "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "MICROSOFT",           "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "SEPHORA",             "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "ULTA",                "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "BATH & BODY",         "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "VICTORIA'S SECRET",   "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "GAP",                 "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "OLD NAVY",            "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "H&M",                 "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "ZARA",                "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "NORDSTROM",           "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "MACY'S",              "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "MACYS",               "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "KOHL'S",              "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "KOHLS",               "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "TJ MAXX",             "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "MARSHALLS",           "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "ROSS",                "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "BURLINGTON",          "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "DICK'S SPORTING",     "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "BASS PRO",            "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "CABELA'S",            "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "HOBBY LOBBY",         "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "MICHAELS",            "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "JOANN",               "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "PETSMART",            "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "PETCO",               "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "EBAY",                "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "ETSY",                "is_regex": False, "category": "Merchandise",    "priority": 110},
    {"pattern": "PAYPAL",              "is_regex": False, "category": "Merchandise",    "priority": 110},

    # ── Personal Care (priority 120) ──────────────────────────────────────────
    {"pattern": "HAIR SALON",          "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "HAIR CUT",            "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "BARBER",              "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "NAIL SALON",          "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "SPA",                 "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "MASSAGE",             "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "GREAT CLIPS",         "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "SPORT CLIPS",         "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "SUPERCUTS",           "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "FANTASTIC SAMS",      "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "DRY CLEAN",           "is_regex": False, "category": "Personal Care",  "priority": 120},
    {"pattern": "LAUNDRY",             "is_regex": False, "category": "Personal Care",  "priority": 120},

    # ── Investments (priority 130) ────────────────────────────────────────────
    {"pattern": "FIDELITY",            "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "VANGUARD",            "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "SCHWAB",              "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "ROBINHOOD",           "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "ETRADE",              "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "AMERITRADE",          "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "ACORNS",              "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "BETTERMENT",          "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "WEALTHFRONT",         "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "SOFI",                "is_regex": False, "category": "Investments",    "priority": 130},
    {"pattern": "COINBASE",            "is_regex": False, "category": "Investments",    "priority": 130},
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

def load_category_config(family_id: int) -> CategoryConfig:
    try:
        from services.config_repo import load_categories
        data = load_categories(family_id)
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


def save_category_config(cfg: CategoryConfig, family_id: int) -> None:
    try:
        from services.config_repo import save_categories
        save_categories(cfg.to_dict(), family_id)
        return
    except Exception as e:
        print(f"[category_rules] DB save failed ({e}), falling back to file")
    RULES_FILE.write_text(json.dumps(cfg.to_dict(), indent=2))
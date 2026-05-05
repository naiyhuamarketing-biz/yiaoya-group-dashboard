"""Yiaoya Group account registry — brand/operator mapping for the 5 active ad accounts.

Source of truth for which Meta ad account belongs to which sub-brand and which
operator. Used by the multi-account data layer to filter and aggregate.

Excluded from this list: 3 (Cancel) accounts that are closed/pending closure
and never queried.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional


# Brand IDs (used in API params, JS, CSS — keep stable)
BRAND_YIAOYA = "yiaoya"
BRAND_RESTO = "resto"
BRAND_KNEECARE = "kneecare"

# Operator IDs
OP_FA = "fa"
OP_CHUN = "chun"


@dataclass(frozen=True)
class Account:
    id: str               # Meta ad account numeric ID (no "act_" prefix)
    name: str             # Display name as shown in Business Manager
    brand: str            # One of BRAND_*
    operator: str         # One of OP_*


# Active accounts — DO NOT add (Cancel) accounts here
ACCOUNTS: List[Account] = [
    Account("1271368901828972", "KneeCare (FA-N)",   BRAND_KNEECARE, OP_FA),
    Account("1014027174637621", "Yiaoya (FA-N)",     BRAND_YIAOYA,   OP_FA),
    Account("702987921684167",  "Yiaoya",            BRAND_YIAOYA,   OP_CHUN),
    Account("941491285320998",  "Resto Pilates (N)", BRAND_RESTO,    OP_CHUN),
    Account("843784871383763",  "Resto Pilates",     BRAND_RESTO,    OP_CHUN),
]


# Brand display metadata (Thai labels + accent colors for UI)
BRANDS = {
    BRAND_YIAOYA: {
        "label": "เยียวยา",
        "label_en": "Yiaoya",
        "accent": "#2D6A4F",   # forest green
        "accent_soft": "#95D5B2",
    },
    BRAND_RESTO: {
        "label": "RESTO Pilates",
        "label_en": "Resto Pilates",
        "accent": "#1A1A1A",   # charcoal
        "accent_soft": "#E8E5DD",
    },
    BRAND_KNEECARE: {
        "label": "KneeCare",
        "label_en": "KneeCare",
        "accent": "#5BA3C9",   # sky blue
        "accent_soft": "#BFE0F2",
    },
}

OPERATORS = {
    OP_FA:   {"label": "ฟา",  "color": "#2D6A4F"},
    OP_CHUN: {"label": "ชุน", "color": "#7A4F3A"},
}


def filter_accounts(
    brand: Optional[str] = None,
    operator: Optional[str] = None,
) -> List[Account]:
    """Filter active accounts by brand and/or operator.

    None means "any". Empty list returned if no accounts match (e.g., Resto + ฟา).
    """
    out = ACCOUNTS
    if brand and brand != "all":
        out = [a for a in out if a.brand == brand]
    if operator and operator != "all":
        out = [a for a in out if a.operator == operator]
    return out


def operators_for_brand(brand: str) -> List[str]:
    """Which operators run ads on this brand. Used to enable/disable head-to-head."""
    if brand == "all":
        return [OP_FA, OP_CHUN]
    ops = {a.operator for a in ACCOUNTS if a.brand == brand}
    return sorted(ops)


def has_head_to_head(brand: str) -> bool:
    """True only when both ฟา and ชุน run ads on this brand (= Yiaoya only)."""
    if brand == "all":
        return True
    return len(operators_for_brand(brand)) >= 2

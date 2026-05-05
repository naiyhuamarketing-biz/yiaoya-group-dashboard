"""Multi-account data layer for Yiaoya Group.

Wraps lib.meta_loader.fetch_daily to query multiple ad accounts in parallel
(one per Yiaoya brand/operator combo) and aggregate into the same daily-record
shape consumed by api_server.py and dashboard.html.

Aggregation rules:
- spent / impression / reach / result / conversion → SUM across accounts for the day
- frequency = impression / reach (recomputed)
- cpm = (spent / impression) * 1000 (recomputed)
- ctr = unchanged (not currently summed because ad-level only; left blank here)
- roas = conversion / spent (recomputed)
- cost_per_result = spent / result (recomputed)
- top_campaign → from the account with highest spend that day

Use:
    from lib.multi_loader import fetch_daily_multi
    records = fetch_daily_multi(since, until, brand=None, operator=None)
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import List, Optional

from lib.meta_loader import fetch_daily
from lib.yiaoya_accounts import Account, filter_accounts


def _safe_fetch(account: Account, since: date, until: date) -> List[dict]:
    """Fetch one account's daily records; on any failure return []."""
    try:
        records = fetch_daily(account.id, since, until)
    except Exception:
        return []
    # Tag every record with the source account so downstream can split by operator
    for r in records:
        r["_account_id"] = account.id
        r["_brand"] = account.brand
        r["_operator"] = account.operator
    return records


def fetch_daily_multi(
    since: date,
    until: date,
    brand: Optional[str] = None,
    operator: Optional[str] = None,
) -> List[dict]:
    """Fetch and aggregate daily records across matching accounts.

    Returns the same shape as fetch_daily — list of {date, month, day, ads:[...]}.
    Empty list if no accounts match the filter.
    """
    accounts = filter_accounts(brand=brand, operator=operator)
    if not accounts:
        return []

    # Fetch all matching accounts in parallel — Meta API is the bottleneck
    per_account: List[List[dict]] = []
    with ThreadPoolExecutor(max_workers=min(5, len(accounts))) as pool:
        futs = {pool.submit(_safe_fetch, acc, since, until): acc for acc in accounts}
        for f in as_completed(futs):
            per_account.append(f.result())

    # Bucket by date string
    by_date: dict = {}
    for records in per_account:
        for r in records:
            d = r.get("date")
            if not d:
                continue
            by_date.setdefault(d, []).append(r)

    # Merge each day's records
    out: List[dict] = []
    for d_str in sorted(by_date.keys()):
        day_records = by_date[d_str]
        merged_ad = _merge_day(day_records)
        if not merged_ad:
            continue
        first = day_records[0]
        out.append({
            "date": d_str,
            "month": first.get("month"),
            "day": first.get("day"),
            "ads": [merged_ad],
        })
    return out


def _merge_day(day_records: List[dict]) -> Optional[dict]:
    """Sum metrics across the multi-account records for one day."""
    if not day_records:
        return None

    spent = 0.0
    result = 0
    conversion = 0.0
    impression = 0
    reach = 0
    top_camp = ""
    top_camp_spend = -1.0

    for r in day_records:
        ads = r.get("ads") or []
        if not ads:
            continue
        a = ads[0]
        s = float(a.get("spent", 0) or 0)
        spent += s
        result += int(a.get("result", 0) or 0)
        conversion += float(a.get("conversion", 0) or 0)
        impression += int(a.get("impression", 0) or 0)
        reach += int(a.get("reach", 0) or 0)
        if s > top_camp_spend:
            top_camp_spend = s
            top_camp = a.get("campaign", "") or ""

    frequency = (impression / reach) if reach else 0
    cpm = (spent / impression * 1000) if impression else 0
    roas = (conversion / spent) if spent else 0
    cost_per_result = (spent / result) if result else 0

    return {
        "spent": spent,
        "result": result,
        "conversion": conversion,
        "impression": impression,
        "reach": reach,
        "frequency": frequency,
        "cpm": cpm,
        "ctr": 0,  # account-level ctr aggregation isn't meaningful; skip
        "roas": roas,
        "cost_per_result": cost_per_result,
        "campaign": top_camp,
        "status": "ACTIVE",
        "objective": "MESSAGES",
    }


def fetch_split_by_operator(
    since: date,
    until: date,
    brand: Optional[str] = None,
) -> dict:
    """Return per-operator daily records for head-to-head comparison.

    Shape: {"fa": [records...], "chun": [records...]}
    Each operator's records are themselves multi-account aggregates if that
    operator runs >1 account on the brand.
    """
    return {
        "fa": fetch_daily_multi(since, until, brand=brand, operator="fa"),
        "chun": fetch_daily_multi(since, until, brand=brand, operator="chun"),
    }

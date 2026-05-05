"""FastAPI bridge — expose Yiaoya Group Meta data to the HTML dashboard.

Reuses lib/meta_loader (same source as dashboard.py / Streamlit Cloud) so the
HTML dashboard reads the exact same numbers as Streamlit (when both running).

Run:   uvicorn api_server:app --port 8000 --reload
       (or: python api_server.py)
"""
from __future__ import annotations
import os
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

import sys
sys.path.insert(0, str(ROOT))

from lib.meta_loader import fetch_daily, signature
from lib.fb_ads import fetch_top3_ads, to_dict_list
from lib.multi_loader import fetch_daily_multi, fetch_split_by_operator
from lib.yiaoya_accounts import (
    ACCOUNTS, BRANDS, OPERATORS, filter_accounts,
    operators_for_brand, has_head_to_head,
)

BANGKOK_TZ = timezone(timedelta(hours=7))

app = FastAPI(title="Yiaoya Group Data API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Path to the dashboard HTML (lives inside the repo for Render deploy)
DASHBOARD_FILE = ROOT / "dashboard.html"
SUMMARY_SNAPSHOT_DIR = Path(os.getenv("SUMMARY_SNAPSHOT_DIR", "/tmp/yiaoya-summary-snapshots"))


# ── Cache (mirrors Streamlit's @st.cache_data ttl=600) ─────────────
_CACHE: dict = {}
TTL = 600  # 10 min


def _cached(key: str, loader):
    now = time.time()
    if key in _CACHE:
        ts, data = _CACHE[key]
        if now - ts < TTL:
            return data
    data = loader()
    _CACHE[key] = (now, data)
    return data


def now_bkk() -> datetime:
    return datetime.now(BANGKOK_TZ)


def today_bkk() -> date:
    return now_bkk().date()


# ── Helpers ─────────────────────────────────────────────────────────
def _day_totals(record: dict) -> dict:
    """Sum the day's ads (account-level loader returns 1 synthetic ad/day)."""
    ads = record.get("ads", [])
    if not ads:
        return {}
    a = ads[0]
    return {
        "date": record["date"],
        "spent": float(a.get("spent", 0) or 0),
        "result": int(a.get("result", 0) or 0),
        "conversion": float(a.get("conversion", 0) or 0),
        "impression": int(a.get("impression", 0) or 0),
        "reach": int(a.get("reach", 0) or 0),
        "cpm": float(a.get("cpm", 0) or 0),
        "ctr": float(a.get("ctr", 0) or 0),
        "frequency": float(a.get("frequency", 0) or 0),
        "roas": float(a.get("roas", 0) or 0),
        "cost_per_result": float(a.get("cost_per_result", 0) or 0),
        "top_campaign": a.get("campaign", ""),
    }


def _fetch_range(since: date, until: date, brand: Optional[str] = None, operator: Optional[str] = None):
    """Multi-account fetch with brand/operator filter, cached for 10 min."""
    b = brand or "all"
    o = operator or "all"
    key = f"range:{b}:{o}:{since.isoformat()}:{until.isoformat()}:{signature()}"
    return _cached(key, lambda: fetch_daily_multi(since, until, brand=brand, operator=operator))


def _default_report_date() -> date:
    """Pick the report date safely for scheduled jobs.

    Normal daily send runs at 23:59 BKK, so it should report today.
    If a delayed/retry job runs shortly after midnight, it should still report
    yesterday instead of accidentally sending an empty new-day report.
    """
    now = now_bkk()
    if now.hour < 1:
        return now.date() - timedelta(days=1)
    return now.date()


def _summary_snapshot_path(since: date, until: date) -> Path:
    return SUMMARY_SNAPSHOT_DIR / f"{since.isoformat()}__{until.isoformat()}.json"


def _read_summary_snapshot(since: date, until: date) -> Optional[dict]:
    path = _summary_snapshot_path(since, until)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            data["stale"] = True
            data["snapshot_loaded_at"] = now_bkk().isoformat()
            return data
    except Exception:
        pass
    return None


def _write_summary_snapshot(since: date, until: date, data: dict) -> None:
    try:
        SUMMARY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["stale"] = False
        payload["snapshot_saved_at"] = now_bkk().isoformat()
        _summary_snapshot_path(since, until).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


# ── Endpoints ───────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    has_token = bool(os.getenv("FB_ACCESS_TOKEN"))
    return {
        "ok": True,
        "accounts": [{"id": a.id, "name": a.name, "brand": a.brand, "operator": a.operator} for a in ACCOUNTS],
        "has_token": has_token,
        "now_bkk": now_bkk().isoformat(),
        "cache_keys": list(_CACHE.keys()),
    }


@app.get("/api/yiaoya/summary")
def yiaoya_summary(
    since: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to month start"),
    until: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today"),
    brand: Optional[str] = Query(None, description="all|yiaoya|resto|kneecare"),
    operator: Optional[str] = Query(None, description="all|fa|chun"),
):
    """Aggregated KPIs + per-day chart data for a date range, filtered by brand/operator."""
    today = today_bkk()
    s = date.fromisoformat(since) if since else date(today.year, today.month, 1)
    u = date.fromisoformat(until) if until else today

    try:
        records = _fetch_range(s, u, brand=brand, operator=operator)
    except Exception as e:
        snapshot = _read_summary_snapshot(s, u)
        if snapshot:
            snapshot["warning"] = f"Meta API error; showing last good snapshot: {str(e)[:160]}"
            return snapshot
        raise HTTPException(status_code=502, detail=f"Meta API error: {e}")

    days = [_day_totals(r) for r in records]
    days = [d for d in days if d]

    spend = sum(d["spent"] for d in days)
    result = sum(d["result"] for d in days)
    conv = sum(d["conversion"] for d in days)
    impressions = sum(d["impression"] for d in days)
    reach = sum(d["reach"] for d in days)
    roas = (conv / spend) if spend else 0
    cpr = (spend / result) if result else 0
    frequency = (impressions / reach) if reach else 0
    net = conv - spend
    n_days = len(days) or 1
    avg_daily_spend = spend / n_days

    target_roas = 10.0

    response = {
        "since": s.isoformat(),
        "until": u.isoformat(),
        "n_days": len(days),
        "totals": {
            "spend": round(spend, 2),
            "revenue": round(conv, 2),
            "result": result,
            "roas": round(roas, 2),
            "cost_per_result": round(cpr, 2),
            "frequency": round(frequency, 2),
            "impressions": impressions,
            "reach": reach,
            "net": round(net, 2),
            "avg_daily_spend": round(avg_daily_spend, 2),
            "target_roas": target_roas,
            "roas_pct_of_target": round((roas / target_roas * 100) if target_roas else 0, 1),
        },
        "days": days,
        "fetched_at": now_bkk().isoformat(),
    }
    _write_summary_snapshot(s, u, response)
    return response


@app.get("/api/yiaoya/day")
def yiaoya_day(
    target: Optional[str] = None,
    brand: Optional[str] = Query(None),
    operator: Optional[str] = Query(None),
):
    """Single-day totals. `target` defaults to today."""
    today = today_bkk()
    d = date.fromisoformat(target) if target else today
    try:
        records = _fetch_range(d, d, brand=brand, operator=operator)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Meta API error: {e}")
    if not records:
        return {"date": d.isoformat(), "spent": 0, "result": 0, "conversion": 0, "roas": 0, "cost_per_result": 0, "top_campaign": "", "empty": True}
    return _day_totals(records[0])


@app.get("/api/yiaoya/top-ads")
def yiaoya_top_ads(
    target: Optional[str] = None,
    brand: Optional[str] = Query(None),
    operator: Optional[str] = Query(None),
):
    """Top 3 ads by spend for a given day across matching accounts."""
    d = date.fromisoformat(target) if target else (today_bkk() - timedelta(days=1))
    accounts = filter_accounts(brand=brand, operator=operator)
    merged: list = []
    for acc in accounts:
        try:
            rows = _cached(
                f"top3:{acc.id}:{d.isoformat()}:{signature()}",
                lambda a=acc: fetch_top3_ads(a.id, d, "Inbox"),
            )
            for r in to_dict_list(rows):
                r["_brand"] = acc.brand
                r["_operator"] = acc.operator
                merged.append(r)
        except Exception:
            continue
    merged.sort(key=lambda r: -float(r.get("spent") or 0))
    return {"date": d.isoformat(), "ads": merged[:3]}


@app.get("/api/yiaoya/top-ads-range")
def yiaoya_top_ads_range(
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 10,
    brand: Optional[str] = Query(None),
    operator: Optional[str] = Query(None),
):
    """Top ads aggregated over a date range across matching accounts, sorted by best ฿/inbox."""
    today = today_bkk()
    s = date.fromisoformat(since) if since else date(today.year, today.month, 1)
    u = date.fromisoformat(until) if until else today

    accounts = filter_accounts(brand=brand, operator=operator)
    if not accounts:
        return {"since": s.isoformat(), "until": u.isoformat(), "n_ads": 0, "ads": [], "fetched_at": now_bkk().isoformat()}

    def _load_account(account_id: str):
        from facebook_business.api import FacebookAdsApi
        from facebook_business.adobjects.adaccount import AdAccount
        FacebookAdsApi.init(
            app_id=os.getenv("FB_APP_ID"),
            app_secret=os.getenv("FB_APP_SECRET"),
            access_token=os.getenv("FB_ACCESS_TOKEN"),
        )
        account = AdAccount(f"act_{account_id}")
        fields = [
            "ad_name", "campaign_name", "spend", "impressions", "reach",
            "cpm", "actions", "action_values",
        ]
        params = {
            "time_range": {"since": s.isoformat(), "until": u.isoformat()},
            "level": "ad",
        }
        return list(account.get_insights(fields=fields, params=params))

    rows = []
    for acc in accounts:
        key = f"top-range:{acc.id}:{s.isoformat()}:{u.isoformat()}:{signature()}"
        try:
            insights = _cached(key, lambda aid=acc.id: _load_account(aid))
        except Exception:
            continue
        for ins in insights:
            spent = float(ins.get("spend", 0) or 0)
            impression = int(ins.get("impressions", 0) or 0)
            reach = int(ins.get("reach", 0) or 0)
            cpm = float(ins.get("cpm", 0) or 0)
            result = 0
            for a in ins.get("actions") or []:
                if a.get("action_type") in (
                    "onsite_conversion.messaging_first_reply",
                    "onsite_conversion.messaging_conversation_started_7d",
                ):
                    result += int(float(a.get("value", 0) or 0))
            conv_value = 0.0
            for v in ins.get("action_values") or []:
                if v.get("action_type") in (
                    "purchase", "omni_purchase",
                    "offsite_conversion.fb_pixel_purchase",
                    "onsite_conversion.purchase",
                ):
                    conv_value = max(conv_value, float(v.get("value", 0) or 0))
            rows.append({
                "campaign": (ins.get("ad_name") or "")[:80],
                "spent": round(spent, 2),
                "impression": impression,
                "reach": reach,
                "cpm": round(cpm, 2),
                "result": result,
                "cost_per_result": round(spent / result, 2) if result else 0,
                "conversion": round(conv_value, 2),
                "roas": round(conv_value / spent, 2) if spent else 0,
                "_brand": acc.brand,
                "_operator": acc.operator,
            })

    with_result = sorted(
        [r for r in rows if r["result"] > 0],
        key=lambda r: r["cost_per_result"],
    )
    no_result = sorted(
        [r for r in rows if r["result"] == 0 and r["spent"] > 0],
        key=lambda r: -r["spent"],
    )
    sorted_rows = with_result + no_result

    return {
        "since": s.isoformat(),
        "until": u.isoformat(),
        "n_ads": len(sorted_rows),
        "ads": sorted_rows[:limit],
        "fetched_at": now_bkk().isoformat(),
    }


@app.get("/api/yiaoya/report")
def yiaoya_report(target: Optional[str] = None):
    """Pre-formatted daily report text + structured data.
    Defaults to **yesterday** (today is usually incomplete).
    """
    d = date.fromisoformat(target) if target else (today_bkk() - timedelta(days=1))

    try:
        day = _fetch_range(d, d)
        # Top ads merged across all 5 active accounts
        merged_ads: list = []
        for acc in ACCOUNTS:
            try:
                rows = _cached(
                    f"top3:{acc.id}:{d.isoformat()}:{signature()}",
                    lambda a=acc: fetch_top3_ads(a.id, d, "Inbox"),
                )
                merged_ads.extend(rows)
            except Exception:
                continue
        ads = merged_ads
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Meta API error: {e}")

    if not day:
        return {"date": d.isoformat(), "text": "", "totals": None, "top_ads": [], "empty": True}

    totals = _day_totals(day[0])
    spend = totals["spent"]
    result = totals["result"]
    cpr = (spend / result) if result else 0

    top = sorted(
        [a for a in ads if a.spent > 0],
        key=lambda a: ((a.spent / a.result) if a.result else 9e9, -a.spent),
    )[:3]

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    lines.append(f"🌿 Yiaoya Group — Daily Report {d.strftime('%-d/%-m/%Y')}")
    lines.append(
        f"💸 Spend: ฿{spend:,.2f} | 📨 Inbox: {result} ข้อความ | "
        f"฿/Inbox: ฿{cpr:,.0f}"
    )
    lines.append("")

    if top:
        lines.append("🏆 Top Performers:")
        for i, a in enumerate(top):
            star = " ⭐" if i == 0 else ""
            unit = (a.spent / a.result) if a.result else 0
            lines.append(
                f"{medals[i]} {a.campaign} — {a.result} inbox / "
                f"฿{a.spent:,.0f} / ฿{unit:,.0f}{star}"
            )

    text = "\n".join(lines)

    return {
        "date": d.isoformat(),
        "text": text,
        "totals": {
            "spend": round(spend, 2),
            "result": result,
            "cost_per_result": round(cpr, 2),
            "top_campaign": totals["top_campaign"],
        },
        "top_ads": [
            {
                "rank": i + 1,
                "medal": medals[i],
                "campaign": a.campaign,
                "spent": a.spent,
                "result": a.result,
                "cost_per_result": (a.spent / a.result) if a.result else 0,
                "impression": a.impression,
                "reach": a.reach,
                "cpm": a.cpm,
            }
            for i, a in enumerate(top)
        ],
        "fetched_at": now_bkk().isoformat(),
    }


@app.get("/api/yiaoya/cache/clear")
def clear_cache():
    n = len(_CACHE)
    _CACHE.clear()
    return {"cleared": n}


@app.get("/api/yiaoya/keepalive")
def keepalive():
    """Lightweight ping that:
      1. Keeps Render free dyno warm (avoids 15-min sleep)
      2. Touches Meta API so the long-lived FB token auto-extends
         (Meta extends tokens that are used at least every 24h within the 60-day window)
      3. Returns immediately — does NOT send LINE
    """
    today = today_bkk()
    try:
        # Touching Meta API keeps token warm
        records = _fetch_range(today, today)
        ok = bool(records) or True  # even empty result counts as "API call succeeded"
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {
        "ok": True,
        "now_bkk": now_bkk().isoformat(),
        "cached_keys": len(_CACHE),
        "message": "Render warm + FB token extended",
    }


@app.get("/api/yiaoya/token-info")
def token_info():
    """Show FB long-lived token expiry status — useful for proactive monitoring."""
    import requests as _r
    token = os.getenv("FB_ACCESS_TOKEN", "")
    app_id = os.getenv("FB_APP_ID", "")
    app_secret = os.getenv("FB_APP_SECRET", "")
    if not (token and app_id and app_secret):
        raise HTTPException(503, "FB credentials not fully configured")
    try:
        resp = _r.get(
            "https://graph.facebook.com/v20.0/debug_token",
            params={
                "input_token": token,
                "access_token": f"{app_id}|{app_secret}",
            },
            timeout=10,
        )
        data = resp.json().get("data", {})
        expires_at = data.get("data_access_expires_at") or data.get("expires_at") or 0
        if expires_at:
            from datetime import datetime as _dt
            exp_dt = _dt.fromtimestamp(expires_at, tz=BANGKOK_TZ)
            now = now_bkk()
            days_left = (exp_dt - now).days
            return {
                "ok": True,
                "expires_at": exp_dt.isoformat(),
                "expires_at_unix": expires_at,
                "days_left": days_left,
                "warning": "RENEW SOON" if days_left < 14 else None,
                "is_valid": data.get("is_valid", False),
                "scopes": data.get("scopes", []),
            }
        return {"ok": True, "raw": data}
    except Exception as e:
        raise HTTPException(502, f"Token check failed: {e}")


# ── Yiaoya-specific: brand/operator metadata + head-to-head ────────
@app.get("/api/yiaoya/accounts")
def yiaoya_accounts():
    """Return brand/operator config for the dashboard UI to render filters."""
    return {
        "brands": BRANDS,
        "operators": OPERATORS,
        "accounts": [
            {"id": a.id, "name": a.name, "brand": a.brand, "operator": a.operator}
            for a in ACCOUNTS
        ],
        "head_to_head_brands": [b for b in BRANDS.keys() if has_head_to_head(b)],
    }


@app.get("/api/yiaoya/self-compare")
def yiaoya_self_compare(
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    brand: Optional[str] = Query(None, description="all|yiaoya|resto|kneecare; defaults to all"),
):
    """Per-operator self-comparison: current period vs prior equivalent period.

    Returns each operator's totals and daily records for two periods:
      - current: the requested [since, until]
      - previous: the same length ending the day before `since`

    Each side compares against ITS OWN past, not against the other operator.
    """
    today = today_bkk()
    s = date.fromisoformat(since) if since else date(today.year, today.month, 1)
    u = date.fromisoformat(until) if until else today
    b = brand or "all"

    # Compute previous-period range of the same length
    span_days = (u - s).days
    prev_until = s - timedelta(days=1)
    prev_since = prev_until - timedelta(days=span_days)

    bf = b if b != "all" else None
    key_cur  = f"sc:cur:{b}:{s.isoformat()}:{u.isoformat()}:{signature()}"
    key_prev = f"sc:prev:{b}:{prev_since.isoformat()}:{prev_until.isoformat()}:{signature()}"
    try:
        split_cur  = _cached(key_cur,  lambda: fetch_split_by_operator(s, u, brand=bf))
        split_prev = _cached(key_prev, lambda: fetch_split_by_operator(prev_since, prev_until, brand=bf))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Meta API error: {e}")

    def summarise(records: list) -> dict:
        days = [_day_totals(r) for r in records]
        days = [d for d in days if d]
        spend = sum(d["spent"] for d in days)
        result = sum(d["result"] for d in days)
        conv = sum(d["conversion"] for d in days)
        return {
            "spend": round(spend, 2),
            "revenue": round(conv, 2),
            "result": result,
            "roas": round((conv / spend) if spend else 0, 2),
            "cost_per_result": round((spend / result) if result else 0, 2),
            "n_days": len(days),
            "days": days,
        }

    return {
        "current":  {"since": s.isoformat(),         "until": u.isoformat()},
        "previous": {"since": prev_since.isoformat(),"until": prev_until.isoformat()},
        "brand": b,
        "fa": {
            "current":  summarise(split_cur["fa"]),
            "previous": summarise(split_prev["fa"]),
        },
        "chun": {
            "current":  summarise(split_cur["chun"]),
            "previous": summarise(split_prev["chun"]),
        },
        "fetched_at": now_bkk().isoformat(),
    }


THAI_MONTHS = ['มกราคม', 'กุมภาพันธ์', 'มีนาคม', 'เมษายน', 'พฤษภาคม', 'มิถุนายน',
               'กรกฎาคม', 'สิงหาคม', 'กันยายน', 'ตุลาคม', 'พฤศจิกายน', 'ธันวาคม']


def _thai_date(d: date) -> str:
    return f"{d.day} {THAI_MONTHS[d.month - 1]} {d.year}"


def _thai_range(d1: date, d2: date) -> str:
    if d1.year == d2.year and d1.month == d2.month:
        return f"{d1.day}–{d2.day} {THAI_MONTHS[d1.month - 1]} {d1.year}"
    if d1.year == d2.year:
        return (f"{d1.day} {THAI_MONTHS[d1.month - 1]} – "
                f"{d2.day} {THAI_MONTHS[d2.month - 1]} {d1.year}")
    return (f"{d1.day} {THAI_MONTHS[d1.month - 1]} {d1.year} – "
            f"{d2.day} {THAI_MONTHS[d2.month - 1]} {d2.year}")


def _fmt_pl(profit: float) -> str:
    if profit < 0:
        return f"-฿{abs(round(profit)):,}"
    if profit > 0:
        return f"+฿{round(profit):,}"
    return "฿0"


def _build_daily_text(target_d: date) -> str:
    """Build the doctor-friendly daily report text exactly like dashboard."""
    today = today_bkk()
    month_start = date(target_d.year, target_d.month, 1)

    # Selected day totals
    day_records = _fetch_range(target_d, target_d)
    if day_records:
        sel = _day_totals(day_records[0])
        sel_spend = sel["spent"]
        sel_inbox = sel["result"]
        sel_conv = sel["conversion"]
    else:
        sel_spend = sel_inbox = sel_conv = 0

    # Month-to-date totals
    mtd_records = _fetch_range(month_start, target_d)
    mtd_days = [_day_totals(r) for r in mtd_records if r]
    mtd_spend = sum(d["spent"] for d in mtd_days)
    mtd_inbox = sum(d["result"] for d in mtd_days)
    mtd_conv = sum(d["conversion"] for d in mtd_days)
    mtd_n = len(mtd_days) or 1
    avg_daily = round(mtd_spend / mtd_n)

    sel_cpr = round(sel_spend / sel_inbox) if sel_inbox else 0
    mtd_cpr = round(mtd_spend / mtd_inbox) if mtd_inbox else 0
    day_profit = sel_conv - sel_spend
    mtd_profit = mtd_conv - mtd_spend

    L = []
    L.append("EVERLY CLINIC — DAILY REPORT")
    L.append("")
    L.append(f"Report ประจำวัน ({_thai_date(target_d)})")
    L.append("")
    L.append(f"วันนี้ ใช้เงิน: ฿{sel_spend:,.2f}")
    L.append(f"คนทัก: {sel_inbox} คน")
    L.append(f"เฉลี่ยต่อคนทัก: ฿{sel_cpr:,}" if sel_inbox else "เฉลี่ยต่อคนทัก: —")
    L.append(f"ยอดขาย: ฿{round(sel_conv):,}")
    L.append(f"กำไร/ขาดทุน: {_fmt_pl(day_profit)}")
    L.append("")
    L.append("============")
    L.append(f"Report สะสมตั้งแต่ต้นเดือน – ปัจจุบัน ({_thai_range(month_start, target_d)})")
    L.append("")
    L.append(f"ภาพรวม ใช้เงินรวม: ฿{round(mtd_spend):,}")
    L.append(f"เฉลี่ยต่อวัน: ฿{avg_daily:,}")
    L.append(f"คนทักรวม: {mtd_inbox} คน")
    L.append(f"เฉลี่ยต่อคนทัก: ฿{mtd_cpr:,}" if mtd_inbox else "เฉลี่ยต่อคนทัก: —")
    L.append(f"ยอดขาย: ฿{round(mtd_conv):,}")
    L.append(f"กำไร/ขาดทุน: {_fmt_pl(mtd_profit)}")
    L.append("")
    L.append("============")
    return "\n".join(L)


@app.get("/api/yiaoya/daily-text")
def daily_text(target: Optional[str] = Query(None)):
    """Preview the daily report text. Used by dashboard's Daily Report section."""
    d = date.fromisoformat(target) if target else _default_report_date()
    try:
        return {"date": d.isoformat(), "text": _build_daily_text(d)}
    except Exception as e:
        raise HTTPException(502, f"Failed to build report: {e}")


# ── Static assets (brand logos under /assets/) ─────────────
ASSETS_DIR = ROOT / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


# ── Dashboard routes (HTML at same origin as API) ─────────────
@app.get("/")
def root_dashboard():
    return FileResponse(DASHBOARD_FILE)


@app.get("/dashboard")
def dashboard_alias():
    return FileResponse(DASHBOARD_FILE)


if __name__ == "__main__":
    import uvicorn
    # Render injects PORT env var; default to 8000 for local
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

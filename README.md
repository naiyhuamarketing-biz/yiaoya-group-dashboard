# Yiaoya Group · Ads Dashboard

Multi-brand ads dashboard for Yiaoya Group (3 sub-brands · 5 ad accounts · 2 operators).
Pulls live Meta Marketing API data and presents per-brand + per-operator views with self-comparison.

Sister project to Glow Visage / Everly / Beautier (all under นายหัว 2497 กรุ๊ป).
Ads-only build — no LINE integration.

---

## Live links

| | URL |
|---|---|
| Live dashboard | `https://yiaoya-group.onrender.com` |
| GitHub repo | `https://github.com/naiyhuamarketing-biz/yiaoya-group-dashboard` |
| Render dashboard | (created during deploy) |

---

## Brands and accounts

| Brand | Account ID | Operator |
|---|---|---|
| KneeCare | 1271368901828972 (FA-N) | ฟา |
| Yiaoya | 1014027174637621 (FA-N) | ฟา |
| Yiaoya | 702987921684167 | ชุน |
| Resto Pilates | 941491285320998 (N) | ชุน |
| Resto Pilates | 843784871383763 | ชุน |

3 (Cancel) accounts excluded from the registry.

---

## Dashboard structure (6 sections)

1. **Performance Overview** — Spend / Revenue / ROAS / Inbox / CPI cards
2. **Daily Operations** — date picker → daily smartboard
3. **Top Highlights** — top ads in selected range, sorted by best ฿/inbox
4. **Performance Trend** — month-over-month + ROAS trend
5. **Self Compare** — ฟา & ชุน each vs their own previous period (NOT vs each other)
6. **Daily Report** — Thai daily summary text · click to copy

Filters at the top:
- Brand tabs: ทั้งหมด / เยียวยา / RESTO / KneeCare
- Operator filter: ทุกคน / ฟา / ชุน

---

## Tech

- Python 3.11 (FastAPI + uvicorn)
- HTML + Tailwind CDN + Chart.js
- facebook-business SDK
- Render (web service, free tier)
- GitHub Actions for keep-alive ping (every 14 min)

---

## Env vars (set in Render dashboard)

```
FB_ACCESS_TOKEN=...   (60-day long-lived; auto-renews on every API call)
FB_APP_ID=...
FB_APP_SECRET=...
TZ=Asia/Bangkok
PYTHON_VERSION=3.11
```

Account IDs are hardcoded in `lib/yiaoya_accounts.py` — no per-account env var.

---

## Run locally

```bash
cd ~/Desktop/Code/ads-report-yiaoya
./.venv/bin/python api_server.py     # FastAPI :8000
# Open http://localhost:8000
```

---

## Files

| File | Purpose |
|---|---|
| `api_server.py` | FastAPI server — serves HTML + Meta API endpoints |
| `dashboard.html` | Single-page Tailwind + Chart.js dashboard |
| `lib/yiaoya_accounts.py` | 5-account registry with brand/operator mapping |
| `lib/multi_loader.py` | Multi-account fetch + aggregation |
| `lib/meta_loader.py` | Meta Marketing API client (cache 10 min) |
| `lib/fb_ads.py` | Top ads query |
| `render.yaml` | Render blueprint (web service free tier) |
| `Procfile` | Render start command |
| `.github/workflows/keepalive.yml` | Pings Render every 14 min to prevent sleep |

---

## Brand palette

Yiaoya green `#2D6A4F` (primary) · Sage `#4A8765` · Mint `#74A57F` · Pale `#B7D5BC` · Mist `#DCEEDF` · Cream `#F5F2E7` · Gold accent `#B8945F`

Per-tab accent: Yiaoya forest green · RESTO charcoal+cream · KneeCare sky blue.

Fonts: **Italiana** (hero numbers) · **Noto Serif Thai** (เยียวยา hero word) · **Cormorant Garamond** (numbers) · **Prompt** (Thai body) · **Inter** (tabular nums).

# NSE Gap-Up Scanner + Telegram Alert Monitor
## Full Project Handover Document
**Owner:** Dhruv Thakkar (@itsdht)  
**Handover Date:** 26-May-2026  
**Status:** ~70% complete — core logic done, needs testing + fixes

---

## 1. PROJECT GOAL

Build a fully automated NSE trading tool that:
- **scanner.py** → Run manually at 9:08 AM and 9:20 AM IST → Outputs TradingView watchlist
- **monitor.py** → Runs in background 9:30 AM–3:30 PM → Sends Telegram alerts for intraday movers

No paid APIs. No subscriptions. Just Python + NSE public APIs + Google News RSS + Telegram bot.

---

## 2. TELEGRAM CREDENTIALS (REAL — DO NOT SHARE PUBLICLY)

```
Bot Token : 8629498177:AAFK2Hbs-FCB9E9fEHCmwhZv8eqQREf3IO8
Chat ID   : 7139375544
Bot Name  : @Tomorrow_SIA_bot
Owner     : @itsdht (Dhruv Thakkar)
```

**Action needed:** In `monitor.py` line ~40, replace:
```python
TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID_HERE'
```
With:
```python
TELEGRAM_CHAT_ID = '7139375544'
```

---

## 3. SYSTEM INFO

```
Machine  : MacBook Air (Apple Silicon)
OS       : macOS
Python   : 3.9.6
Packages : requests (installed via pip3)
           csv, io, xml, os, time — all built-in (no install needed)
Run from : Terminal (zsh)
```

---

## 4. FILTER LOGIC — EXACT RULES

### scanner.py (9:08 AM and 9:20 AM)
| Filter | Rule |
|--------|------|
| Gap Up | ≥ 3% from previous close (from NSE pre-open data) |
| Market Cap | > 1000 Cr |
| Avg Daily Turnover | > 5 Cr (last 5 trading days bhavcopy average) |
| Circuit Limit | ONLY 10% or 20% — exclude 2%, 5%, No Band |
| F&O stocks | EXCLUDE all F&O stocks |
| SME/Emerge | EXCLUDE all SME/Emerge board stocks |
| News (Non-IPO only) | Must have business/management news in last 3 days via Google News RSS |
| News exclusions | Skip GST notice, IT notice, court orders, penalty, sebi notice etc |
| IPO classification | Listed within last 365 days = IPO (news filter not applied) |

### RVOL Definition (Option C — % of avg daily volume)
```
RVOL% = (Volume traded today so far / Avg daily volume from bhavcopy) × 100
Examples:
  15%  = Stock already traded 15% of its typical full-day volume
  50%  = Half the typical day's volume done
  100% = Full avg daily volume already traded
```

### Time-Based RVOL Thresholds (auto-detected by script)
| Time Window | RVOL Threshold | Notes |
|------------|----------------|-------|
| Before 9:15 AM | None | Pre-open scan — market not open |
| 9:15 – 9:25 AM | RVOL ≥ 15% | Post-open first minutes |
| 9:25 – 11:00 AM | RVOL ≥ 50% | Early session |
| 11:00 AM onwards | RVOL ≥ 100% | Mid/late session |

### TradingView Watchlist Output — 4 Sections
```
###20% CIRCUIT — IPO
NSE:SYMBOL1
NSE:SYMBOL2

###20% CIRCUIT — NON-IPO
NSE:SYMBOL3

###10% CIRCUIT — IPO
NSE:SYMBOL4

###10% CIRCUIT — NON-IPO
NSE:SYMBOL5
```
Saved as `.txt` file to Desktop → Import via TradingView → Watchlist → ⋮ → Import list

---

### monitor.py (9:30 AM – 3:30 PM background)
| Filter | Rule |
|--------|------|
| Price move | ≥ 3% from previous close |
| RVOL | ≥ 100% (has traded full avg daily volume) |
| Market Cap | > 1000 Cr |
| Avg Turnover | > 5 Cr daily |
| Circuit | Only 10% or 20% |
| F&O | Excluded |
| SME | Excluded |
| Universe | NSE mainboard — Nifty Total Market + Midcap150 + Smallcap250 + Microcap250 |
| Alert frequency | Once per stock per day (no repeat spam) |
| Scan interval | Every 90 seconds |

---

## 5. FILES IN THIS PROJECT

```
nse-scanner/
├── scanner.py         ← Main watchlist generator (run at 9:08 and 9:20)
├── monitor.py         ← Background Telegram alert monitor (run at 9:30)
├── get_chat_id.py     ← One-time helper to find Telegram Chat ID (DONE — no longer needed)
└── HANDOVER.md        ← This file
```

---

## 6. WHAT IS DONE ✅

| Component | Status | Notes |
|-----------|--------|-------|
| NSE pre-open API fetch | ✅ Done | Handles cookies/warmup |
| Bhavcopy download (5-day avg) | ✅ Done | Turnover + volume baseline |
| F&O exclusion list | ✅ Done | From NSE API |
| SME exclusion | ✅ Done | NSE Emerge index |
| Circuit limit detection | ✅ Done | From price band string + upperCP calculation |
| Market cap filter | ✅ Done | From NSE trade_info API |
| Turnover filter | ✅ Done | From bhavcopy |
| RVOL% calculation | ✅ Done | (today_vol / avg_daily_vol) × 100 |
| Time-based RVOL auto-detection | ✅ Done | 4 time windows |
| IPO classification | ✅ Done | From NSE listing date |
| Google News RSS news filter | ✅ Done | Business keywords + exclusion list |
| TradingView .txt output | ✅ Done | 4-section format, saved to Desktop |
| Telegram alert format | ✅ Done | HTML format with emoji |
| Monitor universe (mainboard) | ✅ Done | Multi-index coverage |
| Daily alert dedup | ✅ Done | alerted_today set, resets daily |
| Auto session cookie refresh | ✅ Done | Re-warms every 20 min |
| Telegram credentials | ✅ Filled | Token + Chat ID hardcoded |

---

## 7. WHAT IS NOT DONE / NEEDS FIXING ❌

### Priority 1 — Critical (must fix before first real use)

**P1.1 — NSE API reliability**
- NSE returns 401 randomly, especially after 20-30 mins
- Current fix: re-warm cookies. Needs more robust retry logic.
- Suggestion: Add exponential backoff, rotate between 2-3 NSE API endpoints

**P1.2 — Market cap from NSE API is unreliable**
- `tradeInfo.totalMarketCap` is sometimes missing or in wrong units
- Current fallback: if not found, pass the stock through (don't block)
- Better fix: Cross-check with screener.in or use NSE's `/api/market-capitalization` endpoint
- Alternative: Maintain a local CSV of symbol→mcap updated weekly

**P1.3 — Circuit limit detection edge cases**
- Some stocks show band as "No Band" or blank — currently excluded (safe)
- Verify with real market data that 10%/20% detection logic is correct

**P1.4 — RVOL in pre-open (9:08 scan)**
- `quantityTraded` in pre-open is IEP quantity, not actual traded volume
- At 9:08, RVOL is correctly skipped (phase='preopen') ✅
- But confirm that at 9:20 the live volume starts flowing correctly

**P1.5 — Telegram Chat ID**
- Update `monitor.py` TELEGRAM_CHAT_ID from `YOUR_CHAT_ID_HERE` to `7139375544`

### Priority 2 — Nice to Have

**P2.1 — IPO listing date detection**
- NSE API `metadata.listingDate` field is inconsistent across stocks
- Some newer IPOs don't have this field → classified as Non-IPO incorrectly
- Fix: Maintain a local `ipo_list.csv` updated manually weekly OR scrape NSE new listings page

**P2.2 — News quality**
- Google News RSS is good but sometimes returns irrelevant results
- Consider adding a second check: if company name appears in title
- Or use NewsAPI free tier (1000 requests/day) for better filtering

**P2.3 — SME detection**
- NSE Emerge index API endpoint is unstable
- Better approach: Download NSE's official SME list CSV from archives

**P2.4 — TradingView direct import**
- NOT possible via API — TradingView has no public watchlist API
- The .txt copy-paste method is the only option
- Consider building a small Mac menubar app (using rumps library) that copies watchlist to clipboard automatically

**P2.5 — monitor.py bulk price fetch**
- Currently hits NSE index endpoints every 90s for ~1000 stocks
- This is 4-5 API calls per scan cycle — should be fine but monitor for 429 errors
- If throttled: increase scan_interval to 120s

### Priority 3 — Future Enhancements

- [ ] macOS LaunchAgent: auto-start monitor.py at 9:30, auto-kill at 3:31
- [ ] Daily summary Telegram message at 3:31 PM (how many alerts fired, top movers)
- [ ] Backtesting: did gapup stocks actually move further intraday?
- [ ] Web dashboard: simple HTML page showing today's alerts with timestamps

---

## 8. HOW TO RUN (COMPLETE STEP-BY-STEP)

### Setup (one time only)
```bash
# 1. Install requests (only package needed)
pip3 install requests

# 2. Download all 3 files to same folder e.g. ~/trading/
mkdir ~/trading
cd ~/trading
# Put scanner.py, monitor.py here

# 3. Fill in Chat ID in monitor.py
# Open monitor.py, find line with TELEGRAM_CHAT_ID
# Change 'YOUR_CHAT_ID_HERE' to '7139375544'
```

### Every trading morning
```bash
# Terminal 1 — at 9:08 AM
cd ~/trading
python3 scanner.py
# → Prints watchlist to terminal
# → Saves watchlist_PRE-OPEN_9:08_26May2026_0908.txt to Desktop
# → Import into TradingView (Watchlist → ⋮ → Import)

# Terminal 1 — at 9:20 AM (run again, same command)
python3 scanner.py
# → Now auto-applies RVOL ≥ 15% filter
# → Saves new watchlist file to Desktop

# Terminal 2 — at 9:30 AM (keep running all day)
python3 monitor.py
# → Runs in background, sends Telegram alerts
# → Keep this terminal open all day
# → Press Ctrl+C to stop after 3:30 PM
```

---

## 9. NSE API ENDPOINTS USED

| Purpose | Endpoint |
|---------|----------|
| Pre-open data | `GET /api/market-data-pre-open?key=ALL` |
| Stock quote + circuit | `GET /api/quote-equity?symbol={sym}` |
| Trade info + mcap | `GET /api/quote-equity?symbol={sym}&section=trade_info` |
| F&O list | `GET /api/equity-stockIndices?index=SECURITIES%20IN%20F%26O` |
| Nifty indices | `GET /api/equity-stockIndices?index={INDEX_NAME}` |
| Bhavcopy archive | `GET archives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv` |

**Important:** All NSE API calls require:
- Valid cookies from visiting `https://www.nseindia.com` first (handled by `_warm_up()`)
- `Referer: https://www.nseindia.com/` header
- Normal browser User-Agent string
- No API key needed

---

## 10. KNOWN ISSUES ENCOUNTERED

| Issue | Root Cause | Current Handling |
|-------|-----------|-----------------|
| `result:[]` on Telegram getUpdates | Bot not messaged by user yet | Send any message to bot first |
| NSE returns 401 | Cookie expired | Auto re-warm every 20 min |
| Bhavcopy not available | Weekend / holiday | Skip silently, no crash |
| MCap returns 0 | NSE API inconsistency | Pass stock through (not blocked) |
| CSV file has multiline headers | NSE export format | Handled with regex parser |

---

## 11. GITHUB SETUP PLAN

### What to put on GitHub
```
nse-scanner/
├── README.md              ← Setup instructions for new users
├── scanner.py             ← Main scanner (REMOVE hardcoded token)
├── monitor.py             ← Monitor (REMOVE hardcoded token — use config.py)
├── config.example.py      ← Template: TELEGRAM_BOT_TOKEN = '', CHAT_ID = ''
├── config.py              ← GITIGNORED — real credentials go here
├── requirements.txt       ← Just: requests
├── .gitignore             ← Ignore config.py, *.txt watchlists, __pycache__
└── HANDOVER.md            ← This file
```

### Step-by-step GitHub setup
```bash
# 1. Create repo on github.com → "New repository" → name: nse-scanner → Private

# 2. On your Mac terminal:
cd ~/trading
git init
git add scanner.py monitor.py HANDOVER.md requirements.txt
git commit -m "Initial commit — NSE scanner + Telegram monitor"
git remote add origin https://github.com/itsdht/nse-scanner.git
git push -u origin main

# 3. Create .gitignore
echo "config.py" >> .gitignore
echo "*.txt" >> .gitignore
echo "__pycache__/" >> .gitignore
git add .gitignore
git commit -m "Add gitignore"
git push
```

### CRITICAL: Before pushing to GitHub
Move credentials out of code:
```python
# config.py (NEVER push this file)
TELEGRAM_BOT_TOKEN = '8629498177:AAFK2Hbs-FCB9E9fEHCmwhZv8eqQREf3IO8'
TELEGRAM_CHAT_ID   = '7139375544'
```
Then in monitor.py replace hardcoded values with:
```python
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

---

## 12. WHAT TO ASK CHATGPT TO COMPLETE

Copy-paste this to ChatGPT:

---
> I have a Python project for NSE trading alerts. Here's the full context: [paste this entire HANDOVER.md]
> 
> Please help me:
> 1. Fix P1.1 — Make NSE API calls more robust (exponential backoff, better 401 handling)
> 2. Fix P1.2 — Market cap detection: if NSE API fails, fallback to bhavcopy-based mcap estimate
> 3. Fix P1.5 — Update TELEGRAM_CHAT_ID to 7139375544 in monitor.py
> 4. Add config.py pattern so credentials are not hardcoded
> 5. Create requirements.txt and README.md
> 6. Help me set up GitHub repo with proper .gitignore
> 
> Start with the files as-is and make targeted fixes only. Do not rewrite the whole script.

---

## 13. TESTING CHECKLIST (do this before relying on it for real trading)

- [ ] Run `scanner.py` during pre-open (9:08–9:15) → check output
- [ ] Run `scanner.py` at 9:20 → confirm RVOL filter activates
- [ ] Run `monitor.py` → confirm Telegram startup message received
- [ ] Wait for a stock to move 3%+ → confirm Telegram alert fires
- [ ] Check watchlist .txt file → import into TradingView → confirm 4 sections
- [ ] Run on a Monday after a weekend → confirm bhavcopy skips Saturday/Sunday
- [ ] Check that F&O stocks (e.g. RELIANCE) never appear in output
- [ ] Check that a 2% circuit stock never appears in output

---

*End of handover document*

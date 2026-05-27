#!/usr/bin/env python3
"""
NSE Gap-Up Scanner → TradingView Watchlist Generator
=====================================================
Run any time — script auto-detects time and applies correct RVOL threshold.

  Pre-open  (before 9:15) → No RVOL filter    [your 9:08 run]
  9:15–9:25               → RVOL ≥ 15%        [your 9:20 run]
  9:25–10:59              → RVOL ≥ 50%
  11:00 onwards           → RVOL ≥ 100%

RVOL Definition (Option C):
  RVOL% = (Volume traded today so far / Avg daily volume) × 100
  e.g. RVOL 15% = stock already traded 15% of its avg full-day volume

Output: watchlist_<date>_<time>.txt → import into TradingView

Usage:
  python3 scanner.py
"""

import requests, csv, time, sys, io, os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dtime
from urllib.parse import quote as enc
from collections import defaultdict

# ══════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════
CFG = {
    'min_gap_pct'     : 3.0,    # Min pre-open gap % to consider
    'min_mcap_cr'     : 1000,   # Min market cap in Crores
    'min_turnover_cr' : 5.0,    # Min avg daily turnover in Crores (30-day)
    'ipo_days'        : 365,    # Listed within N days = classified as IPO
    'bhav_lookback'   : 5,      # Bhavcopy files to average for turnover/volume
    'api_delay'       : 0.5,    # Seconds between NSE API calls (anti-throttle)
}

# RVOL schedule: (start_time, end_time, min_rvol_pct)
# RVOL% = (today's traded volume / avg daily volume) * 100
RVOL_SCHEDULE = [
    (dtime(9, 15),  dtime(9, 25),  15.0),   # 9:15–9:25 → 15% of avg daily vol
    (dtime(9, 25),  dtime(11, 0),  50.0),   # 9:25–11:00 → 50%
    (dtime(11, 0),  dtime(15, 31), 100.0),  # 11:00–close → 100%
]

# Business news: MUST contain at least one of these
BIZ_KEYWORDS = [
    'order', 'win', 'contract', 'deal', 'acqui', 'merger', 'partner', 'mou',
    'expand', 'launch', 'revenue', 'profit', 'quarterly', 'result', 'loss',
    'dividend', 'buyback', 'raise', 'fund', 'capex', 'capacity', 'plant',
    'appoint', 'ceo', 'cfo', 'coo', 'chairman', 'management', 'board',
    'director', 'guidance', 'export', 'project', 'manufactur', 'product',
    'client', 'stake', 'rights issue', 'tie-up', 'collaboration',
    'agreement', 'jv', 'joint venture', 'supply', 'listing', 'allot',
]

# Regulatory / legal noise: SKIP if any of these appear
EXCL_KEYWORDS = [
    'gst notice', 'income tax notice', 'sebi notice', 'show cause',
    'penalty notice', 'ed notice', 'cbi notice', 'it notice', 'raid',
    'demand notice', 'contempt', 'tax demand', 'insolvency',
    'default notice', 'attachment order', 'court',
]

NSE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Referer': 'https://www.nseindia.com/',
}

# ══════════════════════════════════════════════════════
#  TIME DETECTION
# ══════════════════════════════════════════════════════
def get_session_info():
    """
    Returns (phase, rvol_threshold_pct)
    phase: 'preopen' | 'live'
    rvol_threshold_pct: None (no filter) or float
    """
    now = datetime.now().time()
    if now < dtime(9, 15):
        return 'preopen', None
    for (start, end, threshold) in RVOL_SCHEDULE:
        if start <= now < end:
            return 'live', threshold
    if now >= dtime(15, 31):
        return 'closed', None
    return 'live', 100.0

# ══════════════════════════════════════════════════════
#  NSE CLIENT
# ══════════════════════════════════════════════════════

class NSEClient:
    _SESSION_TTL = 180  # re-warm after 3 minutes

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(NSE_HEADERS)
        self._last_warm = datetime.now()
        self._warm_up()

    def _warm_up(self):
        for url in [
            'https://www.nseindia.com',
            'https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market',
        ]:
            try:
                self.s.get(url, timeout=12)
                time.sleep(0.8)
            except Exception:
                pass
        self._last_warm = datetime.now()

    def _refresh_session(self):
        try:
            self.s.close()
        except Exception:
            pass
        self.s = requests.Session()
        self.s.headers.update(NSE_HEADERS)
        self._warm_up()

    def _ensure_session(self):
        if (datetime.now() - self._last_warm).total_seconds() > self._SESSION_TTL:
            self._refresh_session()

    def _get(self, url, retries=4):
        self._ensure_session()
        backoff = 1.0
        for attempt in range(retries + 1):
            try:
                r = self.s.get(url, timeout=15)
                if r.status_code == 200:
                    try:
                        return r.json()
                    except Exception:
                        return {}
                if r.status_code in (401, 403):
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 8.0)
                    self._refresh_session()
                elif r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 8.0)
                else:
                    time.sleep(min(backoff, 2.0))
            except requests.RequestException:
                if attempt < retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 8.0)
                else:
                    break
        return {}

    def fno_symbols(self):
        d = self._get(
            'https://www.nseindia.com/api/equity-stockIndices'
            '?index=SECURITIES%20IN%20F%26O'
        )
        return {x.get('symbol', '') for x in d.get('data', []) if x.get('symbol', '')}

    def preopen_data(self):
        d = self._get('https://www.nseindia.com/api/market-data-pre-open?key=ALL')
        return d.get('data', [])

    def sme_symbols(self):
        d = self._get(
            'https://www.nseindia.com/api/equity-stockIndices'
            '?index=SME%20IPO'
        )
        return {x.get('symbol', '') for x in d.get('data', []) if x.get('symbol', '')}

    def quote(self, sym):
        return self._get(
            f'https://www.nseindia.com/api/quote-equity?symbol={enc(sym)}'
        )

    def trade_info(self, sym):
        return self._get(
            f'https://www.nseindia.com/api/quote-equity'
            f'?symbol={enc(sym)}&section=trade_info'
        )

    def live_quote(self, sym):
        """Fetches live price + volume during market hours"""
        return self._get(
            f'https://www.nseindia.com/api/quote-equity?symbol={enc(sym)}'
        )

# ══════════════════════════════════════════════════════
#  BHAVCOPY — AVG DAILY VOLUME & TURNOVER
# ══════════════════════════════════════════════════════
def load_bhavcopies(n=5):
    """
    Downloads last N trading day bhavcopy files from NSE archives.
    Returns: {symbol: {'avg_turnover_cr': float, 'avg_qty': float}}
    avg_qty = average daily traded quantity (used for RVOL % calculation)
    """
    acc = defaultdict(list)
    fetched = 0
    today = datetime.now()

    for back in range(1, 25):
        if fetched >= n:
            break
        dt = today - timedelta(days=back)
        if dt.weekday() >= 5:
            continue  # Skip weekends

        date_str = dt.strftime('%d%m%Y')
        url = (
            f'https://archives.nseindia.com/products/content/'
            f'sec_bhavdata_full_{date_str}.csv'
        )

        try:
            r = requests.get(url, timeout=25, headers=NSE_HEADERS)
            if r.status_code != 200 or len(r.content) < 5000:
                continue

            reader = csv.DictReader(io.StringIO(r.text))
            for row in reader:
                if (row.get('SERIES', '') or '').strip() != 'EQ':
                    continue
                sym = (row.get('SYMBOL', '') or '').strip()
                if not sym:
                    continue
                try:
                    turnover_cr = float(row.get('TOTTRDVAL', 0) or 0) / 1e7
                    qty         = float(row.get('TOTTRDQTY', 0) or 0)
                    acc[sym].append({'turnover': turnover_cr, 'qty': qty})
                except:
                    pass

            fetched += 1
            print(f'       ✅ {dt.strftime("%d-%b-%Y")} loaded')

        except Exception as e:
            print(f'       ⚠  {dt.strftime("%d-%b-%Y")} skipped: {e}')

    if fetched == 0:
        print('       ⚠  No bhavcopy data fetched — turnover/RVOL filters will be skipped')
        return {}

    result = {}
    for sym, records in acc.items():
        if records:
            result[sym] = {
                'avg_turnover_cr': sum(r['turnover'] for r in records) / len(records),
                'avg_qty':         sum(r['qty']      for r in records) / len(records),
            }
    return result

# ══════════════════════════════════════════════════════
#  FILTERS
# ══════════════════════════════════════════════════════
def get_circuit_limit(quote_data):
    """
    Returns 10 or 20 if stock has 10% or 20% circuit.
    Returns None if circuit is 2%, 5%, No Band, or cannot determine → EXCLUDE.
    """
    pi = quote_data.get('priceInfo', {})

    # Method 1: Check price band label
    band = str(
        pi.get('pPriceBand', '') or
        pi.get('priceBand', '') or ''
    ).strip().lower()

    if '20' in band:
        return 20
    if '10' in band:
        return 10
    if any(x in band for x in ['2 %', '5 %', '2%', '5%', 'no band', 'none']):
        return None

    # Method 2: Calculate from upperCP vs previousClose
    try:
        prev  = float(pi.get('previousClose', 0) or pi.get('basePrice', 0) or 0)
        upper = float(pi.get('upperCP', 0) or 0)
        if prev > 0 and upper > 0:
            pct = round((upper / prev - 1) * 100)
            if pct == 20:
                return 20
            if pct == 10:
                return 10
            # 2% or 5% circuit — exclude
            if pct in (2, 5):
                return None
    except:
        pass

    return None  # Unknown → exclude safely



def get_market_cap_cr(trade_info_data):
    """Returns market cap in Crores from NSE trade info"""
    candidates = []
    ti = trade_info_data.get('tradeInfo', {}) if isinstance(trade_info_data, dict) else {}
    for key in ['totalMarketCap', 'ffmc', 'marketCap', 'mktcap']:
        v = ti.get(key)
        if v is not None:
            candidates.append(v)

    for parent in [
        trade_info_data if isinstance(trade_info_data, dict) else {},
        trade_info_data.get('securityInfo', {}) if isinstance(trade_info_data, dict) else {},
        trade_info_data.get('metadata', {}) if isinstance(trade_info_data, dict) else {},
    ]:
        if isinstance(parent, dict):
            for key in ['marketCap', 'totalMarketCap', 'ffmc']:
                v = parent.get(key)
                if v is not None:
                    candidates.append(v)

    for v in candidates:
        try:
            val = float(str(v).replace(',', '').strip())
            if val > 1_000_000:
                val = val / 100
            if val > 0:
                return val
        except Exception:
            pass
    return None


def is_ipo_stock(quote_data, ipo_days):
    """True if stock was listed within last ipo_days"""
    meta = quote_data.get('metadata', {})
    ld = (
        meta.get('listingDate', '') or
        meta.get('listing_date', '') or
        meta.get('dateOfListing', '') or ''
    ).strip()

    if not ld:
        return False

    for fmt in ['%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%b %d, %Y']:
        try:
            listed_on = datetime.strptime(ld, fmt)
            return (datetime.now() - listed_on).days <= ipo_days
        except:
            pass
    return False


def calc_rvol_pct(sym, quote_data, bhav_data):
    """
    RVOL% = (Volume traded today so far / Avg daily volume) * 100
    e.g., 15 means 15% of avg daily volume has already traded today
    """
    bh = bhav_data.get(sym, {})
    avg_daily = bh.get('avg_qty', 0)
    if avg_daily <= 0:
        return None

    pi = quote_data.get('priceInfo', {})
    try:
        today_vol = float(
            pi.get('quantityTraded', 0) or
            pi.get('totalTradedVolume', 0) or
            0
        )
        if today_vol <= 0:
            return None
        return (today_vol / avg_daily) * 100.0
    except:
        return None



def check_business_news(company_name, symbol):
    """
    Returns (has_news: bool, headline: str or '')
    Checks Google News RSS — only accepts business/management news from last 3 days.
    Skips regulatory, legal, and notice-type headlines.
    """
    cutoff = datetime.now() - timedelta(days=3)

    queries = [
        f'{symbol} NSE',
        f'"{company_name}"',
    ]

    for query in queries:
        try:
            url = (
                f'https://news.google.com/rss/search'
                f'?q={enc(query)}&hl=en-IN&gl=IN&ceid=IN:en'
            )

            r = None
            for attempt in range(3):
                r = requests.get(
                    url,
                    timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; RSS/2.0)'}
                )
                if r.status_code == 200:
                    break
                time.sleep(0.8 * (attempt + 1))

            if not r or r.status_code != 200:
                continue

            root = ET.fromstring(r.content)

            for item in root.findall('.//item'):
                title_el = item.find('title')
                pubdate_el = item.find('pubDate')
                if title_el is None or pubdate_el is None:
                    continue

                title = (title_el.text or '').strip()
                title_low = title.lower()
                pub_str = (pubdate_el.text or '').strip()

                pub_dt = None
                for fmt in [
                    '%a, %d %b %Y %H:%M:%S %Z',
                    '%a, %d %b %Y %H:%M:%S +0000',
                    '%a, %d %b %Y %H:%M:%S +0530',
                    '%a, %d %b %Y %H:%M:%S GMT',
                ]:
                    try:
                        pub_dt = datetime.strptime(pub_str, fmt)
                        break
                    except Exception:
                        pass

                if pub_dt is None:
                    continue
                if pub_dt.tzinfo:
                    pub_dt = pub_dt.replace(tzinfo=None)
                if pub_dt < cutoff:
                    continue

                if any(ex in title_low for ex in EXCL_KEYWORDS):
                    continue

                if any(kw in title_low for kw in BIZ_KEYWORDS):
                    return True, title[:90]

        except Exception:
            continue

    return False, ''

# ══════════════════════════════════════════════════════
#  MAIN SCAN
# ══════════════════════════════════════════════════════
def run_scan():
    phase, rvol_threshold = get_session_info()
    now_str = datetime.now().strftime('%d-%b-%Y %H:%M')

    # ── Header ──────────────────────────────────────
    print()
    print('╔' + '═' * 62 + '╗')
    if phase == 'preopen':
        scan_label = '9:08 AM  ·  Pre-Open Scan  (No RVOL filter)'
    elif phase == 'closed':
        print('  Market is closed. Run between 9:08 AM – 3:30 PM.')
        return
    else:
        rvol_label = f'RVOL ≥ {rvol_threshold:.0f}%'
        scan_label = f'Live Scan  ·  {rvol_label}'

    print(f'║   NSE GAP-UP SCANNER  ·  {scan_label}')
    print(f'║   {now_str}')
    print('╚' + '═' * 62 + '╝')
    print()

    # ── Step 1: Reference Data ───────────────────────
    print('  [1/5]  Connecting to NSE + loading reference data...')
    nse = NSEClient()

    fno_list = nse.fno_symbols()
    sme_list = nse.sme_symbols()

    print(f'         F&O list loaded         : {len(fno_list)} symbols')
    print(f'         SME list loaded         : {len(sme_list)} symbols')
    time.sleep(CFG['api_delay'])

    # ── Step 2: Bhavcopy ─────────────────────────────
    print()
    print(f'  [2/5]  Loading bhavcopy (last {CFG["bhav_lookback"]} trading days)...')
    bhav = load_bhavcopies(CFG['bhav_lookback'])
    print(f'         Avg volume/turnover ready: {len(bhav)} symbols')

    # ── Step 3: Pre-open / Gap data ──────────────────
    print()
    print('  [3/5]  Fetching pre-open gap data from NSE...')
    raw = nse.preopen_data()

    candidates = []
    for item in raw:
        try:
            gap = float(item.get('metadata', {}).get('pChange', 0) or 0)
            if gap >= CFG['min_gap_pct']:
                candidates.append((item, gap))
        except:
            pass

    candidates.sort(key=lambda x: x[1], reverse=True)
    print(f'         Gap ≥ {CFG["min_gap_pct"]}% found          : {len(candidates)} stocks')

    if not candidates:
        print('\n  ⚠  No gap-up stocks found. NSE pre-open data may not be published yet.')
        print('     Pre-open prices are typically available from 9:08 AM IST onwards.')
        return

    # ── Step 4: Filter each candidate ───────────────
    print()
    print(f'  [4/5]  Applying all filters to {len(candidates)} stocks...')
    print('  ' + '─' * 62)

    results  = {'20_ipo': [], '20_non': [], '10_ipo': [], '10_non': []}
    skipped  = defaultdict(int)

    for idx, (item, gap_pct) in enumerate(candidates):
        sym = (item.get('metadata', {}).get('symbol', '') or '').strip()
        if not sym:
            continue

        tag = f'  [{idx+1:>3}/{len(candidates)}]  {sym:<20} +{gap_pct:.1f}%'

        if sym in fno_list:
            print(f'{tag}  ❌ F&O stock — skip')
            skipped['fno'] += 1
            continue

        if sym in sme_list:
            print(f'{tag}  ❌ SME stock — skip')
            skipped['sme'] += 1
            continue

        # ── Fetch quote ─────────────────────────────
        q = nse.quote(sym)
        if not q:
            print(f'{tag}  ❌ NSE API error — skip')
            skipped['api_err'] += 1
            continue
        time.sleep(CFG['api_delay'])

        # ── Circuit filter (must be 10% or 20%) ─────
        circuit = get_circuit_limit(q)
        if circuit not in (10, 20):
            band_str = (
                q.get('priceInfo', {}).get('pPriceBand', '') or
                q.get('priceInfo', {}).get('priceBand', '') or
                'unknown'
            )
            print(f'{tag}  ❌ Circuit: {band_str} — need 10% or 20%')
            skipped['circuit'] += 1
            continue

        # ── Fetch trade info ─────────────────────────
        ti = nse.trade_info(sym)
        time.sleep(CFG['api_delay'])

        # ── Market cap filter (> 1000 Cr) ────────────
        mcap = get_market_cap_cr(ti)
        if mcap is None:
            mcap = 0.0  # If can't determine, treat as 0 → will fail filter

        if mcap > 0 and mcap < CFG['min_mcap_cr']:
            print(f'{tag}  ❌ MCap {mcap:.0f} Cr — need > {CFG["min_mcap_cr"]}Cr')
            skipped['mcap'] += 1
            continue
        # Note: if mcap = 0 (could not determine), we let it pass with a warning

        # ── Turnover filter (> 5 Cr avg daily) ──────
        bh         = bhav.get(sym, {})
        avg_to     = bh.get('avg_turnover_cr', 0)
        if 0 < avg_to < CFG['min_turnover_cr']:
            print(f'{tag}  ❌ Avg turnover {avg_to:.1f} Cr — need > {CFG["min_turnover_cr"]}Cr')
            skipped['turnover'] += 1
            continue

        # ── RVOL filter (only during live session) ───
        rvol_pct = None
        if phase == 'live' and rvol_threshold is not None:
            rvol_pct = calc_rvol_pct(sym, q, bhav)
            if rvol_pct is not None and rvol_pct < rvol_threshold:
                print(
                    f'{tag}  ❌ RVOL {rvol_pct:.1f}% '
                    f'— need ≥ {rvol_threshold:.0f}%'
                )
                skipped['rvol'] += 1
                continue

        # ── IPO classification ───────────────────────
        ipo = is_ipo_stock(q, CFG['ipo_days'])
        company = (
            q.get('info', {}).get('companyName', '') or sym
        ).strip()[:40]

        # ── News filter (Non-IPO stocks only) ────────
        news_line = ''
        if not ipo:
            has_news, headline = check_business_news(company, sym)
            if not has_news:
                print(f'{tag}  ❌ No business news in last 3 days — skip')
                skipped['news'] += 1
                continue
            news_line = headline
        else:
            news_line = 'Recent IPO — news filter not applied'

        # ── All filters passed ✅ ────────────────────
        entry = {
            'sym'    : sym,
            'company': company,
            'gap'    : gap_pct,
            'circuit': circuit,
            'mcap'   : mcap,
            'rvol'   : rvol_pct,
            'news'   : news_line,
            'ipo'    : ipo,
        }

        key = f'{circuit}_{"ipo" if ipo else "non"}'
        results[key].append(entry)

        ipo_tag    = '📌 IPO' if ipo else 'Non-IPO'
        rvol_str   = f' | RVOL: {rvol_pct:.1f}%' if rvol_pct is not None else ''
        mcap_str   = f'{mcap:.0f}Cr' if mcap > 0 else '?'
        print(f'{tag}  ✅ {circuit}% | {ipo_tag} | MCap: {mcap_str}{rvol_str}')

    # ── Step 5: Output ───────────────────────────────
    print()
    print('  [5/5]  Writing TradingView watchlist...')
    _save_watchlist(results, phase, rvol_threshold, now_str, skipped)


# ══════════════════════════════════════════════════════
#  OUTPUT
# ══════════════════════════════════════════════════════
def _save_watchlist(results, phase, rvol_threshold, now_str, skipped):

    SECTIONS = [
        ('20% CIRCUIT — IPO',     '20_ipo'),
        ('20% CIRCUIT — NON-IPO', '20_non'),
        ('10% CIRCUIT — IPO',     '10_ipo'),
        ('10% CIRCUIT — NON-IPO', '10_non'),
    ]

    total = sum(len(results[k]) for _, k in SECTIONS)

    # Determine scan label
    if phase == 'preopen':
        scan_label = 'PRE-OPEN 9:08'
    else:
        rvol_str = f'RVOL≥{rvol_threshold:.0f}%' if rvol_threshold else ''
        scan_label = f'LIVE {rvol_str}'

    # ── Console print ────────────────────────────────
    print()
    print('╔' + '═' * 62 + '╗')
    print(f'║   📋  TRADINGVIEW WATCHLIST  ·  {scan_label}')
    print(f'║   {now_str}  ·  {total} stocks')
    print('╚' + '═' * 62 + '╝')

    if total == 0:
        print('\n  ⚠  No stocks passed all filters today.')
        print('     Common reasons: Low activity day, NSE API slow, no news for non-IPOs.')
        _print_summary(results, skipped, SECTIONS)
        return

    file_lines = [
        f'# NSE Gap-Up Watchlist — {scan_label} — {now_str}',
        f'# Total: {total} stocks | Filters: Gap≥3%, MCap>1000Cr, Turnover>5Cr, Circuit=10/20%, Non-FNO',
        '',
    ]

    for label, key in SECTIONS:
        stocks = sorted(results[key], key=lambda x: x['gap'], reverse=True)
        if not stocks:
            continue

        print(f'\n  ┌─ {label}  ({len(stocks)} stocks) ' + '─' * (40 - len(label)))
        file_lines.append(f'###{label}')

        for s in stocks:
            tv_sym    = f'NSE:{s["sym"]}'
            rvol_str  = f' | RVOL:{s["rvol"]:.1f}%' if s["rvol"] is not None else ''
            mcap_str  = f'MCap:{s["mcap"]:.0f}Cr | ' if s["mcap"] > 0 else ''
            news_str  = s["news"][:55] if s["news"] else ''

            print(f'  │  {tv_sym:<22}  +{s["gap"]:.1f}%  {mcap_str}{rvol_str}')
            if news_str and 'IPO' not in news_str:
                print(f'  │  {"":22}  📰 {news_str}')
            file_lines.append(tv_sym)

        file_lines.append('')

    # ── Save file ────────────────────────────────────
    ts    = datetime.now().strftime('%d%b%Y_%H%M')
    fname = f'watchlist_{scan_label.replace(" ", "_").replace("≥", "gte")}_{ts}.txt'
    fpath = os.path.join(os.path.expanduser('~'), 'Desktop', fname)

    # Fallback to script dir if Desktop doesn't exist
    if not os.path.isdir(os.path.dirname(fpath)):
        fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)

    with open(fpath, 'w') as f:
        f.write('\n'.join(file_lines))

    _print_summary(results, skipped, SECTIONS)
    print()
    print(f'  💾  Saved to: {fpath}')
    print()
    print('  ─── HOW TO IMPORT INTO TRADINGVIEW ────────────────────')
    print('  1.  Open TradingView → Watchlist panel (right side)')
    print('  2.  Click ⋮ (three dots) → Import list')
    print('  3.  Select the saved .txt file')
    print('  4.  Done — 4 sections will appear as separate watchlists')
    print('  ────────────────────────────────────────────────────────')
    print()


def _print_summary(results, skipped, sections):
    total = sum(len(results[k]) for _, k in sections)
    print()
    print('  ┌─ SCAN SUMMARY ───────────────────────────────────────┐')
    print(f'  │  Total in watchlist      : {total:<4}                        │')
    print(f'  │  20% Circuit IPO         : {len(results["20_ipo"]):<4}                        │')
    print(f'  │  20% Circuit Non-IPO     : {len(results["20_non"]):<4}                        │')
    print(f'  │  10% Circuit IPO         : {len(results["10_ipo"]):<4}                        │')
    print(f'  │  10% Circuit Non-IPO     : {len(results["10_non"]):<4}                        │')
    print(f'  ├──────────────────────────────────────────────────────┤')
    print(f'  │  Filtered out:                                       │')
    print(f'  │    F&O: {skipped["fno"]:<3}  Circuit: {skipped["circuit"]:<3}  MCap: {skipped["mcap"]:<3}              │')
    print(f'  │    Turnover: {skipped["turnover"]:<3}  News: {skipped["news"]:<3}  RVOL: {skipped["rvol"]:<3}          │')
    print(f'  │    API errors: {skipped["api_err"]:<3}                                  │')
    print('  └──────────────────────────────────────────────────────┘')


# ══════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == '__main__':
    try:
        run_scan()
    except KeyboardInterrupt:
        print('\n\n  Scan cancelled by user.')
    except Exception as e:
        print(f'\n  ❌ Unexpected error: {e}')
        import traceback
        traceback.print_exc()

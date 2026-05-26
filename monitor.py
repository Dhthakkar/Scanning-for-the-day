#!/usr/bin/env python3
"""
NSE Intraday Alert Monitor → Telegram Notifications
=====================================================
Runs in background 9:30 AM – 3:30 PM IST.

Alert fires when ANY stock in NSE mainboard universe meets ALL of:
  ✅ Price moved ≥ 3% from previous close
  ✅ RVOL% ≥ 100  (has already traded 100%+ of avg daily volume)
  ✅ Market cap > 1000 Cr
  ✅ Circuit limit = 10% or 20% only  (2% and 5% excluded)
  ✅ Not an F&O stock
  ✅ Not an SME / Emerge stock
  ✅ Avg daily turnover > 5 Cr (liquid stock only)

Each stock fires ONE alert per day max. No spam.

Setup:
  1. Run:  python3 get_chat_id.py   ← find your TELEGRAM_CHAT_ID
  2. Paste it below
  3. Run:  python3 monitor.py
  4. Leave terminal open (background: nohup python3 monitor.py &)
  5. Stop:  Ctrl+C

RVOL% = (volume traded today so far / avg daily volume) × 100
"""


import requests, csv, time, io, os
from datetime import datetime, timedelta, time as dtime
from urllib.parse import quote as enc
from collections import defaultdict

try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except Exception:
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# ══════════════════════════════════════════════════════
#  ⚙️  CONFIG — credentials loaded from config.py or env vars
# ══════════════════════════════════════════════════════
ALERT_CFG = {
    'min_change_pct'   : 3.0,
    'min_rvol_pct'     : 100.0,   # 100% = already traded full avg daily volume
    'min_mcap_cr'      : 1000,
    'min_turnover_cr'  : 5.0,
    'scan_interval'    : 90,      # seconds between each scan cycle
    'market_open'      : dtime(9, 30),
    'market_close'     : dtime(15, 30),
    'bhav_lookback'    : 5,
}

NSE_HDR = {
    'User-Agent' : 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
    'Accept'     : 'application/json, text/plain, */*',
    'Referer'    : 'https://www.nseindia.com/',
}

# ══════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print()
        print(f"  [TELEGRAM OFF] {text[:120]}")
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=10
        )
    except Exception as e:
        print(f'  ⚠ Telegram: {e}')


def alert_message(sym, company, chg, rvol, mcap, circuit):
    arrow = '🚀' if chg >= 0 else '🔻'
    sign  = '+' if chg >= 0 else ''
    t     = datetime.now().strftime('%H:%M')
    return (
        f'{arrow} <b>{sym}</b>  {sign}{chg:.1f}%\n'
        f'🏢 {company}\n'
        f'📊 RVOL: <b>{rvol:.0f}%</b>  ⚡ Circuit: {circuit}%\n'
        f'💰 MCap: {mcap:.0f} Cr  🕐 {t} IST\n'
        f'🔗 https://www.nseindia.com/get-quotes/equity?symbol={sym}'
    )

# ══════════════════════════════════════════════════════
#  NSE CLIENT
# ══════════════════════════════════════════════════════

class NSEClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(NSE_HDR)
        self._last_warm = datetime.now()
        self._warm()

    def _warm(self):
        try:
            self.s.get('https://www.nseindia.com', timeout=12)
            time.sleep(1.0)
        except Exception:
            pass
        self._last_warm = datetime.now()

    def _refresh_session(self):
        try:
            self.s.close()
        except Exception:
            pass
        self.s = requests.Session()
        self.s.headers.update(NSE_HDR)
        self._warm()

    def _auto_warm(self):
        if (datetime.now() - self._last_warm).total_seconds() > 1200:
            self._warm()

    def _get(self, url, retries=4):
        self._auto_warm()
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

    def index_stocks(self, index_name):
        d = self._get(
            'https://www.nseindia.com/api/equity-stockIndices'
            f'?index={enc(index_name)}'
        )
        return d.get('data', [])

    def fno_symbols(self):
        d = self._get(
            'https://www.nseindia.com/api/equity-stockIndices'
            '?index=SECURITIES%20IN%20F%26O'
        )
        return {x.get('symbol', '') for x in d.get('data', []) if x.get('symbol', '')}

    def sme_symbols(self):
        """NSE Emerge (SME) stocks to exclude"""
        symbols = set()
        for idx in ['NIFTY EMERGE', 'NIFTY SME EMERGE']:
            d = self._get(
                f'https://www.nseindia.com/api/equity-stockIndices?index={enc(idx)}'
            )
            for x in d.get('data', []):
                s = x.get('symbol', '')
                if s:
                    symbols.add(s)
        return symbols

    def quote(self, sym):
        return self._get(f'https://www.nseindia.com/api/quote-equity?symbol={enc(sym)}')

    def trade_info(self, sym):
        return self._get(
            f'https://www.nseindia.com/api/quote-equity?symbol={enc(sym)}&section=trade_info'
        )

# ══════════════════════════════════════════════════════
#  BHAVCOPY — baseline volume & turnover
# ══════════════════════════════════════════════════════
def load_bhav(n=5):
    print('  Loading bhavcopy baseline...')
    acc = defaultdict(list)
    fetched = 0
    for back in range(1, 20):
        if fetched >= n:
            break
        dt = datetime.now() - timedelta(days=back)
        if dt.weekday() >= 5:
            continue
        url = (f'https://archives.nseindia.com/products/content/'
               f'sec_bhavdata_full_{dt.strftime("%d%m%Y")}.csv')
        try:
            r = requests.get(url, timeout=25, headers=NSE_HDR)
            if r.status_code != 200 or len(r.content) < 5000:
                continue
            for row in csv.DictReader(io.StringIO(r.text)):
                if (row.get('SERIES','') or '').strip() != 'EQ':
                    continue
                sym = (row.get('SYMBOL','') or '').strip()
                if not sym:
                    continue
                try:
                    acc[sym].append({
                        'to' : float(row.get('TOTTRDVAL', 0) or 0) / 1e7,
                        'qty': float(row.get('TOTTRDQTY', 0) or 0),
                    })
                except:
                    pass
            fetched += 1
            print(f'    ✅ {dt.strftime("%d-%b-%Y")}')
        except:
            pass

    out = {}
    for sym, recs in acc.items():
        if recs:
            out[sym] = {
                'avg_to' : sum(r['to']  for r in recs) / len(recs),
                'avg_qty': sum(r['qty'] for r in recs) / len(recs),
            }
    print(f'  Baseline ready: {len(out)} symbols\n')
    return out

# ══════════════════════════════════════════════════════
#  FILTER HELPERS
# ══════════════════════════════════════════════════════
def circuit_limit(q):
    pi   = q.get('priceInfo', {})
    band = str(pi.get('pPriceBand','') or pi.get('priceBand','')).lower()
    if '20' in band: return 20
    if '10' in band: return 10
    if any(x in band for x in ['2%','5%','2 ','5 ','no band']): return None
    try:
        prev  = float(pi.get('previousClose', 0) or pi.get('basePrice', 0) or 0)
        upper = float(pi.get('upperCP', 0) or 0)
        if prev > 0 and upper > 0:
            r = round((upper / prev - 1) * 100)
            if r == 20: return 20
            if r == 10: return 10
            if r in (2, 5): return None
    except:
        pass
    return None


def mcap_cr(ti):
    for k in ['totalMarketCap','ffmc','marketCap']:
        v = ti.get('tradeInfo', {}).get(k)
        if v:
            try:
                val = float(str(v).replace(',',''))
                if val > 1_000_000: val /= 100
                if val > 0: return val
            except:
                pass
    return None


def rvol_pct(sym, q, bhav):
    avg = bhav.get(sym, {}).get('avg_qty', 0)
    if avg <= 0: return None
    pi = q.get('priceInfo', {})
    try:
        vol = float(pi.get('quantityTraded', 0) or pi.get('totalTradedVolume', 0) or 0)
        if vol <= 0: return None
        return (vol / avg) * 100.0
    except:
        return None

# ══════════════════════════════════════════════════════
#  BUILD UNIVERSE: all mainboard NSE stocks > 1000 Cr
# ══════════════════════════════════════════════════════
def build_universe(nse):
    """
    Fetches stocks from multiple NSE indices to get comprehensive
    mainboard coverage. Filters: Not SME, not already in excluded sets.
    """
    indices = [
        'NIFTY TOTAL MARKET',      # ~750 stocks — broadest NSE index
        'NIFTY 500',
        'NIFTY MIDCAP 150',
        'NIFTY SMALLCAP 250',
        'NIFTY MICROCAP 250',
    ]

    all_stocks = {}
    for idx in indices:
        try:
            data = nse.index_stocks(idx)
            for s in data:
                sym = (s.get('symbol','') or '').strip()
                if sym and sym not in all_stocks:
                    all_stocks[sym] = s
            print(f'    ✅ {idx}: {len(data)} stocks')
            time.sleep(0.6)
        except:
            print(f'    ⚠  {idx}: failed to load')

    return all_stocks  # {symbol: data_dict}

# ══════════════════════════════════════════════════════
#  MAIN MONITOR LOOP
# ══════════════════════════════════════════════════════
def run():
    print()
    print('╔' + '═' * 58 + '╗')
    print('║   📡  NSE INTRADAY ALERT MONITOR                     ║')
    print(f'║   Started : {datetime.now().strftime("%d-%b-%Y %H:%M:%S")}               ║')
    print(f'║   Alert   : ≥3% move + RVOL ≥ 100% + MCap >1000Cr  ║')
    print(f'║   Scan    : every {ALERT_CFG["scan_interval"]}s  ·  Universe: NSE mainboard ║')
    print('╚' + '═' * 58 + '╝')
    print()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('  ⚠  TELEGRAM NOT CONFIGURED')
        print('     Create config.py from config.example.py')
        print('     Continuing in console-only mode...\n')
    else:
        send_telegram(
            '✅ <b>NSE Monitor Started</b>\n'
            f'Watching NSE mainboard for:\n'
            f'• ≥3% move from prev close\n'
            f'• RVOL ≥ 100%  (volume ≥ full avg daily)\n'
            f'• MCap > 1000 Cr  |  Circuit 10% or 20%\n'
            f'• Non-SME  |  Non-F&O\n'
            f'Scan every 90 seconds. 9:30 AM – 3:30 PM IST'
        )
        print('  ✅ Telegram connected\n')

    # ── Load reference data once ──────────────────
    print('  [1/3] Connecting to NSE...')
    nse = NSEClient()

    print('  [2/3] Loading exclusion lists...')
    fno = nse.fno_symbols()
    sme = nse.sme_symbols()
    print(f'         F&O: {len(fno)} | SME/Emerge: {len(sme)}\n')

    print('  [3/3] Loading bhavcopy (avg volume baseline)...')
    bhav = load_bhav(ALERT_CFG['bhav_lookback'])

    print('  Building stock universe...')
    universe = build_universe(nse)
    # Pre-exclude F&O and SME from universe
    universe = {
        sym: data for sym, data in universe.items()
        if sym not in fno and sym not in sme
    }
    print(f'  Universe ready: {len(universe)} mainboard non-F&O non-SME stocks\n')

    alerted_today = set()
    last_date     = datetime.now().date()
    scan_num      = 0

    while True:
        now = datetime.now()

        # Daily reset
        if now.date() != last_date:
            alerted_today.clear()
            last_date = now.date()
            # Refresh universe daily (new listings, index changes)
            universe = build_universe(nse)
            universe = {s: d for s, d in universe.items() if s not in fno and s not in sme}
            print(f'\n  📅 New day — universe refreshed: {len(universe)} stocks\n')

        cur_time = now.time()

        if cur_time < ALERT_CFG['market_open']:
            left = (datetime.combine(now.date(), ALERT_CFG['market_open']) - now).seconds
            print(f'  ⏳ Market opens 9:30 AM — {left//60}m {left%60}s to go...')
            time.sleep(min(left, 60))
            continue

        if cur_time >= ALERT_CFG['market_close']:
            print('\n  🔴 Market closed. Shutting down.')
            send_telegram('🔴 <b>NSE Monitor OFF</b> — Market closed. Back tomorrow 9:30 AM.')
            break

        scan_num += 1
        ts = now.strftime('%H:%M:%S')
        print(f'\n  [{ts}] Scan #{scan_num}  ', end='', flush=True)

        # ── STEP 1: Bulk fetch latest prices from indices ────────
        # Re-fetch Nifty Total Market for fresh prices (bulk, 1 API call)
        try:
            bulk = nse.index_stocks('NIFTY TOTAL MARKET')
            bulk += nse.index_stocks('NIFTY MIDCAP 150')
            bulk += nse.index_stocks('NIFTY SMALLCAP 250')
            bulk += nse.index_stocks('NIFTY MICROCAP 250')

            # Deduplicate
            seen, fresh = set(), {}
            for s in bulk:
                sym = (s.get('symbol','') or '').strip()
                if sym and sym not in seen and sym in universe:
                    seen.add(sym)
                    fresh[sym] = s

            print(f'{len(fresh)} stocks  ', end='', flush=True)

        except Exception as e:
            print(f'Bulk fetch error: {e}')
            time.sleep(30)
            continue

        # ── STEP 2: Pre-filter — price ≥ 3% change ──────────────
        movers = []
        for sym, s in fresh.items():
            if sym in alerted_today:
                continue
            try:
                chg = float(s.get('pChange', 0) or 0)
                if abs(chg) >= ALERT_CFG['min_change_pct']:
                    movers.append((sym, chg))
            except:
                pass

        print(f'→ {len(movers)} with ≥{ALERT_CFG["min_change_pct"]}%  ', end='', flush=True)

        if not movers:
            print('nothing new')
            time.sleep(ALERT_CFG['scan_interval'])
            continue

        # ── STEP 3: Deep filter each mover ──────────────────────
        alerts_sent = 0

        for sym, chg in movers:
            try:
                time.sleep(0.4)
                q = nse.quote(sym)
                if not q:
                    continue

                # Circuit — must be 10% or 20%
                ckt = circuit_limit(q)
                if ckt not in (10, 20):
                    continue

                # RVOL — must be ≥ 100%
                rv = rvol_pct(sym, q, bhav)
                if rv is None or rv < ALERT_CFG['min_rvol_pct']:
                    continue

                # Turnover — avg daily > 5 Cr
                avg_to = bhav.get(sym, {}).get('avg_to', 0)
                if 0 < avg_to < ALERT_CFG['min_turnover_cr']:
                    continue

                # Market cap — > 1000 Cr
                time.sleep(0.3)
                ti = nse.trade_info(sym)
                mc = mcap_cr(ti)
                if mc is not None and mc < ALERT_CFG['min_mcap_cr']:
                    continue

                # ✅ All passed — send alert
                alerted_today.add(sym)
                company = (q.get('info', {}).get('companyName','') or sym)[:35]
                mc_val  = mc if mc else 0
                msg     = alert_message(sym, company, chg, rv, mc_val, ckt)

                send_telegram(msg)
                print(f'\n  🚨 {sym}  {chg:+.1f}%  RVOL:{rv:.0f}%  {ckt}%  MCap:{mc_val:.0f}Cr')
                alerts_sent += 1

            except:
                pass

        if alerts_sent == 0:
            print('no new alerts')
        else:
            print(f'\n  → {alerts_sent} alert(s) sent')

        time.sleep(ALERT_CFG['scan_interval'])


if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        print('\n\n  Stopped by user.')
        send_telegram('⚠️ <b>NSE Monitor</b> — Manually stopped.')
    except Exception as e:
        import traceback
        traceback.print_exc()
        send_telegram(f'❌ <b>Monitor crashed:</b>\n{str(e)[:200]}')

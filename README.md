# NSE Gap-Up Scanner + Telegram Alert Monitor

Automated NSE trading tool for Indian stock market.

## What it does
- **scanner.py** → Run at 9:08 AM + 9:20 AM → Outputs TradingView watchlist (4 sections)
- **monitor.py** → Background process 9:30–3:30 PM → Telegram alerts for intraday movers
- **config.py** → Local-only credentials file; never commit this

## Setup
```bash
pip3 install -r requirements.txt
cp config.example.py config.py
# edit config.py with your Telegram bot token and chat id
```

## Usage
```bash
python3 scanner.py    # 9:08 AM and 9:20 AM — auto-detects time
python3 monitor.py    # 9:30 AM — keep running all day
```

See HANDOVER.md for full documentation.

## Daily workflow
1. Run `python3 scanner.py` at 9:08 AM IST.
2. Run `python3 scanner.py` again around 9:20 AM IST.
3. Start `python3 monitor.py` at 9:30 AM IST and leave it running.

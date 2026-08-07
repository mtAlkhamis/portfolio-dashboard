"""
update_etf_holdings.py
──────────────────────
Downloads the latest SPUS holdings CSV from SP Funds and rewrites the
SPUS_HOLDINGS_FALLBACK constant in the dashboard HTML.

Run automatically by the weekly benchmark task (Monday 09:05).
Safe to run even if the download fails — exits gracefully with a message.

Requirements: pip install requests --break-system-packages
"""

import re
import io
import json
import requests
from datetime import date
from typing import Optional

from pathlib import Path
HTML_PATH  = Path(__file__).parent / "Family Portfolio Dashboard.html"
SPUS_URL   = 'https://www.sp-funds.com/wp-content/uploads/data/TidalFG_Holdings_SPUS.csv'
HEADERS    = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def fetch_spus() -> Optional[list]:
    try:
        r = requests.get(SPUS_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f'  ✗ Could not download SPUS CSV: {e}')
        return None

    lines = r.text.strip().split('\n')
    if len(lines) < 2:
        print('  ✗ SPUS CSV too short')
        return None

    headers = [h.strip() for h in lines[0].split(',')]
    ticker_idx  = next((i for i, h in enumerate(headers) if h == 'StockTicker'), None)
    name_idx    = next((i for i, h in enumerate(headers) if h == 'SecurityName'), None)
    weight_idx  = next((i for i, h in enumerate(headers) if h == 'Weightings'), None)

    if ticker_idx is None or weight_idx is None:
        print(f'  ✗ Unexpected CSV columns: {headers}')
        return None

    rows = []
    for line in lines[1:]:
        cols = line.split(',')
        if len(cols) <= max(filter(None, [ticker_idx, name_idx, weight_idx])):
            continue
        ticker = cols[ticker_idx].strip()
        name   = cols[name_idx].strip() if name_idx is not None else ticker
        try:
            pct = float(cols[weight_idx].strip().replace('%', ''))
        except ValueError:
            continue
        if not ticker or ticker in ('nan', '-', '') or pct <= 0 or len(ticker) > 10:
            continue
        if 'cash' in ticker.lower() or 'cash' in name.lower():
            continue
        rows.append({'symbol': ticker, 'name': name, 'percent': round(pct, 4)})

    rows.sort(key=lambda x: -x['percent'])
    print(f'  ✓ Parsed {len(rows)} SPUS holdings')
    return rows


def js_array(holdings: list[dict]) -> str:
    lines = [f"  {{symbol:'{h['symbol']}', name:{json.dumps(h['name'])}, percent:{h['percent']}}}" for h in holdings]
    return '[\n' + ',\n'.join(lines) + '\n]'


def update_html(holdings: list[dict]) -> bool:
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    pattern = r'(const SPUS_HOLDINGS_FALLBACK\s*=\s*)\[[\s\S]*?\](\s*;)'
    replacement = r'\g<1>' + js_array(holdings) + r'\2'
    new_html, n = re.subn(pattern, replacement, html, count=1)

    if n == 0:
        print('  ✗ Could not find SPUS_HOLDINGS_FALLBACK in HTML — skipping write')
        return False

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print(f'  ✓ Updated SPUS_HOLDINGS_FALLBACK in HTML ({len(holdings)} stocks, {date.today()})')
    return True


def main():
    print(f'[ETF Holdings] SPUS refresh — {date.today()}')
    holdings = fetch_spus()
    if holdings and len(holdings) > 50:
        update_html(holdings)
    else:
        print('  ⚠ Not enough holdings parsed — HTML left unchanged')


if __name__ == '__main__':
    main()

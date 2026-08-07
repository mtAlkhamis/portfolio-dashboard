#!/usr/bin/env python3
"""
update_benchmark.py
───────────────────
Appends new weekly data points to the benchmark chart arrays inside
Family Portfolio Dashboard.html.

Run weekly (e.g. every Monday morning after markets have closed Friday).

When holdings change, update SPUS_SHARES / SMH_SHARES below.

Data source strategy:
  1. Try yfinance (Yahoo Finance) — gives full historical weekly candles,
     so it can backfill every missed week accurately.
  2. If Yahoo is unreachable (e.g. this sandbox's network proxy blocks it),
     fall back to the Finnhub quote endpoint — the same API the dashboard's
     HTML already uses for live prices, and reachable from this sandbox.
     Finnhub's free tier only gives the *current* quote (no historical
     candles), so the fallback path persists last-seen raw prices in
     benchmark_state.json and uses them as the baseline for next run.
     This means multiple missed weeks get compressed into a single jump
     instead of being backfilled week-by-week — acceptable since the task
     runs weekly on schedule.
"""

import json
import re
import sys
import site
from pathlib import Path
from datetime import datetime, timedelta

# ── Ensure user site-packages are on the path ─────────────────────────────────
for _p in site.getusersitepackages() if isinstance(site.getusersitepackages(), list) else [site.getusersitepackages()]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Config — update when holdings change ─────────────────────────────────────
HTML_FILE   = Path(__file__).parent / "Family Portfolio Dashboard.html"
STATE_FILE  = Path(__file__).parent / "benchmark_state.json"
SPUS_SHARES = 1847.21
SMH_SHARES  = 184.60
TICKERS     = ["SPY", "SPUS", "QQQ", "SMH"]
FINNHUB_KEY = "d8gutopr01qhjpmpqt00d8gutopr01qhjpmpqt0g"  # same key used by the HTML dashboard

# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_js_array(html: str, var_name: str) -> list:
    m = re.search(rf"const {var_name}\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not m:
        raise ValueError(f"Could not find {var_name} in HTML")
    return json.loads(m.group(1))


def replace_js_array(html: str, var_name: str, data: list) -> str:
    parts = []
    for v in data:
        parts.append(json.dumps(v) if isinstance(v, str) else str(v))
    new_array = "[" + ", ".join(parts) + "]"
    return re.sub(
        rf"const {var_name}\s*=\s*\[.*?\];",
        f"const {var_name} = {new_array};",
        html,
        flags=re.DOTALL,
    )


def append_point(dates, port_vals, spy_vals, spus_vals, qqq_vals,
                  date_str, spy_base, spus_base, qqq_base, smh_base,
                  spy_new, spus_new, qqq_new, smh_new, port_base):
    port_new = SPUS_SHARES * spus_new + SMH_SHARES * smh_new
    dates.append(date_str)
    port_vals.append(round(port_vals[-1] * port_new / port_base, 2))
    spy_vals.append(round(spy_vals[-1] * spy_new / spy_base, 2))
    spus_vals.append(round(spus_vals[-1] * spus_new / spus_base, 2))
    qqq_vals.append(round(qqq_vals[-1] * qqq_new / qqq_base, 2))
    print(f"  + {date_str}  PORT={port_vals[-1]}  SPY={spy_vals[-1]}  "
          f"SPUS={spus_vals[-1]}  QQQ={qqq_vals[-1]}")


# ── Path 1: Yahoo Finance (full historical backfill) ──────────────────────────
def try_yfinance(dates, port_vals, spy_vals, spus_vals, qqq_vals, last_date, today):
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        import subprocess
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "yfinance", "pandas", "--user", "-q"
        ])
        import yfinance as yf
        import pandas as pd

    try:
        cache = yf.utils.get_cache_path()
        import shutil, os
        if os.path.exists(cache):
            shutil.rmtree(cache)
    except Exception:
        pass

    fetch_start = (last_date - timedelta(days=10)).strftime("%Y-%m-%d")
    fetch_end   = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    raw = yf.download(
        TICKERS, start=fetch_start, end=fetch_end,
        interval="1wk", auto_adjust=True, progress=False,
    )

    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    closes = closes.dropna(how="all")
    if closes.empty:
        raise RuntimeError("yfinance returned no data")

    base_row = closes[closes.index <= pd.Timestamp(last_date)].iloc[-1]
    spy_base, spus_base, qqq_base, smh_base = (
        float(base_row["SPY"]), float(base_row["SPUS"]),
        float(base_row["QQQ"]), float(base_row["SMH"]),
    )
    port_base = SPUS_SHARES * spus_base + SMH_SHARES * smh_base

    new_rows = closes[(closes.index > pd.Timestamp(last_date)) & (closes.index <= pd.Timestamp(today))]
    if new_rows.empty:
        print("No new weekly data available yet (Yahoo).")
        return 0

    added = 0
    for dt, row in new_rows.iterrows():
        if row[["SPY", "SPUS", "QQQ", "SMH"]].isna().any():
            print(f"  Skipping {dt.date()} — incomplete price data")
            continue
        date_str = dt.strftime("%Y-%m-%d")
        spy_new, spus_new, qqq_new, smh_new = (
            float(row["SPY"]), float(row["SPUS"]), float(row["QQQ"]), float(row["SMH"]),
        )
        append_point(dates, port_vals, spy_vals, spus_vals, qqq_vals,
                     date_str, spy_base, spus_base, qqq_base, smh_base,
                     spy_new, spus_new, qqq_new, smh_new, port_base)
        spy_base, spus_base, qqq_base, smh_base = spy_new, spus_new, qqq_new, smh_new
        port_base = SPUS_SHARES * spus_new + SMH_SHARES * smh_new
        added += 1

    if added:
        # Persist the freshest raw prices so the Finnhub fallback has an
        # accurate baseline if Yahoo is unreachable next time.
        save_state(dates[-1], spy_base, spus_base, qqq_base, smh_base)
    return added


# ── Path 2: Finnhub fallback (current quote only) ──────────────────────────────
def fetch_finnhub_quote(symbol):
    import urllib.request
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_KEY}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    price = data.get("c")
    if not price:
        raise RuntimeError(f"Finnhub returned no price for {symbol}: {data}")
    return float(price)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(date_str, spy, spus, qqq, smh):
    STATE_FILE.write_text(json.dumps({
        "date": date_str, "SPY": spy, "SPUS": spus, "QQQ": qqq, "SMH": smh,
    }, indent=2))


def try_finnhub(dates, port_vals, spy_vals, spus_vals, qqq_vals, today):
    quotes = {t: fetch_finnhub_quote(t) for t in TICKERS}
    print(f"  Finnhub quotes: {quotes}")

    state = load_state()
    today_str = today.strftime("%Y-%m-%d")

    if state is None:
        # No baseline yet — seed it now, don't add a chart point (we'd be
        # comparing today's price to itself). Next run will have a real week
        # of movement to measure.
        save_state(today_str, quotes["SPY"], quotes["SPUS"], quotes["QQQ"], quotes["SMH"])
        print("  No prior Finnhub baseline found — seeded benchmark_state.json. "
              "Run again next week to get the first Finnhub-derived data point.")
        return 0

    if state["date"] == today_str:
        print("  Finnhub baseline already recorded today — nothing new to add.")
        return 0

    port_base = SPUS_SHARES * state["SPUS"] + SMH_SHARES * state["SMH"]
    append_point(dates, port_vals, spy_vals, spus_vals, qqq_vals,
                 today_str,
                 state["SPY"], state["SPUS"], state["QQQ"], state["SMH"],
                 quotes["SPY"], quotes["SPUS"], quotes["QQQ"], quotes["SMH"],
                 port_base)

    save_state(today_str, quotes["SPY"], quotes["SPUS"], quotes["QQQ"], quotes["SMH"])
    return 1


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    html = HTML_FILE.read_text(encoding="utf-8")

    dates     = extract_js_array(html, "BENCHMARK_DATES")
    port_vals = extract_js_array(html, "BENCHMARK_PORT")
    spy_vals  = extract_js_array(html, "BENCHMARK_SPY")
    spus_vals = extract_js_array(html, "BENCHMARK_SPUS")
    qqq_vals  = extract_js_array(html, "BENCHMARK_QQQ")

    last_date = datetime.strptime(dates[-1], "%Y-%m-%d")
    today     = datetime.today()

    if (today - last_date).days < 5:
        print(f"Last update was {dates[-1]} — less than 5 days ago. Nothing to add.")
        return

    print(f"Last date in chart: {dates[-1]}. Fetching weekly prices...")

    added = 0
    try:
        added = try_yfinance(dates, port_vals, spy_vals, spus_vals, qqq_vals, last_date, today)
        source = "Yahoo Finance"
    except Exception as e:
        print(f"  Yahoo Finance unavailable ({e}). Falling back to Finnhub...")
        try:
            added = try_finnhub(dates, port_vals, spy_vals, spus_vals, qqq_vals, today)
            source = "Finnhub"
        except Exception as e2:
            print(f"  Finnhub fallback also failed: {e2}")
            print("HTML left unchanged.")
            return

    if added == 0:
        return

    html = replace_js_array(html, "BENCHMARK_DATES", dates)
    html = replace_js_array(html, "BENCHMARK_PORT",  port_vals)
    html = replace_js_array(html, "BENCHMARK_SPY",   spy_vals)
    html = replace_js_array(html, "BENCHMARK_SPUS",  spus_vals)
    html = replace_js_array(html, "BENCHMARK_QQQ",   qqq_vals)

    HTML_FILE.write_text(html, encoding="utf-8")
    print(f"\n✓ Added {added} new data point(s) via {source}. HTML saved.")
    print("  → Upload the updated HTML to GitHub to publish the changes.")


if __name__ == "__main__":
    main()

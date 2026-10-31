"""Daily bullish F&O screener (monthly view).

A stock passes only if BOTH rules hold:
  1. Trend:   close > 20-DMA > 50-DMA and the last ~1 month return is positive
  2. Build-up: total stock-futures open interest is higher than ~20 sessions ago
               while the price is also higher (long build-up on a monthly view)

Top N passing stocks (default 10) are ranked and sent to Telegram, and a
mobile-friendly page is written to docs/index.html.

This is a screen, not advice. It never places orders.
"""
import datetime as dt
import html
import io
import os
import sys
import time
import zipfile

import numpy as np
import pandas as pd
import requests

TOP_N = int(os.environ.get("TOP_N", "10"))
LOOKBACK = 20  # trading sessions, roughly one month
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


# --------------------------------------------------------------------------
# NSE open interest (F&O bhavcopy)
# --------------------------------------------------------------------------
def nse_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=15)
    except requests.RequestException:
        pass
    return s


def fetch_fo_bhav(session, day):
    """Return (DataFrame or None, status) for one trading day."""
    url = (
        "https://nsearchives.nseindia.com/content/fo/"
        f"BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    )
    for _ in range(3):
        try:
            r = session.get(url, timeout=30)
        except requests.RequestException:
            time.sleep(2)
            continue
        if r.status_code == 404:
            return None, "missing"  # holiday or not published yet
        if r.status_code == 200:
            try:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                df = pd.read_csv(z.open(z.namelist()[0]))
                df.columns = [c.strip() for c in df.columns]
                return df, "ok"
            except Exception:
                return None, "bad"
        time.sleep(2)
    return None, "blocked"


def stock_future_oi(df):
    """Total open interest per stock symbol across all expiries."""
    f = df[df["FinInstrmTp"].astype(str).str.strip() == "STF"]
    return f.groupby(f["TckrSymb"].astype(str).str.strip())["OpnIntrst"].sum()


def collect_oi():
    """Return DataFrame: index=symbol, columns=dates (oldest->newest), or None."""
    session = nse_session()
    series, blocked = {}, 0
    day = dt.datetime.now(IST).date()
    for _ in range(60):  # look back up to 60 calendar days
        if len(series) >= LOOKBACK + 1:
            break
        if day.weekday() < 5:
            df, status = fetch_fo_bhav(session, day)
            if status == "ok":
                series[day] = stock_future_oi(df)
            elif status in ("blocked", "bad"):
                blocked += 1
                if blocked >= 3 and not series:
                    print("NSE data looks blocked; skipping OI rule.")
                    return None
        day -= dt.timedelta(days=1)
    if len(series) < 6:
        return None
    out = pd.DataFrame(series).sort_index(axis=1)
    return out


# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------
def collect_prices(symbols):
    import yfinance as yf

    tickers = [f"{s}.NS" for s in symbols]
    data = yf.download(
        tickers, period="8mo", interval="1d", auto_adjust=True,
        group_by="ticker", progress=False, threads=True,
    )
    rows = []
    for s in symbols:
        try:
            close = data[f"{s}.NS"]["Close"].dropna()
        except Exception:
            continue
        if len(close) < 55:
            continue
        last = float(close.iloc[-1])
        sma20 = float(close.iloc[-20:].mean())
        sma50 = float(close.iloc[-50:].mean())
        ret = last / float(close.iloc[-(LOOKBACK + 2)]) - 1
        rows.append({
            "symbol": s, "price": last, "sma20": sma20, "sma50": sma50,
            "ret_1m": ret * 100,
            "above_sma50": (last / sma50 - 1) * 100,
            "price_date": close.index[-1].date(),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------
def rank_stocks(prices, oi, top_n=TOP_N):
    """prices: per-symbol trend frame. oi: wide OI frame or None."""
    df = prices.copy()
    df["trend_ok"] = (
        (df.price > df.sma20) & (df.sma20 > df.sma50) & (df.ret_1m > 0)
    )
    if oi is not None:
        first, last = oi.columns[0], oi.columns[-1]
        chg = (oi[last] / oi[first].replace(0, np.nan) - 1) * 100
        df = df.merge(chg.rename("oi_chg_1m"), left_on="symbol",
                      right_index=True, how="left")
        df["oi_ok"] = df.oi_chg_1m > 0
        passed = df[df.trend_ok & df.oi_ok].copy()
        passed["score"] = (
            0.45 * passed.ret_1m.rank(pct=True)
            + 0.35 * passed.oi_chg_1m.rank(pct=True)
            + 0.20 * passed.above_sma50.rank(pct=True)
        ) * 100
    else:
        df["oi_chg_1m"] = np.nan
        passed = df[df.trend_ok].copy()
        passed["score"] = (
            0.65 * passed.ret_1m.rank(pct=True)
            + 0.35 * passed.above_sma50.rank(pct=True)
        ) * 100
    return passed.sort_values("score", ascending=False).head(top_n), len(df)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def build_message(top, scanned, oi_used, asof):
    lines = [f"Bullish F&O (monthly view) - data as of {asof}"]
    if not oi_used:
        lines.append("NOTE: OI data unavailable today, trend rule only.")
    if top.empty:
        lines.append("No stock passed all rules today.")
    for i, r in enumerate(top.itertuples(), 1):
        oi_txt = "" if np.isnan(r.oi_chg_1m) else f" | OI {r.oi_chg_1m:+.0f}%"
        lines.append(
            f"{i}. {r.symbol}  {r.price:,.1f}  1M {r.ret_1m:+.1f}%{oi_txt}"
        )
    lines.append(f"Scanned {scanned} F&O stocks. Screen only, not advice.")
    return "\n".join(lines)


def write_page(top, scanned, oi_used, asof, path="docs/index.html"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = ""
    for i, r in enumerate(top.itertuples(), 1):
        oi_txt = "n/a" if np.isnan(r.oi_chg_1m) else f"{r.oi_chg_1m:+.1f}%"
        rows += (
            f"<tr><td>{i}</td><td><b>{html.escape(r.symbol)}</b></td>"
            f"<td>{r.price:,.1f}</td><td>{r.ret_1m:+.1f}%</td>"
            f"<td>{oi_txt}</td><td>{r.above_sma50:+.1f}%</td>"
            f"<td>{r.score:.0f}</td></tr>"
        )
    if not rows:
        rows = '<tr><td colspan="7">No stock passed all rules today.</td></tr>'
    warn = "" if oi_used else (
        '<p class="warn">OI data was unavailable today, so only the trend '
        "rule was applied.</p>"
    )
    now = dt.datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bullish F&amp;O Monthly</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e3e3e3;--acc:#0b7a4b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121212;--fg:#eee;--mut:#9a9a9a;--line:#2c2c2c;--acc:#4ac48a}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}}p{{color:var(--mut);margin:4px 0 12px}}
.warn{{color:#b45309}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:420px}}
th,td{{padding:8px 6px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}
th:nth-child(-n+2),td:nth-child(-n+2){{text-align:left}}th{{color:var(--mut);font-weight:600;font-size:13px}}
td:nth-child(4){{color:var(--acc)}}
</style></head><body>
<h1>Bullish F&amp;O stocks: monthly view</h1>
<p>Price data as of {html.escape(str(asof))}. Page updated {now}. Scanned {scanned} F&amp;O stocks.</p>
{warn}
<div class="wrap"><table>
<tr><th>#</th><th>Stock</th><th>Price</th><th>1M</th><th>OI 1M</th><th>vs 50DMA</th><th>Score</th></tr>
{rows}
</table></div>
<p>Rules: close above 20DMA, 20DMA above 50DMA, 1M return positive, and futures
open interest up over ~20 sessions. A screen only, not investment advice.
It never places orders.</p>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def send_telegram(text):
    """Send the alert to ntfy (phone app) and/or Telegram, whichever is set."""
    sent = False
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        r = requests.post(
            f"https://ntfy.sh/{topic}",
            data=text.encode("utf-8"),
            headers={"Title": "Bullish F&O stocks"},
            timeout=20,
        )
        print("ntfy status:", r.status_code)
        r.raise_for_status()
        sent = True
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text}, timeout=20,
        )
        print("Telegram status:", r.status_code)
        r.raise_for_status()
        sent = True
    if not sent:
        print("No alert channel set; printing instead:\n" + text)


def main():
    oi = collect_oi()
    if oi is not None:
        symbols = sorted(oi.index[oi.iloc[:, -1] > 0])
    else:
        symbols = [s.strip() for s in open("fallback_symbols.txt") if s.strip()]
    prices = collect_prices(symbols)
    if prices.empty:
        send_telegram("F&O screener failed: no price data. Check the Actions log.")
        sys.exit(1)
    top, scanned = rank_stocks(prices, oi)
    asof = prices.price_date.max()
    send_telegram(build_message(top, scanned, oi is not None, asof))
    write_page(top, scanned, oi is not None, asof)


if __name__ == "__main__":
    main()

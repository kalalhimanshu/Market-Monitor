#!/usr/bin/env python3
"""Download Nifty 500 prices, compute breadth + RS data, write JSON for the static site."""
import io, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
CACHE = ROOT / "data" / "nifty500.csv"
NSE_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]
# Benchmarks offered in the dropdown. Any that Yahoo fails to return are skipped.
BENCH = {
    "Nifty 500": "^CRSLDX",
    "Nifty 50": "^NSEI",
    "Nifty Midcap 150": "NIFTY_MIDCAP_150.NS",
    "Nifty Smallcap 250": "NIFTYSMLCAP250.NS",
    "Nifty Bank": "^NSEBANK",
    "Nifty IT": "^CNXIT",
}
KEEP = 300   # trading days of prices shipped to the browser
HIST = 500   # trading days of breadth history shipped to the browser


def load_universe():
    for url in NSE_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            if "Symbol" in df.columns and len(df) > 400:
                CACHE.parent.mkdir(exist_ok=True)
                df.to_csv(CACHE, index=False)
                return df
        except Exception as e:
            print(f"constituent download failed ({url}): {e}")
    if CACHE.exists():
        print("using cached constituent list")
        return pd.read_csv(CACHE)
    sys.exit("No constituent list available. Download ind_nifty500list.csv from NSE into data/nifty500.csv")


def download(tickers, period="3y"):
    import yfinance as yf
    frames = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        for attempt in range(3):
            try:
                d = yf.download(chunk, period=period, auto_adjust=True, progress=False, threads=True)
                c = d["Close"]
                if isinstance(c, pd.Series):
                    c = c.to_frame(chunk[0])
                frames.append(c)
                break
            except Exception as e:
                print(f"batch {i} attempt {attempt + 1} failed: {e}")
                time.sleep(5)
    if not frames:
        return pd.DataFrame()
    c = pd.concat(frames, axis=1)
    c = c.loc[:, ~c.columns.duplicated()]
    idx = pd.to_datetime(c.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    c.index = idx.normalize()
    return c.dropna(axis=1, how="all").sort_index()


def clean(arr, nd=2):
    return [None if (v is None or pd.isna(v)) else round(float(v), nd) for v in arr]


def rs_rating(close, k):
    """IBD-style RS: 40% last 3m + 20% each of the 3 quarters before, ranked 1-99. k = trading days back."""
    if len(close) < 253 + k:
        return pd.Series(np.nan, index=close.columns)
    now = close.iloc[-1 - k]
    score = sum(w * (now / close.iloc[-1 - k - n] - 1) for n, w in ((63, .4), (126, .2), (189, .2), (252, .2)))
    return (score.rank(pct=True) * 98 + 1).round()


def compute(close, bench, meta):
    close = close.loc[:, close.notna().sum() >= 60].ffill(limit=5)
    cnt = close.notna().sum(axis=1)
    close = close[cnt >= 0.8 * cnt.max()]
    bench = bench.reindex(close.index).ffill()
    n = close.shape[1]

    d = close.diff()
    adv, dec = (d > 0).sum(axis=1), (d < 0).sum(axis=1)
    ma50, ma200 = close.rolling(50).mean(), close.rolling(200).mean()

    def pct_above(ma):
        ok = ma.notna().sum(axis=1)
        return ((close > ma).sum(axis=1) / ok.replace(0, np.nan) * 100).where(ok >= 0.8 * n)

    hi = close.rolling(252, min_periods=240).max()
    lo = close.rolling(252, min_periods=240).min()
    nh = ((close >= hi) & hi.notna()).sum(axis=1).where(hi.notna().any(axis=1))
    nl = ((close <= lo) & lo.notna()).sum(axis=1).where(lo.notna().any(axis=1))
    ad_line = (adv - dec).cumsum()
    t = slice(-HIST, None)
    overlay_name = "Nifty 500" if "Nifty 500" in bench else bench.columns[0]
    breadth = {
        "dates": [x.strftime("%Y-%m-%d") for x in close.index[t]],
        "adv": adv[t].tolist(), "dec": dec[t].tolist(),
        "ad_line": ad_line[t].tolist(),
        "p50": clean(pct_above(ma50)[t], 1), "p200": clean(pct_above(ma200)[t], 1),
        "nh": clean(nh[t], 0), "nl": clean(nl[t], 0),
        "index": clean(bench[overlay_name][t]), "index_name": overlay_name,
        "n": n, "asof": close.index[-1].strftime("%Y-%m-%d"),
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
    }

    r0, r5, r21 = rs_rating(close, 0), rs_rating(close, 5), rs_rating(close, 21)
    last, prev = close.iloc[-1], close.iloc[-2]
    high52 = close.tail(252).max()
    stocks = []
    for s in close.columns:
        sym = s.replace(".NS", "")
        m = meta.get(sym, {})

        def flag(ma):
            return None if pd.isna(ma[s].iloc[-1]) else int(last[s] > ma[s].iloc[-1])

        stocks.append({
            "s": sym, "n": m.get("n", sym), "i": m.get("i", ""),
            "c": clean([last[s]])[0], "d1": clean([(last[s] / prev[s] - 1) * 100])[0],
            "rs": clean([r0[s]], 0)[0], "rs1w": clean([r5[s]], 0)[0], "rs1m": clean([r21[s]], 0)[0],
            "fh": clean([(last[s] / high52[s] - 1) * 100], 1)[0],
            "a50": flag(ma50), "a200": flag(ma200),
        })

    tail = close.index[-KEEP:]
    prices = {
        "dates": [x.strftime("%Y-%m-%d") for x in tail],
        "p": {s.replace(".NS", ""): clean(close[s].loc[tail].values) for s in close.columns},
        "idx": {name: clean(bench[name].loc[tail].values) for name in bench.columns},
    }
    return breadth, stocks, prices


def main():
    uni = load_universe()
    meta = {r["Symbol"]: {"n": r["Company Name"], "i": r["Industry"]} for _, r in uni.iterrows()}
    close = download([s + ".NS" for s in uni["Symbol"]])
    if close.shape[1] < 300:
        sys.exit(f"Only {close.shape[1]} stocks downloaded; refusing to publish partial data.")
    raw = download(list(BENCH.values())).rename(columns={v: k for k, v in BENCH.items()})
    bench = raw[[k for k in BENCH if k in raw.columns]]
    if bench.empty:
        sys.exit("No benchmark index data downloaded.")
    breadth, stocks, prices = compute(close, bench, meta)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, obj in (("breadth", breadth), ("stocks", stocks), ("prices", prices)):
        (OUT / f"{name}.json").write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    print(f"OK: {close.shape[1]} stocks, as of {breadth['asof']}, benchmarks: {list(bench.columns)}")


if __name__ == "__main__":
    main()

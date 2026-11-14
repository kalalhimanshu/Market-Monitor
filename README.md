# Nifty 500 breadth and relative strength

Static dashboard on GitHub Pages, refreshed every weekday by GitHub Actions.

## Set up
1. Create a public GitHub repo and push these files.
2. Settings > Pages > Source: **Deploy from a branch**, branch `main`, folder `/docs`.
3. Actions tab > **Update market data** > Run workflow (first run builds the data files).
4. Your site appears at `https://<username>.github.io/<repo>/`.

## Local run
    pip install -r requirements.txt
    python scripts/update.py
    cd docs && python -m http.server 8000

## Notes
- Prices come from Yahoo Finance via `yfinance` (unofficial; free for personal use). Constituents come from NSE's Nifty 500 CSV.
- 52-week highs/lows use closing prices. RS rating = IBD-style weighted 12-month return, ranked 1-99 within the Nifty 500.
- Benchmark tickers are in `BENCH` in `scripts/update.py`; indices Yahoo doesn't return are skipped.

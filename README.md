# AI Sentiment Forecast

A public daily newsletter: AI investor-sentiment reports (built from finviz news feeds, novelty-filtered) plus a **graded next-day SPY direction forecast**.

Live: https://ai-sentiment-forecast.vercel.app

## How it works

1. Every weekday, sentiment editions (9 AM pre-market, 5 PM post-close ET) are produced and stored in a private archive.
2. Each evening at 6:30 PM ET, `scripts/daily_update.py` pulls the day's Post-Close edition, computes the forecast, grades yesterday's pending forecast against actual SPY closes (Yahoo Finance), regenerates `index.html`, and commits.
3. Vercel auto-deploys on every push to `main`.

## Forecast rule

- Post-close sentiment score > 50 → **UP**; < 50 → **DOWN**; exactly 50 → the majority sentiment of that day's macro drivers decides.
- Actual = SPY close-to-close % from the forecast day's close to the next trading day's close. Sign matches the call → HIT. A flat 0.00% day is a PUSH (excluded from the hit rate).
- Misses are published, not hidden.

## Repo layout

- `index.html` — generated site (committed; do not edit by hand)
- `gen_site.py` — static site generator
- `data/daily.json` — day objects (editions, drivers, movers, analyst take, forecast, result)
- `data/forecasts.json` — forecast ledger (the graded record)
- `data/takes/YYYY-MM-DD.txt` — analyst takes, one per day
- `scripts/daily_update.py` — idempotent evening updater (GitHub contents API; no git clone needed)

## Running the updater manually

```bash
# 1. Save the artifact's listreports result to data/incoming.json
# 2. Write the analyst take to data/takes/YYYY-MM-DD.txt
# 3. Run (venv has yfinance for SPY grading):
~/workspace/.venv-sp500/bin/python scripts/daily_update.py \
  --reports-json data/incoming.json --date YYYY-MM-DD
```

Add `--dry-run` to preview without committing. Forecasts are directional experiments, not financial advice.

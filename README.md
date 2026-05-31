# Market Gap Identifier

A daily scan for **supply/demand imbalances** — sectors where demand looks to be
outrunning supply right now, i.e. potential openings created by disrupted supply
chains. It blends free public signals with your own watchlist and writes a dated
Markdown report.

No API keys. No third-party packages. Pure Python standard library, so it keeps
working on a bare install and never breaks on an expired credential.

## What it looks at

| Signal | Source (free) | What it tells us |
|--------|---------------|------------------|
| **Input price tightness** | Yahoo Finance public chart JSON | Fast-rising commodity/material prices (copper, steel, energy, grains, lumber…) often precede downstream shortages and pricing power. Spikes are flagged 🔥. |
| **Disruption news** | Google News RSS | Volume of recent headlines containing shortage / export-ban / factory-shutdown / lead-time language, per sector. |
| **Your own data** | `data/watchlist.csv` | Terms/materials/products you care about — these boost matching sectors and appear in the report. |

Each sector gets a **0–100 gap score** = `40% price momentum + 40% disruption news + 20% watchlist match`. Higher = stronger evidence worth a human look.

## Run it

```powershell
python run_daily.py
```

Writes `reports/YYYY-MM-DD.md` and prints the top candidates to the console.

Options:

```powershell
python run_daily.py --config config.json --watchlist data\watchlist.csv --report-dir reports
```

## Add your own watchlist (optional)

Copy the example and edit it:

```powershell
Copy-Item data\watchlist.example.csv data\watchlist.csv
```

CSV columns (header row required): `term` (required), `sector` (optional), `note` (optional).
Any sector whose name or headlines match a `term` gets the 20% watchlist boost and
the note is carried into the report.

## Configure what it scans

Edit [`config.json`](config.json):

- **`commodities`** — `{ "YahooSymbol": "Display name" }`. Futures symbols use `=F`
  (e.g. `HG=F` copper, `HRC=F` steel, `CL=F` crude). Add/remove freely.
- **`sectors`** — each sector ties together a set of commodities and a Google News
  `news_query`. The query supports `OR` and `"quoted phrases"`.
- **`scoring`** — weights and thresholds (`w_price`, `w_news`, `w_watchlist`,
  `spike_pct_strong`, `news_count_saturate`).
- **`news_lookback_days`** — how recent a headline must be to count (default 4).

## Run it automatically every day (Windows Task Scheduler)

```powershell
$action  = New-ScheduledTaskAction -Execute "python" `
  -Argument "run_daily.py" `
  -WorkingDirectory "C:\Users\ianja\Documents\Rav Engineering\Market gap identifier"
$trigger = New-ScheduledTaskTrigger -Daily -At 7am
Register-ScheduledTask -TaskName "MarketGapScan" -Action $action -Trigger $trigger
```

Each morning a fresh `reports/<date>.md` appears.

## Project layout

```
config.json              # commodity basket, sectors, news queries, scoring
run_daily.py             # entry point  ->  python run_daily.py
data/
  watchlist.example.csv  # copy to watchlist.csv and edit
reports/                 # dated markdown reports land here
src/market_gap/
  cli.py                 # orchestration: gather -> score -> write
  scoring.py             # blends signals into the 0-100 gap score
  report.py              # markdown rendering
  http_util.py           # stdlib HTTP GET, graceful failure
  sources/
    commodities.py       # Yahoo Finance price momentum + spike detection
    news.py              # Google News RSS disruption headlines
    user_data.py         # watchlist loader
```

## Caveats (read before acting)

This is a **screening tool, not advice**. A high score says "look here", not "buy".
Price momentum can reflect plain cost inflation rather than a true shortage; news
volume measures attention, which lags or overshoots reality. Always click through
to primary sources and verify before committing.

## Ideas for v2

- Add Google Trends search-interest momentum (demand-side confirmation).
- Per-sector price/news history so the report can show *change vs yesterday*.
- A `--format html` dashboard output.
- Email/Slack delivery of the daily digest.
- Trade/customs data (import volume drops) as a harder supply-disruption signal.

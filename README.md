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

## Local AI analyst (token-free)

After the mechanical scan, an optional **local LLM analyst** reads the scored
report and writes a decision-first brief (top gaps, REAL GAP / WATCH / NOISE
verdicts, the specific opportunity, risks). It runs entirely on your machine via
[Ollama](https://ollama.com) — **no tokens, no cloud, no API keys.**

- Default model: `qwen2.5:14b` (configurable in `config.json` → `local_llm`).
- One-time setup: `ollama pull qwen2.5:14b` (server: `ollama serve`).
- The brief is appended to the same `reports/YYYY-MM-DD.md`.
- If Ollama or the model isn't available, the scan still produces its mechanical
  report and notes that the analyst was skipped — it never crashes.

Toggle it with `--analyst` / `--no-analyst`, or `local_llm.enabled` in config.

### How the local model gets current information

A local model is just a reasoning engine with a fixed knowledge cutoff — it can't
browse on its own. Fresh information reaches it through its **prompt**, which this
pipeline fills each run with:

1. **Real-time price + news signals** the scan already collects (Yahoo Finance,
   Google News RSS).
2. **Free web search** (`web_search` in config): for the top-ranked sectors we
   query DuckDuckGo and feed the result snippets into the prompt — the local,
   token-free stand-in for cloud web research. If DuckDuckGo rate-limits, it
   automatically falls back to Google News RSS, so the analyst always gets
   widened context. Tune `top_sectors`, `max_results_per_query`, `query_suffix`.

So the model has a "stale brain, fresh eyes": frozen weights for reasoning, but
current data handed to it every run. No tokens used — only your bandwidth.

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

This is already set up on this machine as task **`MarketGapScan-Local`** (weekdays
07:00), which runs [`run_local.ps1`](run_local.ps1) — it ensures the Ollama server
is up, runs the scan + local analyst, and logs to `logs/`.

To (re)create or inspect it:

```powershell
# Inspect
Get-ScheduledTask -TaskName "MarketGapScan-Local"
Get-ScheduledTaskInfo -TaskName "MarketGapScan-Local"   # next run time

# Run it right now to test
Start-ScheduledTask -TaskName "MarketGapScan-Local"

# Recreate
$proj = "C:\Users\ianja\Documents\Rav Engineering\Market gap identifier"
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$proj\run_local.ps1`"" -WorkingDirectory $proj
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 7:00am
Register-ScheduledTask -TaskName "MarketGapScan-Local" -Action $action -Trigger $trigger -Force
```

Each weekday morning a fresh `reports/<date>.md` appears, ending with the local
analyst brief.

### Cloud routine (disabled)

There is also a cloud routine (`trig_01L7DhSWD8uWAckGbSHmAVqV`) that ran the
analyst on Claude Sonnet and delivered to Google Drive. It is **disabled** to
avoid token costs now that the local analyst exists. Re-enable at
[claude.ai/code/routines](https://claude.ai/code/routines) if you ever want the
higher-quality cloud analysis (with live web research) as a fallback.

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

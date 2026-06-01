"""Orchestrate the daily scan: gather signals -> score -> write report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, local_llm
from .report import render
from .scoring import rank_sectors, score_sector
from .sources import commodities, news, websearch
from .sources.user_data import load_watchlist

# Project root = three levels up from this file (src/market_gap/cli.py).
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config.json"
DEFAULT_WATCHLIST = ROOT / "data" / "watchlist.csv"
DEFAULT_REPORT_DIR = ROOT / "reports"


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def run(
    config_path: Path,
    watchlist_path: Path,
    report_dir: Path,
    analyst: bool | None = None,
    model: str | None = None,
) -> Path:
    cfg = load_config(config_path)
    run_dt = datetime.now(timezone.utc).astimezone()

    timeout = int(cfg.get("http_timeout_seconds", 20))
    price_range = cfg.get("price_range", "3mo")
    lookback = int(cfg.get("news_lookback_days", 4))

    # 1. Prices (fetch the full basket once, then map to sectors).
    print("Fetching commodity prices ...")
    price_map = commodities.fetch_all(cfg["commodities"], price_range, timeout)

    # 2. Watchlist (user's own data).
    watchlist = load_watchlist(watchlist_path)
    if watchlist:
        print(f"Loaded {len(watchlist)} watchlist term(s) from {watchlist_path}")

    # 3. Per-sector news + scoring.
    scores = []
    for sector_cfg in cfg["sectors"]:
        name = sector_cfg["sector"]
        print(f"Scanning sector: {name} ...")
        sector_prices = [price_map[s] for s in sector_cfg.get("commodities", []) if s in price_map]
        news_signal = news.fetch_news_signal(sector_cfg["news_query"], lookback, timeout)
        scores.append(
            score_sector(name, sector_prices, news_signal, watchlist, cfg.get("scoring", {}))
        )

    ranked = rank_sectors(scores)

    # 4. Source status summary.
    n_prices_ok = sum(1 for s in price_map.values() if s.available)
    n_news_ok = sum(1 for sc in scores if sc.news and sc.news.available)
    status = [
        f"Commodity prices (Yahoo Finance): {n_prices_ok}/{len(price_map)} symbols OK",
        f"Disruption news (Google News RSS): {n_news_ok}/{len(scores)} sector queries OK",
        f"Watchlist (data/watchlist.csv): {'loaded' if watchlist else 'not found — public signals only'}",
        f"Tool version: market-gap-identifier {__version__}",
    ]

    # 5. Render + write.
    md = render(ranked, run_dt, status, len(watchlist))
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / f"{run_dt.strftime('%Y-%m-%d')}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"\nReport written: {out_path}")
    _print_summary(ranked)

    # 6. Local LLM analyst layer (token-free, runs on this machine via Ollama).
    llm_cfg = cfg.get("local_llm", {})
    use_analyst = llm_cfg.get("enabled", False) if analyst is None else analyst
    if use_analyst:
        _run_analyst(out_path, md, run_dt.strftime("%Y-%m-%d"), llm_cfg, model, ranked, cfg)

    return out_path


def _gather_web_context(ranked, cfg: dict, timeout: int) -> str:
    """Run free web searches for the top-ranked sectors to widen the analyst's context."""
    ws_cfg = cfg.get("web_search", {})
    if not ws_cfg.get("enabled", False):
        return ""
    top_n = int(ws_cfg.get("top_sectors", 3))
    max_results = int(ws_cfg.get("max_results_per_query", 5))
    suffix = ws_cfg.get("query_suffix", "shortage supply disruption")
    queries = [f"{s.sector} {suffix}".strip() for s in ranked[:top_n]]
    print(f"Gathering web context for top {len(queries)} sectors (free DuckDuckGo search) ...")
    return websearch.gather_context(queries, max_results, timeout)


def _run_analyst(out_path: Path, report_md: str, date_str: str, llm_cfg: dict,
                 model_override: str | None, ranked=None, cfg: dict | None = None) -> None:
    model = model_override or llm_cfg.get("model", "qwen2.5:14b")
    host = llm_cfg.get("host", "http://localhost:11434")

    web_context = ""
    if ranked is not None and cfg is not None:
        web_context = _gather_web_context(ranked, cfg, int(cfg.get("http_timeout_seconds", 20)))

    print(f"\nRunning local analyst ({model}) — this may take a few minutes ...")

    ready, msg = local_llm.check_available(model, host)
    if not ready:
        print(f"  Local analyst skipped: {msg}")
        _append(out_path, f"\n---\n\n## Analyst brief — {date_str}\n\n_Local model unavailable: {msg}_\n")
        return

    result = local_llm.generate_brief(
        report_md,
        date_str,
        model=model,
        host=host,
        temperature=float(llm_cfg.get("temperature", 0.3)),
        num_ctx=int(llm_cfg.get("num_ctx", 8192)),
        timeout=int(llm_cfg.get("timeout_seconds", 600)),
        web_context=web_context,
    )
    if not result.ok:
        print(f"  Local analyst failed: {result.error}")
        _append(out_path, f"\n---\n\n## Analyst brief — {date_str}\n\n_Local analyst failed: {result.error}_\n")
        return

    _append(out_path, f"\n---\n\n_Analyst brief generated locally by {result.model} (no tokens used)._\n\n{result.text}\n")
    print(f"  Analyst brief appended to {out_path}")


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def _print_summary(ranked) -> None:
    print("\nTop gap candidates today:")
    for i, s in enumerate(ranked[:5], 1):
        print(f"  {i}. {s.sector:40s} {s.score:5.1f}  ({s.rationale})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="market-gap", description="Daily scan for supply/demand market gaps (free data, no keys)."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--model", default=None, help="Override the local Ollama model tag.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--analyst", dest="analyst", action="store_true", default=None,
                       help="Force-run the local LLM analyst layer.")
    group.add_argument("--no-analyst", dest="analyst", action="store_false",
                       help="Skip the local LLM analyst layer (mechanical scan only).")
    args = parser.parse_args(argv)
    try:
        run(args.config, args.watchlist, args.report_dir, analyst=args.analyst, model=args.model)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

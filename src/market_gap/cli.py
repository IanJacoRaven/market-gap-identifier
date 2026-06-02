"""Orchestrate the daily scan: gather signals -> score -> write report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, delivery as delivery_mod, focus as focus_mod, local_llm
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
    dedupe_jaccard = float(cfg.get("news_dedupe_jaccard", 0.5))
    scores = []
    for sector_cfg in cfg["sectors"]:
        name = sector_cfg["sector"]
        print(f"Scanning sector: {name} ...")
        sector_prices = [price_map[s] for s in sector_cfg.get("commodities", []) if s in price_map]
        news_signal = news.fetch_news_signal(sector_cfg["news_query"], lookback, timeout, dedupe_jaccard)
        scores.append(
            score_sector(name, sector_prices, news_signal, watchlist, cfg.get("scoring", {}))
        )

    # Apply the focus layer's priority-sector boost before ranking.
    focus = focus_mod.FocusConfig.from_dict(cfg.get("focus"))
    focus_mod.apply_priority_boost(scores, focus)
    ranked = rank_sectors(scores)

    # 4. Source status summary.
    n_prices_ok = sum(1 for s in price_map.values() if s.available)
    n_news_ok = sum(1 for sc in scores if sc.news and sc.news.available)
    status = [
        f"Commodity prices (Yahoo Finance): {n_prices_ok}/{len(price_map)} symbols OK",
        f"Disruption news (Google News RSS): {n_news_ok}/{len(scores)} sector queries OK",
        f"Watchlist (data/watchlist.csv): {'loaded' if watchlist else 'not found — public signals only'}",
        f"Focus: {('geographies ' + ', '.join(focus.geographies)) if focus.geographies else 'global'}"
        + (f"; priority sectors {', '.join(focus.priority_sectors)}" if focus.priority_sectors else ""),
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


def _gather_web_context(ranked, cfg: dict, timeout: int, focus) -> str:
    """Gather widened context: focus-aware web searches + the user's own RSS feeds."""
    ws_cfg = cfg.get("web_search", {})
    parts: list[str] = []

    if ws_cfg.get("enabled", False):
        top_n = int(ws_cfg.get("top_sectors", 3))
        max_results = int(ws_cfg.get("max_results_per_query", 5))
        suffix = ws_cfg.get("query_suffix", "shortage supply disruption")
        queries = focus_mod.build_search_queries(ranked, focus, suffix, top_n)
        geo_note = f" (geo: {', '.join(focus.geographies)})" if focus.geographies else ""
        print(f"Gathering web context — {len(queries)} searches{geo_note} ...")
        web = websearch.gather_context(queries, max_results, timeout)
        if web:
            parts.append(web)

    if focus.custom_rss:
        print(f"Pulling {len(focus.custom_rss)} custom RSS feed(s) ...")
        rss = focus_mod.fetch_rss_context(focus.custom_rss, max_items=6, timeout=timeout)
        if rss:
            parts.append(rss)

    return "\n\n".join(parts)


def _run_analyst(out_path: Path, report_md: str, date_str: str, llm_cfg: dict,
                 model_override: str | None, ranked=None, cfg: dict | None = None) -> None:
    model = model_override or llm_cfg.get("model", "qwen2.5:14b")
    host = llm_cfg.get("host", "http://localhost:11434")

    web_context = ""
    focus_note = ""
    if ranked is not None and cfg is not None:
        focus = focus_mod.FocusConfig.from_dict(cfg.get("focus"))
        focus_note = focus_mod.prompt_note(focus)
        web_context = _gather_web_context(ranked, cfg, int(cfg.get("http_timeout_seconds", 20)), focus)

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
        focus_note=focus_note,
    )
    if not result.ok:
        print(f"  Local analyst failed: {result.error}")
        _append(out_path, f"\n---\n\n## Analyst brief — {date_str}\n\n_Local analyst failed: {result.error}_\n")
        return

    _append(out_path, f"\n---\n\n_Analyst brief generated locally by {result.model} (no tokens used)._\n\n{result.text}\n")
    print(f"  Analyst brief appended to {out_path}")

    # Deliver a brief-first copy somewhere visible (OneDrive / Desktop).
    if cfg is not None:
        for line in delivery_mod.deliver(result.text, report_md, date_str, cfg.get("delivery", {})):
            print(f"  {line}")


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def _print_summary(ranked) -> None:
    print("\nTop gap candidates today:")
    for i, s in enumerate(ranked[:5], 1):
        print(f"  {i}. {s.sector:40s} {s.score:5.1f}  ({s.rationale})")


def main(argv: list[str] | None = None) -> int:
    # Harden console/file output against Unicode (Windows defaults to cp1252).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
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

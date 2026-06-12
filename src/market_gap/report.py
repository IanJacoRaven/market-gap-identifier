"""Render the daily scan into a dated Markdown report."""

from __future__ import annotations

from datetime import datetime

from .scoring import SectorScore
from .sources.commodities import PriceSignal


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _md_table(headers: list[str], rows: list[list[str]], aligns: list[str]) -> list[str]:
    """Build a Markdown table with cells padded so columns line up as raw text.

    The final column is left un-padded (nothing follows it), which keeps long or
    emoji-containing trailing cells from throwing the alignment off.
    """
    ncol = len(headers)
    rows = [(r + [""] * ncol)[:ncol] for r in rows]  # normalise row length
    widths = [len(headers[i]) for i in range(ncol)]
    for r in rows:
        for i in range(ncol):
            widths[i] = max(widths[i], len(r[i]))

    def pad(cell: str, i: int) -> str:
        if i == ncol - 1:  # don't pad the last column
            return cell
        w = widths[i]
        return cell.rjust(w) if aligns[i] == "r" else cell.ljust(w)

    def row_line(cells: list[str]) -> str:
        return "| " + " | ".join(pad(c, i) for i, c in enumerate(cells)) + " |"

    sep: list[str] = []
    for i in range(ncol):
        w = widths[i] if i < ncol - 1 else max(3, len(headers[i]))
        sep.append(("-" * max(1, w - 1) + ":") if aligns[i] == "r" else ("-" * max(1, w)))

    return [row_line(headers), "| " + " | ".join(sep) + " |", *[row_line(r) for r in rows]]


def _price_cells(s: PriceSignal) -> list[str]:
    name = f"{s.name} ({s.symbol})"
    if not s.available:
        return [name, "—", "—", "—", "—", f"_{s.error or 'unavailable'}_"]
    z = f"{s.zscore:.2f}" if s.zscore is not None else "n/a"
    spike = "🔥" if (s.zscore is not None and s.zscore >= 1.5) else ""
    return [name, f"{s.last_close}", _fmt_pct(s.pct_change_5d), _fmt_pct(s.pct_change_20d), z, spike]


def _score_label(score: float) -> str:
    if score >= 60:
        return "🟢 strong"
    if score >= 35:
        return "🟡 watch"
    return "⚪ weak"


def render(
    ranked: list[SectorScore],
    run_dt: datetime,
    source_status: list[str],
    watchlist_count: int,
) -> str:
    date_str = run_dt.strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append(f"# Market Gap Scan — {date_str}")
    lines.append("")
    lines.append(
        f"_Generated {run_dt.strftime('%Y-%m-%d %H:%M %Z').strip()} · "
        f"{len(ranked)} sectors scanned · {watchlist_count} watchlist term(s) loaded_"
    )
    lines.append("")

    # --- Top candidates table ---
    lines.append("## Ranked gap candidates")
    lines.append("")
    rank_rows = [
        [str(i), s.sector, f"{s.score:.1f}", _score_label(s.score), s.rationale]
        for i, s in enumerate(ranked, 1)
    ]
    lines.extend(
        _md_table(
            ["#", "Sector", "Score", "Signal", "Why"],
            rank_rows,
            ["r", "l", "r", "l", "l"],
        )
    )
    lines.append("")
    lines.append(
        "_Score 0–100 = blend of input-price momentum (40%), recent disruption "
        "headlines (40%), and watchlist match (20%). Higher = stronger evidence "
        "that demand is outrunning supply right now._"
    )
    lines.append("")

    # --- Per-sector detail ---
    lines.append("## Detail by sector")
    lines.append("")
    for s in ranked:
        lines.append(f"### {s.sector} — {s.score:.1f} ({_score_label(s.score)})")
        lines.append("")
        lines.append(
            f"- Price momentum: **{s.price_score:.0f}** · "
            f"Disruption news: **{s.news_score:.0f}** · "
            f"Watchlist: **{s.watchlist_score:.0f}**"
        )
        if s.watchlist_hits:
            terms = ", ".join(
                f"`{h.term}`" + (f" — {h.note}" if h.note else "") for h in s.watchlist_hits
            )
            lines.append(f"- Watchlist hits: {terms}")
        lines.append("")

        # price table
        if s.price_signals:
            lines.extend(
                _md_table(
                    ["Input", "Last", "5d", "20d", "z-score", "Spike"],
                    [_price_cells(ps) for ps in s.price_signals],
                    ["l", "r", "r", "r", "r", "l"],
                )
            )
            lines.append("")

        # headlines
        if s.news is not None and s.news.available and s.news.items:
            disruption_items = [i for i in s.news.items if i.disruption][:6]
            shown = disruption_items or s.news.items[:5]
            heading = "Disruption headlines" if disruption_items else "Recent headlines"
            lines.append(f"**{heading}:**")
            lines.append("")
            for item in shown:
                date = item.published.strftime("%b %d") if item.published else ""
                src = f" — {item.source}" if item.source else ""
                meta = f" _({date}{src})_" if (date or src) else ""
                lines.append(f"- [{item.title}]({item.link}){meta}")
            lines.append("")
        elif s.news is not None and not s.news.available:
            lines.append(f"_News unavailable: {s.news.error}_")
            lines.append("")
        else:
            lines.append("_No recent matching headlines._")
            lines.append("")

    # --- Methodology / caveats ---
    lines.append("## How to read this & caveats")
    lines.append("")
    lines.append(
        "- This is a **screening tool**, not a recommendation. A high score flags a "
        "sector worth a human look — verify with primary sources before acting."
    )
    lines.append(
        "- Price momentum uses public commodity futures as a proxy for input "
        "tightness. Rising input prices often precede downstream shortages and "
        "pricing power, but can also just reflect cost inflation."
    )
    lines.append(
        "- News volume reflects *attention*, which can lag or overshoot the real "
        "supply situation. Always click through."
    )
    lines.append(
        "- Free public sources can rate-limit or change format. See source status below."
    )
    lines.append("")

    lines.append("## Source status")
    lines.append("")
    for status in source_status:
        lines.append(f"- {status}")
    lines.append("")

    return "\n".join(lines)

"""Blend price, news and watchlist signals into a 0-100 gap score per sector.

The score answers: "how strong is the evidence that demand currently outstrips
supply in this sector right now?" Higher = more likely an opening.

Each component is normalised to 0-100 and combined with configurable weights:

  price_score    - trailing 20d momentum of the sector's commodities, plus a
                   bonus when any constituent is spiking (z-score / strong %).
  news_score     - volume of recent *disruption-flagged* headlines, saturating.
  watchlist_score- 100 if any of the user's watchlist terms match the sector or
                   one of its headlines, else 0.

All inputs are transparent and surfaced in the report so a human can judge.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sources.commodities import PriceSignal
from .sources.news import NewsItem, NewsSignal
from .sources.user_data import WatchItem


@dataclass
class SectorScore:
    sector: str
    score: float = 0.0
    price_score: float = 0.0
    news_score: float = 0.0
    watchlist_score: float = 0.0
    price_signals: list[PriceSignal] = field(default_factory=list)
    news: NewsSignal | None = None
    watchlist_hits: list[WatchItem] = field(default_factory=list)
    rationale: str = ""


def _price_component(signals: list[PriceSignal], spike_pct_strong: float) -> float:
    """0-100 from trailing momentum + spike bonus across a sector's commodities."""
    available = [s for s in signals if s.available and s.pct_change_20d is not None]
    if not available:
        return 0.0

    # Base: average 20d % change mapped so ~+20% -> 100, flat/negative -> 0.
    avg_20d = sum(s.pct_change_20d for s in available) / len(available)
    base = max(0.0, min(100.0, avg_20d / 20.0 * 100.0))

    # Spike bonus: strongest recent move among constituents (z-score or 5d %).
    bonus = 0.0
    for s in available:
        if s.zscore is not None and s.zscore >= 1.5:
            bonus = max(bonus, 20.0)
        if s.pct_change_5d is not None and s.pct_change_5d >= spike_pct_strong:
            bonus = max(bonus, 25.0)
    return min(100.0, base + bonus)


def _news_component(news: NewsSignal | None, saturate: int) -> float:
    """0-100 from count of disruption-flagged headlines, saturating at `saturate`."""
    if news is None or not news.available:
        return 0.0
    count = news.disruption_count
    if saturate <= 0:
        saturate = 1
    return min(100.0, count / saturate * 100.0)


def _watchlist_component(
    sector_name: str, news: NewsSignal | None, watchlist: list[WatchItem]
) -> tuple[float, list[WatchItem]]:
    if not watchlist:
        return 0.0, []
    hits: list[WatchItem] = []
    sector_low = sector_name.lower()
    headline_blob = " ".join(i.title.lower() for i in (news.items if news else []))
    for w in watchlist:
        term_low = w.term.lower()
        sector_match = bool(w.sector) and w.sector.lower() in sector_low
        in_sector_name = term_low in sector_low
        in_headlines = term_low in headline_blob
        if sector_match or in_sector_name or in_headlines:
            hits.append(w)
    return (100.0 if hits else 0.0), hits


def score_sector(
    sector_name: str,
    price_signals: list[PriceSignal],
    news: NewsSignal | None,
    watchlist: list[WatchItem],
    scoring_cfg: dict,
) -> SectorScore:
    w_price = float(scoring_cfg.get("w_price", 0.4))
    w_news = float(scoring_cfg.get("w_news", 0.4))
    w_watch = float(scoring_cfg.get("w_watchlist", 0.2))
    spike_pct_strong = float(scoring_cfg.get("spike_pct_strong", 8.0))
    saturate = int(scoring_cfg.get("news_count_saturate", 8))

    price_score = _price_component(price_signals, spike_pct_strong)
    news_score = _news_component(news, saturate)
    watch_score, hits = _watchlist_component(sector_name, news, watchlist)

    composite = w_price * price_score + w_news * news_score + w_watch * watch_score

    rationale = _build_rationale(price_score, news_score, watch_score, news, hits)

    return SectorScore(
        sector=sector_name,
        score=round(composite, 1),
        price_score=round(price_score, 1),
        news_score=round(news_score, 1),
        watchlist_score=round(watch_score, 1),
        price_signals=price_signals,
        news=news,
        watchlist_hits=hits,
        rationale=rationale,
    )


def _build_rationale(
    price: float, news_s: float, watch: float, news: NewsSignal | None, hits: list[WatchItem]
) -> str:
    parts: list[str] = []
    if price >= 50:
        parts.append("strong upward price pressure on inputs")
    elif price >= 20:
        parts.append("mild input price momentum")
    if news is not None and news.available and news.disruption_count:
        parts.append(f"{news.disruption_count} recent disruption headline(s)")
    if watch:
        terms = ", ".join(h.term for h in hits[:3])
        parts.append(f"matches your watchlist ({terms})")
    if not parts:
        return "no strong signal today"
    return "; ".join(parts)


def rank_sectors(scores: list[SectorScore]) -> list[SectorScore]:
    return sorted(scores, key=lambda s: s.score, reverse=True)

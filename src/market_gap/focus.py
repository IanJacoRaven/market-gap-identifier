"""Focus layer — steer WHERE the scan looks.

Driven by the `focus` block in config.json. It lets the user bias the scan toward
specific geographies, prefer/cite certain sources, boost chosen sectors, and pull
their own RSS feeds or custom search queries into the analyst's context.

Everything here is additive and safe: empty/missing focus config = original
global behaviour.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .http_util import get_text

# Geography tokens that mean "no regional bias" — ignored when building queries.
_GLOBAL_TOKENS = {"global", "worldwide", "world", ""}


@dataclass
class FocusConfig:
    mode: str = "augment"
    geographies: list[str] = None  # type: ignore[assignment]
    priority_sectors: list[str] = None  # type: ignore[assignment]
    priority_sector_boost: float = 8.0
    preferred_sources: list[str] = None  # type: ignore[assignment]
    custom_rss: list[str] = None  # type: ignore[assignment]
    extra_queries: list[str] = None  # type: ignore[assignment]
    max_web_searches: int = 8

    @classmethod
    def from_dict(cls, d: dict | None) -> "FocusConfig":
        d = d or {}
        return cls(
            mode=d.get("mode", "augment"),
            geographies=[g for g in d.get("geographies", []) if g.strip().lower() not in _GLOBAL_TOKENS],
            priority_sectors=list(d.get("priority_sectors", [])),
            priority_sector_boost=float(d.get("priority_sector_boost", 8.0)),
            preferred_sources=list(d.get("preferred_sources", [])),
            custom_rss=list(d.get("custom_rss", [])),
            extra_queries=list(d.get("extra_queries", [])),
            max_web_searches=int(d.get("max_web_searches", 8)),
        )

    @property
    def geo_or(self) -> str:
        """Geographies joined for a single search clause, e.g. 'South Africa OR Europe'."""
        return " OR ".join(self.geographies)


def build_search_queries(ranked, focus: FocusConfig, suffix: str, top_sectors: int) -> list[str]:
    """Prioritised list of web-search queries: global per sector, then geo-focused, then extras.

    Truncated to focus.max_web_searches to stay polite to the search endpoint.
    """
    sectors = [s.sector for s in ranked[:top_sectors]]
    global_q = [f"{name} {suffix}".strip() for name in sectors]
    geo_q: list[str] = []
    if focus.geographies:
        clause = focus.geo_or
        geo_q = [f"{name} {suffix} {clause}".strip() for name in sectors]

    # Interleave so the top sector's global + geo queries come first.
    ordered: list[str] = []
    for i in range(len(sectors)):
        ordered.append(global_q[i])
        if i < len(geo_q):
            ordered.append(geo_q[i])
    ordered.extend(focus.extra_queries)

    # De-dupe preserving order, then cap.
    seen: set[str] = set()
    out: list[str] = []
    for q in ordered:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[: max(1, focus.max_web_searches)]


def prompt_note(focus: FocusConfig) -> str:
    """A short instruction block telling the analyst how to apply the focus."""
    parts: list[str] = []
    if focus.geographies:
        parts.append(
            f"Geographic focus: weight opportunities relevant to {', '.join(focus.geographies)} "
            "more highly, but still flag major global gaps. Note when an opportunity is "
            "specifically accessible from these regions."
        )
    if focus.priority_sectors:
        parts.append(f"Priority sectors to emphasise: {', '.join(focus.priority_sectors)}.")
    if focus.preferred_sources:
        parts.append(
            "Preferred sources — when corroborating or citing, prefer these where present: "
            + ", ".join(focus.preferred_sources)
            + "."
        )
    if not parts:
        return ""
    return "FOCUS DIRECTIVES:\n- " + "\n- ".join(parts)


def fetch_rss_context(urls: list[str], max_items: int, timeout: int) -> str:
    """Pull the user's own RSS feeds (any standard RSS/Atom) into a context block."""
    blocks: list[str] = []
    for url in urls:
        raw = get_text(url, timeout=timeout)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        items = list(root.iter("item")) or list(root.iter("{http://www.w3.org/2005/Atom}entry"))
        if not items:
            continue
        lines = [f"### Your feed: {url}"]
        for item in items[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title:
                continue
            lines.append(f"- {title}{(' (' + link + ')') if link else ''}")
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def apply_priority_boost(scores, focus: FocusConfig):
    """Bump the composite score of any configured priority sector."""
    if not focus.priority_sectors:
        return scores
    targets = {s.lower() for s in focus.priority_sectors}
    for s in scores:
        if s.sector.lower() in targets:
            s.score = round(s.score + focus.priority_sector_boost, 1)
            s.rationale = (s.rationale + "; priority sector").lstrip("; ")
    return scores

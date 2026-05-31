"""Disruption signals via Google News RSS search (free, no key).

For each sector we run its news query and keep recent items. Headlines that
contain explicit disruption language ("shortage", "export ban", ...) are
flagged — those are the strongest gap signals.
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from ..http_util import get_text

_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

DISRUPTION_TERMS = (
    "shortage", "shortages", "supply disruption", "export ban", "import ban",
    "factory shutdown", "factory fire", "backorder", "back-order", "stockout",
    "out of stock", "lead time", "lead times", "rationing", "force majeure",
    "production halt", "plant closure", "sanction", "embargo", "recall",
    "bottleneck", "scarcity", "curtailment", "strike",
)


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime | None
    disruption: bool = False


@dataclass
class NewsSignal:
    query: str
    available: bool = False
    items: list[NewsItem] = field(default_factory=list)
    error: str | None = None

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def disruption_count(self) -> int:
        return sum(1 for i in self.items if i.disruption)


def _is_disruption(title: str) -> bool:
    low = title.lower()
    return any(term in low for term in DISRUPTION_TERMS)


def fetch_news_signal(query: str, lookback_days: int, timeout: int) -> NewsSignal:
    url = _RSS_URL.format(query=urllib.parse.quote(query))
    raw = get_text(url, timeout=timeout)
    if raw is None:
        return NewsSignal(query=query, error="fetch failed (network/blocked)")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return NewsSignal(query=query, error="RSS parse error")

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    items: list[NewsItem] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        pub_raw = item.findtext("pubDate")
        published = None
        if pub_raw:
            try:
                published = parsedate_to_datetime(pub_raw)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        if published is not None and published < cutoff:
            continue
        if not title:
            continue
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=source,
                published=published,
                disruption=_is_disruption(title),
            )
        )

    items.sort(key=lambda i: i.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return NewsSignal(query=query, available=True, items=items)

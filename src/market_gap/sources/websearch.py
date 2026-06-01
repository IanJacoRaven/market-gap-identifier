"""Free web search via DuckDuckGo — no API key, stdlib only.

Used to widen the local analyst's context: for the top-ranked sectors we run a
search and feed the result titles + snippets into the model's prompt. This is the
local, token-free stand-in for the cloud agent's live web research. It uses your
bandwidth, not tokens.

Robustness notes:
- DuckDuckGo rate-limits scraping and will serve an anti-bot "challenge" page on
  bursty traffic. We use the lighter `lite.duckduckgo.com` endpoint first, fall
  back to the `html.` endpoint, space requests out, and retry once with backoff.
- In normal daily use this runs only a handful of times, so blocking is unlikely.
- On any failure it returns an empty list and the pipeline continues with the
  price + news signals it already has.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

from ..http_util import get_text

_ENDPOINTS = (
    "https://lite.duckduckgo.com/lite/?q={query}",
    "https://html.duckduckgo.com/html/?q={query}",
)
# Markers that indicate DuckDuckGo served a bot-challenge / error page.
_CHALLENGE_MARKERS = ("unusual traffic", "challenge", "if this error persists", "anomaly")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class _DDGParser(HTMLParser):
    """Parses both the lite and html DuckDuckGo result formats.

    lite: <a class="result-link" href=...>title</a> ... <td class="result-snippet">snippet</td>
    html: <a class="result__a" href=...>title</a> ... <a class="result__snippet">snippet</a>
    """

    _TITLE_MARKERS = ("result-link", "result__a")
    _SNIPPET_MARKERS = ("result-snippet", "result__snippet")

    def __init__(self) -> None:
        super().__init__()
        self.results: list[SearchResult] = []
        self._in_title = False
        self._in_snippet = False
        self._snippet_tag = ""
        self._cur_title: list[str] = []
        self._cur_snippet: list[str] = []
        self._cur_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = (dict(attrs).get("class", "") or "")
        if tag == "a" and any(m in cls for m in self._TITLE_MARKERS):
            self._in_title = True
            self._cur_title = []
            self._cur_url = _decode_ddg_url(dict(attrs).get("href", "") or "")
        elif any(m in cls for m in self._SNIPPET_MARKERS):
            self._in_snippet = True
            self._snippet_tag = tag
            self._cur_snippet = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag == "a":
            self._in_title = False
            title = " ".join("".join(self._cur_title).split())
            if title:
                self.results.append(SearchResult(title=title, url=self._cur_url, snippet=""))
        elif self._in_snippet and tag == self._snippet_tag:
            self._in_snippet = False
            snippet = " ".join("".join(self._cur_snippet).split())
            if snippet and self.results and not self.results[-1].snippet:
                self.results[-1].snippet = snippet

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._cur_title.append(data)
        elif self._in_snippet:
            self._cur_snippet.append(data)


def _decode_ddg_url(href: str) -> str:
    """DuckDuckGo wraps result links as /l/?uddg=<encoded real url>. Unwrap it."""
    if "uddg=" not in href:
        return href if not href.startswith("//") else "https:" + href
    try:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if "uddg" in params:
            return urllib.parse.unquote(params["uddg"][0])
    except Exception:  # noqa: BLE001
        pass
    return href


def _looks_blocked(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in _CHALLENGE_MARKERS) and "result-link" not in low and "result__a" not in low


def search(query: str, max_results: int = 5, timeout: int = 20, retries: int = 1) -> list[SearchResult]:
    """Search DuckDuckGo, trying lite then html endpoints, with one backoff retry."""
    for attempt in range(retries + 1):
        for endpoint in _ENDPOINTS:
            html = get_text(endpoint.format(query=urllib.parse.quote(query)), timeout=timeout)
            if not html or _looks_blocked(html):
                continue
            parser = _DDGParser()
            try:
                parser.feed(html)
            except Exception:  # noqa: BLE001 - malformed HTML shouldn't crash the run
                pass
            if parser.results:
                return parser.results[:max_results]
        if attempt < retries:
            time.sleep(3.0 * (attempt + 1))  # back off before retrying
    return []


def gather_context(
    queries: list[str],
    max_results: int,
    timeout: int,
    delay_seconds: float = 2.0,
    news_fallback_days: int = 7,
) -> str:
    """Run several searches (spaced out to be polite) and format as a context block.

    For each query, try DuckDuckGo first; if it's blocked/empty, fall back to a
    Google News RSS search so the analyst still gets widened, current context.
    """
    from . import news  # local import to avoid any import-order issues

    blocks: list[str] = []
    for i, q in enumerate(queries):
        if i > 0:
            time.sleep(delay_seconds)  # space requests to avoid rate-limiting

        results = search(q, max_results=max_results, timeout=timeout)
        if results:
            lines = [f"### Search: {q}"]
            for r in results:
                snip = f" — {r.snippet}" if r.snippet else ""
                lines.append(f"- {r.title}{snip} ({r.url})")
            blocks.append("\n".join(lines))
            continue

        # Fallback: Google News RSS (reliable, free) when web search is unavailable.
        sig = news.fetch_news_signal(q, news_fallback_days, timeout)
        if sig.available and sig.items:
            lines = [f"### Search (via Google News): {q}"]
            for item in sig.items[:max_results]:
                src = f" — {item.source}" if item.source else ""
                lines.append(f"- {item.title}{src} ({item.link})")
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

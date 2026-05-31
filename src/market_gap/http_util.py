"""Tiny stdlib HTTP GET helper with a browser User-Agent and graceful failure.

Kept dependency-free on purpose: this tool must run on a bare Python install
with no pip packages and no API keys.
"""

from __future__ import annotations

import urllib.error
import urllib.request

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def get(url: str, timeout: int = 20) -> bytes | None:
    """Return response body bytes, or None on any failure (logged by caller)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def get_text(url: str, timeout: int = 20, encoding: str = "utf-8") -> str | None:
    body = get(url, timeout=timeout)
    if body is None:
        return None
    return body.decode(encoding, errors="replace")

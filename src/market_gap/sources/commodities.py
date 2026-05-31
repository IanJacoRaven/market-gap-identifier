"""Commodity / raw-material price signals via Yahoo Finance's public chart JSON.

A fast price rise (positive momentum + a recent spike) is a proxy for supply
tightening relative to demand — exactly the imbalance a market-gap hunt cares
about. We compute, per symbol:

  * last close
  * % change over ~5 and ~20 trading days
  * a z-score of the latest close vs the trailing window (spike detector)

No API key required. If Yahoo is unreachable the symbol is simply marked
unavailable and the pipeline continues.
"""

from __future__ import annotations

import json
import statistics
import urllib.parse
from dataclasses import dataclass, field

from ..http_util import get_text

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"


@dataclass
class PriceSignal:
    symbol: str
    name: str
    available: bool = False
    last_close: float | None = None
    pct_change_5d: float | None = None
    pct_change_20d: float | None = None
    zscore: float | None = None
    error: str | None = None
    history: list[float] = field(default_factory=list)


def _pct_change(series: list[float], lookback: int) -> float | None:
    if len(series) <= lookback:
        return None
    past = series[-(lookback + 1)]
    last = series[-1]
    if past == 0:
        return None
    return (last - past) / past * 100.0


def fetch_price_signal(symbol: str, name: str, price_range: str, timeout: int) -> PriceSignal:
    url = _CHART_URL.format(symbol=urllib.parse.quote(symbol), range=price_range)
    raw = get_text(url, timeout=timeout)
    if raw is None:
        return PriceSignal(symbol=symbol, name=name, error="fetch failed (network/blocked)")

    try:
        data = json.loads(raw)
        result = data["chart"]["result"][0]
        closes_raw = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return PriceSignal(symbol=symbol, name=name, error="unexpected response shape")

    closes = [c for c in closes_raw if isinstance(c, (int, float))]
    if len(closes) < 6:
        return PriceSignal(symbol=symbol, name=name, error="insufficient history")

    window = closes[-20:] if len(closes) >= 20 else closes
    mean = statistics.fmean(window)
    stdev = statistics.pstdev(window) if len(window) > 1 else 0.0
    z = ((closes[-1] - mean) / stdev) if stdev > 0 else 0.0

    return PriceSignal(
        symbol=symbol,
        name=name,
        available=True,
        last_close=round(closes[-1], 4),
        pct_change_5d=_round_opt(_pct_change(closes, 5)),
        pct_change_20d=_round_opt(_pct_change(closes, 20)),
        zscore=round(z, 2),
        history=closes[-30:],
    )


def _round_opt(v: float | None, ndigits: int = 2) -> float | None:
    return round(v, ndigits) if v is not None else None


def fetch_all(commodities: dict[str, str], price_range: str, timeout: int) -> dict[str, PriceSignal]:
    """commodities: {symbol: display_name}. Returns {symbol: PriceSignal}."""
    return {
        sym: fetch_price_signal(sym, name, price_range, timeout)
        for sym, name in commodities.items()
    }

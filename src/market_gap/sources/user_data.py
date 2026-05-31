"""Load the user's own watchlist (CSV) to focus and boost the scan.

CSV columns (header row required, case-insensitive, extra columns ignored):
    term     - keyword/material/product to watch (required)
    sector   - optional sector name to associate it with
    note     - optional free-text note carried into the report

If the file is missing the scan still runs on public signals alone.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WatchItem:
    term: str
    sector: str = ""
    note: str = ""


def load_watchlist(path: str | Path) -> list[WatchItem]:
    p = Path(path)
    if not p.exists():
        return []
    items: list[WatchItem] = []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return []
        cols = {(c or "").strip().lower(): c for c in reader.fieldnames}
        term_col = cols.get("term")
        if term_col is None:
            return []
        for row in reader:
            term = (row.get(term_col) or "").strip()
            if not term:
                continue
            items.append(
                WatchItem(
                    term=term,
                    sector=(row.get(cols.get("sector", "")) or "").strip(),
                    note=(row.get(cols.get("note", "")) or "").strip(),
                )
            )
    return items

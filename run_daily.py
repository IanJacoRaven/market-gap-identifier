#!/usr/bin/env python3
"""Convenience entry point: `python run_daily.py`.

Equivalent to `python -m market_gap.cli` but works without setting PYTHONPATH.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from market_gap.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

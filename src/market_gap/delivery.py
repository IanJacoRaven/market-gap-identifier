"""Deliver the daily brief somewhere the user will actually see it.

Local + token-free. Default target is a OneDrive folder (syncs to phone/web), with
an optional Desktop "latest" copy. The delivered document is brief-FIRST (the
decision content on top), with the full mechanical scan below for detail.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def _onedrive_root() -> Path | None:
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        val = os.environ.get(var)
        if val and Path(val).exists():
            return Path(val)
    fallback = Path(os.environ.get("USERPROFILE", "")) / "OneDrive"
    return fallback if fallback.exists() else None


def _compose(brief_text: str, mechanical_md: str, date_str: str) -> str:
    return (
        f"# Market Gap Brief — {date_str}\n\n"
        f"{brief_text.strip()}\n\n"
        "---\n\n"
        "<sub>Full mechanical scan (data the brief is based on) below.</sub>\n\n"
        f"{mechanical_md.strip()}\n"
    )


def deliver(
    brief_text: str,
    mechanical_md: str,
    date_str: str,
    delivery_cfg: dict,
) -> list[str]:
    """Write the brief to configured destinations. Returns human-readable status lines."""
    if not delivery_cfg.get("enabled", False):
        return []
    doc = _compose(brief_text, mechanical_md, date_str)
    status: list[str] = []

    od = delivery_cfg.get("onedrive", {})
    if od.get("enabled", False):
        status.append(_deliver_onedrive(doc, date_str, od))

    dt = delivery_cfg.get("desktop", {})
    if dt.get("enabled", False):
        status.append(_deliver_desktop(doc, dt))

    return status


def _deliver_onedrive(doc: str, date_str: str, cfg: dict) -> str:
    root = _onedrive_root()
    if root is None:
        return "OneDrive delivery skipped: OneDrive folder not found"
    folder = root / cfg.get("subfolder", "Market Gap Briefs")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"Market Gap Brief {date_str}.md"
        out.write_text(doc, encoding="utf-8")
        return f"Delivered to OneDrive: {out}"
    except OSError as e:
        return f"OneDrive delivery failed: {e}"


def _deliver_desktop(doc: str, cfg: dict) -> str:
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    if not desktop.exists():
        return "Desktop delivery skipped: Desktop folder not found"
    try:
        out = desktop / cfg.get("filename", "Market Gap Brief (latest).md")
        out.write_text(doc, encoding="utf-8")
        return f"Delivered to Desktop: {out}"
    except OSError as e:
        return f"Desktop delivery failed: {e}"

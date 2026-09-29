"""Timestamps. Event times are stored as canonical UTC strings, "2026-09-30T02:00:00Z", which sort
correctly as text. Bookkeeping times (last pull and so on) are epoch seconds."""
from __future__ import annotations

from datetime import datetime, timezone


def to_iso(value: datetime | float | int) -> str:
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, tz=timezone.utc)
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp with an offset or Z. Returns None for anything unusable."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def canonical(value: object) -> str | None:
    """Coerce an upstream timestamp (ISO string, epoch seconds or ms, Firestore {seconds}) to canonical UTC."""
    try:
        if isinstance(value, dict):
            seconds = value.get("seconds", value.get("_seconds"))
            return to_iso(float(seconds)) if seconds is not None else None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return to_iso(value / 1000 if value > 1e12 else value)
        if isinstance(value, str):
            parsed = parse_iso(value)
            return to_iso(parsed) if parsed else None
    except (OverflowError, OSError, ValueError, TypeError):
        return None
    return None

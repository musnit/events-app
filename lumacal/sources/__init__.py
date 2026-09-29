"""Clients for the upstreams. Each module turns upstream JSON into listing dicts of one shape:

    {
      "id", "source", "name", "url", "start_at", "end_at", "all_day", "timezone", "cover_url",
      "location": {"type", "venue", "address", "city", "neighborhood", "region", "country", "lat", "lng"},
      "presenter": {"id", "name", "avatar_url", "url", "description"} | None,
      "hosts": [{"name", "avatar_url"}],
      "tags": [str],
      "ticket": {"free", "price_cents", "max_price_cents", "currency", "sold_out", "spots_left",
                 "approval", "waitlist", "availability"} | None,
      "guest_count": int | None,
      "going_status": str | None,   # the user's own RSVP when the upstream reports it
    }

Times are canonical UTC strings (see ``timeutil``).
"""
from __future__ import annotations


def empty_location(kind: str = "unknown") -> dict:
    return {"type": kind, "venue": None, "address": None, "city": None, "neighborhood": None,
            "region": None, "country": None, "lat": None, "lng": None}


def clean_text(value: object) -> str:
    """Collapse whitespace and drop lone UTF-16 surrogates, which JSON allows but UTF-8 cannot encode."""
    text = str(value or "")
    if any("\ud800" <= ch <= "\udfff" for ch in text):
        text = text.encode("utf-8", "replace").decode("utf-8")
    return " ".join(text.split())


def scrub(value: object) -> object:
    """Recursively drop lone surrogates from every string in a JSON-like value."""
    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8") if any("\ud800" <= ch <= "\udfff" for ch in value) else value
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def as_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN

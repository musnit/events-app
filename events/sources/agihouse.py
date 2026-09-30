"""AGI House publishes its events as public JSON behind agihouse.org/events."""
from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable

from .. import net
from ..timeutil import canonical
from . import clean_text, empty_location

API = "https://jtwthn6xog.execute-api.us-east-1.amazonaws.com/events"
LOGO = "https://www.agihouse.org/og-logo.png"
CALENDAR = {"id": "agihouse", "source": "agihouse", "name": "AGI House", "slug": None, "avatar_url": LOGO,
            "tint_color": "#2dd4bf", "url": "https://www.agihouse.org/events", "description": "AI hacker house in the Bay Area"}


def normalize_event(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("status") not in (None, "published") or raw.get("privacy") not in (None, "public") or raw.get("dateTbd"):
        return None
    start = canonical(raw.get("startTime"))
    slug = raw.get("slug") or raw.get("id")
    if not start or not slug:
        return None
    place = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    city_text = clean_text(place.get("city"))
    loc = empty_location("online" if place.get("isVirtual") else "offline")
    loc.update({
        "venue": clean_text(place.get("name")) or None,
        "address": clean_text(place.get("address")) or None,
        "city": re.sub(r",?\s*CA\b.*$", "", city_text).strip() or None,
        "region": "CA" if re.search(r"\bCA\b", city_text) else None,
    })
    return {
        "id": f"agi-{slug}", "source": "agihouse", "name": clean_text(raw.get("title")) or "Untitled event",
        "url": "https://www.agihouse.org/events/" + urllib.parse.quote(str(slug)),
        "start_at": start, "end_at": canonical(raw.get("endTime")), "all_day": False,
        "timezone": raw.get("timezone") or None, "cover_url": raw.get("coverImageUrl") or None,
        "location": loc,
        "presenter": {"id": "agihouse", "name": "AGI House", "avatar_url": LOGO, "url": CALENDAR["url"],
                      "description": CALENDAR["description"]},
        "hosts": [], "tags": [clean_text(raw["type"])] if clean_text(raw.get("type")) else [], "ticket": None,
        "guest_count": None, "going_status": None,
    }


class AgiHouseClient:
    def __init__(self, request: Callable[..., net.Response] = net.request):
        self._request = request

    def events(self) -> list[dict]:
        resp = self._request(API, headers={"accept": "application/json", "origin": "https://www.agihouse.org"})
        try:
            data = resp.json()
        except ValueError:
            raise ValueError("AGI House answered with something that is not JSON") from None
        raw = data.get("events") if isinstance(data, dict) else None
        return [ev for ev in (normalize_event(r) for r in raw or []) if ev]

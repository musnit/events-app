"""Luma, through the same internal endpoints luma.com uses (undocumented; may change).

- ``GET /calendar/get-items?calendar_api_id=cal-…&period=future`` is public and paginated.
- ``GET /home/get-following-calendars`` and ``GET /home/get-events`` need the
  ``luma.auth-session-key`` cookie.
- Email-code sign-in is blocked by a Cloudflare Turnstile check, so a session comes from a pasted
  cookie. Calendars usually come from the bookmarklet, which reads the followed list in the user's
  own browser.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from .. import ics, net
from ..timeutil import canonical
from . import as_float, clean_text, empty_location

API = "https://api.luma.com"
SITE = "https://luma.com"
COOKIE_NAME = "luma.auth-session-key"
ONLINE_TYPES = {"zoom", "meet", "google_meet", "online", "virtual", "teams", "youtube", "twitch", "webinar",
                "livestream", "link"}
GOING_STATUSES = {"approved", "pending_approval", "waitlist", "registered", "going"}
NOT_CALENDAR_PATHS = {"user", "discover", "signin", "home", "settings", "create", "event", "e", "explore", "pricing",
                      "calendar", "ics", "search", "login"}
LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:lu\.ma|luma\.com)/([A-Za-z0-9_./-]+)|\b(cal-[A-Za-z0-9]{10,24})\b")
ORG_WORDS_RE = re.compile(
    r"\b(club|lab|labs|house|society|community|collective|group|network|team|events|inc|co|ventures|capital|"
    r"foundation|institute|university|studio|studios|ai|meetup|commons|hub|school|academy|alliance|association|"
    r"council|guild|company|partners|fund|accelerator|incubator|org)\b|&", re.I)


class LumaError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"Luma {status or 'unreachable'}: {message}")
        self.status = status
        self.message = message

    @property
    def rate_limited(self) -> bool:
        return self.status == 429

    @property
    def unauthorized(self) -> bool:
        return self.status == 401


@dataclass
class ImportPayload:
    calendars: list[dict]
    going: list[str] = field(default_factory=list)
    session_key: str | None = None


# ---------- normalization (pure) ----------

def normalize_calendar(item: object) -> dict | None:
    """Following-calendars entries may be bare calendar objects or wrappers around one."""
    if not isinstance(item, dict):
        return None
    cal = item.get("calendar") if isinstance(item.get("calendar"), dict) else item
    api_id = cal.get("api_id") or cal.get("calendar_api_id")
    if not isinstance(api_id, str) or not api_id.startswith("cal-"):
        return None
    name = clean_text(cal.get("name"))
    owner = cal.get("personal_user") if isinstance(cal.get("personal_user"), dict) else None
    # Personal calendars are all called "Personal"; the owner's name says more.
    if owner and clean_text(owner.get("name")) and (not name or cal.get("is_personal")):
        name = clean_text(owner["name"])
    slug = cal.get("slug") if isinstance(cal.get("slug"), str) and cal.get("slug") else None
    return {
        "id": api_id, "source": "luma", "name": name or api_id, "slug": slug,
        "avatar_url": cal.get("avatar_url") or None, "tint_color": cal.get("tint_color") or None,
        "url": f"{SITE}/{slug or api_id}",
        "description": clean_text(cal.get("description_short")) or None,
    }


def find_calendar_objects(obj: object, out: list[dict]) -> None:
    """Collect every dict that looks like a Luma calendar (api_id cal-…) from nested JSON."""
    if isinstance(obj, dict):
        if str(obj.get("api_id", "")).startswith("cal-") and "name" in obj:
            out.append(obj)
            return
        for value in obj.values():
            find_calendar_objects(value, out)
    elif isinstance(obj, list):
        for value in obj:
            find_calendar_objects(value, out)


def host_org_score(host: dict) -> int:
    """Higher when a Luma host record looks like an organisation rather than a person."""
    name = clean_text(host.get("name"))
    personal = " ".join(x for x in (clean_text(host.get("first_name")), clean_text(host.get("last_name"))) if x)
    score = 0
    if personal and name.lower() != personal.lower():
        score += 2  # display name differs from the person's own name, e.g. "Weights & Biases"
    if ORG_WORDS_RE.search(name):
        score += 2
    if host.get("website"):
        score += 1
    if len(name.split()) >= 3 or "-" in name or "|" in name:
        score += 1
    return score


def _location(ev: dict) -> dict:
    geo = ev.get("geo_address_info") if isinstance(ev.get("geo_address_info"), dict) else {}
    kind = (ev.get("location_type") or "").lower()
    coord = ev.get("coordinate") if isinstance(ev.get("coordinate"), dict) else geo.get("place_coordinate") or {}
    loc = empty_location("online" if kind in ONLINE_TYPES else "offline" if (kind == "offline" or geo) else "unknown")
    address = clean_text(geo.get("short_address") or geo.get("full_address")) or None
    venue = clean_text(geo.get("address")) or None
    if venue and address and address.startswith(venue):
        venue = None  # Luma repeats the street as the "venue" when there is no place name
    loc.update({
        "venue": venue, "address": address,
        "city": clean_text(geo.get("city") or geo.get("city_state")) or None,
        "neighborhood": clean_text(geo.get("sublocality")) or None,
        "region": geo.get("region_short") or geo.get("region") or None,
        "country": geo.get("country_code") or None,
        "lat": as_float(coord.get("latitude")) if isinstance(coord, dict) else None,
        "lng": as_float(coord.get("longitude")) if isinstance(coord, dict) else None,
    })
    return loc


def _ticket(entry: dict, ev: dict) -> dict | None:
    info = entry.get("ticket_info") if isinstance(entry.get("ticket_info"), dict) else None
    availability = entry.get("registration_availability") if isinstance(entry.get("registration_availability"), str) else None
    if not info and not availability:
        return None
    info = info or {}
    price = info.get("price") if isinstance(info.get("price"), dict) else {}
    max_price = info.get("max_price") if isinstance(info.get("max_price"), dict) else {}
    spots = info.get("spots_remaining")
    return {
        "free": info.get("is_free") if isinstance(info.get("is_free"), bool) else None,
        "price_cents": price.get("cents") if isinstance(price.get("cents"), int) else None,
        "max_price_cents": max_price.get("cents") if isinstance(max_price.get("cents"), int) else None,
        "currency": price.get("currency") or None,
        "sold_out": bool(info.get("is_sold_out")) or availability == "sold-out",
        "spots_left": spots if isinstance(spots, int) else None,
        "approval": bool(info.get("require_approval")),
        "waitlist": bool(entry.get("waitlist_active")) or ev.get("waitlist_status") == "active",
        "availability": availability,
    }


def normalize_entry(entry: object, *, going_status: str | None = None) -> dict | None:
    """One get-items / get-events entry to a listing dict."""
    if not isinstance(entry, dict):
        return None
    ev = entry.get("event") if isinstance(entry.get("event"), dict) else None
    if not ev or not isinstance(ev.get("api_id"), str):
        return None
    start = canonical(ev.get("start_at") or entry.get("start_at"))
    if not start:
        return None
    hosts = sorted((h for h in entry.get("hosts") or [] if isinstance(h, dict) and clean_text(h.get("name"))),
                   key=host_org_score, reverse=True)
    presenter = normalize_calendar(entry["calendar"]) if isinstance(entry.get("calendar"), dict) else None
    guest = entry.get("guest_info") if isinstance(entry.get("guest_info"), dict) else {}
    status = guest.get("approval_status") if guest.get("approval_status") in GOING_STATUSES else None
    tags = [clean_text(t.get("name")) for t in entry.get("tags") or [] if isinstance(t, dict) and clean_text(t.get("name"))]
    guest_count = entry.get("guest_count")
    return {
        "id": ev["api_id"], "source": "luma", "name": clean_text(ev.get("name")) or "Untitled event",
        "url": f"{SITE}/{ev.get('url') or ev['api_id']}",
        "start_at": start, "end_at": canonical(ev.get("end_at")), "all_day": False,
        "timezone": ev.get("timezone") or None, "cover_url": ev.get("cover_url") or None,
        "location": _location(ev),
        "presenter": {k: presenter[k] for k in ("id", "name", "avatar_url", "url", "description")} if presenter else None,
        "hosts": [{"name": clean_text(h["name"]), "avatar_url": h.get("avatar_url") or None} for h in hosts],
        "tags": tags, "ticket": _ticket(entry, ev),
        "guest_count": guest_count if isinstance(guest_count, int) else None,
        "going_status": status or going_status,
    }


def calendar_tokens(text: str) -> list[str]:
    """Pull calendar slugs or cal- ids out of pasted text (links, share sheets, bare ids)."""
    seen: list[str] = []
    for match in LINK_RE.finditer(text or ""):
        token = (match.group(2) or match.group(1) or "").strip("/").split("?")[0].split("#")[0]
        embedded = re.search(r"\b(cal-[A-Za-z0-9]{10,24})\b", token)
        if embedded:  # e.g. luma.com/calendar/manage/cal-…
            token = embedded.group(1)
        if token and token not in seen:
            seen.append(token)
    return seen


def normalize_ics_url(url: str) -> str:
    """Accept the raw ics/get link, a webcal:// form, or a Google 'add by URL' link carrying cid=."""
    url = (url or "").strip()
    match = re.search(r"[?&]cid=([^&]+)", url)
    if match and "google.com" in url:
        url = urllib.parse.unquote(match.group(1))
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    host = urllib.parse.urlsplit(url).hostname or ""
    if not url.startswith("https://") or "/ics/get" not in url or not re.search(r"(^|\.)(lu\.ma|luma\.com)$", host):
        raise ValueError("expected a Luma iCal subscription link (…/ics/get?entity=user&id=…)")
    return url


def parse_personal_feed(text: str) -> list[dict]:
    """Events from the user's personal Luma iCal feed: the ones they are registered for."""
    if not ics.looks_like_calendar(text):
        raise ValueError("that URL did not return an iCalendar feed")
    out = []
    for vevent in ics.parse(text):
        uid = vevent.get("UID", "")
        start, end, all_day = ics.event_times(vevent)
        if not start:
            continue
        url = vevent.get("URL") or ""
        event_id = uid.split("@")[0] if uid.startswith("evt-") else None
        if not event_id:
            match = re.search(r"\b(evt-[A-Za-z0-9]+)", url + " " + vevent.get("DESCRIPTION", ""))
            event_id = match.group(1) if match else "luma-ics-" + re.sub(r"[^A-Za-z0-9_.-]", "", uid)[:80]
        location = ics.unescape(vevent.get("LOCATION", "")).strip()
        loc = empty_location("offline" if location else "unknown")
        if location.startswith("http"):
            loc["type"] = "online"
        elif location:
            loc["address"] = location
        out.append({
            "id": event_id, "source": "luma", "name": clean_text(ics.unescape(vevent.get("SUMMARY", ""))) or "Untitled event",
            "url": url or (f"{SITE}/{event_id}" if event_id.startswith("evt-") else f"{SITE}/home"),
            "start_at": start, "end_at": end, "all_day": all_day, "timezone": vevent.get("DTSTART__params", {}).get("TZID"),
            "cover_url": None, "location": loc, "presenter": None, "hosts": [], "tags": [], "ticket": None,
            "guest_count": None, "going_status": "registered",
        })
    return out


def parse_import_payload(payload: object) -> ImportPayload:
    """The bookmarklet sends {calendars: [...], going: [evt-…], session_key}; older versions sent raw JSON."""
    if not isinstance(payload, (dict, list)):
        raise ValueError("the import payload is not JSON")
    source = payload.get("calendars", payload) if isinstance(payload, dict) else payload
    found: list[dict] = []
    find_calendar_objects(source, found)
    calendars: dict[str, dict] = {}
    for item in found:
        cal = normalize_calendar(item)
        if cal:
            calendars.setdefault(cal["id"], cal)
    going = []
    session_key = None
    if isinstance(payload, dict):
        going = [g for g in payload.get("going") or [] if isinstance(g, str) and g.startswith("evt-")]
        key = payload.get("session_key")
        session_key = key.strip() if isinstance(key, str) and key.strip() else None
    return ImportPayload(calendars=list(calendars.values()), going=going, session_key=session_key)


def parse_session_key(text: str) -> str:
    key = (text or "").strip().strip(";").strip()
    if key.startswith(COOKIE_NAME + "="):
        key = key.split("=", 1)[1]
    if not key or any(ch.isspace() for ch in key) or len(key) > 4096:
        raise ValueError("paste the value of the luma.auth-session-key cookie")
    return key


# ---------- client ----------

class LumaClient:
    def __init__(self, request: Callable[..., net.Response] = net.request):
        self._request = request

    def call(self, path: str, params: dict | None = None, *, body: object = None, session_key: str | None = None) -> dict:
        headers = {"accept": "application/json", "origin": SITE, "referer": SITE + "/"}
        if session_key:
            headers["cookie"] = f"{COOKIE_NAME}={session_key}"
        try:
            resp = self._request(API + path, method="POST" if body is not None else "GET", params=params,
                                 headers=headers, json_body=body)
        except net.HttpError as e:
            raise LumaError(e.status, _error_message(e.body)) from None
        except net.NetError as e:
            raise LumaError(None, str(e)) from None
        try:
            data = resp.json()
        except ValueError:
            raise LumaError(resp.status, "the response was not JSON") from None
        return data if isinstance(data, dict) else {"entries": data}

    def paginated(self, path: str, params: dict, *, session_key: str | None = None, page_size: int = 50,
                  max_pages: int = 20) -> list[dict]:
        entries: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            page = dict(params, pagination_limit=page_size)
            if cursor:
                page["pagination_cursor"] = cursor
            data = self.call(path, page, session_key=session_key)
            if not isinstance(data.get("entries"), list):
                # Treat a changed response shape as an error; an empty pull would wipe the feed's events.
                raise LumaError(200, f"unexpected response from {path} (no entries list)")
            entries.extend(e for e in data["entries"] if isinstance(e, dict))
            if not data.get("has_more") or not data.get("next_cursor"):
                break
            cursor = data["next_cursor"]
        return entries

    def calendar_events(self, calendar_id: str, session_key: str | None = None) -> list[dict]:
        entries = self.paginated("/calendar/get-items", {"calendar_api_id": calendar_id, "period": "future"},
                                 session_key=session_key)
        return [ev for ev in (normalize_entry(e) for e in entries) if ev]

    def following(self, session_key: str) -> list[dict]:
        found: list[dict] = []
        cursor = None
        for _ in range(20):
            params: dict = {"pagination_limit": 100}
            if cursor:
                params["pagination_cursor"] = cursor
            data = self.call("/home/get-following-calendars", params, session_key=session_key)
            find_calendar_objects(data, found)
            if not data.get("has_more") or not data.get("next_cursor"):
                break
            cursor = data["next_cursor"]
        calendars: dict[str, dict] = {}
        for item in found:
            cal = normalize_calendar(item)
            if cal:
                calendars.setdefault(cal["id"], cal)
        return list(calendars.values())

    def my_events(self, session_key: str) -> list[dict]:
        entries = self.paginated("/home/get-events", {"period": "future"}, session_key=session_key)
        return [ev for ev in (normalize_entry(e, going_status="registered") for e in entries) if ev]

    def check_session(self, session_key: str) -> None:
        self.call("/home/get-events", {"period": "future", "pagination_limit": 1}, session_key=session_key)

    def resolve_calendar(self, token: str) -> dict:
        """Turn a slug or cal- id from a pasted link into a calendar dict, or raise ValueError."""
        first = token.split("/")[0].lower()
        if first in NOT_CALENDAR_PATHS:
            raise ValueError("not a calendar link")
        if token.startswith("cal-"):
            entries = self.paginated("/calendar/get-items", {"calendar_api_id": token, "period": "future"},
                                     page_size=1, max_pages=1)
            for entry in entries:
                cal = normalize_calendar(entry.get("calendar"))
                if cal and cal["id"] == token:
                    return cal
            return {"id": token, "source": "luma", "name": token, "slug": None, "avatar_url": None,
                    "tint_color": None, "url": f"{SITE}/{token}", "description": None}
        slug = token.strip("/")
        try:
            resp = self._request(f"{SITE}/{urllib.parse.quote(slug)}", headers={"accept": "text/html"})
        except net.HttpError as e:
            raise ValueError("Luma has no page there" if e.status == 404 else f"Luma answered {e.status}") from None
        except net.NetError as e:
            raise ValueError(str(e)) from None
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text(), re.S)
        if not match:
            raise ValueError("no page data")
        found: list[dict] = []
        try:
            find_calendar_objects(json.loads(match.group(1)).get("props", {}).get("pageProps", {}), found)
        except ValueError:
            raise ValueError("unreadable page data") from None
        cals = [c for c in (normalize_calendar(f) for f in found) if c]
        if not cals:
            raise ValueError("that link is not a Luma calendar")
        # Prefer the calendar whose slug matches the link; else the first one on the page.
        return next((c for c in cals if c["slug"] == slug.split("/")[-1]), cals[0])

    def personal_feed(self, url: str) -> list[dict]:
        try:
            resp = self._request(url, headers={"accept": "text/calendar,*/*"})
        except net.NetError as e:
            raise ValueError(f"could not read the feed: {e}") from None
        return parse_personal_feed(resp.text())


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:300] or "no details"
    if isinstance(data, dict):
        return str(data.get("message") or data.get("error") or body)[:300]
    return body[:300]

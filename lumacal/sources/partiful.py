"""Partiful, through the Firebase callables partiful.com itself uses (undocumented; may change).

The Partiful bookmarklet reads the Firebase refresh token from partiful.com's IndexedDB in the
user's browser. With it we mint short-lived ID tokens and call:

- ``getMyFollowedEvents``: events from people and orgs the account follows
- ``getMyUpcomingEventsForHomePage``: the account's own upcoming events and RSVPs

Accounts without a login can instead paste the personal iCal link (invites and RSVPs only).
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
from collections.abc import Callable

from .. import ics, net
from ..timeutil import canonical
from . import clean_text, empty_location

API = "https://api.partiful.com"
FIREBASE_KEY = "AIzaSyCky6PJ7cHRdBKk5X7gjuWERWaKWBHr4_k"  # public web key from partiful.com's bundle
TOKEN_URL = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_KEY}"
GOING_STATUSES = {"going", "yes", "approved", "host", "hosting"}
DEAD_LOGIN_MARKERS = ("INVALID_REFRESH_TOKEN", "TOKEN_EXPIRED", "USER_DISABLED", "USER_NOT_FOUND")

FOLLOWING_CALENDAR = {"id": "partiful-following", "source": "partiful", "name": "Partiful · people I follow",
                      "slug": None, "avatar_url": None, "tint_color": "#ff9f43", "url": "https://partiful.com/explore",
                      "description": None}
MINE_CALENDAR = {"id": "partiful", "source": "partiful", "name": "Partiful · my events", "slug": None,
                 "avatar_url": None, "tint_color": "#ff5c8a", "url": "https://partiful.com/events", "description": None}


class PartifulError(Exception):
    def __init__(self, message: str, *, login_expired: bool = False):
        super().__init__(message)
        self.login_expired = login_expired


def _first(d: dict, *keys: str) -> object:
    for key in keys:
        value = d.get(key)
        if value not in (None, ""):
            return value
    return None


def jwt_claims(token: str | None) -> dict:
    try:
        part = (token or "").split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def find_events(obj: object, out: list[dict]) -> None:
    """Walk a callable's result and pull out anything shaped like an event."""
    if isinstance(obj, dict):
        title = _first(obj, "title", "name")
        start = _first(obj, "startDate", "startTime", "start_date", "start", "date")
        if title and start and _first(obj, "id", "eventId", "slug"):
            out.append(obj)
            return
        for value in obj.values():
            find_events(value, out)
    elif isinstance(obj, list):
        for value in obj:
            find_events(value, out)


def image_url(value: object) -> str | None:
    """Image fields are a URL or an object with upload.path / poster.name / url inside."""
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None
    # Uploads live in a private Firebase bucket (403 to browsers); partiful.com serves them via imgix by path.
    upload = value.get("upload") if isinstance(value.get("upload"), dict) else None
    if upload and upload.get("path"):
        return "https://partiful.imgix.net/" + urllib.parse.quote(upload["path"]) + "?w=800&h=800&fit=clip"
    poster = value.get("poster") if isinstance(value.get("poster"), dict) else None
    if poster and poster.get("name"):
        return "https://partiful-posters.imgix.net/" + urllib.parse.quote(poster["name"]) + "?fit=max&w=800&h=800"
    url = value.get("url")
    if isinstance(url, str) and url and "firebasestorage" not in url:
        return url
    for key in ("poster", "image"):
        found = image_url(value.get(key))
        if found:
            return found
    return None


def normalize_event(raw: dict) -> dict | None:
    event_id = _first(raw, "id", "eventId", "slug")
    start = canonical(_first(raw, "startDate", "startTime", "start_date", "start", "date"))
    if not event_id or not start:
        return None
    place = _first(raw, "location", "address", "venue", "locationName")
    if isinstance(place, dict):
        place = _first(place, "name", "address", "formattedAddress", "city", "description")
    place = clean_text(place) or None
    loc = empty_location("offline" if place else "unknown")
    loc["address"] = place
    hosts = []
    raw_hosts = raw.get("hosts") or raw.get("hostNames") or []
    if isinstance(raw_hosts, list):
        for host in raw_hosts:
            name = clean_text(_first(host, "name", "displayName", "fullName") if isinstance(host, dict) else host)
            if name:
                avatar = _first(host, "avatarUrl", "profileImageUrl", "photoUrl", "imageUrl", "photoURL") if isinstance(host, dict) else None
                hosts.append({"name": name, "avatar_url": avatar if isinstance(avatar, str) else None})
    if not hosts and clean_text(raw.get("hostName")):
        hosts = [{"name": clean_text(raw["hostName"]), "avatar_url": None}]
    guest = raw.get("guest") if isinstance(raw.get("guest"), dict) else {}
    status = str(_first(guest, "status") or _first(raw, "rsvpStatus", "myStatus", "guestStatus") or "").lower()
    counts = raw.get("guestStatusCounts") if isinstance(raw.get("guestStatusCounts"), dict) else {}
    going_count = counts.get("GOING") if isinstance(counts.get("GOING"), int) else None
    return {
        "id": f"pf-{event_id}", "source": "partiful", "name": clean_text(_first(raw, "title", "name")) or "Untitled event",
        "url": f"https://partiful.com/e/{urllib.parse.quote(str(event_id))}",
        "start_at": start, "end_at": canonical(_first(raw, "endDate", "endTime", "end_date", "end")), "all_day": False,
        "timezone": _first(raw, "timezone", "timeZone") if isinstance(_first(raw, "timezone", "timeZone"), str) else None,
        "cover_url": image_url(_first(raw, "imageUrl", "image", "coverImageUrl", "posterUrl", "poster")),
        "location": loc, "presenter": None, "hosts": hosts, "tags": [], "ticket": None,
        "guest_count": going_count if going_count else None,
        "going_status": status if status in GOING_STATUSES else None,
    }


def parse_feed(text: str) -> list[dict]:
    """Events from the personal iCal link (invites and RSVPs)."""
    if not ics.looks_like_calendar(text):
        raise ValueError("that URL did not return an iCalendar feed")
    out = []
    for vevent in ics.parse(text):
        start, end, all_day = ics.event_times(vevent)
        if not start:
            continue
        desc = ics.unescape(vevent.get("DESCRIPTION", ""))
        url = vevent.get("URL") or next((w for w in desc.split() if "partiful.com/e/" in w), None)
        match = re.search(r"partiful\.com/e/([A-Za-z0-9_-]+)", url or "")
        # Use the event id from the link so feed and API copies of an event merge.
        event_id = f"pf-{match.group(1)}" if match else "pf-" + re.sub(r"[^A-Za-z0-9_.-]", "", vevent.get("UID") or start)[:80]
        status = (vevent.get("STATUS") or "").lower()
        place = clean_text(ics.unescape(vevent.get("LOCATION", ""))) or None
        loc = empty_location("offline" if place else "unknown")
        loc["address"] = place
        out.append({
            "id": event_id, "source": "partiful",
            "name": re.sub(r"\s*\|\s*Partiful\s*$", "", clean_text(ics.unescape(vevent.get("SUMMARY", "")))) or "Untitled event",
            "url": f"https://partiful.com/e/{match.group(1)}" if match else (url or "https://partiful.com/events"),
            "start_at": start, "end_at": end, "all_day": all_day, "timezone": vevent.get("DTSTART__params", {}).get("TZID"),
            "cover_url": None, "location": loc, "presenter": None, "hosts": [], "tags": [], "ticket": None,
            "guest_count": None, "going_status": None if status == "tentative" else "going",
        })
    return out


def normalize_feed_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.startswith("https://calendars.partiful.com/"):
        raise ValueError("expected a calendars.partiful.com link")
    return url


def parse_import_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("the import payload is not JSON")
    token = payload.get("refresh_token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("no Partiful login in that payload")
    uid = payload.get("uid") if isinstance(payload.get("uid"), str) else None
    return {"uid": uid, "refresh_token": token.strip()}


class PartifulClient:
    def __init__(self, request: Callable[..., net.Response] = net.request, clock: Callable[[], float] = time.time):
        self._request = request
        self._clock = clock

    def id_token(self, account: dict) -> dict:
        """Return the account with a valid ID token, refreshing it when it is about to expire."""
        if account.get("id_token") and (account.get("expires_at") or 0) > self._clock() + 60:
            return account
        try:
            resp = self._request(TOKEN_URL, method="POST", json_body={"grant_type": "refresh_token",
                                 "refresh_token": account["refresh_token"]},
                                 headers={"referer": "https://partiful.com/"})
        except net.HttpError as e:
            dead = any(marker in e.body for marker in DEAD_LOGIN_MARKERS)
            raise PartifulError("the Partiful login expired; run the Partiful bookmarklet again" if dead
                                else f"token refresh failed ({e.status})", login_expired=dead) from None
        except net.NetError as e:
            raise PartifulError(str(e)) from None
        token = resp.json()
        if not isinstance(token, dict) or not token.get("id_token"):
            raise PartifulError("token refresh returned no ID token")
        claims = jwt_claims(token["id_token"])
        return dict(account, id_token=token["id_token"], refresh_token=token.get("refresh_token") or account["refresh_token"],
                    uid=token.get("user_id") or account.get("uid") or claims.get("user_id"),
                    name=claims.get("name") or account.get("name"),
                    expires_at=self._clock() + int(token.get("expires_in") or 3600))

    def call(self, account: dict, name: str, params: dict | None = None) -> object:
        body = {"data": {"params": params or {}, "userId": account.get("uid")}}
        try:
            resp = self._request(f"{API}/{name}", method="POST", json_body=body, headers={
                "authorization": "Bearer " + account["id_token"], "origin": "https://partiful.com",
                "referer": "https://partiful.com/"})
        except net.HttpError as e:
            raise PartifulError(f"{name} answered {e.status}", login_expired=e.status == 401) from None
        except net.NetError as e:
            raise PartifulError(str(e)) from None
        data = resp.json()
        return data.get("result") if isinstance(data, dict) else None

    def events(self, account: dict, name: str) -> list[dict]:
        found: list[dict] = []
        find_events(self.call(account, name), found)
        events: dict[str, dict] = {}
        for raw in found:
            ev = normalize_event(raw)
            if not ev:
                continue
            if ev["id"] in events:
                events[ev["id"]]["going_status"] = events[ev["id"]]["going_status"] or ev["going_status"]
            else:
                events[ev["id"]] = ev
        return list(events.values())

    def feed(self, url: str) -> list[dict]:
        try:
            resp = self._request(url, headers={"accept": "text/calendar,*/*"})
        except net.NetError as e:
            raise ValueError(f"could not read the feed: {e}") from None
        return parse_feed(resp.text())

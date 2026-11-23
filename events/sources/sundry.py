"""Sundry Mag's weekly "What to Do In Cape Town This Week" (sundrymag.substack.com).

Each Monday's post is one list per day. A heading carries the date, and every event under it is a
list item whose title links to the organiser's own post or ticket page, with three nested lines:

    ### Monday, November 23, 2026
    - [Open Stage: Open Mic Night](https://www.instagram.com/p/…/)
        - ⏰ Time: 7:00PM
        - 📍 Location: [Kaapstad Toneelhuis](https://…)
        - 💸 Cost: [From R80](https://www.quicket.co.za/events/…)

Substack serves posts as JSON under /api/v1. Recent posts are for subscribers (free): without the
reader's ``substack.sid`` cookie the API returns only the opening of the post.
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

from .. import net
from ..timeutil import parse_iso, to_iso
from . import clean_text, empty_location

SITE = "https://sundrymag.substack.com"
CALENDAR = {"id": "sundry", "source": "sundry", "name": "Sundry Mag · Cape Town this week", "slug": None,
            "avatar_url": None, "tint_color": "#e4572e", "url": SITE,
            "description": "A weekly list of things to do in Cape Town"}
TIMEZONE = "Africa/Johannesburg"
SAST = timezone(timedelta(hours=2))  # South Africa keeps UTC+2 all year
POSTS_PER_PULL = 2  # this week's post, and last week's until the next one is out
WEEKLY_TITLE_RE = re.compile(r"\b(?:what|things) to do in cape town this week\b", re.I)
MONTHS = {name: number for number, names in enumerate(
    ("january jan", "february feb", "march mar", "april apr", "may", "june jun", "july jul", "august aug",
     "september sept sep", "october oct", "november nov", "december dec"), start=1) for name in names.split()}
DAY_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                    r"(?:,?\s*(\d{4}))?", re.I)
FIELD_RE = re.compile(r"\W*\b(time|location|cost)\s*:\s*(.*)", re.I | re.S)
CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?"
RANGE_RE = re.compile(CLOCK + r"\s*(?:-|–|—|to|til|till|until)\s*" + CLOCK, re.I)
CLOCK_RE = re.compile(CLOCK, re.I)
PRICE_RE = re.compile(r"\bR\s?(\d[\d ,]*(?:\.\d{1,2})?)")
PLAIN_COST_RE = re.compile(r"(?:free(?: entry| event)?|r\s?\d[\d ,.]*)", re.I)
ONLINE_RE = re.compile(r"\b(online|zoom|virtual|livestream)\b", re.I)
MAP_HOSTS = ("share.google", "maps.app.goo.gl", "goo.gl", "g.co", "maps.google.com", "google.com", "www.google.com")
# Query values that only track where a link was copied from.
TRACKING = re.compile(r"utm_|igsh|jplx|avpt|fbclid|gclid|ref$|hl$|img_index$", re.I)


class SundryError(Exception):
    pass


@dataclass
class Pull:
    events: list[dict]
    # Titles of subscriber posts that came back cut short.
    locked: list[str] = field(default_factory=list)


def parse_session(text: str) -> str:
    """Accept the cookie's value alone, ``substack.sid=…`` or a whole Cookie header."""
    text = text.strip().strip("\"'")
    match = re.search(r"substack\.sid=([^;\s]+)", text)
    value = match.group(1) if match else text
    if not re.fullmatch(r"[A-Za-z0-9%._~+/=:-]{20,2000}", value):
        raise ValueError("that does not look like a substack.sid cookie")
    return value


def clean_url(url: str | None) -> str | None:
    """Drop tracking values so the same page always has the same address."""
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)]
    if parts.hostname.lower().removeprefix("www.") == "instagram.com":
        # A shared Instagram link has a random tracking value under a random name; the path alone is the post.
        return urllib.parse.urlunsplit(("https", "www.instagram.com", parts.path.rstrip("/") + "/", "", ""))
    # Quicket's "#/" is a client-side route, not a place on the page.
    fragment = "" if parts.fragment in ("/", "") else parts.fragment
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), fragment))


class _Blocks(HTMLParser):
    """Flatten a post to headings and list items, each with its text, links and list depth."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self._depth = 0
        self._current: dict | None = None
        self._link: list[str] | None = None

    def _open(self, kind: str) -> None:
        self._current = {"kind": kind, "depth": self._depth, "text": "", "links": []}
        self.blocks.append(self._current)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("ul", "ol"):
            self._depth += 1
            self._current = None
        elif tag == "li":
            self._open("item")
        elif re.fullmatch(r"h[1-6]", tag):
            self._open("heading")
        elif tag == "a" and self._current is not None:
            self._link = [dict(attrs).get("href") or "", ""]
            self._current["links"].append(self._link)
        elif tag == "br" and self._current is not None:
            self._current["text"] += " "

    def handle_endtag(self, tag: str) -> None:
        if tag in ("ul", "ol"):
            self._depth = max(0, self._depth - 1)
            self._current = None
        elif tag == "a":
            self._link = None
        elif re.fullmatch(r"h[1-6]", tag):
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data
            if self._link is not None:
                self._link[1] += data


def _heading_date(text: str, near: date) -> date | None:
    """"Monday, November 23, 2026", "Sunday, August 30th" (the year comes from the post's date)."""
    match = DAY_RE.search(text)
    if not match:
        return None
    month, day = MONTHS[match.group(1).lower()], int(match.group(2))
    years = [int(match.group(3))] if match.group(3) else [near.year, near.year + 1, near.year - 1]
    found = []
    for year in years:
        try:
            found.append(date(year, month, day))
        except ValueError:
            continue
    return min(found, key=lambda d: abs((d - near).days)) if found else None


def _minutes(hour: str, minute: str | None, meridiem: str | None) -> int | None:
    h, m = int(hour), int(minute or 0)
    if m > 59 or h > 23 or (meridiem and not 1 <= h <= 12):
        return None
    if meridiem:
        h = h % 12 + (12 if meridiem[0].lower() == "p" else 0)
    return h * 60 + m


def parse_time(text: str) -> tuple[int | None, int | None, str]:
    """(start, end, leftover): minutes after local midnight, and whatever the clock times did not say.

    "6:15-8:15PM" lends the end's PM to the start; "11:00-1:00PM" starts in the morning, because the
    other reading would end before it starts. An end at or before the start is the next day.
    """
    text = clean_text(text)
    match = RANGE_RE.search(text)
    if match and (match.group(3) or match.group(6) or (match.group(2) and match.group(5))):
        h1, m1, mer1, h2, m2, mer2 = match.groups()
        if mer1 and mer2:
            start, end = _minutes(h1, m1, mer1), _minutes(h2, m2, mer2)
        elif mer2 or mer1:
            # The half without AM/PM borrows it, unless that would turn the range backwards.
            given = mer2 or mer1
            other = "am" if given[0].lower() == "p" else "pm"
            if mer2:
                end = _minutes(h2, m2, mer2)
                start = _minutes(h1, m1, given)
                if start is not None and end is not None and start > end:
                    start = _minutes(h1, m1, other)
            else:
                start = _minutes(h1, m1, mer1)
                end = _minutes(h2, m2, given)
                if start is not None and end is not None and end <= start:
                    end = _minutes(h2, m2, other)
        else:
            start, end = _bare(h1, m1), _minutes(h2, m2, None)
            if start is not None and end is not None and end < start and end < 12 * 60:
                end += 12 * 60  # "6:30-8:30" is one evening
        if start is not None and end is not None:
            rest = clean_text(text[:match.start()] + " " + text[match.end():])
            return start, end, rest
    clocks = [m for m in CLOCK_RE.finditer(text) if m.group(2) or m.group(3)]
    if clocks:
        first = clocks[0]
        meridiem = first.group(3) or next((m.group(3) for m in clocks[1:] if m.group(3)), None)
        start = _minutes(first.group(1), first.group(2), meridiem) if meridiem else _bare(first.group(1), first.group(2))
        if start is not None:
            return start, None, clean_text(text[:first.start()] + " " + text[first.end():])
    return None, None, text


def _bare(hour: str, minute: str | None) -> int | None:
    """A time with no AM/PM: 13 and up is a 24-hour clock, 1 to 7 is taken as the evening."""
    value = _minutes(hour, minute, None)
    if value is None:
        return None
    return value + 12 * 60 if 60 <= value < 8 * 60 else value


def parse_cost(text: str) -> dict | None:
    text = clean_text(text)
    if not text or text.lower() in ("n/a", "varies", "various"):
        return None
    free = text.lower().startswith("free")
    price = PRICE_RE.search(text)
    cents = None
    if price and not free:
        try:
            cents = round(float(price.group(1).replace(" ", "").replace(",", "")) * 100)
        except ValueError:
            cents = None
    return {"free": free, "price_cents": cents, "max_price_cents": None, "currency": "zar", "sold_out": False,
            "spots_left": None, "approval": bool(re.search(r"\b(approval|application)\b", text, re.I)),
            "waitlist": False, "availability": None}


def _link_label(url: str, fallback: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return "Map" if host in MAP_HOSTS or host.startswith("maps.") else fallback


def _event(title: dict, fields: dict[str, dict], day: date) -> dict | None:
    name = clean_text(title["text"])
    if not name:
        return None
    url = next((u for u in (clean_url(href) for href, _ in title["links"]) if u), None)
    time_text = clean_text(fields.get("time", {}).get("text"))
    place_text = clean_text(fields.get("location", {}).get("text"))
    cost_text = clean_text(fields.get("cost", {}).get("text"))
    start_min, end_min, time_rest = parse_time(time_text)
    midnight = datetime(day.year, day.month, day.day, tzinfo=SAST)
    all_day = start_min is None
    if all_day:
        start, end = midnight, midnight + timedelta(days=1)
    else:
        start = midnight + timedelta(minutes=start_min)
        end = None
        if end_min is not None:
            end = midnight + timedelta(minutes=end_min)
            if end <= start:
                end += timedelta(days=1)
    location = empty_location("online" if ONLINE_RE.search(place_text) else "offline")
    location.update({"venue": place_text or None, "city": "Cape Town", "region": "Western Cape", "country": "ZA"})
    links = []
    for key, label in (("cost", "Tickets"), ("location", "Venue")):
        href = next((u for u in (clean_url(h) for h, _ in fields.get(key, {}).get("links", [])) if u), None)
        if href and href != url:
            links.append({"label": _link_label(href, label), "url": href})
    # Say in words whatever the structured fields could not hold ("also Sunday", "R80 at the door").
    notes = []
    if time_text and (all_day or re.search(r"[A-Za-z0-9]", time_rest)):
        notes.append(f"Time: {time_text}")
    if cost_text and not PLAIN_COST_RE.fullmatch(cost_text):
        notes.append(f"Cost: {cost_text}")
    identity = f"{url or name.lower()}|{day.isoformat()}"
    return {
        "id": "sundry-" + hashlib.sha1(identity.encode()).hexdigest()[:16], "source": "sundry", "name": name,
        "url": url or SITE, "start_at": to_iso(start), "end_at": to_iso(end) if end else None, "all_day": all_day,
        "timezone": TIMEZONE, "cover_url": None, "location": location, "presenter": None, "hosts": [], "tags": [],
        "ticket": parse_cost(cost_text), "guest_count": None, "going_status": None,
        "description": "\n".join(notes) or None, "links": links,
    }


def parse_post(body_html: str, published: date) -> list[dict]:
    """Every event in one weekly post. Items before the first dated heading are skipped."""
    parser = _Blocks()
    parser.feed(body_html or "")
    events: dict[str, dict] = {}
    day: date | None = None
    title: dict | None = None
    fields: dict[str, dict] = {}

    def flush() -> None:
        if title is not None and day is not None:
            ev = _event(title, fields, day)
            if ev:
                events.setdefault(ev["id"], ev)

    for block in parser.blocks:
        if block["kind"] == "heading":
            found = _heading_date(clean_text(block["text"]), published)
            if found:
                flush()
                day, title, fields = found, None, {}
        elif block["depth"] == 1:
            flush()
            title, fields = block, {}
        elif block["depth"] == 2 and title is not None:
            match = FIELD_RE.match(block["text"])
            if match:
                fields[match.group(1).lower()] = {"text": match.group(2), "links": block["links"]}
    flush()
    return sorted(events.values(), key=lambda e: (e["start_at"], e["name"]))


def is_cut_short(post: dict) -> bool:
    """Substack reports the full word count even when it sends only the opening."""
    if post.get("audience") in (None, "everyone"):
        return False
    words = len(re.sub(r"<[^>]+>", " ", str(post.get("body_html") or "")).split())
    total = post.get("wordcount")
    return isinstance(total, (int, float)) and total > 0 and words < 0.6 * total


class SundryClient:
    def __init__(self, request: Callable[..., net.Response] = net.request):
        self._request = request

    def _get(self, path: str, session: str | None, **params: object) -> object:
        headers = {"accept": "application/json"}
        if session:
            headers["cookie"] = f"substack.sid={session}"
        try:
            return self._request(SITE + path, params=params or None, headers=headers).json()
        except net.HttpError as e:
            raise SundryError(f"Substack answered {e.status} for {path}") from None
        except ValueError:
            raise SundryError(f"Substack answered {path} with something that is not JSON") from None

    def weekly_posts(self, session: str | None = None) -> list[dict]:
        """The newest weekly posts, as archive entries (slug, title, post_date, audience)."""
        data = self._get("/api/v1/archive", session, sort="new", limit=12)
        posts = data.get("posts") if isinstance(data, dict) else data
        if not isinstance(posts, list):
            raise SundryError("Substack's archive is not a list of posts")
        weekly = [p for p in posts if isinstance(p, dict) and p.get("slug") and WEEKLY_TITLE_RE.search(str(p.get("title") or ""))]
        return weekly[:POSTS_PER_PULL]

    def events(self, session: str | None = None) -> Pull:
        posts = self.weekly_posts(session)
        if not posts:
            raise SundryError("no weekly Cape Town post in Sundry Mag's archive")
        pull = Pull([])
        seen: set[str] = set()
        for entry in posts:
            post = self._get("/api/v1/posts/" + urllib.parse.quote(str(entry["slug"])), session)
            if not isinstance(post, dict):
                raise SundryError("Substack's post is not an object")
            published = parse_iso(post.get("post_date")) or parse_iso(entry.get("post_date"))
            if published is None:
                raise SundryError("a Sundry Mag post has no date")
            for ev in parse_post(str(post.get("body_html") or ""), published.astimezone(SAST).date()):
                if ev["id"] not in seen:
                    seen.add(ev["id"])
                    pull.events.append(ev)
            if is_cut_short(post):
                pull.locked.append(clean_text(post.get("title")) or str(entry["slug"]))
        if not pull.events:
            raise SundryError("found no events in Sundry Mag's weekly posts; the format may have changed")
        return pull

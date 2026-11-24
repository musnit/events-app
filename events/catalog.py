"""Turn stored listings into the event list the web app shows.

One event can be listed by several feeds (a Luma calendar, the user's registrations, a personal
iCal feed) and even by several upstreams (AGI House also posts on Luma). Here we merge those,
apply RSVPs, marks and mutes, and add derived fields: area/zone, vibes/topics, crowd size.
The result is cached per data version, so polling clients cost almost nothing.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import areas, categorize
from .sources.luma import LINKED_CALENDAR
from .store import Store
from .timeutil import parse_iso, to_iso

HISTORY_DAYS = 45  # enough for the month view to show earlier weeks of the current month
# Which listing supplies an event's details when several feeds list it.
FEED_RICHNESS = {"calendar": 6, "luma-event": 6, "luma-mine": 5, "partiful-mine": 4, "partiful-following": 4, "agihouse": 3,
                 "partiful-feed": 2, "sundry": 2, "instagram": 2, "luma-ics": 1}
SOURCE_RANK = {"luma": 0, "partiful": 1, "agihouse": 2, "sundry": 3, "instagram": 4}
# A weekly list and the organiser's account both point at the same post; a day apart they are still one event.
SAME_LINK_WINDOW_S = 20 * 3600
# The same organiser, a day apart at most, under titles that share most words or where one is the other with
# more said ("Dinner with Strangers" and "Dinner with Strangers (Final of the Year)").
SAME_ORGANISER_WINDOW_S = 24 * 3600
SAME_ORGANISER_SIMILARITY = 0.4
DUPLICATE_WINDOW_S = 3 * 3600
DUPLICATE_SIMILARITY = 0.6
# Sources that read what organisers post about themselves, where one event is often told twice in different words.
RETOLD_SOURCES = {"sundry", "instagram"}
# Words that say nothing about which venue is meant.
VENUE_FILLER = {"the", "and", "at", "of", "in", "on", "cape", "town"}


@dataclass(frozen=True)
class Snapshot:
    version: str
    body: bytes
    gzipped: bytes
    etag: str


def title_words(name: str | None) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split())


def _similar(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= DUPLICATE_SIMILARITY


def _contained(a: set[str], b: set[str]) -> bool:
    """One title is the other with more said: "Studio Sessions" and "Studio Sessions: Holiday Still Life"."""
    return min(len(a), len(b)) >= 2 and (a <= b or b <= a)


def _same_under_one_organiser(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    return _contained(a, b) or len(a & b) / len(a | b) >= SAME_ORGANISER_SIMILARITY


def _organiser(ev: dict) -> str | None:
    return (ev.get("presenter") or {}).get("id")


def _venue_words(ev: dict) -> set[str]:
    return title_words((ev.get("location") or {}).get("venue")) - VENUE_FILLER


def _same_title(ev: dict, words: set[str], other: dict, other_words: set[str]) -> bool:
    """Whether two events starting close together are one event told twice.

    Two upstreams rarely carry different events under one name, so a like title is enough. A weekly list and the
    Instagram accounts retell one event in their own words, and two accounts can also mean different events by one
    name (an open mic at two bars on one night; a guide listing a gig the venue announced too). Among those, a
    title that is the other with more said also counts, and two accounts fold at all, only while the two copies do
    not name venues with no word in common."""
    retold = {ev["source"], other["source"]} <= RETOLD_SOURCES
    if other["source"] != ev["source"]:
        if _similar(words, other_words):
            return True
        return retold and _contained(words, other_words) and not _venues_differ(ev, other)
    if ev["source"] != "instagram" or not _organiser(ev) or not _organiser(other) or _organiser(ev) == _organiser(other):
        return False
    return (_similar(words, other_words) or _contained(words, other_words)) and not _venues_differ(ev, other)


def _venues_differ(a: dict, b: dict) -> bool:
    here, there = _venue_words(a), _venue_words(b)
    return bool(here and there) and not here & there


def _epoch(iso: str | None) -> float | None:
    parsed = parse_iso(iso)
    return parsed.timestamp() if parsed else None


class Catalog:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self.store = store
        self.clock = clock
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None

    # ---------- building ----------

    def build(self) -> dict:
        now = self.clock()
        calendars = self.store.calendars()
        cal_names = {c["id"]: c["name"] for c in calendars}
        prefs = self.store.prefs()
        muted = {c for c in prefs.get("muted_calendars") or [] if isinstance(c, str)}
        going = self.store.going()
        marks = self.store.marks()

        groups: dict[str, list[dict]] = {}
        for listing in self.store.listings(to_iso(now - HISTORY_DAYS * 86400)):
            groups.setdefault(listing["event_id"], []).append(listing)

        events = []
        for event_id, items in groups.items():
            primary = max(items, key=lambda i: (FEED_RICHNESS.get(i["feed_kind"], 0), bool(i["data"].get("cover_url"))))
            if categorize.is_placeholder(primary["data"]):
                continue
            ev = dict(primary["data"])
            ev["calendar_ids"] = sorted({i["calendar_id"] for i in items})
            statuses = [i["data"]["going_status"] for i in items if i["data"].get("going_status")]
            if event_id in going:
                statuses.append(going[event_id])
            ev["going"] = bool(statuses)
            ev["going_status"] = statuses[0] if statuses else None
            first_seen = min((i["first_seen_at"] for i in items if i["first_seen_at"]), default=None)
            announced = min((i["announced_at"] for i in items if i["announced_at"]), default=None)
            ev["first_seen_at"] = to_iso(first_seen) if first_seen else None
            ev["announced_at"] = to_iso(announced) if announced else None
            ev["also_on"] = []
            ev["merged_ids"] = []
            events.append(ev)

        events = self._merge_duplicates(events)

        for ev in events:
            ev["area"], ev["zone"] = areas.classify(ev)
            ev["vibes"], ev["topics"] = categorize.classify(ev, [cal_names.get(c, "") for c in ev["calendar_ids"]])
            ev["size"] = categorize.size_of(ev.get("guest_count"))
            # A star or hide set on a copy that was later folded into this event still counts.
            own = [marks.get(i, {}) for i in (ev["id"], *ev.pop("merged_ids"))]
            ev["starred"] = any(m.get("starred") for m in own)
            ev["hidden"] = any(m.get("hidden") for m in own)
            ev["muted"] = bool(ev["calendar_ids"]) and all(c in muted for c in ev["calendar_ids"])
            # You asked for an event added by link, so the area filter leaves it in.
            ev["linked"] = LINKED_CALENDAR["id"] in ev["calendar_ids"]
        events.sort(key=lambda e: (e["start_at"], e["name"]))

        upcoming_cutoff = to_iso(now)
        counts: dict[str, int] = {}
        for ev in events:
            if (ev.get("end_at") or ev["start_at"]) >= upcoming_cutoff:
                for cid in ev["calendar_ids"]:
                    counts[cid] = counts.get(cid, 0) + 1
        feeds = {f.calendar_id: f for f in self.store.feeds() if f.kind in ("calendar", "instagram") and f.calendar_id}
        cal_out = []
        for cal in calendars:
            feed = feeds.get(cal["id"])
            cal_out.append({
                "id": cal["id"], "source": cal["source"], "name": cal["name"], "avatar_url": cal["avatar_url"],
                "url": cal["url"], "tint_color": cal["tint_color"], "description": cal["description"],
                "origins": cal["origins"], "muted": cal["id"] in muted, "upcoming_count": counts.get(cal["id"], 0),
                "last_ok_at": to_iso(feed.last_ok_at) if feed and feed.last_ok_at else None,
                "last_error": feed.last_error if feed else None,
            })
        return {"generated_at": to_iso(now), "events": events, "calendars": cal_out, "taxonomy": categorize.taxonomy(),
                "zones": [{"id": k, "label": v} for k, v in areas.ZONES.items()]}

    @staticmethod
    def _merge_duplicates(events: list[dict]) -> list[dict]:
        """Fold copies of one event posted on two upstreams, or by two Instagram accounts, into the richer copy."""
        # A post that announces one event is the organiser's own; one that lists dozens is a guide. The organiser's
        # copy is the one to keep, so among Instagram's events those of the smaller post come first.
        per_post: dict[str, int] = {}
        for ev in events:
            if ev.get("source") == "instagram" and ev.get("url"):
                per_post[ev["url"]] = per_post.get(ev["url"], 0) + 1
        ordered = sorted(events, key=lambda e: (SOURCE_RANK.get(e.get("source"), 9),
                                                per_post.get(e.get("url") or "", 0) if e.get("source") == "instagram" else 0,
                                                e["start_at"]))
        kept: list[dict] = []
        by_hour: dict[int, list[tuple[dict, set[str]]]] = {}
        by_link: dict[str, list[dict]] = {}
        by_organiser: dict[str, list[tuple[dict, set[str]]]] = {}
        for ev in ordered:
            organiser = _organiser(ev)
            start = _epoch(ev["start_at"])
            words = title_words(ev.get("name"))
            match = None
            if start is not None:
                for other in by_link.get(ev.get("url") or "", []):
                    other_start = _epoch(other["start_at"])
                    if (other["source"] != ev["source"] and other_start is not None
                            and abs(other_start - start) <= SAME_LINK_WINDOW_S):
                        match = other
                        break
            if match is None and start is not None and organiser:
                for other, other_words in by_organiser.get(organiser, []):
                    other_start = _epoch(other["start_at"])
                    if (other["source"] != ev["source"] and other_start is not None
                            and abs(other_start - start) <= SAME_ORGANISER_WINDOW_S and _same_under_one_organiser(words, other_words)):
                        match = other
                        break
            if match is None and start is not None and words:
                bucket = int(start // 3600)
                for hour in range(bucket - 3, bucket + 4):
                    for other, other_words in by_hour.get(hour, []):
                        other_start = _epoch(other["start_at"])
                        if (other_start is not None and abs(other_start - start) <= DUPLICATE_WINDOW_S
                                and _same_title(ev, words, other, other_words)):
                            match = other
                            break
                    if match:
                        break
            if match:
                match["merged_ids"].append(ev["id"])
                match["calendar_ids"] = sorted(set(match["calendar_ids"]) | set(ev["calendar_ids"]))
                match["also_on"].append({"source": ev["source"], "url": ev["url"]})
                if ev["going"] and not match["going"]:
                    match["going"], match["going_status"] = True, ev["going_status"]
                continue
            kept.append(ev)
            if start is not None:
                by_hour.setdefault(int(start // 3600), []).append((ev, words))
                if ev.get("url"):
                    by_link.setdefault(ev["url"], []).append(ev)
                if organiser:
                    by_organiser.setdefault(organiser, []).append((ev, words))
        return kept

    # ---------- cached, serialized snapshot ----------

    def snapshot(self) -> Snapshot:
        version = f"{self.store.data_version()}.{categorize.RULES_VERSION}.{int(self.clock() // 3600)}"
        with self._lock:
            if self._snapshot and self._snapshot.version == version:
                return self._snapshot
            payload = self.build()
            payload["version"] = version
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8", "replace")
            etag = '"' + hashlib.sha256(body).hexdigest()[:24] + '"'
            self._snapshot = Snapshot(version, body, gzip.compress(body, compresslevel=6), etag)
            return self._snapshot

    def events_for_feed(self, *, starred_or_going: bool) -> list[dict]:
        events = self.build()["events"]
        now = to_iso(self.clock() - 86400)
        chosen = [e for e in events if (e.get("end_at") or e["start_at"]) >= now and not e["hidden"] and not e["muted"]]
        if starred_or_going:
            chosen = [e for e in chosen if e["going"] or e["starred"]]
        return chosen

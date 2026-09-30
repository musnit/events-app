"""One-time import of the JSON files the first version kept next to its server.

Runs only when ``EVENTS_LEGACY_DIR`` names the directory holding them. After a successful import
the files move to ``<state dir>/legacy/`` so there is one source of truth; the old version can be
restored from there if ever needed.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from .sources import empty_location, scrub
from .sources.luma import ONLINE_TYPES
from .sources import partiful as partiful_src
from .store import Store
from .sync import Sync
from .timeutil import canonical, parse_iso

log = logging.getLogger(__name__)

FILES = {
    "session": ".session.json", "calendars": ".luma_calendars.json", "ics": ".luma_ics.json",
    "going": ".luma_going.json", "partiful_feed": ".partiful.json", "partiful_auth": ".partiful_auth.json",
    "cache": "cache.json", "partiful_debug": "partiful_debug.json",
}


def _load(path: Path) -> object:
    """Read an old JSON file, scrubbed of text SQLite and UTF-8 cannot store."""
    try:
        return scrub(json.loads(path.read_text()))
    except (OSError, ValueError):
        return None


def _event(old: dict) -> dict | None:
    """Old cache events (flat) to listing dicts."""
    start = canonical(old.get("start_at"))
    if not old.get("api_id") or not start:
        return None
    kind = str(old.get("location_type") or "").lower()
    loc = empty_location("online" if kind in ONLINE_TYPES else "offline" if kind == "offline" else "unknown")
    loc.update({"address": old.get("address"), "city": old.get("city"), "region": old.get("region"),
                "country": old.get("country"), "lat": old.get("lat"), "lng": old.get("lng")})
    if old.get("source") != "luma" and old.get("city") and not old.get("address"):
        loc.update({"address": old.get("city"), "city": None})  # Partiful and iCal kept the whole address in city
    pb = old.get("presented_by") if isinstance(old.get("presented_by"), dict) else None
    avatars = old.get("host_avatars") or []
    return {
        "id": old["api_id"], "source": old.get("source") or "luma", "name": old.get("name") or "Untitled event",
        "url": old.get("url"), "start_at": start, "end_at": canonical(old.get("end_at")), "all_day": bool(old.get("all_day")),
        "timezone": old.get("timezone"), "cover_url": old.get("cover_url"), "location": loc,
        "presenter": {"id": pb.get("api_id"), "name": pb.get("name"), "avatar_url": pb.get("avatar_url"), "url": None,
                      "description": None} if pb and pb.get("name") else None,
        "hosts": [{"name": n, "avatar_url": (avatars[i] if i < len(avatars) else None) or None}
                  for i, n in enumerate(old.get("hosts") or []) if n],
        "tags": [], "ticket": None, "guest_count": None,
        "going_status": (old.get("guest_status") or "going") if old.get("going") else None,
    }


def migrate(store: Store, sync: Sync, source_dir: Path, state_dir: Path) -> bool:
    """Import legacy files once. Returns True when anything was imported."""
    if store.get_meta("legacy_migrated_at"):
        return False
    present = {key: source_dir / name for key, name in FILES.items() if (source_dir / name).is_file()}
    if not present:
        store.set_meta("legacy_migrated_at", str(store.clock()))
        return False
    log.info("importing legacy files: %s", ", ".join(sorted(p.name for p in present.values())))

    session = _load(present["session"]) if "session" in present else None
    if isinstance(session, dict) and session.get("session_key"):
        store.set_secret("luma_session", {"session_key": session["session_key"], "via": "legacy"})
    ics_cfg = _load(present["ics"]) if "ics" in present else None
    if isinstance(ics_cfg, dict) and ics_cfg.get("url"):
        store.set_secret("luma_ics", {"url": ics_cfg["url"]})
    pf_feed = _load(present["partiful_feed"]) if "partiful_feed" in present else None
    if isinstance(pf_feed, dict) and pf_feed.get("url"):
        store.set_secret("partiful_feed", {"url": pf_feed["url"]})
    pf_auth = _load(present["partiful_auth"]) if "partiful_auth" in present else None
    accounts = pf_auth.get("accounts") if isinstance(pf_auth, dict) and "accounts" in pf_auth else [pf_auth] if pf_auth else []
    for account in accounts:
        if isinstance(account, dict) and account.get("refresh_token"):
            uid = account.get("uid") or partiful_src.jwt_claims(account.get("id_token")).get("user_id")
            if uid:
                store.save_partiful_account({"uid": uid, "name": account.get("name"), "refresh_token": account["refresh_token"],
                                             "id_token": account.get("id_token"), "expires_at": account.get("expires_at") or 0})
    calendars = _load(present["calendars"]) if "calendars" in present else None
    cals = []
    for c in calendars if isinstance(calendars, list) else []:
        if isinstance(c, dict) and str(c.get("api_id", "")).startswith("cal-"):
            cals.append({"id": c["api_id"], "source": "luma", "name": c.get("name") or c["api_id"], "slug": c.get("slug"),
                         "avatar_url": c.get("avatar_url"), "tint_color": c.get("tint_color"),
                         "url": c.get("url") or f"https://luma.com/{c.get('slug') or c['api_id']}", "description": None})
    if cals:
        store.set_origin_calendars("import", cals)
    going = _load(present["going"]) if "going" in present else None
    if isinstance(going, list):
        store.replace_going("luma-import", {g: "registered" for g in going if isinstance(g, str) and g.startswith("evt-")})

    sync.reconcile()
    cache = _load(present["cache"]) if "cache" in present else None
    if isinstance(cache, dict):
        _import_cache(store, cache)

    legacy_dir = state_dir / "legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(legacy_dir, 0o700)
    for path in present.values():
        shutil.move(str(path), legacy_dir / path.name)
    store.set_meta("legacy_migrated_at", str(store.clock()))
    log.info("legacy import done; originals moved to %s", legacy_dir)
    return True


def _import_cache(store: Store, cache: dict) -> None:
    """Seed Luma calendar listings from the old cache so the app is full right away; a full Luma pass
    takes half an hour. Partiful and AGI House re-sync within seconds, so their first pull is a clean
    baseline instead (otherwise it would flag old events as newly announced)."""
    fetched = parse_iso(cache.get("fetched_at"))
    by_feed: dict[str, list[dict]] = {}
    feeds = {f.key for f in store.feeds()}
    for old in cache.get("events") or []:
        if not isinstance(old, dict) or old.get("source") not in (None, "luma"):
            continue
        ev = _event(old)
        key = f"luma:{old.get('calendar_api_id')}"
        if ev and key in feeds:
            by_feed.setdefault(key, []).append(ev)
    real_clock = store.clock
    if fetched:
        store.clock = lambda: fetched.timestamp()  # record the pulls at the time they really happened
    try:
        # The old app pulled every calendar, including those with nothing upcoming; record those pulls too.
        for key in sorted(k for k in feeds if k.startswith("luma:cal-")):
            store.replace_listings(key, by_feed.get(key, []))
    finally:
        store.clock = real_clock
    log.info("imported %s cached Luma events across %s calendars", sum(len(v) for v in by_feed.values()), len(by_feed))

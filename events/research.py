"""Validated, persistent editorial research, imported separately from upstream sync.

Usage: python -m events.research import FILE --state-dir DIR
The file may be one collection, a list, or {"research_collections": [...]}.
Only explicitly scheduled entries become ordinary calendar events. Notes and uncertain
leads remain available in their collection without inventing a start or end time.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .sources import empty_location
from .timeutil import parse_iso, to_iso

KINDS = {"talk", "side-event", "music-art", "local-scene", "place"}
PRIORITIES = {"essential", "recommended", "alternative", "watchlist", "skip", "reference"}
CALENDAR_PRIORITIES = {"essential", "recommended", "alternative"}
SCHEDULE_STATUSES = {"confirmed", "approximate", "unverified", "unscheduled", "stale"}
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,119}\Z")
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _object(value: object, path: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    if set(value) - allowed:
        raise ValueError(f"{path} contains unsupported fields: {', '.join(sorted(set(value) - allowed))}")
    return value


def _text(value: object, path: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 100_000:
        raise ValueError(f"{path} must be text (at most 100000 characters)")
    if any(ord(ch) < 32 and ch not in "\n\r\t" or 0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise ValueError(f"{path} contains invalid text")
    if required and not value.strip():
        raise ValueError(f"{path} cannot be empty")
    return value.strip()


def _id(value: object, path: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"{path} must be a lowercase slug of 1–120 characters")
    return value


def _url(value: object, path: str) -> str:
    value = _text(value, path)
    if not value:
        return value
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username
                 and not parts.password and not any(ch.isspace() for ch in value)
                 and "\\" not in value)
        parts.port  # validate a supplied port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{path} must be an http(s) URL without credentials")
    return value


def _timezone(value: object, path: str) -> str:
    value = _text(value, path, required=True)
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError(f"{path} must be an IANA timezone") from None
    return value


def _timestamp(value: object, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.match(r"\d{4}-\d{2}-\d{2}T", value):
        raise ValueError(f"{path} must be an ISO datetime with a timezone, or null")
    parsed = parse_iso(value)
    if not parsed:
        raise ValueError(f"{path} must be an ISO datetime with a timezone, or null")
    return to_iso(parsed)


def _date(value: object, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{path} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{path} must be an ISO date") from None
    return value


def _list(value: object, path: str) -> list:
    if not isinstance(value, list) or len(value) > 5000:
        raise ValueError(f"{path} must be a list with at most 5000 items")
    return value


def _records(value: object, path: str, fields: tuple[str, ...]) -> list[dict]:
    result = []
    for i, item in enumerate(_list(value, path)):
        where = f"{path}[{i}]"
        item = _object(item, where, set(fields))
        row = {key: _text(item.get(key, ""), f"{where}.{key}") for key in fields}
        if "url" in row:
            row["url"] = _url(row["url"], f"{where}.url")
            if not row["url"]:
                raise ValueError(f"{where}.url cannot be empty")
        if "date" in row:
            row["date"] = _date(row["date"], f"{where}.date")
        result.append(row)
    return result


def validate_collections(payload: object) -> list[dict]:
    """Return a canonical allowlisted copy; reject the entire import on any invalid row."""
    if isinstance(payload, dict):
        if "research_collections" in payload:
            _object(payload, "import", {"research_collections"})
            payload = payload["research_collections"]
        else:
            payload = [payload]
    collections = _list(payload, "collections")
    seen_collections: set[str] = set()
    seen_entries: set[str] = set()
    result = []
    for i, raw in enumerate(collections):
        where = f"collections[{i}]"
        raw = _object(raw, where, {"id", "title", "city", "timezone", "checked_at", "intro", "notes",
                                   "itinerary", "entries"})
        collection = {key: _text(raw.get(key, ""), f"{where}.{key}", required=key != "intro")
                      for key in ("title", "city", "intro")}
        collection["id"] = _id(raw.get("id"), f"{where}.id")
        if collection["id"] in seen_collections:
            raise ValueError(f"duplicate collection id: {collection['id']}")
        seen_collections.add(collection["id"])
        collection["timezone"] = _timezone(raw.get("timezone"), f"{where}.timezone")
        checked = raw.get("checked_at")
        collection["checked_at"] = (_date(checked, f"{where}.checked_at") if isinstance(checked, str)
                                     and len(checked) == 10 else _timestamp(checked, f"{where}.checked_at"))
        if not collection["checked_at"]:
            raise ValueError(f"{where}.checked_at is required")
        collection["notes"] = _records(raw.get("notes", []), f"{where}.notes", ("title", "body"))
        collection["itinerary"] = _records(raw.get("itinerary", []), f"{where}.itinerary", ("date", "body"))
        entries = []
        for j, entry in enumerate(_list(raw.get("entries"), f"{where}.entries")):
            path = f"{where}.entries[{j}]"
            text_fields = ("title", "verdict", "summary", "date_label", "venue", "address", "city", "country", "access")
            entry = _object(entry, path, set(text_fields) | {"id", "kind", "priority", "schedule_status", "timezone",
                              "start_at", "end_at", "local_date", "url", "people", "sections", "sources", "tags", "conflicts"})
            normalized = {key: _text(entry.get(key, ""), f"{path}.{key}", required=key == "title")
                          for key in text_fields}
            normalized["id"] = _id(entry.get("id"), f"{path}.id")
            if normalized["id"] in seen_entries:
                raise ValueError(f"duplicate research entry id: {normalized['id']}")
            seen_entries.add(normalized["id"])
            for key, options in (("kind", KINDS), ("priority", PRIORITIES), ("schedule_status", SCHEDULE_STATUSES)):
                if not isinstance(entry.get(key), str) or entry[key] not in options:
                    raise ValueError(f"{path}.{key} must be one of {', '.join(sorted(options))}")
                normalized[key] = entry[key]
            normalized["timezone"] = _timezone(entry.get("timezone", collection["timezone"]), f"{path}.timezone")
            normalized["city"] = normalized["city"] or collection["city"]
            for key in ("start_at", "end_at"):
                normalized[key] = _timestamp(entry.get(key), f"{path}.{key}")
            normalized["local_date"] = (_date(entry["local_date"], f"{path}.local_date")
                                        if entry.get("local_date") is not None else None)
            start, end = normalized["start_at"], normalized["end_at"]
            if end and (not start or end <= start):
                raise ValueError(f"{path}.end_at must follow start_at")
            if normalized["schedule_status"] in {"confirmed", "approximate"} and not start:
                raise ValueError(f"{path}.start_at is required for scheduled entries")
            if normalized["schedule_status"] == "unscheduled" and (start or end):
                raise ValueError(f"{path}: unscheduled entries must have null dates")
            normalized["url"] = _url(entry.get("url", ""), f"{path}.url")
            for key, fields in (("people", ("name", "role", "assessment")), ("sections", ("title", "body")),
                                ("sources", ("label", "url"))):
                normalized[key] = _records(entry.get(key, []), f"{path}.{key}", fields)
            for key in ("tags", "conflicts"):
                normalized[key] = [_text(value, f"{path}.{key}", required=True)
                                   for value in _list(entry.get(key, []), f"{path}.{key}")]
            entries.append(normalized)
        collection["entries"] = entries
        result.append(collection)
    return result


def canonical_url(value: str | None) -> str:
    """Normalize exact identities, preserving meaningful query values and fragments."""
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if not host or parts.scheme not in {"http", "https"}:
            return ""
        if host in {"lu.ma", "www.lu.ma", "www.luma.com"}:
            host = "luma.com"
        port = parts.port
        netloc = host if port in (None, 80, 443) else f"{host}:{port}"
        query = sorted((key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                       if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"})
        return urlunsplit(("https", netloc, parts.path.rstrip("/") or "/", urlencode(query), parts.fragment))
    except ValueError:
        return ""


def event_from_entry(entry: dict, collection: dict) -> dict | None:
    if (not entry["start_at"] or entry["schedule_status"] not in {"confirmed", "approximate"}
            or entry["priority"] not in CALENDAR_PRIORITIES):
        return None
    return {
        "id": entry["id"], "source": "research", "name": entry["title"], "url": entry["url"],
        "start_at": entry["start_at"], "end_at": entry["end_at"], "timezone": entry["timezone"],
        "all_day": False, "cover_url": None,
        "location": empty_location("offline") | {key: entry[key] for key in ("venue", "address", "city", "country")},
        "presenter": None, "hosts": [], "tags": entry["tags"], "ticket": None, "guest_count": None,
        "going": False, "going_status": None, "first_seen_at": None, "announced_at": None,
        "calendar_ids": ["research:" + collection["id"]], "also_on": [], "merged_ids": [],
        "research": entry, "collection_ids": [collection["id"]],
    }


def can_export(entry: dict) -> bool:
    """Calendar exports require a confirmed interval, not a placeholder or editorial rejection."""
    return bool(entry["schedule_status"] == "confirmed" and entry["priority"] in CALENDAR_PRIORITIES
                and entry.get("start_at") and entry.get("end_at"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="validate and atomically upsert research collections")
    importer.add_argument("file", type=Path)
    importer.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.file.stat().st_size > MAX_IMPORT_BYTES:
            raise ValueError("research import exceeds 10 MiB")
        collections = validate_collections(json.loads(args.file.read_text(encoding="utf-8")))
        # Validate before even opening the database: rejected files have no state side effects.
        from .db import Database
        from .store import Store
        db = Database(args.state_dir / "events.db")
        try:
            db.migrate()
            result = Store(db).import_research(collections)
        finally:
            db.close()
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Research import failed: {exc}\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Just enough iCalendar (RFC 5545) to read Luma/Partiful personal feeds and write our own export."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .timeutil import parse_iso, to_iso

DEFAULT_TZ = "America/Los_Angeles"


def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def escape(value: str | None) -> str:
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def parse_datetime(value: str, params: dict[str, str]) -> tuple[str | None, bool]:
    """Return (canonical UTC time, is_all_day) for a DTSTART/DTEND value."""
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or len(value) == 8:
            day = datetime.strptime(value, "%Y%m%d").replace(tzinfo=_zone(params.get("TZID")))
            return to_iso(day), True
        if value.endswith("Z"):
            return to_iso(datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)), False
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
        # A floating time without TZID is read as UTC, which is what Luma and Partiful mean by it.
        tz = _zone(params["TZID"]) if params.get("TZID") else timezone.utc
        return to_iso(naive.replace(tzinfo=tz)), False
    except ValueError:
        return None, False


def parse(text: str) -> list[dict]:
    """Minimal VEVENT reader. Each event maps property names to values; parameters live under NAME__params."""
    events: list[dict] = []
    current: dict | None = None
    depth = 0
    for line in unfold(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            current, depth = {}, 0
            continue
        if current is None:
            continue
        if upper.startswith("BEGIN:"):
            depth += 1  # skip nested components such as VALARM
            continue
        if upper.startswith("END:"):
            if upper == "END:VEVENT" and depth == 0:
                events.append(current)
                current = None
            else:
                depth = max(0, depth - 1)
            continue
        if depth or ":" not in line:
            continue
        head, _, value = line.partition(":")
        name, *raw_params = head.split(";")
        params = {}
        for item in raw_params:
            if "=" in item:
                key, _, val = item.partition("=")
                params[key.upper()] = val.strip('"')
        current[name.upper()] = value
        current[name.upper() + "__params"] = params
    return events


def event_times(vevent: dict) -> tuple[str | None, str | None, bool]:
    start, all_day = parse_datetime(vevent.get("DTSTART", ""), vevent.get("DTSTART__params", {}))
    end = None
    if vevent.get("DTEND"):
        end, _ = parse_datetime(vevent["DTEND"], vevent.get("DTEND__params", {}))
    return start, end, all_day


def looks_like_calendar(text: str) -> bool:
    return "BEGIN:VCALENDAR" in text[:2000].upper()


# ---------- writing ----------

def _fold(line: str) -> list[str]:
    """Fold at 75 octets without splitting a UTF-8 character."""
    out, current, size = [], "", 0
    for ch in line:
        width = len(ch.encode())
        limit = 75 if not out else 74
        if size + width > limit:
            out.append(current)
            current, size = ch, width
        else:
            current += ch
            size += width
    out.append(current)
    return [out[0]] + [" " + part for part in out[1:]]


def _stamp(iso: str) -> str:
    parsed = parse_iso(iso)
    assert parsed is not None, iso
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build(events: list[dict], *, name: str, now_iso: str) -> str:
    """Write events (catalog shape) as a VCALENDAR."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//luma-cal//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{escape(name)}"]
    for ev in events:
        if not ev.get("start_at"):
            continue
        loc = ev.get("location") or {}
        where = ", ".join(x for x in (loc.get("venue"), loc.get("address") or loc.get("city")) if x)
        if not where and loc.get("type") == "online":
            where = "Online"
        presenter = (ev.get("presenter") or {}).get("name")
        desc = "\n".join(x for x in (presenter, ", ".join(h["name"] for h in ev.get("hosts") or []), ev.get("url")) if x)
        body = ["BEGIN:VEVENT", f"UID:{ev['id']}@luma-cal", f"DTSTAMP:{_stamp(now_iso)}"]
        if ev.get("all_day"):
            start_day = _local_date(ev["start_at"], ev.get("timezone"))
            end_day = _local_date(ev["end_at"], ev.get("timezone")) if ev.get("end_at") else start_day
            if end_day <= start_day:
                end_day = start_day + timedelta(days=1)
            body += [f"DTSTART;VALUE=DATE:{start_day:%Y%m%d}", f"DTEND;VALUE=DATE:{end_day:%Y%m%d}"]
        else:
            body += [f"DTSTART:{_stamp(ev['start_at'])}", f"DTEND:{_stamp(ev.get('end_at') or ev['start_at'])}"]
        body += [f"SUMMARY:{escape(ev.get('name'))}", f"URL:{ev.get('url') or ''}"]
        if where:
            body.append(f"LOCATION:{escape(where)}")
        if desc:
            body.append(f"DESCRIPTION:{escape(desc)}")
        body.append("END:VEVENT")
        lines += body
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"


def _local_date(iso: str, tz: str | None) -> date:
    parsed = parse_iso(iso)
    assert parsed is not None, iso
    return parsed.astimezone(_zone(tz)).date()

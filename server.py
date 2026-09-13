#!/usr/bin/env python3
"""Luma follow-calendar: one calendar view of every event across the Luma calendars you follow.

Luma's public API only covers calendars you manage, so this server signs in the way the
luma.com frontend does (email code) and then reads the same internal endpoints the site uses.
It binds to 127.0.0.1 and expects nginx to strip a /luma/ prefix and handle auth (Authelia).
"""
import json
import re
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http import cookies as http_cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
SESSION_FILE = os.path.join(ROOT, ".session.json")
CACHE_FILE = os.path.join(ROOT, "cache.json")
PARTIFUL_FILE = os.path.join(ROOT, ".partiful.json")
LUMA_CALS_FILE = os.path.join(ROOT, ".luma_calendars.json")
LUMA_ICS_FILE = os.path.join(ROOT, ".luma_ics.json")
LUMA_GOING_FILE = os.path.join(ROOT, ".luma_going.json")
PARTIFUL_AUTH_FILE = os.path.join(ROOT, ".partiful_auth.json")
PARTIFUL_DEBUG_FILE = os.path.join(ROOT, "partiful_debug.json")
PARTIFUL_API = "https://api.partiful.com"
PARTIFUL_FIREBASE_KEY = "AIzaSyCky6PJ7cHRdBKk5X7gjuWERWaKWBHr4_k"  # public web key from partiful.com's bundle
PARTIFUL_FOLLOW_CAL = {"api_id": "partiful-following", "name": "Partiful · people I follow", "slug": None, "avatar_url": None,
                       "tint_color": "#ff9f43", "url": "https://partiful.com/explore", "source": "partiful"}
PARTIFUL_CAL = {"api_id": "partiful", "name": "Partiful", "slug": None, "avatar_url": None,
                "tint_color": "#ff5c8a", "url": "https://partiful.com/events", "source": "partiful"}
API = "https://api.luma.com"
PORT = int(os.environ.get("PORT", "8771"))
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", str(30 * 60)))
COOKIE_NAME = "luma.auth-session-key"
UA = "Mozilla/5.0 (X11; Linux x86_64) luma-cal/1.0"

lock = threading.Lock()
state = {"refreshing": False, "last_error": None}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


# ---------- session ----------

def load_session():
    try:
        with open(SESSION_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_session(data):
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SESSION_FILE)


def clear_session():
    try:
        os.remove(SESSION_FILE)
    except OSError:
        pass


# ---------- luma http ----------

class LumaError(Exception):
    def __init__(self, status, body):
        super().__init__(f"luma {status}: {body}")
        self.status = status
        self.body = body


def luma(path, params=None, body=None, session_key=None):
    """Call an api.luma.com endpoint. Returns (json, set_cookie_session_key_or_None)."""
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    headers = {"accept": "application/json", "user-agent": UA, "origin": "https://luma.com",
               "referer": "https://luma.com/"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    if session_key:
        headers["cookie"] = f"{COOKIE_NAME}={session_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            new_key = None
            for h in r.headers.get_all("set-cookie") or []:
                c = http_cookies.SimpleCookie()
                try:
                    c.load(h)
                except http_cookies.CookieError:
                    continue
                if COOKIE_NAME in c:
                    new_key = c[COOKIE_NAME].value
            return (json.loads(raw) if raw else {}), new_key
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            msg = json.loads(raw).get("message", raw)
        except ValueError:
            msg = raw
        raise LumaError(e.code, msg)


def luma_paginated(path, params, session_key, max_pages=20):
    out, cursor = [], None
    for _ in range(max_pages):
        p = dict(params, pagination_limit=50)
        if cursor:
            p["pagination_cursor"] = cursor
        res, _ = luma(path, p, session_key=session_key)
        out.extend(res.get("entries") or [])
        if not res.get("has_more") or not res.get("next_cursor"):
            break
        cursor = res["next_cursor"]
    return out


# ---------- small json stores ----------

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------- luma calendars added by link ----------

LUMA_LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:lu\.ma|luma\.com)/([A-Za-z0-9_./-]+)|\b(cal-[A-Za-z0-9]{10,20})\b")


def find_cal_objects(obj, out):
    """Collect every dict that looks like a Luma calendar (api_id cal-…) from nested JSON."""
    if isinstance(obj, dict):
        if str(obj.get("api_id", "")).startswith("cal-") and "name" in obj:
            out.append(obj)
            return
        for v in obj.values():
            find_cal_objects(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_cal_objects(v, out)


def resolve_luma_link(token):
    """Turn a luma.com slug/path or cal- id into a normalized calendar dict, or raise ValueError."""
    if token.startswith("cal-"):
        entries = luma_paginated("/calendar/get-items", {"calendar_api_id": token, "period": "future"}, None, max_pages=1)
        for en in entries:
            if isinstance(en.get("calendar"), dict):
                return normalize_calendar(en["calendar"])
        return {"api_id": token, "name": token, "slug": None, "avatar_url": None, "tint_color": None, "url": "https://luma.com/" + token}
    slug = token.strip("/").split("?")[0]
    req = urllib.request.Request("https://luma.com/" + slug, headers={"user-agent": UA, "accept": "text/html"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", errors="replace")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("no page data")
    cals = []
    find_cal_objects(json.loads(m.group(1)).get("props", {}).get("pageProps", {}), cals)
    if not cals:
        raise ValueError("that link is not a Luma calendar")
    # Prefer the calendar whose slug matches the link; else the first one on the page.
    cal = next((c for c in cals if c.get("slug") == slug.split("/")[-1]), cals[0])
    return normalize_calendar(cal)


def add_luma_calendars_from_text(text):
    stored = load_json(LUMA_CALS_FILE, [])
    known = {c["api_id"] for c in stored}
    added, failed = [], []
    seen = set()
    for m in LUMA_LINK_RE.finditer(text):
        token = m.group(2) or m.group(1)
        if not token or token in seen:
            continue
        seen.add(token)
        if token.split("/")[0] in ("user", "discover", "signin", "home", "settings", "create", "event", "e"):
            failed.append(f"{token}: not a calendar link")
            continue
        try:
            cal = resolve_luma_link(token)
        except Exception as e:
            failed.append(f"{token}: {e}")
            continue
        if not cal or cal["api_id"] in known:
            continue
        cal["manual"] = True
        stored.append(cal)
        known.add(cal["api_id"])
        added.append(cal)
    save_json(LUMA_CALS_FILE, stored)
    return added, failed


def import_luma_calendars(payload):
    """Accept the bookmarklet payload (raw or compacted following-calendars JSON) and store every calendar in it."""
    cals = []
    find_cal_objects(payload.get("calendars", payload) if isinstance(payload, dict) else payload, cals)
    stored = load_json(LUMA_CALS_FILE, [])
    known = {c["api_id"] for c in stored}
    added = 0
    for c in cals:
        n = normalize_calendar(c)
        if n and n["api_id"] not in known:
            n["manual"] = True
            stored.append(n)
            known.add(n["api_id"])
            added += 1
    save_json(LUMA_CALS_FILE, stored)
    return added, len(stored)


def normalize_luma_ics_url(url):
    """Accept the raw ics/get link, a webcal:// form, or a Google 'add by URL' link carrying cid=."""
    url = url.strip()
    m = re.search(r"[?&]cid=([^&]+)", url)
    if m and "google.com" in url:
        url = urllib.parse.unquote(m.group(1))
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if "/ics/get" not in url or "luma" not in url and "lu.ma" not in url:
        raise ValueError("expected a Luma iCal subscription link (…/ics/get?entity=user&id=…)")
    return url


def fetch_luma_ics(url):
    """Return the set of event api_ids the user is registered for, from their personal Luma feed."""
    req = urllib.request.Request(url, headers={"user-agent": UA, "accept": "text/calendar,*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("that URL did not return an iCalendar feed")
    out = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    for ve in parse_ics(text):
        uid = ve.get("UID", "")
        evid = uid.split("@")[0] if uid.startswith("evt-") else None
        start, all_day = ics_parse_dt(ve.get("DTSTART", ""), ve.get("DTSTART__params", {}))
        if not start or start < cutoff:
            continue
        end, _ = ics_parse_dt(ve.get("DTEND", ""), ve.get("DTEND__params", {})) if ve.get("DTEND") else (None, False)
        out[evid or ("luma-ics-" + uid)] = {
            "api_id": evid or ("luma-ics-" + uid), "name": ics_unescape(ve.get("SUMMARY", "")),
            "url": ve.get("URL") or ("https://luma.com/" + evid if evid else "https://luma.com/home"),
            "start_at": start, "end_at": end, "timezone": None, "all_day": all_day, "cover_url": None,
            "location_type": "offline" if ve.get("LOCATION") else "unknown",
            "city": ics_unescape(ve.get("LOCATION", "")) or None, "hosts": [],
            "calendar_api_id": "luma-mine", "going": True, "guest_status": "registered", "source": "luma",
        }
    return out


# ---------- partiful ----------

def load_partiful():
    try:
        with open(PARTIFUL_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_partiful(url):
    tmp = PARTIFUL_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"url": url, "saved_at": datetime.now(timezone.utc).isoformat()}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PARTIFUL_FILE)


def clear_partiful():
    try:
        os.remove(PARTIFUL_FILE)
    except OSError:
        pass


def ics_unfold(text):
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def ics_unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def ics_parse_dt(value, params):
    """Return an ISO-8601 UTC string for a DTSTART/DTEND value, or None."""
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or len(value) == 8:
            d = datetime.strptime(value, "%Y%m%d")
            tz = ZoneInfo(params["TZID"]) if params.get("TZID") else ZoneInfo("America/Los_Angeles")
            return d.replace(tzinfo=tz).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), True
        if value.endswith("Z"):
            d = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        else:
            d = datetime.strptime(value, "%Y%m%dT%H%M%S")
            d = d.replace(tzinfo=ZoneInfo(params["TZID"]) if params.get("TZID") else timezone.utc)
        return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), False
    except (ValueError, KeyError):
        return None, False


def parse_ics(text):
    """Minimal VEVENT parser. Returns a list of dicts keyed by property name (params attached as <NAME>__params)."""
    events, cur = [], None
    for line in ics_unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, _, value = line.partition(":")
            name, *plist = head.split(";")
            params = dict(p.split("=", 1) for p in plist if "=" in p)
            cur[name.upper()] = value
            cur[name.upper() + "__params"] = params
    return events


def fetch_partiful(url):
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    req = urllib.request.Request(url, headers={"user-agent": UA, "accept": "text/calendar,*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("that URL did not return an iCalendar feed")
    out = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    for ve in parse_ics(text):
        start, all_day = ics_parse_dt(ve.get("DTSTART", ""), ve.get("DTSTART__params", {}))
        end, _ = ics_parse_dt(ve.get("DTEND", ""), ve.get("DTEND__params", {})) if ve.get("DTEND") else (None, False)
        if not start or start < cutoff:
            continue
        desc = ics_unescape(ve.get("DESCRIPTION", ""))
        url_ = ve.get("URL") or next((w for w in desc.split() if "partiful.com/e/" in w), None)
        status = (ve.get("STATUS") or "").lower()
        summary = re.sub(r"\s*\|\s*Partiful\s*$", "", ics_unescape(ve.get("SUMMARY", "")))
        out.append({
            "api_id": "pf-" + (ve.get("UID") or summary + start),
            "name": summary,
            "url": url_ or "https://partiful.com/events",
            "start_at": start, "end_at": end, "timezone": ve.get("DTSTART__params", {}).get("TZID"),
            "all_day": all_day, "cover_url": None,
            "location_type": "offline" if ve.get("LOCATION") else "unknown",
            "city": ics_unescape(ve.get("LOCATION", "")) or None,
            "hosts": [], "calendar_api_id": "partiful",
            "going": status != "tentative", "guest_status": status or None,
            "source": "partiful",
        })
    return out


# ---------- partiful api (firebase callables, same ones partiful.com uses) ----------

def partiful_id_token():
    """Return a valid Firebase ID token for the stored Partiful login, refreshing it when needed."""
    auth = load_json(PARTIFUL_AUTH_FILE)
    if not auth:
        return None
    if auth.get("id_token") and auth.get("expires_at", 0) > time.time() + 60:
        return auth["id_token"]
    req = urllib.request.Request(
        f"https://securetoken.googleapis.com/v1/token?key={PARTIFUL_FIREBASE_KEY}",
        data=json.dumps({"grant_type": "refresh_token", "refresh_token": auth["refresh_token"]}).encode(),
        headers={"content-type": "application/json", "referer": "https://partiful.com/", "user-agent": UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if "INVALID_REFRESH_TOKEN" in body or "TOKEN_EXPIRED" in body or "USER_DISABLED" in body:
            os.remove(PARTIFUL_AUTH_FILE)
        raise ValueError(f"Partiful login expired ({e.code})")
    auth.update({"id_token": tok["id_token"], "refresh_token": tok.get("refresh_token", auth["refresh_token"]),
                 "uid": tok.get("user_id", auth.get("uid")), "expires_at": time.time() + int(tok.get("expires_in", 3600))})
    save_json(PARTIFUL_AUTH_FILE, auth)
    return auth["id_token"]


def partiful_call(name, params=None):
    token = partiful_id_token()
    if not token:
        raise ValueError("no Partiful login")
    uid = (load_json(PARTIFUL_AUTH_FILE) or {}).get("uid")
    body = {"data": {"params": params or {}, "userId": uid}}
    req = urllib.request.Request(PARTIFUL_API + "/" + name, data=json.dumps(body).encode(), method="POST", headers={
        "content-type": "application/json", "authorization": "Bearer " + token, "origin": "https://partiful.com",
        "referer": "https://partiful.com/", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("result")
    except urllib.error.HTTPError as e:
        raise ValueError(f"{name}: {e.code} {e.read().decode(errors='replace')[:200]}")


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _iso(v):
    """Coerce Partiful date shapes (ISO string, epoch ms, Firestore {seconds}) to an ISO UTC string."""
    try:
        if isinstance(v, dict):
            v = v.get("seconds") or v.get("_seconds")
            if v is None:
                return None
            return datetime.fromtimestamp(int(v), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(v, (int, float)):
            if v > 1e12:
                v = v / 1000
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None
    return None


def find_partiful_events(obj, out):
    """Walk a callable's result and pull out anything shaped like an event."""
    if isinstance(obj, dict):
        title = _first(obj, "title", "name")
        start = _first(obj, "startDate", "startTime", "start_date", "start", "date")
        if title and start and _first(obj, "id", "eventId", "slug"):
            out.append(obj)
            return
        for v in obj.values():
            find_partiful_events(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_partiful_events(v, out)


def normalize_partiful_event(ev, cal_id, going):
    evid = str(_first(ev, "id", "eventId", "slug"))
    start = _iso(_first(ev, "startDate", "startTime", "start_date", "start", "date"))
    if not start:
        return None
    end = _iso(_first(ev, "endDate", "endTime", "end_date", "end"))
    loc = _first(ev, "location", "address", "venue", "locationName")
    if isinstance(loc, dict):
        loc = _first(loc, "name", "address", "formattedAddress", "city", "description")
    raw_hosts = ev.get("hosts") or ev.get("hostNames") or []
    hosts, host_avatars = [], []
    if isinstance(raw_hosts, list):
        for h in raw_hosts:
            name = _first(h, "name", "displayName", "fullName") if isinstance(h, dict) else str(h)
            if name:
                hosts.append(name)
                host_avatars.append((_first(h, "avatarUrl", "profileImageUrl", "photoUrl", "imageUrl", "photoURL") if isinstance(h, dict) else "") or "")
    status = str(_first(ev, "rsvpStatus", "status", "myStatus", "guestStatus") or "").lower()
    if status in ("going", "yes", "approved", "host", "hosting", "maybe"):
        going = True
    return {
        "api_id": "pf-" + evid, "name": _first(ev, "title", "name"), "url": "https://partiful.com/e/" + evid,
        "start_at": start, "end_at": end, "timezone": _first(ev, "timezone", "timeZone"), "all_day": False,
        "cover_url": _first(ev, "imageUrl", "image", "coverImageUrl", "posterUrl"),
        "location_type": "offline" if loc else "unknown", "city": loc if isinstance(loc, str) else None,
        "hosts": hosts, "host_avatars": host_avatars, "calendar_api_id": cal_id, "going": going, "guest_status": status or None, "source": "partiful",
    }


def fetch_partiful_api(errors):
    """Events from people/orgs the user follows plus their own upcoming events, via Partiful's callables."""
    events, calendars, debug = {}, [], {}
    for fn, cal, going in (("getMyFollowedEvents", PARTIFUL_FOLLOW_CAL, False), ("getMyUpcomingEventsForHomePage", PARTIFUL_CAL, True)):
        try:
            res = partiful_call(fn)
        except Exception as e:
            errors.append(f"Partiful {fn}: {e}")
            continue
        debug[fn] = res
        found = []
        find_partiful_events(res, found)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        n = 0
        for raw in found:
            ev = normalize_partiful_event(raw, cal["api_id"], going)
            if not ev or ev["start_at"] < cutoff:
                continue
            if ev["api_id"] in events:
                events[ev["api_id"]]["going"] = events[ev["api_id"]]["going"] or ev["going"]
            else:
                events[ev["api_id"]] = ev
                n += 1
        log(f"partiful {fn}: {len(found)} event-like objects, {n} upcoming")
        if all(c["api_id"] != cal["api_id"] for c in calendars):
            calendars.append(dict(cal))
    save_json(PARTIFUL_DEBUG_FILE, debug)
    return calendars, list(events.values())


# ---------- area classification ----------

BAY_BBOX = (36.85, 38.60, -123.15, -121.15)  # lat_min, lat_max, lng_min, lng_max: SF Bay Area incl. Santa Cruz/Napa edges
BAY_CITIES = {"san francisco", "oakland", "berkeley", "emeryville", "alameda", "san jose", "palo alto", "menlo park",
              "mountain view", "sunnyvale", "santa clara", "cupertino", "redwood city", "san mateo", "south san francisco",
              "daly city", "burlingame", "millbrae", "foster city", "belmont", "san carlos", "los altos", "los gatos",
              "campbell", "milpitas", "fremont", "hayward", "union city", "newark", "san leandro", "richmond", "el cerrito",
              "albany", "walnut creek", "concord", "pleasanton", "livermore", "dublin", "san rafael", "sausalito",
              "mill valley", "novato", "tiburon", "san gregorio", "half moon bay", "pacifica", "santa cruz", "napa",
              "sonoma", "petaluma", "santa rosa", "stanford", "brisbane", "sf", "east bay", "south bay", "peninsula",
              "bay area", "marin", "marin county", "san mateo county", "santa clara county", "alameda county"}
BAY_TEXT_RE = re.compile(r"\b(san francisco|s\.?f\.?|bay area|oakland|berkeley|palo alto|menlo park|mountain view|sunnyvale|"
                         r"san jose|redwood city|san mateo|emeryville|alameda|marin|sausalito|mill valley|santa clara|"
                         r"cupertino|fremont|hayward|walnut creek|half moon bay|napa|sonoma|petaluma|stanford)\b|"
                         r"\bCA\s+9[45]\d{3}\b", re.I)
ONLINE_TYPES = {"zoom", "meet", "google_meet", "online", "virtual", "teams", "youtube", "twitch", "webinar", "livestream", "link"}


def classify_area(ev):
    """Tag an event as bay / online / other / unknown so the UI can filter to the Bay Area."""
    lt = (ev.get("location_type") or "").lower()
    if lt in ONLINE_TYPES:
        return "online"
    lat, lng = ev.get("lat"), ev.get("lng")
    if lat is not None and lng is not None:
        return "bay" if BAY_BBOX[0] <= lat <= BAY_BBOX[1] and BAY_BBOX[2] <= lng <= BAY_BBOX[3] else "other"
    city = (ev.get("city") or "").strip().lower()
    if city:
        if city in BAY_CITIES or BAY_TEXT_RE.search(city):
            return "bay"
        if ev.get("region") and ev.get("country"):
            return "other"
    text = " ".join(str(x) for x in (ev.get("address"), ev.get("city"), ev.get("name")) if x)
    if BAY_TEXT_RE.search(text):
        return "bay"
    if ev.get("region") or ev.get("country") or ev.get("address"):
        return "other"
    return "unknown"


# ---------- data ----------

def normalize_calendar(item):
    """Following-calendars entries may be bare calendar objects or wrappers. Return a compact dict."""
    cal = item.get("calendar") if isinstance(item.get("calendar"), dict) else item
    api_id = cal.get("api_id") or cal.get("calendar_api_id")
    if not api_id:
        return None
    name = cal.get("name") or ""
    if cal.get("is_personal") and isinstance(cal.get("personal_user"), dict):
        name = name or cal["personal_user"].get("name") or ""
    return {
        "api_id": api_id,
        "name": name,
        "slug": cal.get("slug"),
        "avatar_url": cal.get("avatar_url"),
        "tint_color": cal.get("tint_color"),
        "url": "https://luma.com/" + (cal.get("slug") or api_id),
    }


ORG_WORDS_RE = re.compile(r"\b(club|lab|labs|house|society|community|collective|group|network|team|events|inc|co|ventures|"
                          r"capital|foundation|institute|university|studio|studios|ai|meetup|commons|hub|school|academy|"
                          r"alliance|association|council|guild|company|partners|fund|accelerator|incubator|org)\b|&", re.I)


def host_org_score(h):
    """Higher when a Luma host record looks like an organisation rather than a person."""
    name = (h.get("name") or "").strip()
    personal = " ".join(x for x in ((h.get("first_name") or "").strip(), (h.get("last_name") or "").strip()) if x)
    score = 0
    if personal and name.lower() != personal.lower():
        score += 2  # display name differs from the person's own name, e.g. "Weights & Biases"
    if ORG_WORDS_RE.search(name):
        score += 2
    if h.get("website"):
        score += 1
    if len(name.split()) >= 3 or "-" in name or "|" in name:
        score += 1
    return score


def normalize_event(entry, cal_id, going=False):
    ev = entry.get("event") or {}
    if not ev.get("api_id"):
        return None
    geo = ev.get("geo_address_info") or {}
    ordered = sorted((h for h in entry.get("hosts") or [] if h.get("name")), key=host_org_score, reverse=True)
    hosts = [h["name"] for h in ordered]
    host_avatars = [h.get("avatar_url") or "" for h in ordered]
    guest = entry.get("guest_info") or {}
    if guest.get("approval_status") in ("approved", "pending_approval", "waitlist"):
        going = True
    coord = ev.get("coordinate") or geo.get("place_coordinate") or {}
    out = {
        "api_id": ev["api_id"],
        "name": ev.get("name"),
        "url": "https://luma.com/" + (ev.get("url") or ev["api_id"]),
        "lat": coord.get("latitude"), "lng": coord.get("longitude"),
        "region": geo.get("region_short") or geo.get("region"), "country": geo.get("country_code"),
        "address": geo.get("short_address") or geo.get("full_address") or geo.get("address"),
        "start_at": ev.get("start_at"),
        "end_at": ev.get("end_at"),
        "timezone": ev.get("timezone"),
        "cover_url": ev.get("cover_url"),
        "location_type": ev.get("location_type"),
        "city": geo.get("city") or geo.get("city_state") or geo.get("full_address"),
        "hosts": hosts, "host_avatars": host_avatars,
        "calendar_api_id": cal_id or ev.get("calendar_api_id"),
        "going": going,
        "guest_status": guest.get("approval_status"),
    }
    out["area"] = classify_area(out)
    return out


def fetch_all(session_key, errors_out=None):
    """Pull followed calendars (session) plus link-added ones, every future event on each, plus registrations."""
    calendars = []
    if session_key:
        try:
            raw_cals, _ = luma("/home/get-following-calendars", session_key=session_key)
            found = []
            find_cal_objects(raw_cals, found)
            calendars = [c for c in (normalize_calendar(i) for i in found) if c]
            log(f"following {len(calendars)} calendars")
        except LumaError as e:
            if e.status == 401:
                raise
            (errors_out if errors_out is not None else []).append(f"Luma followed list: {e.body}")
    known = {c["api_id"] for c in calendars}
    for c in load_json(LUMA_CALS_FILE, []):
        if c["api_id"] not in known:
            calendars.append(dict(c))
            known.add(c["api_id"])

    events = {}
    errors = []
    for cal in calendars:
        try:
            entries = luma_paginated("/calendar/get-items", {"calendar_api_id": cal["api_id"], "period": "future"}, session_key)
        except LumaError as e:
            errors.append(f"{cal['name']}: {e}")
            continue
        for en in entries:
            ev = normalize_event(en, cal["api_id"])
            if ev:
                ev["calendar_api_id"] = cal["api_id"]
                events[ev["api_id"]] = ev
        time.sleep(0.15)

    # Events the user registered for, whether or not they come from a followed calendar.
    mine = []
    if session_key:
        try:
            mine = luma_paginated("/home/get-events", {"period": "future"}, session_key)
        except LumaError as e:
            errors.append(f"my events: {e}")
    for evid in load_json(LUMA_GOING_FILE, []):
        if evid in events:
            events[evid]["going"] = True
            events[evid]["guest_status"] = events[evid].get("guest_status") or "registered"
    ics_cfg = load_json(LUMA_ICS_FILE)
    if ics_cfg:
        try:
            mine_ics = fetch_luma_ics(ics_cfg["url"])
            for evid, ev in mine_ics.items():
                if evid in events:
                    events[evid]["going"] = True
                    events[evid]["guest_status"] = events[evid].get("guest_status") or "registered"
                else:
                    events[evid] = ev
            if mine_ics and all(c["api_id"] != "luma-mine" for c in calendars):
                calendars.append({"api_id": "luma-mine", "name": "My Luma events", "slug": None, "avatar_url": None,
                                  "tint_color": "#f0c040", "url": "https://luma.com/home"})
        except Exception as e:
            errors.append(f"Luma iCal feed: {e}")
    for en in mine:
        ev = normalize_event(en, None, going=True)
        if not ev:
            continue
        ev["going"] = True
        if ev["api_id"] in events:
            events[ev["api_id"]]["going"] = True
            events[ev["api_id"]]["guest_status"] = ev["guest_status"]
        else:
            cal = normalize_calendar(en.get("calendar") or {}) if isinstance(en.get("calendar"), dict) else None
            if cal and all(c["api_id"] != cal["api_id"] for c in calendars):
                cal["not_followed"] = True
                calendars.append(cal)
            events[ev["api_id"]] = ev

    evs = sorted(events.values(), key=lambda e: e["start_at"] or "")
    return {"fetched_at": datetime.now(timezone.utc).isoformat(), "calendars": calendars, "events": evs, "errors": errors}


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def refresh(force=False):
    sess, pf = load_session(), load_partiful()
    pf_auth = load_json(PARTIFUL_AUTH_FILE)
    if not sess and not pf and not pf_auth and not load_json(LUMA_CALS_FILE) and not load_json(LUMA_ICS_FILE):
        return
    with lock:
        if state["refreshing"]:
            return
        state["refreshing"] = True
    try:
        old = load_cache() or {}
        data = {"fetched_at": datetime.now(timezone.utc).isoformat(), "calendars": [], "events": [], "errors": []}
        if sess or load_json(LUMA_CALS_FILE) or load_json(LUMA_ICS_FILE):
            try:
                luma_data = fetch_all((sess or {}).get("session_key"), data["errors"])
                for ev in luma_data["events"]:
                    ev["source"] = "luma"
                for c in luma_data["calendars"]:
                    c["source"] = "luma"
                data["calendars"] += luma_data["calendars"]
                data["events"] += luma_data["events"]
                data["errors"] += luma_data["errors"]
            except LumaError as e:
                log("luma refresh failed:", e)
                data["errors"].append(f"Luma: {e.body}")
                if e.status == 401:
                    clear_session()
                else:  # keep the previous Luma data rather than blanking it
                    data["calendars"] += [c for c in old.get("calendars", []) if c.get("source") == "luma"]
                    data["events"] += [e2 for e2 in old.get("events", []) if e2.get("source") == "luma"]
        if pf_auth:
            pf_cals, pf_api_events = fetch_partiful_api(data["errors"])
            data["calendars"] += pf_cals
            data["events"] += pf_api_events
        if pf:
            try:
                pf_events = fetch_partiful(pf["url"])
                have = {e2["url"] for e2 in data["events"] if e2.get("source") == "partiful"}
                pf_events = [e2 for e2 in pf_events if e2["url"] not in have]
                if all(c["api_id"] != PARTIFUL_CAL["api_id"] for c in data["calendars"]):
                    data["calendars"].append(dict(PARTIFUL_CAL))
                data["events"] += pf_events
                log(f"partiful feed: {len(pf_events)} events")
            except Exception as e:
                log("partiful refresh failed:", repr(e))
                data["errors"].append(f"Partiful: {e}")
                data["calendars"] += [c for c in old.get("calendars", []) if c.get("source") == "partiful"]
                data["events"] += [e2 for e2 in old.get("events", []) if e2.get("source") == "partiful"]
        for ev in data["events"]:
            if not ev.get("area"):
                ev["area"] = classify_area(ev)
        data["events"].sort(key=lambda e: e["start_at"] or "")
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, CACHE_FILE)
        state["last_error"] = None
        log(f"refreshed: {len(data['events'])} events, {len(data['errors'])} errors")
    except Exception as e:  # network etc.
        state["last_error"] = str(e)
        log("refresh failed:", repr(e))
    finally:
        state["refreshing"] = False


def refresher():
    while True:
        refresh()
        time.sleep(REFRESH_SECONDS)


# ---------- ics ----------

def ics_escape(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_time(iso):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build_ics(data):
    cals = {c["api_id"]: c for c in data["calendars"]}
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//luma-cal//EN", "X-WR-CALNAME:Luma + Partiful"]
    for ev in data["events"]:
        if not ev.get("start_at"):
            continue
        cal = cals.get(ev["calendar_api_id"], {})
        loc = ev.get("city") or ("Online" if ev.get("location_type") == "online" else "")
        desc = " · ".join(x for x in [cal.get("name"), ", ".join(ev["hosts"]), ev["url"]] if x)
        lines += ["BEGIN:VEVENT", f"UID:{ev['api_id']}@luma", f"DTSTAMP:{ics_time(data['fetched_at'])}",
                  f"DTSTART:{ics_time(ev['start_at'])}",
                  f"DTEND:{ics_time(ev['end_at'] or ev['start_at'])}",
                  f"SUMMARY:{ics_escape(('✓ ' if ev.get('going') else '') + (ev['name'] or ''))}",
                  f"LOCATION:{ics_escape(loc)}", f"DESCRIPTION:{ics_escape(desc)}", f"URL:{ev['url']}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# ---------- http ----------

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css",
        ".json": "application/json", ".webmanifest": "application/manifest+json", ".png": "image/png", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(self.address_string(), fmt % args)

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("content-length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            sess, pf = load_session(), load_partiful()
            data = load_cache() or {}
            manual = load_json(LUMA_CALS_FILE, [])
            ics_cfg = load_json(LUMA_ICS_FILE)
            pf_auth = load_json(PARTIFUL_AUTH_FILE)
            configured = bool(sess) or bool(pf) or bool(manual) or bool(ics_cfg) or bool(pf_auth)
            return self.send_json({"signed_in": configured, "luma_signed_in": bool(sess), "luma_manual": manual,
                                   "luma_ics_url": (ics_cfg or {}).get("url"),
                                   "email": (sess or {}).get("email"), "partiful_url": (pf or {}).get("url"), "partiful_connected": bool(pf_auth),
                                   "refreshing": state["refreshing"], "last_error": state["last_error"],
                                   "fetched_at": data.get("fetched_at"), "calendars": data.get("calendars", []),
                                   "events": data.get("events", []), "errors": data.get("errors", [])})
        if path == "/feed.ics":
            data = load_cache()
            if not data:
                return self.send_json({"error": "no data yet"}, 404)
            body = build_ics(data).encode()
            self.send_response(200)
            self.send_header("content-type", "text/calendar; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        # static
        if path == "/":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not fp.startswith(WEB) or not os.path.isfile(fp):
            return self.send_json({"error": "not found"}, 404)
        with open(fp, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("content-type", MIME.get(os.path.splitext(fp)[1], "application/octet-stream"))
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.read_json()
        try:
            if path == "/api/auth/send-code":
                email = (body.get("email") or "").strip()
                if not email:
                    return self.send_json({"error": "email required"}, 400)
                luma("/auth/email/send-sign-in-code", body={"email": email})
                return self.send_json({"ok": True})
            if path == "/api/auth/verify":
                email, code = (body.get("email") or "").strip(), (body.get("code") or "").strip()
                res, key = luma("/auth/email/sign-in-with-code", body={"email": email, "code": code})
                if res.get("step") == "two_factor":
                    return self.send_json({"two_factor": True, "two_factor_shallow_secret": res.get("two_factor_shallow_secret"),
                                           "user_api_id": res.get("user_api_id")})
                if not key:
                    return self.send_json({"error": "Luma did not return a session cookie", "luma": res}, 502)
                save_session({"session_key": key, "email": email, "saved_at": datetime.now(timezone.utc).isoformat()})
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True})
            if path == "/api/auth/two-factor":
                res, key = luma("/auth/sign-in-with-two-factor", body={
                    "two_factor_otp": body.get("code"), "two_factor_shallow_secret": body.get("two_factor_shallow_secret"),
                    "user_api_id": body.get("user_api_id"), "front_auth_secret": None})
                if not key:
                    return self.send_json({"error": "Luma did not return a session cookie", "luma": res}, 502)
                save_session({"session_key": key, "email": body.get("email"), "saved_at": datetime.now(timezone.utc).isoformat()})
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True})
            if path == "/api/auth/session":
                key = (body.get("session_key") or "").strip()
                key = key.split("=", 1)[1] if key.startswith(COOKIE_NAME + "=") else key
                if not key:
                    return self.send_json({"error": "session key required"}, 400)
                try:
                    luma("/home/get-events", {"period": "future", "pagination_limit": 1}, session_key=key)
                except LumaError as e:
                    return self.send_json({"error": f"Luma rejected that key: {e.body}"}, 400)
                save_session({"session_key": key, "email": body.get("email") or "cookie", "saved_at": datetime.now(timezone.utc).isoformat()})
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True})
            if path == "/api/auth/signout":
                clear_session()
                return self.send_json({"ok": True})
            if path == "/api/luma/calendars":
                added, failed = add_luma_calendars_from_text(body.get("text") or "")
                if added:
                    threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"added": added, "failed": failed})
            if path == "/api/luma/calendars/remove":
                stored = [c for c in load_json(LUMA_CALS_FILE, []) if c["api_id"] != body.get("api_id")]
                save_json(LUMA_CALS_FILE, stored)
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True})
            if path == "/api/luma/import":
                payload = body.get("payload") or {}
                added, total = import_luma_calendars(payload)
                session_ok = False
                key = (payload.get("session_key") or "").strip() if isinstance(payload, dict) else ""
                if key:
                    try:
                        luma("/home/get-events", {"period": "future", "pagination_limit": 1}, session_key=key)
                        save_session({"session_key": key, "email": "bookmarklet", "saved_at": datetime.now(timezone.utc).isoformat()})
                        session_ok = True
                    except LumaError:
                        pass
                going = payload.get("going") if isinstance(payload, dict) else None
                if isinstance(going, list):
                    save_json(LUMA_GOING_FILE, [g for g in going if isinstance(g, str) and g.startswith("evt-")])
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"added": added, "total": total, "session": session_ok})
            if path == "/api/luma/ics":
                url = (body.get("url") or "").strip()
                if not url:
                    try:
                        os.remove(LUMA_ICS_FILE)
                    except OSError:
                        pass
                    return self.send_json({"ok": True})
                try:
                    url = normalize_luma_ics_url(url)
                    n = len(fetch_luma_ics(url))
                except Exception as e:
                    return self.send_json({"error": f"could not read that feed: {e}"}, 400)
                save_json(LUMA_ICS_FILE, {"url": url})
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True, "events": n})
            if path == "/api/partiful/import":
                payload = body.get("payload") or {}
                rt = (payload.get("refresh_token") or "").strip()
                if not rt:
                    return self.send_json({"error": "no Partiful login in that payload"}, 400)
                save_json(PARTIFUL_AUTH_FILE, {"refresh_token": rt, "uid": payload.get("uid"), "id_token": None, "expires_at": 0})
                try:
                    partiful_id_token()
                except Exception as e:
                    return self.send_json({"error": str(e)}, 400)
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True})
            if path == "/api/partiful/disconnect":
                try:
                    os.remove(PARTIFUL_AUTH_FILE)
                except OSError:
                    pass
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True})
            if path == "/api/partiful":
                url = (body.get("url") or "").strip()
                if not url:
                    clear_partiful()
                    return self.send_json({"ok": True})
                if "calendars.partiful.com" not in url:
                    return self.send_json({"error": "expected a calendars.partiful.com link"}, 400)
                try:
                    n = len(fetch_partiful(url))
                except Exception as e:
                    return self.send_json({"error": f"could not read that feed: {e}"}, 400)
                save_partiful(url)
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True, "events": n})
            if path == "/api/refresh":
                if not (load_session() or load_partiful() or load_json(LUMA_CALS_FILE) or load_json(LUMA_ICS_FILE) or load_json(PARTIFUL_AUTH_FILE)):
                    return self.send_json({"error": "not signed in"}, 401)
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True})
        except LumaError as e:
            msg = e.body
            if "additional verification" in str(msg):
                msg = "Luma wants a browser bot-check for this step. Use the session-cookie option instead."
            return self.send_json({"error": msg, "status": e.status}, 502)
        return self.send_json({"error": "not found"}, 404)


def main():
    threading.Thread(target=refresher, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log(f"listening on 127.0.0.1:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()

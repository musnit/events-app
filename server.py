#!/usr/bin/env python3
"""Luma follow-calendar: one calendar view of every event across the Luma calendars you follow.

Luma's public API only covers calendars you manage, so this server signs in the way the
luma.com frontend does (email code) and then reads the same internal endpoints the site uses.
It binds to 127.0.0.1 and expects nginx to strip a /luma/ prefix and handle auth (Authelia).
"""
import json
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
        summary = ics_unescape(ve.get("SUMMARY", ""))
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


def normalize_event(entry, cal_id, going=False):
    ev = entry.get("event") or {}
    if not ev.get("api_id"):
        return None
    geo = ev.get("geo_address_info") or {}
    hosts = [h.get("name") for h in entry.get("hosts") or [] if h.get("name")]
    guest = entry.get("guest_info") or {}
    if guest.get("approval_status") in ("approved", "pending_approval", "waitlist"):
        going = True
    return {
        "api_id": ev["api_id"],
        "name": ev.get("name"),
        "url": "https://luma.com/" + (ev.get("url") or ev["api_id"]),
        "start_at": ev.get("start_at"),
        "end_at": ev.get("end_at"),
        "timezone": ev.get("timezone"),
        "cover_url": ev.get("cover_url"),
        "location_type": ev.get("location_type"),
        "city": geo.get("city") or geo.get("city_state") or geo.get("full_address"),
        "hosts": hosts,
        "calendar_api_id": cal_id or ev.get("calendar_api_id"),
        "going": going,
        "guest_status": guest.get("approval_status"),
    }


def fetch_all(session_key):
    """Pull followed calendars plus every future event on each, plus the user's own registrations."""
    raw_cals, _ = luma("/home/get-following-calendars", session_key=session_key)
    items = raw_cals.get("entries") or raw_cals.get("calendars") or raw_cals.get("subscriptions") or []
    calendars = [c for c in (normalize_calendar(i) for i in items if isinstance(i, dict)) if c]
    log(f"following {len(calendars)} calendars")

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
    try:
        mine = luma_paginated("/home/get-events", {"period": "future"}, session_key)
    except LumaError as e:
        mine = []
        errors.append(f"my events: {e}")
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
    if not sess and not pf:
        return
    with lock:
        if state["refreshing"]:
            return
        state["refreshing"] = True
    try:
        old = load_cache() or {}
        data = {"fetched_at": datetime.now(timezone.utc).isoformat(), "calendars": [], "events": [], "errors": []}
        if sess:
            try:
                luma_data = fetch_all(sess["session_key"])
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
        if pf:
            try:
                pf_events = fetch_partiful(pf["url"])
                data["calendars"].append(dict(PARTIFUL_CAL))
                data["events"] += pf_events
                log(f"partiful: {len(pf_events)} events")
            except Exception as e:
                log("partiful refresh failed:", repr(e))
                data["errors"].append(f"Partiful: {e}")
                data["calendars"] += [c for c in old.get("calendars", []) if c.get("source") == "partiful"]
                data["events"] += [e2 for e2 in old.get("events", []) if e2.get("source") == "partiful"]
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
            return self.send_json({"signed_in": bool(sess) or bool(pf), "luma_signed_in": bool(sess),
                                   "email": (sess or {}).get("email"), "partiful_url": (pf or {}).get("url"),
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
        self.send_header("cache-control", "no-cache")
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
            if path == "/api/auth/signout":
                clear_session()
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
                if not load_session() and not load_partiful():
                    return self.send_json({"error": "not signed in"}, 401)
                threading.Thread(target=refresh, daemon=True).start()
                return self.send_json({"ok": True})
        except LumaError as e:
            return self.send_json({"error": e.body, "status": e.status}, 502)
        return self.send_json({"error": "not found"}, 404)


def main():
    threading.Thread(target=refresher, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log(f"listening on 127.0.0.1:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()

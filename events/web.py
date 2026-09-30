"""The HTTP server serves the JSON API, calendar exports and the built web app.

The portal in front of the app handles sign-in, so every request here is the owner. Mutating
requests must still be same-origin JSON, which blocks cross-site form posts and image tricks.
Unknown paths without a file extension get index.html so client-side routes can be deep-linked.
"""
from __future__ import annotations

import gzip
import json
import logging
import mimetypes
import os
import re
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, ics
from .catalog import Catalog
from .config import Settings
from .sources import luma as luma_src
from .sources import partiful as partiful_src
from .store import Store
from .sync import Sync
from .timeutil import to_iso

log = logging.getLogger(__name__)

MAX_BODY = 2 * 1024 * 1024
COMPRESSIBLE = ("application/json", "text/", "application/javascript", "image/svg+xml", "application/manifest+json")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}
CSP = ("default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "manifest-src 'self'; worker-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")
AREAS = ("bay", "bay-online", "all")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Message
    body: bytes = b""
    params: dict[str, str] = field(default_factory=dict)

    def arg(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body)
        except ValueError:
            raise ApiError(400, "the request body is not valid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "expected a JSON object")
        return data


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    gzipped: bytes | None = None  # a pre-compressed body, when one is at hand


def json_response(data: object, status: int = 200) -> Response:
    return Response(status, json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8", "replace"))


def mask(url: str | None) -> str | None:
    """Show enough of a private feed URL to recognise it, never the token."""
    if not url:
        return None
    parts = urllib.parse.urlsplit(url)
    return f"{parts.netloc}{parts.path} (saved)"


Handler = Callable[[Request], Response]


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        regex = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$"
        self._routes.append((method, re.compile(regex), handler))

    def match(self, method: str, path: str) -> tuple[Handler | None, dict[str, str], set[str]]:
        allowed: set[str] = set()
        for route_method, regex, handler in self._routes:
            m = regex.match(path)
            if not m:
                continue
            if route_method == method or (method == "HEAD" and route_method == "GET"):
                return handler, {k: urllib.parse.unquote(v) for k, v in m.groupdict().items()}, allowed
            allowed.add(route_method)
        return None, {}, allowed


class App:
    def __init__(self, settings: Settings, store: Store, sync: Sync, catalog: Catalog):
        self.settings = settings
        self.store = store
        self.sync = sync
        self.catalog = catalog
        self.web_dir = settings.web_dir
        self._static_cache: dict[str, tuple[float, bytes, bytes | None]] = {}
        self._static_lock = threading.Lock()
        self.router = Router()
        r = self.router.add
        r("GET", "/api/health", self.health)
        r("GET", "/api/events", self.events)
        r("GET", "/api/events/{event_id}/ics", self.event_ics)
        r("PUT", "/api/events/{event_id}/mark", self.mark_event)
        r("GET", "/api/status", self.status)
        r("POST", "/api/sync", self.refresh)
        r("PUT", "/api/prefs", self.update_prefs)
        r("POST", "/api/luma/import", self.luma_import)
        r("POST", "/api/luma/calendars", self.luma_add_calendars)
        r("DELETE", "/api/luma/calendars/{calendar_id}", self.luma_remove_calendar)
        r("PUT", "/api/luma/session", self.luma_set_session)
        r("DELETE", "/api/luma/session", self.luma_clear_session)
        r("PUT", "/api/luma/ics", self.luma_set_ics)
        r("DELETE", "/api/luma/ics", self.luma_clear_ics)
        r("POST", "/api/partiful/import", self.partiful_import)
        r("DELETE", "/api/partiful/accounts/{uid}", self.partiful_remove_account)
        r("PUT", "/api/partiful/feed", self.partiful_set_feed)
        r("DELETE", "/api/partiful/feed", self.partiful_clear_feed)
        r("GET", "/feed.ics", self.feed_ics)

    # ---------- dispatch ----------

    def handle(self, req: Request) -> Response:
        is_api = req.path.startswith("/api/") or req.path == "/feed.ics"
        try:
            if req.method in ("POST", "PUT", "DELETE", "PATCH"):
                self._check_same_origin(req)
            handler, params, allowed = self.router.match(req.method, req.path)
            if handler is None:
                if allowed:
                    resp = json_response({"error": "method not allowed"}, 405)
                    resp.headers["Allow"] = ", ".join(sorted(allowed))
                    return resp
                if is_api:
                    return json_response({"error": "not found"}, 404)
                if req.method not in ("GET", "HEAD"):
                    return json_response({"error": "method not allowed"}, 405)
                return self.static(req)
            req.params = params
            return handler(req)
        except ApiError as e:
            return json_response({"error": e.message}, e.status)
        except Exception:
            log.exception("%s %s failed", req.method, req.path)
            return json_response({"error": "internal error"}, 500)

    @staticmethod
    def _check_same_origin(req: Request) -> None:
        # Browsers label every request with Sec-Fetch-Site, which proxies leave alone, so it decides.
        # Without it (old browsers), compare Origin with the host we were reached at.
        site = req.headers.get("Sec-Fetch-Site")
        if site is not None:
            if site not in ("same-origin", "none"):
                raise ApiError(403, "cross-site request refused")
        else:
            origin = req.headers.get("Origin")
            if origin:  # "null" comes from sandboxed or cross-site contexts and never matches
                hosts = {h.strip() for h in (req.headers.get("X-Forwarded-Host") or "").split(",") if h.strip()}
                hosts.add((req.headers.get("Host") or "").strip())
                if urllib.parse.urlsplit(origin).netloc not in hosts:
                    raise ApiError(403, "cross-origin request refused")
        # JSON bodies force a CORS preflight, which this server never approves.
        if req.body and "application/json" not in (req.headers.get("Content-Type") or ""):
            raise ApiError(415, "send JSON with Content-Type: application/json")

    # ---------- events ----------

    def health(self, req: Request) -> Response:
        return json_response({"ok": True, "version": __version__, "data_version": self.store.data_version()})

    def events(self, req: Request) -> Response:
        snap = self.catalog.snapshot()
        headers = {"ETag": snap.etag, "Cache-Control": "no-cache, private"}
        if snap.etag in (req.headers.get("If-None-Match") or ""):
            return Response(304, b"", headers=headers)
        return Response(200, snap.body, headers=headers, gzipped=snap.gzipped)

    def _event(self, event_id: str) -> dict:
        ev = next((e for e in self.catalog.build()["events"] if e["id"] == event_id), None)
        if ev is None:
            raise ApiError(404, "no such event")
        return ev

    def event_ics(self, req: Request) -> Response:
        ev = self._event(req.params["event_id"])
        body = ics.build([ev], name=ev["name"], now_iso=to_iso(self.store.clock())).encode("utf-8", "replace")
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", ev["name"]).strip("-")[:60] or "event"
        return Response(200, body, "text/calendar; charset=utf-8",
                        {"Content-Disposition": f'attachment; filename="{safe}.ics"', "Cache-Control": "no-store"})

    def mark_event(self, req: Request) -> Response:
        if len(req.params["event_id"]) > 200:
            raise ApiError(400, "event id too long")
        data = req.json()
        starred, hidden = data.get("starred"), data.get("hidden")
        if not isinstance(starred, (bool, type(None))) or not isinstance(hidden, (bool, type(None))):
            raise ApiError(400, "starred and hidden must be true or false")
        if starred is None and hidden is None:
            raise ApiError(400, "nothing to change")
        return json_response(self.store.set_mark(req.params["event_id"], starred=starred, hidden=hidden))

    def feed_ics(self, req: Request) -> Response:
        scope = req.arg("scope", "mine")
        if scope not in ("mine", "all"):
            raise ApiError(400, "scope must be mine or all")
        events = self.catalog.events_for_feed(starred_or_going=scope == "mine")
        name = "My events · Luma + Partiful" if scope == "mine" else "Events · Luma + Partiful"
        body = ics.build(events, name=name, now_iso=to_iso(self.store.clock())).encode("utf-8", "replace")
        return Response(200, body, "text/calendar; charset=utf-8", {"Cache-Control": "no-store"})

    # ---------- status, sync, preferences ----------

    def status(self, req: Request) -> Response:
        session = self.store.get_secret("luma_session")
        ics_cfg = self.store.get_secret("luma_ics")
        feed_cfg = self.store.get_secret("partiful_feed")
        calendars = self.store.calendars()
        feeds = [f.to_dict() for f in self.store.feeds()]
        accounts = [{"uid": a["uid"], "name": a.get("name") or partiful_src.jwt_claims(a.get("id_token")).get("name"),
                     "added_at": a["added_at"]} for a in self.store.partiful_accounts()]
        luma_cals = [c for c in calendars if c["source"] == "luma" and c["id"].startswith("cal-")]
        return json_response({
            "version": __version__,
            "data_version": self.store.data_version(),
            "luma": {
                "session": bool(session), "session_via": (session or {}).get("via"),
                "session_notice": self.store.get_meta("luma_session_notice") if not session else None,
                "ics": mask((ics_cfg or {}).get("url")),
                "calendars": len(luma_cals),
                "calendars_by_origin": {o: sum(1 for c in luma_cals if o in c["origins"])
                                        for o in ("followed", "import", "link", "config")},
                "going_snapshot": sum(1 for _ in self.store.going()),
            },
            "partiful": {"accounts": accounts, "feed": mask((feed_cfg or {}).get("url"))},
            "workers": self.sync.status(),
            "feeds": feeds,
            "prefs": self.store.prefs(),
        })

    def refresh(self, req: Request) -> Response:
        source = req.json().get("source")
        if source not in (None, "luma", "partiful", "agihouse"):
            raise ApiError(400, "unknown source")
        return json_response({"queued": self.sync.refresh(source)})

    def update_prefs(self, req: Request) -> Response:
        data = req.json()
        changes: dict[str, object] = {}
        if "muted_calendars" in data:
            muted = data["muted_calendars"]
            if not isinstance(muted, list) or not all(isinstance(c, str) and len(c) < 200 for c in muted):
                raise ApiError(400, "muted_calendars must be a list of calendar ids")
            changes["muted_calendars"] = sorted(set(muted))
        if "area" in data:
            if data["area"] not in AREAS:
                raise ApiError(400, f"area must be one of {', '.join(AREAS)}")
            changes["area"] = data["area"]
        # Validate everything first so a rejected request changes nothing.
        for key, value in changes.items():
            self.store.set_pref(key, value)
        return json_response(self.store.prefs())

    # ---------- Luma ----------

    def luma_import(self, req: Request) -> Response:
        try:
            payload = luma_src.parse_import_payload(req.json().get("payload"))
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        if not payload.calendars:
            raise ApiError(400, "the import had no calendars in it; are you signed in to luma.com?")
        added, removed = self.store.set_origin_calendars("import", payload.calendars)
        self.store.replace_going("luma-import", {g: "registered" for g in payload.going})
        session_saved = False
        if payload.session_key:
            try:
                self.sync.luma.check_session(payload.session_key)
                self.store.set_secret("luma_session", {"session_key": payload.session_key, "via": "bookmarklet"})
                session_saved = True
            except luma_src.LumaError:
                pass
        self.sync.sources_changed(refresh=["luma:following", "luma:mine"] if session_saved else None)
        log.info("luma import: %s calendars (%s new, %s removed)", len(payload.calendars), len(added), len(removed))
        return json_response({"total": len(payload.calendars), "added": len(added), "removed": len(removed),
                              "going": len(payload.going), "session": session_saved})

    def luma_add_calendars(self, req: Request) -> Response:
        tokens = luma_src.calendar_tokens(str(req.json().get("text") or ""))[:50]
        if not tokens:
            raise ApiError(400, "paste one or more luma.com calendar links")
        added, failed = [], []
        for token in tokens:
            try:
                cal = self.sync.luma.resolve_calendar(token)
            except (ValueError, luma_src.LumaError) as e:
                failed.append({"link": token, "error": str(e)})
                continue
            self.store.upsert_calendar(cal, "link")
            added.append({"id": cal["id"], "name": cal["name"]})
        if added:
            self.sync.sources_changed()
        return json_response({"added": added, "failed": failed})

    def luma_remove_calendar(self, req: Request) -> Response:
        calendar_id = req.params["calendar_id"]
        cal = self.store.calendar(calendar_id)
        if cal is None or cal["source"] != "luma":
            raise ApiError(404, "no such Luma calendar")
        # Only what was added here can be removed here; your Luma follows and the configuration keep theirs.
        kept = [o for o in cal["origins"] if o in ("followed", "config")]
        if kept:
            raise ApiError(409, "this calendar comes from " + " and ".join(
                {"followed": "your Luma follows", "config": "the app's configuration"}[o] for o in kept))
        self.store.remove_calendar(calendar_id)
        self.sync.sources_changed()
        return json_response({"ok": True})

    def luma_set_session(self, req: Request) -> Response:
        try:
            key = luma_src.parse_session_key(str(req.json().get("session_key") or ""))
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        try:
            self.sync.luma.check_session(key)
        except luma_src.LumaError as e:
            raise ApiError(400, f"Luma rejected that session: {e.message}") from None
        self.store.set_secret("luma_session", {"session_key": key, "via": "cookie"})
        self.store.set_meta("luma_session_notice", "")
        self.sync.sources_changed(refresh=["luma:following", "luma:mine"])
        return json_response({"ok": True})

    def luma_clear_session(self, req: Request) -> Response:
        self.store.delete_secret("luma_session")
        self.store.set_origin_calendars("followed", [])
        self.sync.sources_changed()
        return json_response({"ok": True})

    def luma_set_ics(self, req: Request) -> Response:
        try:
            url = luma_src.normalize_ics_url(str(req.json().get("url") or ""))
            events = self.sync.luma.personal_feed(url)
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        self.store.set_secret("luma_ics", {"url": url})
        self.sync.sources_changed(refresh=["luma:ics"])
        return json_response({"ok": True, "events": len(events)})

    def luma_clear_ics(self, req: Request) -> Response:
        self.store.delete_secret("luma_ics")
        self.sync.sources_changed()
        return json_response({"ok": True})

    # ---------- Partiful ----------

    def partiful_import(self, req: Request) -> Response:
        try:
            login = partiful_src.parse_import_payload(req.json().get("payload"))
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        try:
            account = self.sync.partiful.id_token({"uid": login["uid"], "refresh_token": login["refresh_token"]})
        except partiful_src.PartifulError as e:
            raise ApiError(400, str(e)) from None
        if not account.get("uid") or ":" in account["uid"]:
            raise ApiError(400, "Partiful did not say which account this is")
        self.store.save_partiful_account(account)
        uid = account["uid"]
        self.sync.sources_changed(refresh=[f"partiful:{uid}:mine", f"partiful:{uid}:following"])
        return json_response({"uid": uid, "name": account.get("name")})

    def partiful_remove_account(self, req: Request) -> Response:
        if not self.store.delete_partiful_account(req.params["uid"]):
            raise ApiError(404, "no such Partiful account")
        self.sync.sources_changed()
        return json_response({"ok": True})

    def partiful_set_feed(self, req: Request) -> Response:
        try:
            url = partiful_src.normalize_feed_url(str(req.json().get("url") or ""))
            events = self.sync.partiful.feed(url)
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        self.store.set_secret("partiful_feed", {"url": url})
        self.sync.sources_changed(refresh=["partiful:feed"])
        return json_response({"ok": True, "events": len(events)})

    def partiful_clear_feed(self, req: Request) -> Response:
        self.store.delete_secret("partiful_feed")
        self.sync.sources_changed()
        return json_response({"ok": True})

    # ---------- the web app ----------

    def static(self, req: Request) -> Response:
        root = self.web_dir
        rel = urllib.parse.unquote(req.path).lstrip("/")
        if "\0" in rel or "\\" in rel:
            return json_response({"error": "bad path"}, 400)
        candidate = (root / rel) if rel else root / "index.html"
        file = self._resolve(root, candidate)
        if file is None:
            if Path(rel).suffix and not rel.endswith(".html"):
                return Response(404, b"not found", "text/plain; charset=utf-8")
            file = self._resolve(root, root / "index.html")
            if file is None:
                return Response(503, b"The web app is not built yet. Run: npm --prefix web ci && npm --prefix web run build",
                                "text/plain; charset=utf-8")
        body, gz = self._read_static(file)
        ctype = _content_type(file)
        hashed_asset = file.parent == (root / "assets").resolve() and file.name != "index.html"
        headers = {"Cache-Control": "public, max-age=31536000, immutable" if hashed_asset else "no-cache"}
        if file.name == "index.html":
            body = _with_base(body, req.headers.get("X-Forwarded-Prefix"))
            gz = None
            headers["Content-Security-Policy"] = CSP
        if file.name == "sw.js":
            headers["Service-Worker-Allowed"] = "/"
        return Response(200, body, ctype, headers, gzipped=gz)

    @staticmethod
    def _resolve(root: Path, candidate: Path) -> Path | None:
        try:
            real_root = root.resolve(strict=True)
            real = candidate.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError, OSError):
            return None
        if real != real_root and real_root not in real.parents:
            return None
        return real if real.is_file() else None

    def _read_static(self, file: Path) -> tuple[bytes, bytes | None]:
        mtime = file.stat().st_mtime
        key = str(file)
        with self._static_lock:
            cached = self._static_cache.get(key)
            if cached and cached[0] == mtime:
                return cached[1], cached[2]
        body = file.read_bytes()
        gz = gzip.compress(body, 6) if _compressible(_content_type(file)) and len(body) > 1024 else None
        with self._static_lock:
            self._static_cache[key] = (mtime, body, gz)
        return body, gz


def _content_type(file: Path) -> str:
    special = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
               ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
               ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml", ".json": "application/json",
               ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8"}
    return special.get(file.suffix) or mimetypes.guess_type(file.name)[0] or "application/octet-stream"


def _compressible(ctype: str) -> bool:
    return ctype.startswith(COMPRESSIBLE)


def _with_base(html: bytes, prefix: str | None) -> bytes:
    """Point <base href> at the path the proxy mounts the app under, so assets and routes resolve."""
    prefix = (prefix or "").strip()
    if not re.fullmatch(r"(/[A-Za-z0-9._~-]+)*/?", prefix):
        prefix = ""
    base = prefix.rstrip("/") + "/"
    return html.replace(b'<base href="/"', f'<base href="{base}"'.encode(), 1)


class _Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "events"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # requests are logged selectively in _serve
        pass

    def version_string(self) -> str:
        return self.server_version

    def _serve(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            # The unread body would otherwise be parsed as the next request on this connection.
            self.close_connection = True
            error = ("request too large", 413) if length > MAX_BODY else ("bad Content-Length", 400)
            self._send(json_response({"error": error[0]}, error[1]), head=False)
            return
        body = self.rfile.read(length) if length else b""
        # Routing sees the raw path; the router decodes each parameter once, so "%2F" stays inside an id.
        path = parsed.path or "/"
        req = Request(self.command, path, urllib.parse.parse_qs(parsed.query), self.headers, body)
        resp = self.app.handle(req)
        if self.command != "GET" or resp.status >= 400:
            level = logging.WARNING if resp.status >= 500 else logging.INFO
            log.log(level, "%s %s -> %s", self.command, parsed.path, resp.status)
        self._send(resp, head=self.command == "HEAD")

    def _send(self, resp: Response, *, head: bool) -> None:
        body = resp.body
        encoding = None
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if accepts_gzip and resp.status == 200 and len(body) > 1024 and _compressible(resp.content_type):
            body = resp.gzipped if resp.gzipped is not None else gzip.compress(body, 6)
            encoding = "gzip"
        try:
            self.send_response(resp.status, HTTPStatus(resp.status).phrase if resp.status in HTTPStatus._value2member_map_ else None)
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            headers = {"Cache-Control": "no-store", **resp.headers}
            for name, value in headers.items():
                self.send_header(name, value)
            if resp.status != 304:
                self.send_header("Content-Type", resp.content_type)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            if _compressible(resp.content_type):
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head and resp.status != 304:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _serve


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(app: App, host: str, port: int) -> Server:
    handler = type("Handler", (_Handler,), {"app": app})
    return Server((host, port), handler)

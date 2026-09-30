"""The tests share a settable clock, in-memory stores, fake upstream clients and listing builders."""
from __future__ import annotations

import base64
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path

from events import net
from events.config import Settings
from events.db import Database
from events.sources import empty_location
from events.sources.luma import LumaError
from events.store import Feed, Store
from events.timeutil import to_iso

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc).timestamp()
HOUR = 3600
DAY = 86400


def iso(hours: float = 0) -> str:
    """Canonical UTC time ``hours`` after the tests' fixed NOW."""
    return to_iso(NOW + hours * HOUR)


class FakeClock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_db(test: unittest.TestCase, path: str | Path = ":memory:") -> Database:
    db = Database(path)
    test.addCleanup(db.close)
    db.migrate()
    return db


def make_store(test: unittest.TestCase, clock: FakeClock | None = None) -> Store:
    return Store(make_db(test), clock or FakeClock())


def fixture(name: str) -> object:
    return copy.deepcopy(json.loads((FIXTURES / name).read_text()))


def temp_dir(test: unittest.TestCase) -> Path:
    tmp = tempfile.TemporaryDirectory(prefix="events-test-")
    test.addCleanup(tmp.cleanup)
    return Path(tmp.name)


def make_settings(root: Path, **env: str) -> Settings:
    values = {"EVENTS_STATE_DIR": str(root / "state"), "EVENTS_WEB_DIR": str(root / "web"), "EVENTS_SYNC": "0"}
    values.update(env)
    return Settings.from_env(values)


# ---------- upstream fakes ----------

def response(body: object = b"", status: int = 200, content_type: str = "application/json",
             url: str = "https://upstream.test/") -> net.Response:
    if isinstance(body, str):
        body = body.encode()
    elif not isinstance(body, bytes):
        body = json.dumps(body).encode()
    headers = Message()
    headers["Content-Type"] = content_type
    return net.Response(status, headers, body, url)


class FakeRequest:
    """Stands in for ``net.request``. It records each call and answers via ``handler(url, **kwargs)``;
    a handler may return an Exception instance to have it raised."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs) -> net.Response:
        self.calls.append((url, kwargs))
        result = self.handler(url, **kwargs)
        if isinstance(result, BaseException):
            raise result
        return result


def _answer(value, *args):
    if isinstance(value, BaseException):
        raise value
    return value(*args) if callable(value) else copy.deepcopy(value)


class FakeLuma:
    """Client-level fake for LumaClient. Set attributes to values, exceptions or callables."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.events: dict[str, object] = {}  # calendar id -> events | exception | fn(session_key)
        self.following_result: object = []
        self.mine_result: object = []
        self.feed_result: object = []
        self.session_result: object = None
        self.resolved: dict[str, object] = {}  # link token -> calendar | exception
        self.event_links: dict[str, object] = {}  # link token -> event listing | exception
        self.single_events: dict[str, object] = {}  # evt- id -> listing | exception | fn(session_key)

    def calendar_events(self, calendar_id, session_key=None):
        self.calls.append(("calendar_events", calendar_id, session_key))
        return _answer(self.events.get(calendar_id, []), session_key)

    def following(self, session_key):
        self.calls.append(("following", session_key))
        return _answer(self.following_result, session_key)

    def my_events(self, session_key):
        self.calls.append(("my_events", session_key))
        return _answer(self.mine_result, session_key)

    def personal_feed(self, url):
        self.calls.append(("personal_feed", url))
        return _answer(self.feed_result, url)

    def check_session(self, session_key):
        self.calls.append(("check_session", session_key))
        return _answer(self.session_result, session_key)

    def resolve_calendar(self, token):
        self.calls.append(("resolve_calendar", token))
        return _answer(self.resolved.get(token, ValueError("not a calendar or event link")), token)

    def resolve_link(self, token):
        self.calls.append(("resolve_link", token))
        if token in self.event_links:
            return "event", _answer(self.event_links[token], token)
        return "calendar", _answer(self.resolved.get(token, ValueError("not a calendar or event link")), token)

    def event(self, event_id, session_key=None):
        self.calls.append(("event", event_id, session_key))
        return _answer(self.single_events.get(event_id, LumaError(404, "no such event")), session_key)


class FakePartiful:
    """Client-level fake for PartifulClient."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.token_result: object = lambda account: account
        self.events_result: dict[str, object] = {}  # callable name -> events | exception
        self.feed_result: object = []

    def id_token(self, account):
        self.calls.append(("id_token", dict(account)))
        return _answer(self.token_result, account)

    def events(self, account, name):
        self.calls.append(("events", account.get("uid"), name))
        return _answer(self.events_result.get(name, []))

    def feed(self, url):
        self.calls.append(("feed", url))
        return _answer(self.feed_result, url)


class FakeAgiHouse:
    def __init__(self, result: object = ()):
        self.result = list(result) if isinstance(result, (list, tuple)) else result
        self.calls = 0

    def events(self):
        self.calls += 1
        return _answer(self.result)


def jwt(claims: dict) -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"


# ---------- listings, calendars and feeds ----------

def listing(event_id: str, *, name: str = "Community Meetup", source: str = "luma", start: str | None = None,
            end: str | None = None, **fields) -> dict:
    """A listing dict in the shape every source produces (see events.sources)."""
    start = start or iso(24)
    ev = {
        "id": event_id, "source": source, "name": name, "url": f"https://luma.com/{event_id}",
        "start_at": start, "end_at": end, "all_day": False, "timezone": "America/Los_Angeles", "cover_url": None,
        "location": empty_location(), "presenter": None, "hosts": [], "tags": [], "ticket": None,
        "guest_count": None, "going_status": None,
    }
    ev.update(fields)
    return ev


def calendar(cal_id: str, name: str | None = None, source: str = "luma") -> dict:
    return {"id": cal_id, "source": source, "name": name or cal_id, "slug": None, "avatar_url": None,
            "tint_color": None, "url": f"https://luma.com/{cal_id}", "description": None}


def add_feed(store: Store, key: str, *, calendar_id: str | None, kind: str = "calendar", source: str = "luma",
             origin: str = "link", name: str | None = None) -> str:
    """Create a calendar (when given) and a feed that lists under it."""
    if calendar_id:
        store.upsert_calendar(calendar(calendar_id, name, source), origin)
    store.ensure_feed(key, source=source, kind=kind, calendar_id=calendar_id, label=name or key)
    return key


def feed(key: str = "luma:cal-aaaaaaaaaaaa", **fields) -> Feed:
    values = {"key": key, "source": "luma", "kind": "calendar", "calendar_id": None, "label": key,
              "last_attempt_at": None, "last_ok_at": None, "last_error": None, "failures": 0, "retry_at": None,
              "item_count": None}
    values.update(fields)
    return Feed(**values)


# ---------- raw HTTP through the real handler, without sockets ----------

def serve_raw(app, raw: bytes) -> tuple[int, Message, bytes]:
    """Feed one raw HTTP request to events.web's handler class and parse what it writes back."""
    from http.client import parse_headers

    from events import web

    handler_cls = type("TestHandler", (web._Handler,), {"app": app})
    handler = handler_cls.__new__(handler_cls)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.client_address = ("127.0.0.1", 0)
    handler.server = None
    handler.request = None
    handler.close_connection = True
    handler.handle_one_request()
    out = io.BytesIO(handler.wfile.getvalue())
    status = int(out.readline().split()[1])
    headers = parse_headers(out)
    return status, headers, out.read()

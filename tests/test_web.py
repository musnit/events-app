import gzip
import json
import os
import unittest
from email.message import Message

import events
from events import web
from events.catalog import Catalog
from events.sources.luma import LumaError
from events.sources.partiful import PartifulError
from events.sync import NEW, USER, Sync
from events.web import Request

from .helpers import (NOW, FakeAgiHouse, FakeClock, FakeLuma, FakePartiful, add_feed, calendar, fixture, iso, jwt, listing,
                      make_settings, make_store, serve_raw, temp_dir)

INDEX_HTML = ('<!doctype html><html><head><meta charset="utf-8" /><base href="/" /><title>luma-cal</title></head>'
              '<body><div id="root"></div><script type="module" src="assets/app.js"></script></body></html>')
ICS_URL = "https://api.lu.ma/ics/get?entity=user&id=usr-1"


def request(method: str, path: str, body: object = None, *, raw: bytes | None = None, headers: dict | None = None,
            query: dict | None = None, content_type: str = "application/json") -> Request:
    msg = Message()
    data = raw if raw is not None else b"" if body is None else json.dumps(body).encode()
    if data:
        msg["Content-Type"] = content_type
    for name, value in (headers or {}).items():
        del msg[name]
        msg[name] = value
    return Request(method, path, query or {}, msg, data)


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)
        self.web_dir = self.root / "web"
        (self.web_dir / "assets").mkdir(parents=True)
        (self.web_dir / "index.html").write_text(INDEX_HTML)
        (self.web_dir / "assets" / "app.js").write_text("console.log('luma-cal');\n" * 100)
        (self.web_dir / "sw.js").write_text("self.addEventListener('fetch', () => {});\n")
        (self.root / "secret.txt").write_text("TOP SECRET")
        self.clock = FakeClock()
        self.store = make_store(self, self.clock)
        self.luma, self.partiful, self.agihouse = FakeLuma(), FakePartiful(), FakeAgiHouse()
        settings = make_settings(self.root)
        self.sync = Sync(self.store, settings, luma=self.luma, partiful=self.partiful, agihouse=self.agihouse,
                         clock=self.clock)
        self.catalog = Catalog(self.store, clock=self.clock)
        self.app = web.App(settings, self.store, self.sync, self.catalog)

    def call(self, method: str, path: str, body: object = None, **kwargs) -> web.Response:
        return self.app.handle(request(method, path, body, **kwargs))

    def json_of(self, resp: web.Response) -> object:
        return json.loads(resp.body)

    def queued(self, source: str) -> dict[str, int]:
        return {k: t.priority for k, t in self.sync.workers[source]._queue.items()}

    def add_events(self, *events: dict) -> None:
        key = add_feed(self.store, "luma:cal-a", calendar_id="cal-a", name="Alpha")
        self.store.replace_listings(key, list(events))


class JsonResponseTest(unittest.TestCase):
    def test_json_is_compact_utf8_and_tolerates_lone_surrogates(self):
        resp = web.json_response({"name": "Käse 🍷", "odd": json.loads('"Odd \\ud83c name"')}, 201)
        self.assertEqual((resp.status, resp.content_type), (201, "application/json; charset=utf-8"))
        self.assertTrue(resp.body.startswith('{"name":"Käse 🍷","odd":"Odd '.encode()), resp.body)
        self.assertTrue(json.loads(resp.body.decode("utf-8"))["odd"].endswith(" name"))


class RoutingTest(WebTestCase):
    def test_health(self):
        resp = self.call("GET", "/api/health")
        self.assertEqual((resp.status, resp.content_type), (200, "application/json; charset=utf-8"))
        self.assertEqual(self.json_of(resp), {"ok": True, "version": events.__version__,
                                              "data_version": self.store.data_version()})

    def test_unknown_api_paths_are_json_404s(self):
        for method, path in (("GET", "/api/nope"), ("DELETE", "/api/nope"), ("GET", "/api/events/evt-1/nope"),
                             ("GET", "/api/"), ("PUT", "/api/luma/calendars/cal-a/extra")):
            with self.subTest(method=method, path=path):
                resp = self.call(method, path)
                self.assertEqual((resp.status, self.json_of(resp)), (404, {"error": "not found"}))

    def test_wrong_methods_are_405_with_allow(self):
        for method, path, allow in (("POST", "/api/events", "GET"), ("GET", "/api/luma/session", "DELETE, PUT"),
                                    ("POST", "/feed.ics", "GET"), ("GET", "/api/events/evt-1/mark", "PUT"),
                                    ("POST", "/api/luma/calendars/cal-a", "DELETE")):
            with self.subTest(method=method, path=path):
                resp = self.call(method, path)
                self.assertEqual((resp.status, self.json_of(resp)), (405, {"error": "method not allowed"}))
                self.assertEqual(resp.headers["Allow"], allow)
        resp = self.call("DELETE", "/month/2026-10")
        self.assertEqual(resp.status, 405, "writes to app routes are refused")
        self.assertNotIn("Allow", resp.headers)

    def test_head_is_served_by_get_routes(self):
        self.assertEqual(self.call("HEAD", "/api/health").status, 200)
        self.assertEqual(self.call("HEAD", "/month/2026-10").status, 200)

    def test_route_params_are_url_decoded(self):
        handler, params, _ = self.app.router.match("GET", "/api/events/pf-a%20b/ics")
        self.assertEqual((handler, params), (self.app.event_ics, {"event_id": "pf-a b"}))
        self.assertIsNone(self.app.router.match("GET", "/api/events//ics")[0])
        self.assertIsNone(self.app.router.match("GET", "/api/events/a/b/ics")[0])

    def test_unexpected_errors_are_500_without_details(self):
        self.luma.session_result = RuntimeError("secret detail")
        with self.assertLogs("events.web", "ERROR"):
            resp = self.call("PUT", "/api/luma/session", {"session_key": "abc"})
        self.assertEqual((resp.status, self.json_of(resp)), (500, {"error": "internal error"}))


class SameOriginTest(WebTestCase):
    def post(self, headers: dict) -> int:
        return self.call("POST", "/api/sync", {}, headers=headers).status

    def test_fetch_metadata_decides_when_present(self):
        for site in ("cross-site", "same-site"):
            with self.subTest(site=site):
                resp = self.call("POST", "/api/sync", {}, headers={"Sec-Fetch-Site": site})
                self.assertEqual((resp.status, self.json_of(resp)), (403, {"error": "cross-site request refused"}))
        self.assertEqual(self.queued("agihouse"), {}, "refused requests do nothing")
        for site in ("same-origin", "none"):
            with self.subTest(site=site):
                self.assertEqual(self.post({"Sec-Fetch-Site": site}), 200)
        # Proxies may rewrite Host, so a same-origin label outranks an Origin/Host mismatch...
        self.assertEqual(self.post({"Sec-Fetch-Site": "same-origin", "Origin": "https://events.example.test",
                                    "Host": "127.0.0.1:8771"}), 200)
        # ...and a cross-site label outranks a matching Origin.
        self.assertEqual(self.post({"Sec-Fetch-Site": "cross-site", "Origin": "http://localhost:8771",
                                    "Host": "localhost:8771"}), 403)

    def test_origin_must_match_the_host_without_fetch_metadata(self):
        self.assertEqual(self.post({"Origin": "https://evil.example", "Host": "localhost:8771"}), 403)
        self.assertEqual(self.post({"Origin": "null", "Host": "localhost:8771"}), 403)
        self.assertEqual(self.post({"Origin": "http://localhost:8771.evil.example", "Host": "localhost:8771"}), 403)
        self.assertEqual(self.post({"Origin": "https://evil.example"}), 403, "no Host at all")
        self.assertEqual(self.post({"Origin": "http://localhost:8771", "Host": "localhost:8771"}), 200)
        self.assertEqual(self.post({"Host": "localhost:8771"}), 200, "no Origin: not a browser")
        proxied = {"Origin": "https://events.example.test", "Host": "127.0.0.1:8771",
                   "X-Forwarded-Host": "proxy.internal, events.example.test"}
        self.assertEqual(self.post(proxied), 200, "any forwarded host counts")
        self.assertEqual(self.post(proxied | {"X-Forwarded-Host": "other.example"}), 403)
        self.assertEqual(self.post(proxied | {"Origin": "https://127.0.0.1:8771"}), 200, "Host still counts")

    def test_request_bodies_must_be_json(self):
        for content_type in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data; boundary=x"):
            with self.subTest(content_type=content_type):
                resp = self.call("PUT", "/api/prefs", raw=b'{"area": "all"}', content_type=content_type)
                self.assertEqual(resp.status, 415)
        self.assertEqual(self.store.prefs(), {})
        ok = self.call("PUT", "/api/prefs", {"area": "all"}, content_type="application/json; charset=utf-8")
        self.assertEqual(ok.status, 200)

    def test_body_less_writes_and_all_reads_pass(self):
        self.assertEqual(self.call("DELETE", "/api/luma/ics").status, 200)
        self.assertEqual(self.call("GET", "/api/health", headers={"Sec-Fetch-Site": "cross-site"}).status, 200)

    def test_checks_run_before_routing(self):
        resp = self.call("POST", "/api/nope", {}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(resp.status, 403)


class EventsApiTest(WebTestCase):
    def test_events_etag_and_304(self):
        self.add_events(listing("evt-1", name="Käse & Wein"))
        resp = self.call("GET", "/api/events")
        self.assertEqual(resp.status, 200)
        etag = resp.headers["ETag"]
        self.assertEqual(resp.headers["Cache-Control"], "no-cache, private")
        self.assertEqual([e["id"] for e in self.json_of(resp)["events"]], ["evt-1"])
        self.assertEqual(gzip.decompress(resp.gzipped), resp.body)
        for header in (etag, f"W/{etag}", f'"stale", {etag}'):
            with self.subTest(header=header):
                cached = self.call("GET", "/api/events", headers={"If-None-Match": header})
                self.assertEqual((cached.status, cached.body, cached.headers["ETag"]), (304, b"", etag))
        self.assertEqual(self.call("GET", "/api/events", headers={"If-None-Match": '"stale"'}).status, 200)
        self.store.set_mark("evt-1", starred=True)
        changed = self.call("GET", "/api/events", headers={"If-None-Match": etag})
        self.assertEqual(changed.status, 200)
        self.assertNotEqual(changed.headers["ETag"], etag)
        self.assertTrue(self.json_of(changed)["events"][0]["starred"])

    def test_event_ics_download(self):
        self.add_events(listing("evt-1", name="Sex, AI & the Future?"), listing("evt-2", name="東京"))
        resp = self.call("GET", "/api/events/evt-1/ics")
        self.assertEqual((resp.status, resp.content_type), (200, "text/calendar; charset=utf-8"))
        self.assertEqual(resp.headers["Content-Disposition"], 'attachment; filename="Sex-AI-the-Future.ics"')
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertIn(b"UID:evt-1@luma-cal", resp.body)
        self.assertIn(b"DTSTAMP:20260929T120000Z", resp.body)
        self.assertEqual(self.call("GET", "/api/events/evt-2/ics").headers["Content-Disposition"],
                         'attachment; filename="event.ics"')
        missing = self.call("GET", "/api/events/evt-missing/ics")
        self.assertEqual((missing.status, self.json_of(missing)), (404, {"error": "no such event"}))

    def test_marks(self):
        resp = self.call("PUT", "/api/events/evt-x/mark", {"starred": True})
        self.assertEqual((resp.status, self.json_of(resp)), (200, {"starred": True, "hidden": False}))
        resp = self.call("PUT", "/api/events/evt-x/mark", {"starred": False, "hidden": None})
        self.assertEqual(self.json_of(resp), {"starred": False, "hidden": False})
        self.assertEqual(self.store.marks(), {})

    def test_mark_validation(self):
        for body in ({"starred": "yes"}, {"hidden": 1}, {}, {"starred": None, "hidden": None}, [True]):
            with self.subTest(body=body):
                self.assertEqual(self.call("PUT", "/api/events/evt-x/mark", body).status, 400)
        self.assertEqual(self.call("PUT", "/api/events/evt-x/mark", raw=b"{not json").status, 400)
        self.assertEqual(self.call("PUT", "/api/events/" + "x" * 201 + "/mark", {"starred": True}).status, 400)
        self.assertEqual(self.call("PUT", "/api/events/" + "x" * 200 + "/mark", {"starred": True}).status, 200)
        self.assertEqual(list(self.store.marks()), ["x" * 200])

    def test_feed_ics_scopes(self):
        self.add_events(listing("evt-going", going_status="approved"), listing("evt-other"),
                        listing("evt-hidden", going_status="approved"))
        self.store.set_mark("evt-hidden", hidden=True)
        mine = self.call("GET", "/feed.ics")
        self.assertEqual((mine.status, mine.content_type), (200, "text/calendar; charset=utf-8"))
        text = mine.body.decode()
        self.assertIn("X-WR-CALNAME:My events · Luma + Partiful", text)
        self.assertIn("UID:evt-going@luma-cal", text)
        self.assertNotIn("evt-other", text)
        self.assertNotIn("evt-hidden", text)
        everything = self.call("GET", "/feed.ics", query={"scope": ["all"]}).body.decode()
        self.assertIn("UID:evt-other@luma-cal", everything)
        self.assertNotIn("evt-hidden", everything)
        for scope in ("bogus", "", "MINE"):
            with self.subTest(scope=scope):
                bad = self.call("GET", "/feed.ics", query={"scope": [scope]})
                self.assertEqual((bad.status, self.json_of(bad)), (400, {"error": "scope must be mine or all"}))


class PrefsTest(WebTestCase):
    def test_valid_prefs(self):
        resp = self.call("PUT", "/api/prefs", {"muted_calendars": ["cal-b", "cal-a", "cal-b"], "area": "bay-online",
                                               "unknown": 1})
        expected = {"muted_calendars": ["cal-a", "cal-b"], "area": "bay-online"}
        self.assertEqual((resp.status, self.json_of(resp)), (200, expected))
        self.assertEqual(self.json_of(self.call("PUT", "/api/prefs", {})), expected)
        self.assertEqual(self.json_of(self.call("PUT", "/api/prefs", {"muted_calendars": []}))["muted_calendars"], [])

    def test_invalid_prefs(self):
        cases = [({"muted_calendars": "cal-a"}, "muted_calendars must be a list"),
                 ({"muted_calendars": [1]}, "muted_calendars must be a list"),
                 ({"muted_calendars": ["x" * 200]}, "muted_calendars must be a list"),
                 ({"muted_calendars": None}, "muted_calendars must be a list"),
                 ({"area": "mars"}, "area must be one of bay, bay-online, all"),
                 ({"area": None}, "area must be one of")]
        for body, message in cases:
            with self.subTest(body=body):
                resp = self.call("PUT", "/api/prefs", body)
                self.assertEqual(resp.status, 400)
                self.assertIn(message, self.json_of(resp)["error"])
        self.assertEqual(self.store.prefs(), {})

    def test_rejected_prefs_change_nothing(self):
        resp = self.call("PUT", "/api/prefs", {"muted_calendars": ["cal-a"], "area": "mars"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.store.prefs(), {})


class StaticTest(WebTestCase):
    def test_index_and_spa_fallback(self):
        for path in ("/", "/index.html", "/month/2026-10", "/event/evt-x", "/settings/", "/api"):
            with self.subTest(path=path):
                resp = self.call("GET", path)
                self.assertEqual((resp.status, resp.content_type), (200, "text/html; charset=utf-8"))
                self.assertIn(b'<base href="/" />', resp.body)
                self.assertEqual(resp.headers["Content-Security-Policy"], web.CSP)
                self.assertEqual(resp.headers["Cache-Control"], "no-cache")
                self.assertIsNone(resp.gzipped, "index.html is rewritten per request")

    def test_assets(self):
        resp = self.call("GET", "/assets/app.js")
        self.assertEqual((resp.status, resp.content_type), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=31536000, immutable")
        self.assertEqual(gzip.decompress(resp.gzipped), resp.body)
        self.assertNotIn("Content-Security-Policy", resp.headers)
        for path in ("/assets/missing.js", "/missing.png", "/assets/app.js.map"):
            with self.subTest(path=path):
                missing = self.call("GET", path)
                self.assertEqual((missing.status, missing.body, missing.content_type),
                                 (404, b"not found", "text/plain; charset=utf-8"))

    def test_service_worker(self):
        resp = self.call("GET", "/sw.js")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Service-Worker-Allowed"], "/")
        self.assertEqual(resp.headers["Cache-Control"], "no-cache")
        self.assertIsNone(resp.gzipped, "small files are not pre-compressed")

    def test_static_cache_follows_file_changes(self):
        path = self.web_dir / "sw.js"
        self.call("GET", "/sw.js")
        path.write_text("// v2\n")
        os.utime(path, (1, 1))
        self.assertEqual(self.call("GET", "/sw.js").body, b"// v2\n")

    def test_missing_build(self):
        (self.web_dir / "index.html").unlink()
        resp = self.call("GET", "/month/2026-10")
        self.assertEqual((resp.status, resp.content_type), (503, "text/plain; charset=utf-8"))

    def test_base_href_follows_a_valid_forwarded_prefix(self):
        cases = {"/events": "/events/", "/events/": "/events/", "/a/b-c_d.e~f": "/a/b-c_d.e~f/", "": "/", "/": "/",
                 "//evil.example": "/", "https://evil.example/": "/", '/ev"><script>alert(1)</script>': "/",
                 "/ev ents": "/", "events": "/", "/events?x=1": "/"}
        for prefix, base in cases.items():
            with self.subTest(prefix=prefix):
                resp = self.call("GET", "/month/2026-10", headers={"X-Forwarded-Prefix": prefix})
                self.assertIn(f'<base href="{base}" />'.encode(), resp.body)
                self.assertNotIn(b"<script>alert", resp.body)

    def test_path_traversal_never_leaves_the_web_dir(self):
        (self.web_dir / "leak.txt").symlink_to(self.root / "secret.txt")
        (self.web_dir / "outside").symlink_to(self.root, target_is_directory=True)
        cases = {"/../secret.txt": 404, "/assets/../../secret.txt": 404, "/%2e%2e/secret.txt": 404,
                 "/..%2fsecret.txt": 404, "/leak.txt": 404, "/outside/secret.txt": 404, "//../secret.txt": 404,
                 "/..": 200, "/assets/../..": 200, "/outside": 200,
                 "/%252e%252e/secret.txt": 404, "/..\\secret.txt": 400, "\\..\\secret.txt": 400,
                 "/secret.txt\0.js": 400, "/secret.txt%00.js": 400, "/%5c..%5csecret.txt": 400}
        for path, status in cases.items():
            with self.subTest(path=path):
                resp = self.call("GET", path)
                self.assertEqual(resp.status, status)
                self.assertNotIn(b"TOP SECRET", resp.body)
                if status == 200:
                    self.assertIn(b"<base href", resp.body, "only the app shell comes back")


class LumaApiTest(WebTestCase):
    def import_payload(self, **fields) -> dict:
        cal = fixture("luma_get_items.json")["entries"][0]["calendar"]
        payload = {"calendars": [{"calendar": cal}, {"calendar": {"api_id": "cal-other0000001", "name": "Other"}}],
                   "going": ["evt-1", "junk"], "session_key": "sess-1"}
        payload.update(fields)
        return {"payload": payload}

    def test_import_with_a_session(self):
        resp = self.call("POST", "/api/luma/import", self.import_payload())
        self.assertEqual(self.json_of(resp), {"total": 2, "added": 2, "removed": 0, "going": 1, "session": True})
        self.assertEqual(self.luma.calls, [("check_session", "sess-1")])
        self.assertEqual(self.store.get_secret("luma_session"), {"session_key": "sess-1", "via": "bookmarklet"})
        self.assertEqual(self.store.going(), {"evt-1": "registered"})
        self.assertEqual(self.store.calendar("cal-oWJafai4qVBegex")["origins"], ["import"])
        self.assertEqual(self.queued("luma"), {"luma:cal-oWJafai4qVBegex": NEW, "luma:cal-other0000001": NEW,
                                               "luma:following": NEW, "luma:mine": NEW})

        again = self.call("POST", "/api/luma/import", self.import_payload(calendars=self.import_payload()["payload"]["calendars"][:1],
                                                                         going=[], session_key=None))
        self.assertEqual(self.json_of(again), {"total": 1, "added": 0, "removed": 1, "going": 0, "session": False})
        self.assertIsNone(self.store.calendar("cal-other0000001"))
        self.assertIsNone(self.store.feed("luma:cal-other0000001"))
        self.assertEqual(self.store.going(), {}, "each import replaces the RSVP snapshot")
        self.assertIsNotNone(self.store.get_secret("luma_session"), "an import without a key keeps the saved session")

    def test_import_with_a_rejected_session(self):
        self.luma.session_result = LumaError(401, "expired")
        resp = self.call("POST", "/api/luma/import", self.import_payload(session_key="stale"))
        self.assertEqual(resp.status, 200)
        self.assertFalse(self.json_of(resp)["session"])
        self.assertIsNone(self.store.get_secret("luma_session"))
        self.assertNotIn("luma:following", self.queued("luma"))

    def test_import_errors(self):
        for body in ({"payload": "eyJjYWxlbmRhcnMiOltdfQ=="}, {"payload": {"calendars": []}}, {},
                     {"payload": [{"api_id": "usr-1", "name": "Not a calendar"}]}):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/api/luma/import", body).status, 400)
        self.assertEqual(self.store.calendars(), [])

    def test_add_calendars_and_events_by_link(self):
        EVT = "evt-FNsJLjeVNGCdNxs"
        self.luma.resolved = {"Big-Brain-Bay": calendar("cal-oWJafai4qVBegex", "Big Brain"),
                              "nope": ValueError("Luma has no page there"),
                              "busy": LumaError(429, "slow down")}
        self.luma.event_links = {"cisai-886g": listing(EVT, name="CISAI Opening", start=iso(24 * 50))}
        resp = self.call("POST", "/api/luma/links",
                         {"text": "https://luma.com/Big-Brain-Bay lu.ma/nope https://luma.com/cisai-886g "
                                  "https://luma.com/user/x lu.ma/busy"})
        self.assertEqual(self.json_of(resp), {
            "added": [{"kind": "calendar", "id": "cal-oWJafai4qVBegex", "name": "Big Brain"},
                      {"kind": "event", "id": EVT, "name": "CISAI Opening"}],
            "failed": [{"link": "nope", "error": "Luma has no page there"},
                       {"link": "user/x", "error": "not a calendar or event link"},
                       {"link": "busy", "error": "Luma 429: slow down"}]})
        self.assertEqual(self.store.calendar("cal-oWJafai4qVBegex")["origins"], ["link"])
        # The new calendar is pulled next; the event arrived with its link and shows at once.
        self.assertEqual(self.queued("luma"), {"luma:cal-oWJafai4qVBegex": NEW})
        added = next(e for e in self.json_of(self.call("GET", "/api/events"))["events"] if e["id"] == EVT)
        self.assertTrue(added["linked"])
        self.assertEqual(added["calendar_ids"], ["luma-links"])
        self.assertEqual(self.call("POST", "/api/luma/links", {"text": "nothing to see"}).status, 400)
        self.assertEqual(self.call("POST", "/api/luma/links", {}).status, 400)

    def test_remove_an_event_added_by_link(self):
        EVT = "evt-FNsJLjeVNGCdNxs"
        self.sync.link_event(listing(EVT, name="CISAI Opening"))
        status = self.json_of(self.call("GET", "/api/status"))["luma"]
        self.assertEqual(status["linked_events"], [{"id": EVT, "name": "CISAI Opening",
                                                    "url": f"https://luma.com/{EVT}", "last_error": None}])
        self.assertEqual(self.call("DELETE", f"/api/luma/events/{EVT}").status, 200)
        self.assertEqual(self.call("DELETE", f"/api/luma/events/{EVT}").status, 404)
        self.assertNotIn(EVT, [e["id"] for e in self.json_of(self.call("GET", "/api/events"))["events"]])
        self.assertIsNone(self.store.calendar("luma-links"), "the calendar goes with its last event")

    def test_remove_calendar(self):
        self.store.upsert_calendar(calendar("cal-a"), "import")
        self.store.upsert_calendar(calendar("cal-a"), "followed")
        self.sync.reconcile()
        self.assertEqual(self.call("DELETE", "/api/luma/calendars/cal-missing").status, 404)
        self.assertEqual(self.call("DELETE", "/api/luma/calendars/agihouse").status, 404, "only Luma calendars")
        # Your Luma follows (and the configuration) keep a calendar; only what was added here can go.
        refused = self.call("DELETE", "/api/luma/calendars/cal-a")
        self.assertEqual(refused.status, 409)
        self.assertIn("your Luma follows", self.json_of(refused)["error"])
        self.store.remove_calendar("cal-a", "followed")
        self.store.upsert_calendar(calendar("cal-a"), "link")
        self.assertEqual(self.call("DELETE", "/api/luma/calendars/cal-a").status, 200)
        self.assertIsNone(self.store.calendar("cal-a"), "the import and link claims are both dropped")
        self.assertIsNone(self.store.feed("luma:cal-a"))

    def test_session_cookie(self):
        self.store.set_meta("luma_session_notice", "Luma signed this app out.")
        resp = self.call("PUT", "/api/luma/session", {"session_key": "luma.auth-session-key=abc123;"})
        self.assertEqual((resp.status, self.json_of(resp)), (200, {"ok": True}))
        self.assertEqual(self.luma.calls, [("check_session", "abc123")])
        self.assertEqual(self.store.get_secret("luma_session"), {"session_key": "abc123", "via": "cookie"})
        self.assertEqual(self.store.get_meta("luma_session_notice"), "")
        self.assertEqual(self.queued("luma"), {"luma:following": NEW, "luma:mine": NEW})

        self.store.upsert_calendar(calendar("cal-followed0001"), "followed")
        self.store.upsert_calendar(calendar("cal-linked000001"), "link")
        self.assertEqual(self.call("DELETE", "/api/luma/session").status, 200)
        self.assertIsNone(self.store.get_secret("luma_session"))
        self.assertEqual([c["id"] for c in self.store.calendars() if c["id"].startswith("cal-")], ["cal-linked000001"])
        self.assertIsNone(self.store.feed("luma:following"))

    def test_session_errors(self):
        for key in ("has space", "", None):
            with self.subTest(key=key):
                self.assertEqual(self.call("PUT", "/api/luma/session", {"session_key": key}).status, 400)
        self.luma.session_result = LumaError(401, "not signed in")
        resp = self.call("PUT", "/api/luma/session", {"session_key": "abc"})
        self.assertEqual((resp.status, self.json_of(resp)), (400, {"error": "Luma rejected that session: not signed in"}))
        self.assertIsNone(self.store.get_secret("luma_session"))

    def test_personal_ical_feed(self):
        self.luma.feed_result = [listing("evt-1"), listing("evt-2")]
        resp = self.call("PUT", "/api/luma/ics", {"url": "webcal://api.lu.ma/ics/get?entity=user&id=usr-1"})
        self.assertEqual(self.json_of(resp), {"ok": True, "events": 2})
        self.assertEqual(self.luma.calls, [("personal_feed", ICS_URL)])
        self.assertEqual(self.store.get_secret("luma_ics"), {"url": ICS_URL})
        self.assertEqual(self.queued("luma"), {"luma:ics": NEW})
        status = self.json_of(self.call("GET", "/api/status"))
        self.assertEqual(status["luma"]["ics"], "api.lu.ma/ics/get (saved)")
        self.assertNotIn("usr-1", json.dumps(status), "the feed token is never echoed")

        self.assertEqual(self.call("PUT", "/api/luma/ics", {"url": "https://evil.example/ics/get"}).status, 400)
        self.luma.feed_result = ValueError("that URL did not return an iCalendar feed")
        resp = self.call("PUT", "/api/luma/ics", {"url": ICS_URL})
        self.assertEqual((resp.status, self.json_of(resp)), (400, {"error": "that URL did not return an iCalendar feed"}))
        self.assertEqual(self.call("DELETE", "/api/luma/ics").status, 200)
        self.assertIsNone(self.store.get_secret("luma_ics"))
        self.assertIsNone(self.store.feed("luma:ics"))

    def test_refresh(self):
        self.store.upsert_calendar(calendar("cal-a"), "link")
        self.assertEqual(self.json_of(self.call("POST", "/api/sync", {"source": "luma"})), {"queued": 1})
        self.assertEqual(self.queued("luma"), {"luma:cal-a": USER})
        self.assertEqual(self.json_of(self.call("POST", "/api/sync")), {"queued": 2})
        self.assertEqual(self.call("POST", "/api/sync", {"source": "meetup"}).status, 400)


class PartifulApiTest(WebTestCase):
    def test_import_a_login(self):
        self.partiful.token_result = lambda account: dict(account, uid=account["uid"] or "uid-1", id_token="tok",
                                                          name="Jordan", expires_at=NOW + 3600)
        resp = self.call("POST", "/api/partiful/import", {"payload": {"refresh_token": " AMf-1 ", "uid": None}})
        self.assertEqual((resp.status, self.json_of(resp)), (200, {"uid": "uid-1", "name": "Jordan"}))
        self.assertEqual(self.partiful.calls, [("id_token", {"uid": None, "refresh_token": "AMf-1"})])
        account = self.store.partiful_accounts()[0]
        self.assertEqual((account["uid"], account["refresh_token"], account["id_token"]), ("uid-1", "AMf-1", "tok"))
        self.assertEqual(self.queued("partiful"), {"partiful:uid-1:mine": NEW, "partiful:uid-1:following": NEW})

    def test_import_errors(self):
        self.assertEqual(self.call("POST", "/api/partiful/import", {"payload": {}}).status, 400)
        self.partiful.token_result = PartifulError("the Partiful login expired; run the Partiful bookmarklet again",
                                                   login_expired=True)
        resp = self.call("POST", "/api/partiful/import", {"payload": {"refresh_token": "dead"}})
        self.assertEqual((resp.status, self.json_of(resp)),
                         (400, {"error": "the Partiful login expired; run the Partiful bookmarklet again"}))
        self.partiful.token_result = PartifulError("token refresh answered with something that is not JSON")
        resp = self.call("POST", "/api/partiful/import", {"payload": {"refresh_token": "captive-portal"}})
        self.assertEqual(resp.status, 400)
        self.partiful.token_result = lambda account: dict(account, id_token="tok")
        resp = self.call("POST", "/api/partiful/import", {"payload": {"refresh_token": "anonymous"}})
        self.assertEqual((resp.status, self.json_of(resp)), (400, {"error": "Partiful did not say which account this is"}))
        self.assertEqual(self.store.partiful_accounts(), [])

    def test_remove_an_account(self):
        self.store.save_partiful_account({"uid": "uid-1", "refresh_token": "r"})
        self.sync.reconcile()
        self.assertEqual(self.call("DELETE", "/api/partiful/accounts/nobody").status, 404)
        self.assertEqual(self.call("DELETE", "/api/partiful/accounts/uid-1").status, 200)
        self.assertEqual(self.store.partiful_accounts(), [])
        self.assertEqual(self.store.feeds("partiful"), [])
        self.assertNotIn("partiful-following", [c["id"] for c in self.store.calendars()])

    def test_ical_link(self):
        self.partiful.feed_result = [listing("pf-1", source="partiful")]
        resp = self.call("PUT", "/api/partiful/feed", {"url": "webcal://calendars.partiful.com/getCalendar?id=SECRET"})
        self.assertEqual(self.json_of(resp), {"ok": True, "events": 1})
        self.assertEqual(self.store.get_secret("partiful_feed"), {"url": "https://calendars.partiful.com/getCalendar?id=SECRET"})
        self.assertEqual(self.queued("partiful"), {"partiful:feed": NEW})
        status = self.json_of(self.call("GET", "/api/status"))
        self.assertEqual(status["partiful"]["feed"], "calendars.partiful.com/getCalendar (saved)")
        self.assertNotIn("SECRET", json.dumps(status))
        self.assertEqual(self.call("PUT", "/api/partiful/feed", {"url": "https://partiful.com/e/abc"}).status, 400)
        self.partiful.feed_result = ValueError("could not read the feed: HTTP 404")
        self.assertEqual(self.call("PUT", "/api/partiful/feed", {"url": "https://calendars.partiful.com/x"}).status, 400)
        self.assertEqual(self.call("DELETE", "/api/partiful/feed").status, 200)
        self.assertIsNone(self.store.get_secret("partiful_feed"))


class StatusTest(WebTestCase):
    def test_status_summarises_sources_without_secrets(self):
        self.store.set_secret("luma_session", {"session_key": "SECRET-KEY", "via": "cookie"})
        self.store.save_partiful_account({"uid": "uid-1", "refresh_token": "SECRET-REFRESH",
                                          "id_token": jwt({"name": "Jordan", "user_id": "uid-1"})})
        for cal_id, origin in (("cal-followed0001", "followed"), ("cal-imported0001", "import"), ("cal-linked000001", "link")):
            self.store.upsert_calendar(calendar(cal_id), origin)
        self.store.replace_going("luma-import", {"evt-1": "registered"})
        self.store.set_pref("area", "bay")
        self.sync.reconcile()
        status = self.json_of(self.call("GET", "/api/status"))
        self.assertEqual(status["version"], events.__version__)
        self.assertEqual(status["luma"], {"session": True, "session_via": "cookie", "session_notice": None, "ics": None,
                                          "calendars": 3, "calendars_by_origin": {"followed": 1, "import": 1, "link": 1, "config": 0},
                                          "going_snapshot": 1, "linked_events": []})
        self.assertEqual(status["partiful"], {"accounts": [{"uid": "uid-1", "name": "Jordan", "added_at": NOW}], "feed": None})
        self.assertEqual(set(status["workers"]), {"luma", "partiful", "agihouse"})
        self.assertIn("luma:following", [f["key"] for f in status["feeds"]])
        self.assertEqual(status["prefs"], {"area": "bay"})
        body = json.dumps(status)
        self.assertNotIn("SECRET-KEY", body)
        self.assertNotIn("SECRET-REFRESH", body)

    def test_session_notice_shows_after_sign_out(self):
        self.store.set_meta("luma_session_notice", "Luma signed this app out.")
        status = self.json_of(self.call("GET", "/api/status"))
        self.assertEqual((status["luma"]["session"], status["luma"]["session_notice"]), (False, "Luma signed this app out."))


class HandlerTest(WebTestCase):
    """The real BaseHTTPRequestHandler subclass, fed raw bytes instead of a socket."""

    def raw(self, head: str, body: bytes = b"") -> tuple[int, Message, bytes]:
        return serve_raw(self.app, head.replace("\n", "\r\n").encode() + body)

    def test_responses_carry_security_headers(self):
        status, headers, body = self.raw("GET /api/health HTTP/1.1\nHost: localhost\n\n")
        self.assertEqual(status, 200)
        for name, value in web.SECURITY_HEADERS.items():
            self.assertEqual(headers[name], value)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Server"], "events")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertTrue(json.loads(body)["ok"])

    def test_gzip_only_when_accepted(self):
        status, headers, body = self.raw("GET /api/events HTTP/1.1\nHost: x\nAccept-Encoding: gzip, br\n\n")
        self.assertEqual((status, headers["Content-Encoding"], headers["Vary"]), (200, "gzip", "Accept-Encoding"))
        self.assertIn("events", json.loads(gzip.decompress(body)))
        status, headers, body = self.raw("GET /api/events HTTP/1.1\nHost: x\n\n")
        self.assertIsNone(headers["Content-Encoding"])
        self.assertIn("events", json.loads(body))

    def test_head_and_304_send_no_body(self):
        status, headers, body = self.raw("HEAD /api/health HTTP/1.1\nHost: x\n\n")
        self.assertEqual((status, body), (200, b""))
        self.assertGreater(int(headers["Content-Length"]), 0)
        etag = self.catalog.snapshot().etag
        status, headers, body = self.raw(f"GET /api/events HTTP/1.1\nHost: x\nIf-None-Match: {etag}\n\n")
        self.assertEqual((status, body, headers["ETag"]), (304, b"", etag))
        self.assertIsNone(headers["Content-Type"])

    def test_bodies_and_query_strings_reach_the_app(self):
        payload = json.dumps({"source": "agihouse"}).encode()
        status, _, body = self.raw("POST /api/sync HTTP/1.1\nHost: x\nContent-Type: application/json\n"
                                   f"Content-Length: {len(payload)}\n\n", payload)
        self.assertEqual((status, json.loads(body)), (200, {"queued": 1}))
        status, headers, _ = self.raw("GET /feed.ics?scope=all HTTP/1.1\nHost: x\n\n")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/calendar; charset=utf-8"))
        status, _, _ = self.raw("GET /feed.ics?scope=everything HTTP/1.1\nHost: x\n\n")
        self.assertEqual(status, 400)

    def test_oversized_bodies_are_refused(self):
        status, _, body = self.raw("POST /api/sync HTTP/1.1\nHost: x\nContent-Type: application/json\n"
                                   f"Content-Length: {web.MAX_BODY + 1}\n\n")
        self.assertEqual((status, json.loads(body)), (413, {"error": "request too large"}))

    def test_encoded_traversal_is_decoded_then_refused(self):
        for target in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/%2E%2E/%2e%2e/secret.txt", "/%2e%2e%2fsecret.txt"):
            with self.subTest(target=target):
                status, _, body = self.raw(f"GET {target} HTTP/1.1\nHost: x\n\n")
                self.assertEqual(status, 404)
                self.assertNotIn(b"TOP SECRET", body)

    def test_route_params_are_decoded_once(self):
        body = b'{"starred": true}'
        status, _, _ = self.raw("PUT /api/events/evt-a%2541/mark HTTP/1.1\nHost: x\nContent-Type: application/json\n"
                                f"Content-Length: {len(body)}\n\n", body)
        self.assertEqual(status, 200)
        self.assertEqual(self.store.marks(), {"evt-a%41": {"starred": True, "hidden": False}})
        status, _, _ = self.raw("PUT /api/events/pf-a%2Fb/mark HTTP/1.1\nHost: x\nContent-Type: application/json\n"
                                f"Content-Length: {len(body)}\n\n", body)
        self.assertEqual(status, 200)
        self.assertIn("pf-a/b", self.store.marks(), "an encoded slash stays inside the id")


if __name__ == "__main__":
    unittest.main()

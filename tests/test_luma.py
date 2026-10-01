import json
import unittest

from events import net
from events.sources import luma
from events.sources.luma import LumaClient, LumaError

from .helpers import FakeRequest, fixture, response

CAL_ID = "cal-FixtureBrainBay"
ICS_URL = "https://api.lu.ma/ics/get?entity=user&id=usr-FixtureOrg00001"


def entries() -> list[dict]:
    return fixture("luma_get_items.json")["entries"]


class NormalizeEntryTest(unittest.TestCase):
    def test_realistic_entry(self):
        ev = luma.normalize_entry(entries()[0])
        self.assertEqual(ev["id"], "evt-FixtureTalk0001")
        self.assertEqual(ev["source"], "luma")
        self.assertEqual(ev["name"], "Mind, AI and the Future of Human Memory")
        self.assertEqual(ev["url"], "https://luma.com/fixtalk1")
        self.assertEqual((ev["start_at"], ev["end_at"]), ("2026-09-30T02:00:00Z", "2026-09-30T04:00:00Z"))
        self.assertFalse(ev["all_day"])
        self.assertEqual(ev["timezone"], "America/Los_Angeles")
        self.assertEqual(ev["cover_url"], "https://images.lumacdn.com/uploads/fixture/image-1.png")
        self.assertEqual(ev["location"], {
            "type": "offline", "venue": "Fixture Hall", "address": "100 Fixture Ave, San Jose", "city": "San Jose",
            "neighborhood": "Downtown San Jose", "region": "CA", "country": "US",
            "lat": 37.3337, "lng": -121.8907,
        })
        self.assertEqual(ev["presenter"], {
            "id": CAL_ID, "name": "Fixture Brain Lectures - Bay Area",
            "avatar_url": "https://images.lumacdn.com/calendars/fixture/image-3.jpg",
            "url": "https://luma.com/Fixture-Brain-Bay", "description": "A lecture series held in fixture venues around the Bay.",
        })
        # The organisation-looking host leads even though Luma listed the person first.
        self.assertEqual([h["name"] for h in ev["hosts"]], ["Fixture Brain Bay Area", "Jordan Rivera"])
        self.assertEqual(ev["hosts"][1]["avatar_url"], "https://images.lumacdn.com/avatars/fixture/jordan.jpg")
        self.assertEqual(ev["tags"], ["All Ages", "San Jose"])
        self.assertEqual(ev["ticket"], {
            "free": False, "price_cents": 1500, "max_price_cents": None, "currency": "usd", "sold_out": True,
            "spots_left": 0, "approval": False, "waitlist": False, "availability": "sold-out",
        })
        self.assertEqual(ev["guest_count"], 64)
        self.assertEqual(ev["going_status"], "approved")

    def test_venue_repeating_the_street_is_dropped(self):
        ev = luma.normalize_entry(entries()[1])
        self.assertIsNone(ev["location"]["venue"])
        self.assertEqual(ev["location"]["address"], "500 Fixture St, San Francisco")
        self.assertEqual((ev["location"]["lat"], ev["location"]["lng"]), (37.7764, -122.4346),
                         "the event coordinate is used when place_coordinate is null")
        self.assertIsNone(ev["going_status"], "guest_info is null")
        self.assertEqual(ev["ticket"]["spots_left"], 101)
        self.assertFalse(ev["ticket"]["sold_out"])

    def test_online_entry_without_ticket_or_address(self):
        ev = luma.normalize_entry(entries()[2])
        self.assertEqual(ev["name"], "Intro to Evals (Online)")
        self.assertEqual(ev["url"], "https://luma.com/evt-fixtureOnline1", "falls back to the event id")
        self.assertEqual(ev["location"]["type"], "online")
        self.assertIsNone(ev["location"]["lat"])
        self.assertIsNone(ev["ticket"])
        self.assertIsNone(ev["guest_count"])
        self.assertIsNone(ev["end_at"])
        self.assertIsNone(ev["cover_url"])
        self.assertEqual(ev["hosts"], [{"name": "Sam Lee", "avatar_url": None}], "blank host names are dropped")
        self.assertIsNone(ev["going_status"], "'invited' is not an RSVP")
        self.assertIsNone(ev["presenter"]["description"])

    def test_waitlist_and_approval_flags(self):
        entry = entries()[2]
        entry["registration_availability"] = "waitlist"
        entry["ticket_info"] = {"is_free": True, "require_approval": True, "max_price": {"cents": 5000}}
        ticket = luma.normalize_entry(entry)["ticket"]
        self.assertTrue(ticket["waitlist"], "event.waitlist_status is active")
        self.assertTrue(ticket["approval"])
        self.assertTrue(ticket["free"])
        self.assertEqual(ticket["max_price_cents"], 5000)
        self.assertIsNone(ticket["price_cents"])

    def test_going_status_argument_is_a_fallback(self):
        self.assertEqual(luma.normalize_entry(entries()[1], going_status="registered")["going_status"], "registered")
        self.assertEqual(luma.normalize_entry(entries()[0], going_status="registered")["going_status"], "approved")
        for status in ("pending_approval", "waitlist", "going"):
            entry = entries()[1]
            entry["guest_info"] = {"approval_status": status}
            self.assertEqual(luma.normalize_entry(entry)["going_status"], status)

    def test_start_falls_back_to_the_entry(self):
        entry = entries()[1]
        del entry["event"]["start_at"]
        self.assertEqual(luma.normalize_entry(entry)["start_at"], "2026-10-08T02:00:00Z")

    def test_missing_or_invalid_entries_return_none(self):
        broken = entries()[3]
        no_start = entries()[0]
        no_start["event"]["start_at"] = None
        no_start["start_at"] = "soon"
        numeric_id = entries()[0]
        numeric_id["event"]["api_id"] = 42
        for entry in (broken, no_start, numeric_id, None, "evt-1", [], {}, {"event": "evt-1"}):
            with self.subTest(entry=str(entry)[:40]):
                self.assertIsNone(luma.normalize_entry(entry))

    def test_untitled_events_and_odd_types(self):
        entry = entries()[0]
        entry["event"]["name"] = "   "
        entry["guest_count"] = "103"
        entry["tags"] = [{"name": "  "}, "AI", {"name": "Robotics"}]
        entry["hosts"] = [{"name": "Weights & Biases", "first_name": "Sam", "last_name": "Lee"}, "Jordan", None]
        ev = luma.normalize_entry(entry)
        self.assertEqual(ev["name"], "Untitled event")
        self.assertIsNone(ev["guest_count"])
        self.assertEqual(ev["tags"], ["Robotics"])
        self.assertEqual([h["name"] for h in ev["hosts"]], ["Weights & Biases"])


class CalendarTest(unittest.TestCase):
    def test_normalize_calendar_bare_wrapped_and_personal(self):
        bare = entries()[0]["calendar"]
        self.assertEqual(luma.normalize_calendar(bare), {
            "id": CAL_ID, "source": "luma", "name": "Fixture Brain Lectures - Bay Area", "slug": "Fixture-Brain-Bay",
            "avatar_url": bare["avatar_url"], "tint_color": "#7a0000", "url": "https://luma.com/Fixture-Brain-Bay",
            "description": "A lecture series held in fixture venues around the Bay.",
        })
        self.assertEqual(luma.normalize_calendar({"calendar": bare, "follow": {}})["id"], CAL_ID)
        personal = luma.normalize_calendar({"calendar_api_id": "cal-personal00001", "name": "", "slug": "",
                                            "personal_user": {"name": "Jordan Rivera"}})
        self.assertEqual((personal["name"], personal["slug"], personal["url"]),
                         ("Jordan Rivera", None, "https://luma.com/cal-personal00001"))
        self.assertEqual(luma.normalize_calendar({"api_id": "cal-nameless00001"})["name"], "cal-nameless00001")

    def test_personal_calendars_take_the_owners_name(self):
        owner = {"api_id": "usr-1", "name": " Jordan  Rivera "}
        self.assertEqual(luma.normalize_calendar({"api_id": "cal-personal00002", "name": "Personal", "is_personal": True,
                                                  "personal_user": owner})["name"], "Jordan Rivera")
        self.assertEqual(luma.normalize_calendar({"api_id": "cal-team00000001", "name": "SF AI Club", "is_personal": False,
                                                  "personal_user": owner})["name"], "SF AI Club")
        self.assertEqual(luma.normalize_calendar({"api_id": "cal-personal00003", "name": "Personal", "is_personal": True,
                                                  "personal_user": {"name": ""}})["name"], "Personal")

    def test_normalize_calendar_rejects_non_calendars(self):
        for item in (None, "cal-x", [], {"api_id": "usr-123"}, {"api_id": 5}, {"calendar": {"api_id": "evt-1"}}):
            with self.subTest(item=item):
                self.assertIsNone(luma.normalize_calendar(item))

    def test_find_calendar_objects_walks_nested_json(self):
        found = []
        luma.find_calendar_objects({"entries": [{"calendar": {"api_id": "cal-a", "name": "A", "nested": {"api_id": "cal-b", "name": "B"}}},
                                                {"api_id": "cal-c"}, [{"x": {"api_id": "cal-d", "name": "D"}}]]}, found)
        self.assertEqual([c["api_id"] for c in found], ["cal-a", "cal-d"], "calendars are not searched inside, nameless ones skipped")

    def test_link_tokens(self):
        cases = {
            "https://luma.com/Fixture-Brain-Bay?utm_source=share": ["Fixture-Brain-Bay"],
            "see lu.ma/sf-ai-club and https://www.luma.com/frontier#events, also lu.ma/sf-ai-club": ["sf-ai-club", "frontier"],
            "cal-FixtureBrainBay": ["cal-FixtureBrainBay"],
            "http://lu.ma/genai-sf/": ["genai-sf"],
            "https://luma.com/user/someone": ["user/someone"],
            "an event: https://luma.com/studio-opening": ["studio-opening"],
            "https://luma.com/event/evt-PrivateEvent001?tk=abc": ["evt-PrivateEvent001"],
            "evt-PrivateEvent001 and luma.com/event/evt-PrivateEvent001": ["evt-PrivateEvent001"],
            "evt-short": [],
            "hello world": [],
            "cal-short": [],
            "https://luma.com/": [],
            "": [],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(luma.link_tokens(text), expected)
        self.assertEqual(luma.link_tokens(None), [])

    def test_parse_session_key(self):
        self.assertEqual(luma.parse_session_key("  abc.DEF-123  "), "abc.DEF-123")
        self.assertEqual(luma.parse_session_key("luma.auth-session-key=abc123;"), "abc123")
        for bad in ("", "   ", ";", "abc def", "luma.auth-session-key=", "x" * 4097, None):
            with self.subTest(bad=str(bad)[:20]):
                with self.assertRaises(ValueError):
                    luma.parse_session_key(bad)


class IcsUrlTest(unittest.TestCase):
    def test_accepts_raw_webcal_and_google_links(self):
        self.assertEqual(luma.normalize_ics_url(f"  {ICS_URL} "), ICS_URL)
        self.assertEqual(luma.normalize_ics_url("webcal://api.luma.com/ics/get?entity=user&id=usr-1"),
                         "https://api.luma.com/ics/get?entity=user&id=usr-1")
        google = ("https://calendar.google.com/calendar/u/0/r?cid="
                  "webcal%3A%2F%2Fapi.lu.ma%2Fics%2Fget%3Fentity%3Duser%26id%3Dusr-FixtureOrg00001&pli=1")
        self.assertEqual(luma.normalize_ics_url(google), ICS_URL)
        self.assertEqual(luma.normalize_ics_url("https://lu.ma/ics/get?entity=user&id=usr-1"),
                         "https://lu.ma/ics/get?entity=user&id=usr-1")

    def test_rejects_other_hosts_and_shapes(self):
        for url in ("", "https://evil.example/ics/get?host=lu.ma", "https://lu.ma.evil.example/ics/get?x=1",
                    "https://notluma.com/ics/get?x=1", "http://api.lu.ma/ics/get?entity=user&id=usr-1",
                    "https://api.lu.ma/calendar/get-items", "ftp://api.lu.ma/ics/get",
                    "https://calendar.google.com/calendar/r?cid=https%3A%2F%2Fevil.example%2Fics%2Fget"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    luma.normalize_ics_url(url)


class PersonalFeedTest(unittest.TestCase):
    FEED = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "BEGIN:VEVENT", "UID:evt-FixtureTalk0001@events.lu.ma",
        "DTSTART;TZID=America/Los_Angeles:20260929T190000", "DTEND;TZID=America/Los_Angeles:20260929T210000",
        r"SUMMARY:Mind\, AI and  the Future", "URL:https://luma.com/fixtalk1",
        r"LOCATION:Fixture Hall\, 100 Fixture Ave\, San Jose", "END:VEVENT",
        "BEGIN:VEVENT", "UID:0f9c2b@luma.com", "DTSTART:20261014T170000Z", "SUMMARY:Intro to Evals",
        "LOCATION:https://zoom.us/j/123", r"DESCRIPTION:Manage: https://luma.com/event/manage/evt-fixtureOnline1\n",
        "END:VEVENT",
        "BEGIN:VEVENT", "UID:odd uid/with?chars", "DTSTART;VALUE=DATE:20261101", "SUMMARY:", "END:VEVENT",
        "BEGIN:VEVENT", "UID:evt-nostart", "SUMMARY:No start", "END:VEVENT",
        "END:VCALENDAR",
    ])

    def test_parse_personal_feed(self):
        talk, online, allday = luma.parse_personal_feed(self.FEED)
        self.assertEqual(talk["id"], "evt-FixtureTalk0001")
        self.assertEqual(talk["name"], "Mind, AI and the Future")
        self.assertEqual(talk["url"], "https://luma.com/fixtalk1")
        self.assertEqual((talk["start_at"], talk["end_at"], talk["all_day"]), ("2026-09-30T02:00:00Z", "2026-09-30T04:00:00Z", False))
        self.assertEqual(talk["timezone"], "America/Los_Angeles")
        self.assertEqual(talk["location"]["type"], "offline")
        self.assertEqual(talk["location"]["address"], "Fixture Hall, 100 Fixture Ave, San Jose")
        self.assertEqual(talk["going_status"], "registered")

        self.assertEqual(online["id"], "evt-fixtureOnline1", "the id is found in the description")
        self.assertEqual(online["location"]["type"], "online")
        self.assertIsNone(online["location"]["address"])
        self.assertEqual(online["url"], "https://luma.com/evt-fixtureOnline1")

        self.assertEqual(allday["id"], "luma-ics-odduidwithchars")
        self.assertEqual(allday["name"], "Untitled event")
        self.assertEqual(allday["url"], "https://luma.com/home")
        self.assertTrue(allday["all_day"])
        self.assertEqual(allday["location"]["type"], "unknown")

    def test_rejects_non_calendars(self):
        with self.assertRaises(ValueError):
            luma.parse_personal_feed("<html>Sign in</html>")


class ImportPayloadTest(unittest.TestCase):
    def test_bookmarklet_payload(self):
        cal = entries()[0]["calendar"]
        payload = {"calendars": [{"calendar": cal}, {"calendar": dict(cal)}, {"calendar": {"api_id": "cal-other0000001", "name": "Other"}}],
                   "going": ["evt-1", "usr-2", 3, "evt-4"], "session_key": "  sess-abc "}
        result = luma.parse_import_payload(payload)
        self.assertEqual([c["id"] for c in result.calendars], [CAL_ID, "cal-other0000001"])
        self.assertEqual(result.going, ["evt-1", "evt-4"])
        self.assertEqual(result.session_key, "sess-abc")

    def test_raw_list_and_raw_following_response(self):
        cal = entries()[0]["calendar"]
        self.assertEqual([c["id"] for c in luma.parse_import_payload([{"calendar": cal}]).calendars], [CAL_ID])
        raw = luma.parse_import_payload({"entries": [{"calendar": cal}], "has_more": False, "session_key": "   "})
        self.assertEqual([c["id"] for c in raw.calendars], [CAL_ID])
        self.assertEqual((raw.going, raw.session_key), ([], None))

    def test_rejects_non_json(self):
        for payload in ("eyJjYWxlbmRhcnMiOltdfQ==", None, 5):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    luma.parse_import_payload(payload)
        self.assertEqual(luma.parse_import_payload({"calendars": []}).calendars, [])


class LumaClientTest(unittest.TestCase):
    def client(self, handler) -> tuple[LumaClient, FakeRequest]:
        fake = FakeRequest(handler)
        return LumaClient(fake), fake

    def test_call_sends_cookie_and_json_headers(self):
        client, fake = self.client(lambda url, **kw: response({"ok": True}))
        self.assertEqual(client.call("/home/get-events", {"period": "future"}, session_key="sess-1"), {"ok": True})
        url, kwargs = fake.calls[0]
        self.assertEqual(url, "https://api.luma.com/home/get-events")
        self.assertEqual(kwargs["method"], "GET")
        self.assertEqual(kwargs["params"], {"period": "future"})
        self.assertEqual(kwargs["headers"]["cookie"], "luma.auth-session-key=sess-1")
        self.assertEqual(kwargs["headers"]["accept"], "application/json")
        client.call("/auth/sign-out", body={"x": 1})
        self.assertEqual(fake.calls[1][1]["method"], "POST")
        self.assertNotIn("cookie", fake.calls[1][1]["headers"])

    def test_call_maps_http_errors(self):
        cases = [(429, '{"message": "Too many requests"}', "rate_limited", "Too many requests"),
                 (401, '{"error": "not signed in"}', "unauthorized", "not signed in"),
                 (500, "<html>oops</html>", None, "<html>oops</html>"),
                 (502, "", None, "no details")]
        for status, body, flag, message in cases:
            with self.subTest(status=status):
                client, _ = self.client(lambda url, **kw: net.HttpError(status, body, url))
                with self.assertRaises(LumaError) as ctx:
                    client.call("/calendar/get-items")
                self.assertEqual(ctx.exception.status, status)
                self.assertEqual(ctx.exception.message, message)
                self.assertEqual(ctx.exception.rate_limited, flag == "rate_limited")
                self.assertEqual(ctx.exception.unauthorized, flag == "unauthorized")

    def test_call_maps_network_errors_and_bad_json(self):
        client, _ = self.client(lambda url, **kw: net.NetError("could not reach api.luma.com: timed out"))
        with self.assertRaises(LumaError) as ctx:
            client.call("/x")
        self.assertIsNone(ctx.exception.status)
        self.assertIn("unreachable", str(ctx.exception))
        client, _ = self.client(lambda url, **kw: response("<html>maintenance</html>", content_type="text/html"))
        with self.assertRaises(LumaError) as ctx:
            client.call("/x")
        self.assertEqual(ctx.exception.status, 200)

    def test_call_wraps_lists_and_accepts_empty_bodies(self):
        client, _ = self.client(lambda url, **kw: response([{"a": 1}]))
        self.assertEqual(client.call("/x"), {"entries": [{"a": 1}]})
        client, _ = self.client(lambda url, **kw: response(b""))
        self.assertEqual(client.call("/x"), {})

    def test_calendar_events_follow_next_cursor(self):
        page1 = fixture("luma_get_items.json")
        page2 = {"entries": [entries()[1] | {"event": entries()[1]["event"] | {"api_id": "evt-page2"}}],
                 "has_more": False, "next_cursor": None}

        def handler(url, **kw):
            return response(page2 if kw["params"].get("pagination_cursor") == page1["next_cursor"] else page1)

        client, fake = self.client(handler)
        events = client.calendar_events(CAL_ID)
        self.assertEqual([e["id"] for e in events],
                         ["evt-FixtureTalk0001", "evt-FixtureTalk0002", "evt-fixtureOnline1", "evt-page2"])
        first, second = (kw["params"] for _, kw in fake.calls)
        self.assertEqual(first, {"calendar_api_id": CAL_ID, "period": "future", "pagination_limit": 50})
        self.assertEqual(second["pagination_cursor"], page1["next_cursor"])
        self.assertTrue(all(url.endswith("/calendar/get-items") for url, _ in fake.calls))

    def test_pagination_stops_without_cursor_and_at_max_pages(self):
        client, fake = self.client(lambda url, **kw: response({"entries": [{"n": 1}], "has_more": True, "next_cursor": None}))
        self.assertEqual(len(client.paginated("/x", {})), 1)
        self.assertEqual(len(fake.calls), 1)
        client, fake = self.client(lambda url, **kw: response({"entries": [{"n": 1}, "junk"], "has_more": True, "next_cursor": "again"}))
        self.assertEqual(len(client.paginated("/x", {}, max_pages=3)), 3)
        self.assertEqual(len(fake.calls), 3)

    def test_my_events_mark_registrations(self):
        client, fake = self.client(lambda url, **kw: response({"entries": entries()[:2], "has_more": False}))
        events = client.my_events("sess-1")
        self.assertEqual([e["going_status"] for e in events], ["approved", "registered"])
        self.assertTrue(fake.calls[0][0].endswith("/home/get-events"))
        self.assertEqual(fake.calls[0][1]["headers"]["cookie"], "luma.auth-session-key=sess-1")

    def test_following_collects_calendars_across_pages(self):
        cal = entries()[0]["calendar"]
        pages = {None: {"entries": [{"calendar": cal}, {"calendar": {"api_id": "cal-two000000001", "name": "Two"}}],
                        "has_more": True, "next_cursor": "p2"},
                 "p2": {"entries": [{"calendar": dict(cal)}, {"calendar": {"api_id": "cal-three00000001", "name": "Three"}}],
                        "has_more": False}}
        client, fake = self.client(lambda url, **kw: response(pages[kw["params"].get("pagination_cursor")]))
        self.assertEqual([c["id"] for c in client.following("sess-1")], [CAL_ID, "cal-two000000001", "cal-three00000001"])
        self.assertEqual(fake.calls[0][1]["params"], {"pagination_limit": 100})

    def test_check_session_raises_for_rejected_keys(self):
        client, fake = self.client(lambda url, **kw: net.HttpError(401, "{}", url))
        with self.assertRaises(LumaError):
            client.check_session("stale")
        self.assertEqual(fake.calls[0][1]["params"], {"period": "future", "pagination_limit": 1})

    def test_resolve_calendar_by_id(self):
        client, fake = self.client(lambda url, **kw: response({"entries": entries()[:1], "has_more": True, "next_cursor": "c"}))
        self.assertEqual(client.resolve_calendar(CAL_ID)["slug"], "Fixture-Brain-Bay")
        self.assertEqual(len(fake.calls), 1, "one page of one item is enough")
        self.assertEqual(fake.calls[0][1]["params"]["pagination_limit"], 1)
        client, _ = self.client(lambda url, **kw: response({"entries": []}))
        self.assertEqual(client.resolve_calendar("cal-empty0000000001"),
                         {"id": "cal-empty0000000001", "source": "luma", "name": "cal-empty0000000001", "slug": None,
                          "avatar_url": None, "tint_color": None, "url": "https://luma.com/cal-empty0000000001",
                          "description": None})

    def test_resolve_calendar_by_slug_reads_next_data(self):
        cal = entries()[0]["calendar"]
        page = {"props": {"pageProps": {"initialData": {"data": {
            "featured": {"api_id": "cal-featured000001", "name": "Featured", "slug": "featured"},
            "calendar": cal}}}}}
        html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(page)}</script></html>'
        client, fake = self.client(lambda url, **kw: response(html, content_type="text/html; charset=utf-8"))
        self.assertEqual(client.resolve_calendar("Fixture-Brain-Bay")["id"], CAL_ID, "the slug match wins over the first one")
        self.assertEqual(fake.calls[0][0], "https://luma.com/Fixture-Brain-Bay")

    def test_resolve_calendar_errors(self):
        client, fake = self.client(lambda url, **kw: response("{}"))
        for token in ("user/jordan", "discover", "e/abc", "Home"):
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValueError, "not a calendar or event link"):
                    client.resolve_calendar(token)
        self.assertEqual(fake.calls, [])
        cases = [(net.HttpError(404, "", "https://luma.com/nope"), "no page there"),
                 (net.HttpError(503, "", "https://luma.com/nope"), "503"),
                 (net.NetError("could not reach luma.com"), "could not reach"),
                 (response("<html>no data</html>", content_type="text/html"), "no page data"),
                 (response('<script id="__NEXT_DATA__" type="application/json">{not json</script>', content_type="text/html"),
                  "unreadable"),
                 (response('<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>', content_type="text/html"),
                  "not a Luma calendar")]
        for answer, message in cases:
            with self.subTest(message=message):
                client, _ = self.client(lambda url, **kw: answer)
                with self.assertRaisesRegex(ValueError, message):
                    client.resolve_calendar("some-slug")

    def test_event_by_id_includes_private_events(self):
        entry = dict(entries()[0])
        entry["event"] = dict(entry["event"], visibility="private")
        client, fake = self.client(lambda url, **kw: response(entry))
        ev = client.event(entry["event"]["api_id"], "sess-1")
        self.assertEqual(ev["id"], entry["event"]["api_id"])
        url, kwargs = fake.calls[0]
        self.assertEqual((url, kwargs["params"]), ("https://api.luma.com/event/get", {"event_api_id": ev["id"]}))
        self.assertEqual(kwargs["headers"]["cookie"], "luma.auth-session-key=sess-1")
        for answer in ({}, {"event": {"api_id": "evt-someoneelse01", "start_at": "2026-11-20T16:00:00.000Z"}}):
            with self.subTest(answer=answer):
                client, _ = self.client(lambda url, **kw: response(answer))
                with self.assertRaisesRegex(LumaError, "no event"):
                    client.event(entry["event"]["api_id"])

    def test_resolve_link_tells_events_from_calendars(self):
        entry = entries()[0]
        event_page = {"props": {"pageProps": {"initialData": {"kind": "event", "data": entry}}}}
        calendar_page = {"props": {"pageProps": {"initialData": {"kind": "calendar", "data": {"calendar": entry["calendar"]}}}}}
        pages = {f"https://luma.com/{slug}": f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(page)}</script>'
                 for slug, page in (("an-event", event_page), ("Fixture-Brain-Bay", calendar_page))}
        client, fake = self.client(lambda url, **kw: response(pages[url], content_type="text/html")
                                   if url in pages else response(entry))
        kind, found = client.resolve_link("an-event")
        self.assertEqual((kind, found["id"]), ("event", entry["event"]["api_id"]))
        # The event's own calendar is on its page too, but a calendar is only what a calendar page names.
        with self.assertRaisesRegex(ValueError, "an event, not a calendar"):
            client.resolve_calendar("an-event")
        self.assertEqual(client.resolve_link("Fixture-Brain-Bay"), ("calendar", client.resolve_calendar("Fixture-Brain-Bay")))
        # An evt- id needs no page: the API answers for it directly.
        fake.calls.clear()
        kind, found = client.resolve_link(entry["event"]["api_id"])
        self.assertEqual((kind, found["id"]), ("event", entry["event"]["api_id"]))
        self.assertEqual([c[0] for c in fake.calls], ["https://api.luma.com/event/get"])
        fake.calls.clear()
        with self.assertRaisesRegex(ValueError, "an event, not a calendar"):
            client.resolve_calendar(entry["event"]["api_id"])
        self.assertEqual(fake.calls, [], "a calendar lookup never fetches an event")

    def test_personal_feed(self):
        client, fake = self.client(lambda url, **kw: response(PersonalFeedTest.FEED, content_type="text/calendar"))
        self.assertEqual(len(client.personal_feed(ICS_URL)), 3)
        self.assertEqual(fake.calls[0][0], ICS_URL)
        client, _ = self.client(lambda url, **kw: net.HttpError(403, "denied", url))
        with self.assertRaisesRegex(ValueError, "could not read the feed"):
            client.personal_feed(ICS_URL)


if __name__ == "__main__":
    unittest.main()

"""Guards added after review: upstream shape changes must not wipe data, and odd input stays harmless."""
import unittest

from events import areas, categorize, ics
from events.catalog import Catalog
from events.config import Settings
from events.sources import luma
from events.sources.luma import LumaClient, LumaError
from events.store import HISTORY_KEEP_DAYS
from events.sync import NEW, Sync

from .helpers import DAY, HOUR, FakeAgiHouse, FakeClock, FakeLuma, FakePartiful, FakeRequest, add_feed, iso, listing, \
    make_settings, make_store, response, serve_raw, temp_dir
from .test_web import WebTestCase


class UpstreamShapeTest(unittest.TestCase):
    def test_a_luma_page_without_entries_is_an_error_not_an_empty_calendar(self):
        client = LumaClient(FakeRequest(lambda url, **kw: response({"items": [], "has_more": False})))
        with self.assertRaises(LumaError):
            client.calendar_events("cal-aaaaaaaaaaaa")

    def test_an_empty_entries_list_is_a_calendar_with_nothing_upcoming(self):
        client = LumaClient(FakeRequest(lambda url, **kw: response({"entries": [], "has_more": False})))
        self.assertEqual(client.calendar_events("cal-aaaaaaaaaaaa"), [])

    def test_an_empty_followed_list_keeps_existing_follows(self):
        clock = FakeClock()
        store = make_store(self, clock)
        fake = FakeLuma()
        sync = Sync(store, make_settings(temp_dir(self)), luma=fake, partiful=FakePartiful(), agihouse=FakeAgiHouse(), clock=clock)
        store.set_secret("luma_session", {"session_key": "k"})
        store.set_origin_calendars("followed", [
            {"id": f"cal-{c * 12}", "source": "luma", "name": c, "url": None} for c in "abc"])
        sync.reconcile()
        fake.following_result = []
        with self.assertRaises(ValueError):
            sync._run(store.feed("luma:following"), NEW)
        self.assertEqual(len([c for c in store.calendars() if "followed" in c["origins"]]), 3)


class HistoryWindowTest(unittest.TestCase):
    def test_events_older_than_the_history_window_are_not_stored(self):
        clock = FakeClock()
        store = make_store(self, clock)
        key = add_feed(store, "luma:ics", calendar_id="luma-mine", kind="luma-ics", origin="builtin")
        old = listing("evt-old", start=iso(-(HISTORY_KEEP_DAYS + 5) * 24), end=iso(-(HISTORY_KEEP_DAYS + 5) * 24 + 2))
        recent = listing("evt-recent", start=iso(-48), end=iso(-46))
        store.replace_listings(key, [old, recent, listing("evt-next")])
        ids = {row["event_id"] for row in store.listings(iso(-(HISTORY_KEEP_DAYS + 10) * 24))}
        self.assertEqual(ids, {"evt-recent", "evt-next"})
        # Pulling the same feed again changes nothing, so clients are not told to refetch.
        version = store.data_version()
        self.assertFalse(store.replace_listings(key, [old, recent, listing("evt-next")]).changed)
        self.assertEqual(store.data_version(), version)


class CatalogMarksTest(unittest.TestCase):
    def test_a_star_on_a_folded_duplicate_carries_over(self):
        clock = FakeClock()
        store = make_store(self, clock)
        luma_key = add_feed(store, "luma:cal-a", calendar_id="cal-a")
        agi_key = add_feed(store, "agihouse", calendar_id="agihouse", kind="agihouse", source="agihouse", origin="builtin")
        store.replace_listings(luma_key, [listing("evt-1", name="Agent Hack Night", start=iso(30))])
        store.replace_listings(agi_key, [listing("agi-1", name="Agent Hack Night", source="agihouse", start=iso(30))])
        store.set_mark("agi-1", starred=True)
        events = Catalog(store, clock=clock).build()["events"]
        self.assertEqual([(e["id"], e["starred"]) for e in events], [("evt-1", True)])

    def test_the_full_export_leaves_out_muted_calendars(self):
        clock = FakeClock()
        store = make_store(self, clock)
        store.replace_listings(add_feed(store, "luma:cal-a", calendar_id="cal-a"), [listing("evt-a", start=iso(5))])
        store.replace_listings(add_feed(store, "luma:cal-b", calendar_id="cal-b"), [listing("evt-b", start=iso(6))])
        store.set_pref("muted_calendars", ["cal-b"])
        ids = [e["id"] for e in Catalog(store, clock=clock).events_for_feed(starred_or_going=False)]
        self.assertEqual(ids, ["evt-a"])


class PlaceholderRefinementTest(unittest.TestCase):
    def test_real_events_that_look_like_holds_are_kept(self):
        for title in ("Test Kitchen Pop-up Dinner", "TBA: Founders Dinner", "Reserved: VIP night", "Hold Space Meditation",
                      "Launch details to come", "Private Event: Founders Circle"):
            with self.subTest(title=title):
                self.assertFalse(categorize.is_placeholder({"name": title}))


class SmallEdgeCasesTest(unittest.TestCase):
    def test_quoted_parameters_may_contain_colons_and_semicolons(self):
        text = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-1@lu.ma\r\n'
                'ORGANIZER;CN="Ada: Host; Co";SENT-BY="mailto:a@b.c":mailto:x@y.z\r\n'
                'DTSTART;TZID=America/Los_Angeles:20261002T190000\r\nSUMMARY:Talk: agents\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
        ev = ics.parse(text)[0]
        self.assertEqual(ev["ORGANIZER"], "mailto:x@y.z")
        self.assertEqual(ev["ORGANIZER__params"], {"CN": "Ada: Host; Co", "SENT-BY": "mailto:a@b.c"})
        self.assertEqual(ev["SUMMARY"], "Talk: agents")

    def test_management_links_resolve_to_their_calendar_id(self):
        self.assertEqual(luma.calendar_tokens("https://lu.ma/calendar/manage/cal-AbCdEfGhIjKl12/events"), ["cal-AbCdEfGhIjKl12"])

    def test_utc_says_nothing_about_where_an_event_is(self):
        ev = {"location": {"type": "offline", "address": "2 Embarcadero Center, San Francisco"}, "timezone": "UTC", "name": "x"}
        self.assertEqual(areas.classify(ev), ("bay", "sf"))

    def test_sync_can_be_switched_off_in_words(self):
        for value in ("0", "false", "No", " off "):
            with self.subTest(value=value):
                self.assertFalse(Settings.from_env({"EVENTS_SYNC": value, "HOME": "/home/me"}).sync_enabled)
        self.assertTrue(Settings.from_env({"HOME": "/home/me"}).sync_enabled)


class HandlerHardeningTest(WebTestCase):
    def raw(self, head: str, body: bytes = b"") -> tuple:
        return serve_raw(self.app, head.replace("\n", "\r\n").encode() + body)

    def test_a_bad_content_length_is_refused(self):
        for value in ("-1", "lots"):
            with self.subTest(value=value):
                status, _, _ = self.raw(f"POST /api/sync HTTP/1.1\nHost: localhost\nContent-Length: {value}\n\n")
                self.assertEqual(status, 400)

    def test_only_real_hashed_assets_are_cached_for_a_year(self):
        self.assertIn("immutable", self.call("GET", "/assets/app.js").headers["Cache-Control"])
        # An extensionless path under /assets/ falls back to the app shell, which must stay revalidated.
        shell = self.call("GET", "/assets/foo")
        self.assertEqual(shell.status, 200)
        self.assertEqual(shell.headers["Cache-Control"], "no-cache")

    def test_partiful_uids_with_colons_are_refused(self):
        self.partiful.token_result = {"uid": "a:b", "refresh_token": "r", "id_token": "t", "expires_at": 9e9}
        resp = self.call("POST", "/api/partiful/import", {"payload": {"uid": "a:b", "refresh_token": "r"}})
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()

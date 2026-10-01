import json
import os
import stat
import unittest

from events import ics, legacy
from events.catalog import Catalog
from events.sync import Sync
from events.timeutil import to_iso

from .helpers import (HOUR, NOW, FakeAgiHouse, FakeClock, FakeLuma, FakePartiful, iso, jwt, make_settings, make_store,
                      temp_dir)

CAL = "cal-FixtureBrainBay"
FETCHED = NOW - HOUR


def old_event(api_id: str, **fields) -> dict:
    """An event as the first version stored it in cache.json (flat, one record per event)."""
    ev = {"api_id": api_id, "name": "Mind, AI and the Future of Human Memory", "url": "https://luma.com/fixtalk1",
          "start_at": "2026-09-30T02:00:00.000Z", "end_at": "2026-09-30T04:00:00.000Z", "timezone": "America/Los_Angeles",
          "cover_url": "https://images.lumacdn.com/uploads/y3/cover.png", "location_type": "offline",
          "city": "San Jose", "region": "CA", "country": "US", "address": "100 Fixture Ave, San Jose",
          "lat": 37.3337, "lng": -121.8907, "hosts": ["Fixture Brain Bay Area", "Jordan Rivera", ""],
          "host_avatars": ["https://images.lumacdn.com/avatars/bbba.jpg", ""], "calendar_api_id": CAL,
          "going": True, "guest_status": "approved", "source": "luma", "area": "bay",
          "presented_by": {"api_id": CAL, "name": "Fixture Brain Lectures - Bay Area", "avatar_url": "https://images.lumacdn.com/c.jpg"}}
    ev.update(fields)
    return ev


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)
        self.data_dir = self.root / "data"
        self.clock = FakeClock()
        self.store = make_store(self, self.clock)
        self.sync = Sync(self.store, make_settings(self.root), luma=FakeLuma(), partiful=FakePartiful(),
                         agihouse=FakeAgiHouse(), clock=self.clock)

    def write(self, name: str, data: object) -> None:
        (self.root / name).write_text(data if isinstance(data, str) else json.dumps(data))

    def migrate(self) -> bool:
        return legacy.migrate(self.store, self.sync, self.root, self.data_dir)

    def write_everything(self) -> None:
        self.write(".session.json", {"session_key": "sess-legacy", "email": "jordan@example.com"})
        self.write(".luma_calendars.json", [
            {"api_id": CAL, "name": "Fixture Brain Lectures - Bay Area", "slug": "Fixture-Brain-Bay", "avatar_url": "https://a/c.jpg",
             "tint_color": "#7a0000", "url": "https://luma.com/Fixture-Brain-Bay", "manual": True},
            {"api_id": "cal-quiet00000001", "name": "", "slug": None, "avatar_url": None, "tint_color": None, "url": None},
            {"api_id": "usr-notacalendar", "name": "Someone"}, "junk"])
        self.write(".luma_going.json", ["evt-FixtureTalk0001", "usr-x", 5])
        self.write(".luma_ics.json", {"url": "https://api.lu.ma/ics/get?entity=user&id=usr-1"})
        self.write(".partiful.json", {"url": "https://calendars.partiful.com/getCalendar?id=abc",
                                      "saved_at": "2026-09-01T00:00:00+00:00"})
        self.write(".partiful_auth.json", {"accounts": [
            {"uid": "pf-user-1", "name": "Jordan", "refresh_token": "r1", "id_token": "t1", "expires_at": 1790000000},
            {"refresh_token": "r2", "id_token": jwt({"user_id": "pf-user-2", "name": "Sam"})},
            {"name": "no token"}, {"refresh_token": "r3"}]})
        self.write("partiful_debug.json", {"pf-user-1": {"getMyFollowedEvents": []}})
        self.write("cache.json", {"fetched_at": to_iso(FETCHED), "errors": [], "calendars": [], "events": [
            old_event("evt-FixtureTalk0001"),
            old_event("evt-mine1", calendar_api_id="luma-mine"),
            old_event("evt-unknown", calendar_api_id="cal-unknown0000001"),
            old_event("evt-nostart", start_at=None),
            old_event("agi-demo-day", source="agihouse", calendar_api_id="agihouse"),
            old_event("pf-AbC123", source="partiful", calendar_api_id="partiful"),
            "junk"]})

    def test_imports_everything_once(self):
        self.write_everything()
        self.assertTrue(self.migrate())

        self.assertEqual(self.store.get_secret("luma_session"), {"session_key": "sess-legacy", "via": "legacy"})
        self.assertEqual(self.store.get_secret("luma_ics"), {"url": "https://api.lu.ma/ics/get?entity=user&id=usr-1"})
        self.assertEqual(self.store.get_secret("partiful_feed"), {"url": "https://calendars.partiful.com/getCalendar?id=abc"})
        accounts = {a["uid"]: a for a in self.store.partiful_accounts()}
        self.assertEqual(set(accounts), {"pf-user-1", "pf-user-2"}, "uids come from the account or its ID token")
        self.assertEqual((accounts["pf-user-1"]["name"], accounts["pf-user-1"]["expires_at"]), ("Jordan", 1790000000))
        self.assertEqual(accounts["pf-user-2"]["refresh_token"], "r2")

        calendars = {c["id"]: c for c in self.store.calendars()}
        self.assertEqual(set(calendars), {CAL, "cal-quiet00000001", "luma-mine", "partiful", "partiful-following", "agihouse"})
        self.assertEqual(calendars[CAL]["origins"], ["import"])
        self.assertEqual((calendars[CAL]["slug"], calendars[CAL]["tint_color"]), ("Fixture-Brain-Bay", "#7a0000"))
        self.assertEqual(calendars["cal-quiet00000001"]["name"], "cal-quiet00000001")
        self.assertEqual(calendars["cal-quiet00000001"]["url"], "https://luma.com/cal-quiet00000001")
        self.assertEqual(self.store.going(), {"evt-FixtureTalk0001": "registered"})
        self.assertEqual({f.key for f in self.store.feeds()}, {
            f"luma:{CAL}", "luma:cal-quiet00000001", "luma:following", "luma:mine", "luma:ics", "partiful:pf-user-1:mine",
            "partiful:pf-user-1:following", "partiful:pf-user-2:mine", "partiful:pf-user-2:following", "partiful:feed",
            "agihouse"})

    def test_cache_seeds_luma_calendar_listings_at_the_cache_time(self):
        self.write_everything()
        self.migrate()
        rows = self.store.listings(iso(-24))
        self.assertEqual([(r["event_id"], r["feed_key"]) for r in rows], [("evt-FixtureTalk0001", f"luma:{CAL}")])
        ev = rows[0]["data"]
        self.assertEqual((ev["start_at"], ev["end_at"]), ("2026-09-30T02:00:00Z", "2026-09-30T04:00:00Z"))
        self.assertEqual(ev["going_status"], "approved")
        self.assertEqual(ev["hosts"], [{"name": "Fixture Brain Bay Area", "avatar_url": "https://images.lumacdn.com/avatars/bbba.jpg"},
                                       {"name": "Jordan Rivera", "avatar_url": None}])
        self.assertEqual(ev["presenter"], {"id": CAL, "name": "Fixture Brain Lectures - Bay Area",
                                           "avatar_url": "https://images.lumacdn.com/c.jpg", "url": None, "description": None})
        self.assertEqual(ev["location"], {"type": "offline", "venue": None, "address": "100 Fixture Ave, San Jose",
                                          "city": "San Jose", "neighborhood": None, "region": "CA", "country": "US",
                                          "lat": 37.3337, "lng": -121.8907})
        self.assertEqual((rows[0]["first_seen_at"], rows[0]["announced_at"]), (FETCHED, None), "old events are not news")

        for key in (f"luma:{CAL}", "luma:cal-quiet00000001"):
            with self.subTest(feed=key):
                feed = self.store.feed(key)
                self.assertEqual(feed.last_ok_at, FETCHED, "every Luma calendar counts as pulled when the cache was written")
                self.assertFalse(self.sync.is_due(feed, NOW))
        for key in ("agihouse", "partiful:feed", "luma:mine", "luma:ics"):
            with self.subTest(feed=key):
                self.assertIsNone(self.store.feed(key).last_ok_at, "other sources re-sync from scratch")
        self.assertIs(self.store.clock, self.clock, "the store clock is restored")

        catalog = {e["id"]: e for e in Catalog(self.store, clock=self.clock).build()["events"]}
        self.assertTrue(catalog["evt-FixtureTalk0001"]["going"])

    def test_files_move_to_data_legacy_and_the_second_run_is_a_no_op(self):
        self.write_everything()
        self.migrate()
        legacy_dir = self.data_dir / "legacy"
        for name in legacy.FILES.values():
            with self.subTest(name=name):
                self.assertFalse((self.root / name).exists())
                self.assertTrue((legacy_dir / name).is_file())
        self.assertEqual(stat.S_IMODE(os.stat(legacy_dir).st_mode), 0o700)
        self.assertEqual(self.store.get_meta("legacy_migrated_at"), str(NOW))

        version = self.store.data_version()
        self.write(".session.json", {"session_key": "newer"})
        self.assertFalse(self.migrate())
        self.assertEqual(self.store.get_secret("luma_session")["session_key"], "sess-legacy")
        self.assertTrue((self.root / ".session.json").exists(), "files are not touched after the import")
        self.assertEqual(self.store.data_version(), version)

    def test_nothing_to_import(self):
        self.assertFalse(self.migrate())
        self.assertEqual(self.store.get_meta("legacy_migrated_at"), str(NOW))
        self.assertFalse((self.data_dir / "legacy").exists())
        self.write(".session.json", {"session_key": "late"})
        self.assertFalse(self.migrate(), "the check happens once")
        self.assertIsNone(self.store.get_secret("luma_session"))

    def test_single_login_format_and_unreadable_files(self):
        self.write(".partiful_auth.json", {"uid": "pf-solo", "refresh_token": "r", "id_token": None, "name": "Solo"})
        self.write(".session.json", "{not json")
        self.write(".luma_going.json", {"evt-1": True})
        self.write("cache.json", {"events": [old_event("evt-1")]})
        self.assertTrue(self.migrate())
        self.assertEqual([(a["uid"], a["name"]) for a in self.store.partiful_accounts()], [("pf-solo", "Solo")])
        self.assertIsNone(self.store.get_secret("luma_session"))
        self.assertEqual(self.store.going(), {})
        self.assertEqual(self.store.listings(iso(-24)), [], "no calendars, so no cached listings")
        self.assertTrue((self.data_dir / "legacy" / ".session.json").is_file(), "unreadable files are still moved")

    def test_cache_without_a_timestamp_counts_as_pulled_now(self):
        self.write(".luma_calendars.json", [{"api_id": CAL, "name": "Fixture Brain"}])
        self.write("cache.json", {"events": [old_event("evt-FixtureTalk0001")]})
        self.migrate()
        self.assertEqual(self.store.feed(f"luma:{CAL}").last_ok_at, NOW)

    def test_a_lone_surrogate_in_an_old_calendar_name_does_not_stop_the_import(self):
        self.write(".luma_calendars.json", '[{"api_id": "%s", "name": "Fixture Brain \\ud83c"}]' % CAL)
        self.assertTrue(self.migrate())
        self.assertTrue(self.store.calendar(CAL)["name"].startswith("Fixture Brain"))

    def test_imported_text_is_cleaned_like_upstream_text(self):
        self.write(".luma_calendars.json", [{"api_id": CAL, "name": "Fixture Brain"}])
        self.write("cache.json", '{"fetched_at": "%s", "events": [%s]}' % (
            to_iso(FETCHED), json.dumps(old_event("evt-1")).replace("Mind, AI", "Launch party \\ud83c")))
        self.migrate()
        events = Catalog(self.store, clock=self.clock).events_for_feed(starred_or_going=False)
        self.assertIn("Launch party", ics.build(events, name="All", now_iso=iso(0)))


if __name__ == "__main__":
    unittest.main()

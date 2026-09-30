"""These tests cover deployment-time configuration: calendars named in EVENTS_LUMA_CALENDARS, turning
AGI House off, the database's old name, and calendar details filled in from pulls."""
import json
import unittest

from events.app import adopt_old_database
from events.sources.luma import LumaError
from events.sync import CONFIG_FEED, NEW, RateLimited, Sync

from .helpers import FakeAgiHouse, FakeClock, FakeLuma, FakePartiful, calendar, listing, make_settings, make_store, temp_dir
from .test_web import WebTestCase

A, B = "cal-aaaaaaaaaaaa", "cal-bbbbbbbbbbbb"


class ConfigTestCase(unittest.TestCase):
    def make_sync(self, **env: str) -> Sync:
        self.luma, self.partiful, self.agihouse = FakeLuma(), FakePartiful(), FakeAgiHouse()
        return Sync(self.store, make_settings(self.root, **env), luma=self.luma, partiful=self.partiful,
                    agihouse=self.agihouse, clock=self.clock)

    def setUp(self):
        self.clock = FakeClock()
        self.store = make_store(self, self.clock)
        self.root = temp_dir(self)

    def config_calendars(self) -> dict[str, str]:
        return {c["id"]: c["name"] for c in self.store.calendars() if "config" in c["origins"]}


class ConfiguredCalendarsTest(ConfigTestCase):
    def test_ids_are_followed_even_offline_and_named_later(self):
        sync = self.make_sync(EVENTS_LUMA_CALENDARS=f"{A} https://luma.com/thecommons")
        self.assertIn(CONFIG_FEED, sync.reconcile())
        self.luma.resolved = {A: LumaError(None, "could not reach api.luma.com"), "thecommons": calendar(B, "The Commons")}
        sync._run(self.store.feed(CONFIG_FEED), NEW)
        self.assertEqual(self.config_calendars(), {A: A, B: "The Commons"})
        self.assertIn(f"luma:{A}", [f.key for f in self.store.feeds()])
        self.assertIsNotNone(self.store.feed(CONFIG_FEED).last_ok_at)

        # A's first pull brings its own name and picture with its events.
        self.luma.events = {A: [listing("evt-1", presenter={"id": A, "name": "Alpha Club", "avatar_url": "https://img/a",
                                                                 "url": "https://luma.com/alpha", "description": "Hikes"})]}
        sync._run(self.store.feed(f"luma:{A}"), NEW)
        self.assertEqual(self.store.calendar(A)["name"], "Alpha Club")
        self.assertEqual(self.store.calendar(A)["origins"], ["config"])

    def test_a_link_that_cannot_be_resolved_is_retried_and_the_rest_apply(self):
        sync = self.make_sync(EVENTS_LUMA_CALENDARS=f"{A},https://luma.com/missing")
        sync.reconcile()
        self.luma.resolved = {A: calendar(A, "Alpha")}
        with self.assertRaises(ValueError) as caught:
            sync._run(self.store.feed(CONFIG_FEED), NEW)
        self.assertIn("missing", str(caught.exception))
        self.assertEqual(self.config_calendars(), {A: "Alpha"})
        # The worker records the failure; the next attempt asks Luma about the missing link only.
        self.luma.calls.clear()
        self.luma.resolved["missing"] = calendar(B, "Found")
        sync._run(self.store.feed(CONFIG_FEED), NEW)
        self.assertEqual([c for c in self.luma.calls if c[0] == "resolve_calendar"], [("resolve_calendar", "missing")])
        self.assertEqual(self.config_calendars(), {A: "Alpha", B: "Found"})

    def test_rate_limits_keep_what_was_resolved(self):
        sync = self.make_sync(EVENTS_LUMA_CALENDARS=f"https://luma.com/one https://luma.com/two")
        sync.reconcile()
        self.luma.resolved = {"one": calendar(A, "One"), "two": LumaError(429, "slow down")}
        with self.assertRaises(RateLimited):
            sync._run(self.store.feed(CONFIG_FEED), NEW)
        self.assertEqual(set(json.loads(self.store.get_meta("luma_config_resolved"))), {"one"})

    def test_changing_the_list_makes_it_due_again_and_drops_removed_calendars(self):
        sync = self.make_sync(EVENTS_LUMA_CALENDARS=f"{A} {B}")
        sync.reconcile()
        sync._apply_configured_list()
        self.luma.resolved = {A: calendar(A, "Alpha"), B: calendar(B, "Beta")}
        sync._run(self.store.feed(CONFIG_FEED), NEW)
        self.store.upsert_calendar(calendar(B, "Beta"), "import")  # B is also one of your Luma follows
        self.assertFalse(sync.is_due(self.store.feed(CONFIG_FEED), self.clock()))

        later = self.make_sync(EVENTS_LUMA_CALENDARS=A)
        later.reconcile()
        later._apply_configured_list()
        self.assertIsNone(self.store.feed(CONFIG_FEED).last_ok_at, "a new list is resolved again")
        later._run(self.store.feed(CONFIG_FEED), NEW)
        self.assertEqual(self.config_calendars(), {A: "Alpha"})
        self.assertEqual(self.store.calendar(B)["origins"], ["import"], "the import still claims B")

        self.make_sync().reconcile()  # nothing configured any more
        self.assertEqual(self.config_calendars(), {})
        self.assertIsNone(self.store.feed(CONFIG_FEED))


    def test_a_restart_keeps_the_backoff_of_an_unchanged_list(self):
        env = {"EVENTS_LUMA_CALENDARS": "https://luma.com/missing"}
        sync = self.make_sync(**env)
        sync.reconcile()
        sync._apply_configured_list()
        with self.assertRaises(ValueError):
            sync._run(self.store.feed(CONFIG_FEED), NEW)
        sync._record_failure(self.store.feed(CONFIG_FEED), "could not resolve missing", 600)

        restarted = self.make_sync(**env)
        restarted.reconcile()
        restarted._apply_configured_list()
        feed = self.store.feed(CONFIG_FEED)
        self.assertEqual(feed.failures, 1)
        self.assertFalse(restarted.is_due(feed, self.clock()), "waits out the backoff")
        self.assertTrue(restarted.is_due(feed, self.clock() + 600))

class AgiHouseSwitchTest(ConfigTestCase):
    def test_turning_agi_house_off_removes_its_calendar_and_feed(self):
        self.make_sync().reconcile()
        self.assertIsNotNone(self.store.calendar("agihouse"))
        self.make_sync(EVENTS_AGIHOUSE="0").reconcile()
        self.assertIsNone(self.store.calendar("agihouse"))
        self.assertIsNone(self.store.feed("agihouse"))


class OldDatabaseNameTest(unittest.TestCase):
    def test_lumacal_db_and_its_wal_files_are_renamed_once(self):
        state = temp_dir(self)
        for suffix in ("", "-wal", "-shm"):
            (state / f"lumacal.db{suffix}").write_text(suffix or "db")
        adopt_old_database(state)
        self.assertEqual(sorted(p.name for p in state.iterdir()), ["events.db", "events.db-shm", "events.db-wal"])
        self.assertEqual((state / "events.db").read_text(), "db")
        (state / "lumacal.db").write_text("stray")
        adopt_old_database(state)
        self.assertEqual((state / "events.db").read_text(), "db", "an existing events.db is never replaced")


class ConfiguredCalendarRemovalTest(WebTestCase):
    def test_configured_calendars_cannot_be_removed_from_the_app(self):
        self.store.upsert_calendar(calendar(A), "config")
        resp = self.call("DELETE", f"/api/luma/calendars/{A}")
        self.assertEqual(resp.status, 409)
        self.assertIn("configuration", self.json_of(resp)["error"])
        self.assertIsNotNone(self.store.calendar(A))


if __name__ == "__main__":
    unittest.main()

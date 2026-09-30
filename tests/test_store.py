import os
import sqlite3
import stat
import unittest

from events import db as dbmod
from events.db import Database

from .helpers import DAY, HOUR, NOW, FakeClock, add_feed, calendar, iso, listing, make_db, make_store, temp_dir


class DatabaseTest(unittest.TestCase):
    def test_migrate_is_idempotent(self):
        db = Database(":memory:")
        self.addCleanup(db.close)
        self.assertEqual(db.migrate(), len(dbmod.MIGRATIONS))
        self.assertEqual(db.migrate(), len(dbmod.MIGRATIONS))
        tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"meta", "secrets", "partiful_accounts", "calendars", "calendar_origins", "feeds", "listings",
                         "event_seen", "going", "marks", "prefs"} <= tables)

    def test_file_database_is_private(self):
        path = temp_dir(self) / "data" / "events.db"
        db = make_db(self, path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
        self.assertEqual(db.query_one("PRAGMA journal_mode")[0], "wal")

    def test_transactions_roll_back_and_nest(self):
        db = make_db(self)
        with self.assertRaises(RuntimeError):
            with db.transaction() as conn:
                conn.execute("INSERT INTO prefs (key, value) VALUES ('a', '1')")
                with db.transaction() as inner:  # joins the outer transaction
                    inner.execute("INSERT INTO prefs (key, value) VALUES ('b', '2')")
                raise RuntimeError("boom")
        self.assertEqual(db.query("SELECT * FROM prefs"), [])
        with db.transaction() as conn:
            conn.execute("INSERT INTO prefs (key, value) VALUES ('a', '1')")
        self.assertEqual(len(db.query("SELECT * FROM prefs")), 1)
        db.execute("INSERT INTO prefs (key, value) VALUES ('b', '2')")
        self.assertEqual(db.query_one("SELECT value FROM prefs WHERE key = 'b'")["value"], "2")

    def test_foreign_keys_are_enforced(self):
        db = make_db(self)
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO feeds (key, source, kind, calendar_id, label) VALUES ('k', 'luma', 'calendar', 'cal-missing', 'x')")

    def test_statements_strip_comments(self):
        script = "CREATE TABLE a (x TEXT); -- a comment; with a semicolon\n-- whole line;\nCREATE TABLE b (y TEXT);\n"
        self.assertEqual(dbmod._statements(script), ["CREATE TABLE a (x TEXT)", "CREATE TABLE b (y TEXT)"])


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = make_store(self, self.clock)

    def origins(self) -> dict[str, list[str]]:
        return {c["id"]: c["origins"] for c in self.store.calendars()}

    def listing_ids(self, feed_key: str | None = None) -> list[str]:
        rows = self.store.listings(iso(-24 * 365))
        return sorted(r["event_id"] for r in rows if feed_key in (None, r["feed_key"]))

    def seen(self, event_id: str):
        return self.store.db.query_one("SELECT first_seen_at, announced_at FROM event_seen WHERE event_id = ?", (event_id,))


class CalendarStoreTest(StoreTestCase):
    def test_upsert_records_origins_and_refreshes_fields(self):
        self.assertTrue(self.store.upsert_calendar(calendar("cal-a", "Alpha") | {"slug": "alpha"}, "import"))
        self.assertFalse(self.store.upsert_calendar({"id": "cal-a", "source": "luma", "name": "", "slug": None,
                                                     "description": "About"}, "followed"))
        cal = self.store.calendar("cal-a")
        self.assertEqual((cal["name"], cal["slug"], cal["description"]), ("Alpha", "alpha", "About"),
                         "empty names and missing fields never erase stored ones")
        self.assertEqual(cal["origins"], ["followed", "import"])
        self.assertEqual(cal["added_at"], NOW)
        self.assertIsNone(self.store.calendar("cal-missing"))
        with self.assertRaises(AssertionError):
            self.store.upsert_calendar(calendar("cal-b"), "somewhere")

    def test_calendars_are_sorted_by_name_ignoring_case(self):
        for cal_id, name in (("cal-1", "beta"), ("cal-2", "Alpha"), ("cal-3", "gamma")):
            self.store.upsert_calendar(calendar(cal_id, name), "link")
        self.assertEqual([c["name"] for c in self.store.calendars()], ["Alpha", "beta", "gamma"])

    def test_calendar_survives_while_any_origin_claims_it(self):
        self.store.upsert_calendar(calendar("cal-a"), "import")
        self.store.upsert_calendar(calendar("cal-a"), "followed")
        self.assertFalse(self.store.remove_calendar("cal-a", "import"))
        self.assertEqual(self.origins(), {"cal-a": ["followed"]})
        self.assertTrue(self.store.remove_calendar("cal-a", "followed"))
        self.assertEqual(self.origins(), {})
        self.store.upsert_calendar(calendar("cal-b"), "import")
        self.store.upsert_calendar(calendar("cal-b"), "link")
        self.assertTrue(self.store.remove_calendar("cal-b"), "no origin drops every claim")

    def test_set_origin_calendars_returns_added_and_removed(self):
        self.store.upsert_calendar(calendar("cal-shared"), "followed")
        added, removed = self.store.set_origin_calendars("import", [calendar("cal-a"), calendar("cal-shared")])
        self.assertEqual((added, removed), (["cal-a"], []))
        added, removed = self.store.set_origin_calendars("import", [calendar("cal-b")])
        self.assertEqual((added, removed), (["cal-b"], ["cal-a"]))
        self.assertEqual(self.origins(), {"cal-b": ["import"], "cal-shared": ["followed"]})
        self.assertEqual(self.store.set_origin_calendars("import", [calendar("cal-b")]), ([], []))

    def test_losing_the_last_origin_cascades_to_feeds_and_listings(self):
        add_feed(self.store, "luma:cal-a", calendar_id="cal-a", origin="import")
        add_feed(self.store, "luma:cal-b", calendar_id="cal-b", origin="import")
        self.store.replace_listings("luma:cal-a", [listing("evt-1")])
        self.store.replace_listings("luma:cal-b", [listing("evt-2")])
        _, removed = self.store.set_origin_calendars("import", [calendar("cal-b")])
        self.assertEqual(removed, ["cal-a"])
        self.assertIsNone(self.store.feed("luma:cal-a"))
        self.assertEqual(self.listing_ids(), ["evt-2"])

    def test_unchanged_calendars_do_not_bump_the_data_version(self):
        self.store.upsert_calendar(calendar("cal-a", "Alpha"), "import")
        self.store.set_origin_calendars("followed", [calendar("cal-b")])
        version = self.store.data_version()
        self.assertFalse(self.store.upsert_calendar(calendar("cal-a", "Alpha"), "import"))
        self.store.upsert_calendar({"id": "cal-a", "source": "luma", "name": ""}, "import")
        self.assertEqual(self.store.set_origin_calendars("followed", [calendar("cal-b")]), ([], []))
        self.assertTrue(self.store.remove_calendar("cal-missing"))
        self.assertFalse(self.store.remove_calendar("cal-a", "link"), "an origin that never claimed it")
        self.assertEqual(self.store.data_version(), version)
        self.store.upsert_calendar(calendar("cal-a", "Alpha"), "link")
        self.assertGreater(self.store.data_version(), version, "a new claim is a change")

    def test_every_calendar_change_bumps_the_data_version(self):
        version = self.store.data_version()
        self.store.upsert_calendar(calendar("cal-a"), "link")
        self.assertGreater(self.store.data_version(), version)
        version = self.store.data_version()
        self.store.remove_calendar("cal-a")
        self.assertGreater(self.store.data_version(), version)


class FeedStoreTest(StoreTestCase):
    def test_ensure_feed_upserts(self):
        add_feed(self.store, "luma:cal-a", calendar_id="cal-a", name="Alpha")
        self.store.ensure_feed("luma:cal-a", source="luma", kind="calendar", calendar_id="cal-a", label="Alpha renamed")
        self.store.ensure_feed("agihouse", source="agihouse", kind="agihouse", calendar_id=None, label="AGI House")
        self.assertEqual([f.key for f in self.store.feeds()], ["agihouse", "luma:cal-a"])
        self.assertEqual([f.key for f in self.store.feeds("luma")], ["luma:cal-a"])
        self.assertEqual(self.store.feed("luma:cal-a").label, "Alpha renamed")
        self.assertIsNone(self.store.feed("nope"))

    def test_attempts_failures_and_successes(self):
        add_feed(self.store, "luma:cal-a", calendar_id="cal-a")
        self.store.mark_attempt("luma:cal-a")
        self.store.record_failure("luma:cal-a", "x" * 900, retry_in=600)
        self.store.record_failure("luma:cal-a", "Luma 500: oops", retry_in=1200)
        feed = self.store.feed("luma:cal-a")
        self.assertEqual((feed.last_attempt_at, feed.failures, feed.retry_at, feed.last_error),
                         (NOW, 2, NOW + 1200, "Luma 500: oops"))
        self.clock.advance(60)
        self.store.record_success("luma:cal-a", 12)
        self.store.record_success("luma:cal-a")
        feed = self.store.feed("luma:cal-a")
        self.assertEqual((feed.last_ok_at, feed.failures, feed.retry_at, feed.last_error, feed.item_count),
                         (NOW + 60, 0, None, None, 12))
        self.assertEqual(set(feed.to_dict()), {"key", "source", "kind", "calendar_id", "label", "last_attempt_at",
                                               "last_ok_at", "last_error", "failures", "retry_at", "item_count"})

    def test_failure_messages_are_truncated(self):
        add_feed(self.store, "agihouse", calendar_id=None, kind="agihouse", source="agihouse")
        self.store.record_failure("agihouse", "x" * 900, retry_in=600)
        self.assertEqual(len(self.store.feed("agihouse").last_error), 500)

    def test_delete_feed_removes_listings(self):
        add_feed(self.store, "luma:cal-a", calendar_id="cal-a")
        self.store.replace_listings("luma:cal-a", [listing("evt-1")])
        version = self.store.data_version()
        self.store.delete_feed("luma:cal-a")
        self.assertEqual(self.listing_ids(), [])
        self.assertGreater(self.store.data_version(), version)
        version = self.store.data_version()
        self.store.delete_feed("luma:cal-a")
        self.assertEqual(self.store.data_version(), version, "deleting nothing changes nothing")


class ReplaceListingsTest(StoreTestCase):
    KEY = "luma:cal-a"

    def setUp(self):
        super().setUp()
        add_feed(self.store, self.KEY, calendar_id="cal-a")

    def test_first_pull_stores_listings_without_announcing_them(self):
        result = self.store.replace_listings(self.KEY, [listing("evt-1"), listing("evt-2", start=iso(48))])
        self.assertTrue(result.changed)
        self.assertEqual(sorted(result.new_event_ids), ["evt-1", "evt-2"])
        self.assertEqual(self.listing_ids(), ["evt-1", "evt-2"])
        self.assertEqual(tuple(self.seen("evt-1")), (NOW, None))
        feed = self.store.feed(self.KEY)
        self.assertEqual((feed.last_ok_at, feed.item_count, feed.failures), (NOW, 2, 0))
        rows = self.store.listings(iso(-1))
        self.assertEqual({r["calendar_id"] for r in rows}, {"cal-a"})
        self.assertEqual(rows[0]["feed_kind"], "calendar")
        self.assertEqual(rows[0]["feed_source"], "luma")
        self.assertEqual(rows[0]["data"]["name"], "Community Meetup")

    def test_identical_re_pull_is_not_a_change(self):
        events = [listing("evt-1"), listing("evt-past", start=iso(-72), end=iso(-70))]
        self.store.replace_listings(self.KEY, events)
        version = self.store.data_version()
        self.clock.advance(HOUR)
        result = self.store.replace_listings(self.KEY, [dict(e) for e in reversed(events)])
        self.assertFalse(result.changed)
        self.assertEqual(result.new_event_ids, [])
        self.assertEqual(self.store.data_version(), version)
        self.assertEqual(self.store.feed(self.KEY).last_ok_at, NOW + HOUR, "a good pull is still recorded")

    def test_later_pulls_replace_future_listings_and_announce_new_events(self):
        self.store.replace_listings(self.KEY, [listing("evt-1"), listing("evt-2")])
        self.clock.advance(HOUR)
        result = self.store.replace_listings(self.KEY, [listing("evt-1", name="Renamed"), listing("evt-3")])
        self.assertTrue(result.changed)
        self.assertEqual(result.new_event_ids, ["evt-3"])
        self.assertEqual(self.listing_ids(), ["evt-1", "evt-3"], "evt-2 was cancelled upstream")
        self.assertEqual(tuple(self.seen("evt-3")), (NOW + HOUR, NOW + HOUR))
        self.assertEqual(tuple(self.seen("evt-1")), (NOW, None))
        names = {r["event_id"]: r["data"]["name"] for r in self.store.listings(iso(-1))}
        self.assertEqual(names["evt-1"], "Renamed")

    def test_past_listings_are_kept_as_history(self):
        self.store.replace_listings(self.KEY, [listing("evt-past", start=iso(-5), end=iso(-3)), listing("evt-soon", start=iso(1))])
        self.clock.advance(2 * HOUR)  # evt-soon has started, so it is history too
        self.store.replace_listings(self.KEY, [listing("evt-new", start=iso(30))])
        self.assertEqual(self.listing_ids(), ["evt-new", "evt-past", "evt-soon"])
        self.store.replace_listings(self.KEY, [])
        self.assertEqual(self.listing_ids(), ["evt-past", "evt-soon"], "an empty pull clears only upcoming listings")

    def test_announcements_are_per_event_not_per_feed(self):
        other = add_feed(self.store, "luma:cal-b", calendar_id="cal-b")
        self.store.replace_listings(self.KEY, [listing("evt-1")])
        self.clock.advance(HOUR)
        self.store.replace_listings(other, [listing("evt-1"), listing("evt-2")])  # a new feed's first pull
        self.assertIsNone(self.seen("evt-2")["announced_at"])
        self.assertEqual(self.seen("evt-1")["first_seen_at"], NOW)

    def test_invalid_rows_and_unknown_feeds(self):
        result = self.store.replace_listings(self.KEY, [listing("evt-1"), {"id": "evt-2"}, listing("", name="no id"),
                                                        {"start_at": iso(1)}])
        self.assertEqual(result.new_event_ids, ["evt-1"])
        self.assertEqual(self.store.feed(self.KEY).item_count, 1)
        with self.assertRaises(KeyError):
            self.store.replace_listings("luma:missing", [listing("evt-1")])

    def test_listings_include_events_still_running(self):
        self.store.replace_listings(self.KEY, [listing("evt-running", start=iso(-2), end=iso(2)),
                                               listing("evt-over", start=iso(-4), end=iso(-3)),
                                               listing("evt-open", start=iso(-4))])
        self.assertEqual([r["event_id"] for r in self.store.listings(iso(0))], ["evt-running"])


class OtherStateTest(StoreTestCase):
    def test_secrets_round_trip(self):
        self.assertIsNone(self.store.get_secret("luma_session"))
        self.store.set_secret("luma_session", {"session_key": "abc", "via": "cookie"})
        self.assertEqual(self.store.get_secret("luma_session"), {"session_key": "abc", "via": "cookie"})
        self.assertTrue(self.store.delete_secret("luma_session"))
        self.assertFalse(self.store.delete_secret("luma_session"))
        self.store.db.execute("INSERT INTO secrets (key, value, updated_at) VALUES ('bad', 'not json', 0), ('list', '[1]', 0)")
        self.assertIsNone(self.store.get_secret("bad"))
        self.assertIsNone(self.store.get_secret("list"))

    def test_meta(self):
        self.assertIsNone(self.store.get_meta("x"))
        self.store.set_meta("x", "1")
        self.store.set_meta("x", "2")
        self.assertEqual(self.store.get_meta("x"), "2")

    def test_prefs(self):
        self.assertEqual(self.store.prefs(), {})
        version = self.store.data_version()
        self.store.set_pref("area", "bay")
        self.store.set_pref("muted_calendars", ["cal-a"])
        self.store.set_pref("area", "all")
        self.assertEqual(self.store.prefs(), {"area": "all", "muted_calendars": ["cal-a"]})
        self.assertGreater(self.store.data_version(), version)

    def test_marks_round_trip_and_clear(self):
        self.assertEqual(self.store.set_mark("evt-1", starred=True), {"starred": True, "hidden": False})
        self.assertEqual(self.store.set_mark("evt-1", hidden=True), {"starred": True, "hidden": True})
        self.assertEqual(self.store.set_mark("evt-1", starred=False), {"starred": False, "hidden": True})
        self.assertEqual(self.store.marks(), {"evt-1": {"starred": False, "hidden": True}})
        self.assertEqual(self.store.set_mark("evt-1", hidden=False), {"starred": False, "hidden": False})
        self.assertEqual(self.store.marks(), {})
        self.assertEqual(self.store.db.query("SELECT * FROM marks"), [], "cleared marks leave no row")
        self.assertEqual(self.store.set_mark("evt-2"), {"starred": False, "hidden": False})

    def test_going_is_replaced_per_origin(self):
        self.store.replace_going("luma-import", {"evt-1": "registered", "evt-2": "registered"})
        self.store.replace_going("other", {"evt-3": None})
        self.assertEqual(self.store.going(), {"evt-1": "registered", "evt-2": "registered", "evt-3": "going"})
        self.store.replace_going("luma-import", {"evt-2": "approved"})
        self.assertEqual(self.store.going(), {"evt-2": "approved", "evt-3": "going"})

    def test_partiful_accounts_upsert(self):
        self.store.save_partiful_account({"uid": "u1", "name": "Jordan", "refresh_token": "r1"})
        self.clock.advance(60)
        self.store.save_partiful_account({"uid": "u2", "refresh_token": "r2", "id_token": "t2", "expires_at": NOW + 3600})
        self.store.save_partiful_account({"uid": "u1", "name": None, "refresh_token": "r1b", "id_token": "t1"})
        u1, u2 = self.store.partiful_accounts()
        self.assertEqual((u1["uid"], u1["name"], u1["refresh_token"], u1["id_token"], u1["added_at"]),
                         ("u1", "Jordan", "r1b", "t1", NOW), "name and added_at survive an update")
        self.assertEqual((u2["uid"], u2["expires_at"], u2["added_at"]), ("u2", NOW + 3600, NOW + 60))

    def test_delete_partiful_account_removes_only_its_feeds(self):
        uids = ("a_b", "aXb", "u%", "u1")
        for uid in uids:
            self.store.save_partiful_account({"uid": uid, "refresh_token": "r"})
        self.store.upsert_calendar(calendar("partiful", "Partiful", "partiful"), "builtin")
        for uid in uids:
            for which in ("mine", "following"):
                key = add_feed(self.store, f"partiful:{uid}:{which}", calendar_id="partiful", source="partiful",
                               kind=f"partiful-{which}", origin="builtin")
                self.store.replace_listings(key, [listing(f"pf-{uid}-{which}", source="partiful")])
        add_feed(self.store, "partiful:feed", calendar_id="partiful", source="partiful", kind="partiful-feed", origin="builtin")

        self.assertTrue(self.store.delete_partiful_account("a_b"))
        self.assertTrue(self.store.delete_partiful_account("u%"))
        self.assertFalse(self.store.delete_partiful_account("nobody"))
        keys = {f.key for f in self.store.feeds()}
        self.assertEqual(keys, {"partiful:aXb:mine", "partiful:aXb:following", "partiful:u1:mine", "partiful:u1:following",
                                "partiful:feed"})
        self.assertEqual(sorted(a["uid"] for a in self.store.partiful_accounts()), ["aXb", "u1"])
        self.assertNotIn("pf-a_b-mine", self.listing_ids())
        self.assertIn("pf-aXb-mine", self.listing_ids())

    def test_prune_forgets_old_events(self):
        key = add_feed(self.store, "luma:cal-a", calendar_id="cal-a")
        self.clock.advance(-90 * DAY)
        self.store.replace_listings(key, [listing("evt-old", start=iso(-24 * 61), end=iso(-24 * 61 + 2)),
                                          listing("evt-recent", start=iso(-24 * 59)),
                                          listing("evt-open-ended", start=iso(-24 * 70))])
        self.store.replace_going("luma-import", {"evt-old": "registered", "evt-unlisted": "registered"})
        self.clock.advance(90 * DAY)
        self.store.replace_going("fresh", {"evt-new-rsvp": "registered"})
        version = self.store.data_version()

        self.assertEqual(self.store.prune(keep_days=60), 2)
        self.assertEqual(self.listing_ids(), ["evt-recent"])
        self.assertIsNone(self.seen("evt-old"))
        self.assertIsNotNone(self.seen("evt-recent"))
        self.assertEqual(self.store.going(), {"evt-new-rsvp": "registered"})
        self.assertGreater(self.store.data_version(), version)
        version = self.store.data_version()
        self.assertEqual(self.store.prune(keep_days=60), 0)
        self.assertEqual(self.store.data_version(), version)


if __name__ == "__main__":
    unittest.main()

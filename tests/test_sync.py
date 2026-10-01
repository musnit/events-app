import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from events import sync as syncmod
from events.config import PACKAGE_DIR, Settings
from events.sources.luma import LumaError
from events.sources.partiful import PartifulError
from events.sync import NEW, SCHEDULED, USER, RateLimited, Sync, Task, Worker, failure_backoff

from .helpers import (HOUR, NOW, FakeAgiHouse, FakeClock, FakeLuma, FakePartiful, calendar, feed, iso, listing,
                      make_settings, make_store, temp_dir)

CAL = "cal-aaaaaaaaaaaa"
KEY = f"luma:{CAL}"
ICS_URL = "https://api.lu.ma/ics/get?entity=user&id=usr-1"
FEED_URL = "https://calendars.partiful.com/getCalendar?id=abc"


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        s = Settings.from_env({"HOME": "/home/me"})
        self.assertEqual((s.host, s.port), ("127.0.0.1", 8771))
        self.assertEqual(s.state_dir, Path("/home/me/.local/state/events"))
        self.assertEqual(s.web_dir, PACKAGE_DIR.parent / "web" / "dist")
        self.assertEqual((s.legacy_dir, s.luma_calendars, s.agihouse), (None, (), True))
        self.assertEqual((s.luma_interval, s.personal_interval, s.partiful_interval, s.agihouse_interval),
                         (4 * HOUR, HOUR, HOUR, 2 * HOUR))
        self.assertEqual((s.luma_spacing, s.manual_spacing), (15, 2))
        self.assertTrue(s.sync_enabled)

    def test_state_directory_follows_xdg_and_is_required_without_a_home(self):
        self.assertEqual(Settings.from_env({"XDG_STATE_HOME": "/xdg", "HOME": "/home/me"}).state_dir, Path("/xdg/events"))
        with self.assertRaises(SystemExit):
            Settings.from_env({})

    def test_overrides(self):
        s = Settings.from_env({
            "EVENTS_LISTEN": "0.0.0.0:9000", "EVENTS_STATE_DIR": "/tmp/d", "EVENTS_WEB_DIR": "/tmp/w",
            "EVENTS_LEGACY_DIR": "/old", "EVENTS_LUMA_CALENDARS": "https://luma.com/one, cal-aaaaaaaaaaaa\ncal-aaaaaaaaaaaa",
            "EVENTS_AGIHOUSE": "off", "EVENTS_LUMA_INTERVAL": "600", "EVENTS_LUMA_SPACING": " 0.5 ", "EVENTS_SYNC": "0",
            "EVENTS_MANUAL_SPACING": "",
        })
        self.assertEqual((s.host, s.port, s.state_dir, s.web_dir, s.legacy_dir),
                         ("0.0.0.0", 9000, Path("/tmp/d"), Path("/tmp/w"), Path("/old")))
        self.assertEqual(s.luma_calendars, ("https://luma.com/one", "cal-aaaaaaaaaaaa"))
        self.assertEqual((s.luma_interval, s.luma_spacing, s.manual_spacing, s.agihouse, s.sync_enabled),
                         (600, 0.5, 2, False, False))

    def test_listen_accepts_ipv6_and_every_interface(self):
        self.assertEqual((Settings.from_env({"HOME": "/h", "EVENTS_LISTEN": "[::1]:8771"}).host), "::1")
        self.assertEqual((Settings.from_env({"HOME": "/h", "EVENTS_LISTEN": ":8771"}).host), "")

    def test_invalid_values_stop_start_up(self):
        for env in ({"EVENTS_LISTEN": "http"}, {"EVENTS_LISTEN": "localhost:70000"}, {"EVENTS_LISTEN": "localhost:-1"},
                    {"EVENTS_LUMA_SPACING": "fast"}, {"EVENTS_LUMA_INTERVAL": "10"}):
            with self.subTest(env=env):
                with self.assertRaises(SystemExit):
                    Settings.from_env({"HOME": "/h", **env})


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.feeds = {k: feed(k, label=f"Feed {k}") for k in ("a", "b", "c")}
        self.ran: list[tuple[str, int]] = []
        self.errors: dict[str, BaseException] = {}
        self.failures: list[tuple[str, str, float]] = []
        self.due: list = []
        self.worker = Worker("luma", run=self.run_feed, due=lambda now: list(self.due), feed=self.feeds.get,
                             on_failure=lambda f, message, retry: self.failures.append((f.key, message, retry)),
                             spacing=0.05, fast_spacing=0.0, clock=self.clock)

    def run_feed(self, f, priority):
        self.ran.append((f.key, priority))
        if f.key in self.errors:
            raise self.errors[f.key]

    def priorities(self) -> dict[str, int]:
        return {k: t.priority for k, t in self.worker._queue.items()}

    def test_enqueue_dedupes_and_keeps_the_most_urgent_priority(self):
        self.worker.enqueue(["a", "b"], SCHEDULED)
        self.worker.enqueue(["a"], USER)
        seq = self.worker._queue["a"].seq
        self.worker.enqueue(["a", "b"], NEW)
        self.assertEqual(self.priorities(), {"a": USER, "b": NEW})
        self.assertEqual(self.worker._queue["a"].seq, seq, "a less urgent request does not requeue")
        status = self.worker.status()
        self.assertEqual((status["queued_by_user"], status["busy"], status["total"]), (1, True, 2))

    def test_next_takes_the_most_urgent_then_the_oldest(self):
        self.worker.enqueue(["a"], SCHEDULED)
        self.worker.enqueue(["b", "c"], NEW)
        order = []
        for _ in range(3):
            self.clock.advance(1)
            task = self.worker._next()
            self.assertEqual(self.worker.status()["current"], f"Feed {task.key}")
            self.worker._execute(task)
            order.append((task.key, task.priority))
        self.assertEqual(order, [("b", NEW), ("c", NEW), ("a", SCHEDULED)])
        self.assertEqual(self.ran, order)
        self.assertEqual(self.worker.status()["done"], 3)

    def test_scheduled_pulls_wait_for_the_spacing_but_user_pulls_do_not(self):
        self.worker._last_request_at = self.clock()
        self.worker.enqueue(["a"], SCHEDULED)
        self.assertIsNone(self.worker._next(), "waits out at most the 0.05 s spacing, then yields")
        self.assertIn("a", self.worker._queue)
        self.worker.enqueue(["a"], USER)
        task = self.worker._next()
        self.assertEqual((task.key, task.priority), ("a", USER))
        self.assertEqual(self.worker._queue, {})

    def test_rate_limit_pause_holds_every_task(self):
        self.worker._paused_until = self.clock() + 0.05
        self.worker.enqueue(["a"], USER)
        self.assertIsNone(self.worker._next())
        self.clock.advance(1)
        self.assertEqual(self.worker._next().key, "a")

    def test_an_empty_queue_is_filled_from_due_feeds(self):
        self.due = [self.feeds["b"], self.feeds["a"]]
        task = self.worker._next()
        self.assertEqual((task.key, task.priority), ("b", SCHEDULED))
        self.assertEqual(self.priorities(), {"a": SCHEDULED})

    def test_idle_worker_closes_the_run_and_survives_planning_errors(self):
        self.worker.enqueue(["a"], USER)
        self.worker._execute(self.worker._next())
        self.assertIsNotNone(self.worker.status()["started_at"])

        def broken_due(now):
            raise RuntimeError("database is locked")

        self.worker._due = broken_due
        with mock.patch.object(syncmod, "IDLE_CHECK_S", 0.01), self.assertLogs("events.sync", "ERROR"):
            self.assertIsNone(self.worker._next())
        status = self.worker.status()
        self.assertEqual((status["started_at"], status["last_finished_at"], status["done"], status["busy"]),
                         (None, NOW, 0, False))

    def test_nothing_is_handed_out_once_stopping(self):
        self.worker.enqueue(["a"], USER)
        self.worker.stop()
        self.assertIsNone(self.worker._next())

    def test_success_resets_rate_limit_strikes(self):
        self.worker._rate_limit_strikes = 2
        self.worker._execute(Task(USER, 1, "a"))
        self.assertEqual(self.ran, [("a", USER)])
        self.assertEqual(self.worker._rate_limit_strikes, 0)
        self.assertEqual(self.worker._last_request_at, NOW)
        status = self.worker.status()
        self.assertEqual((status["done"], status["failed"], status["current"]), (1, 0, None))

    def test_rate_limited_requeues_and_pauses_longer_each_time(self):
        self.errors["a"] = RateLimited("Luma is rate limiting requests.")
        task = Task(SCHEDULED, 7, "a")
        with self.assertLogs("events.sync", "WARNING"):
            self.worker._execute(task)
        self.assertIs(self.worker._queue["a"], task)
        self.assertEqual(self.worker._paused_until, NOW + 45)
        self.assertEqual(self.failures, [], "a rate limit is not the feed's fault")
        status = self.worker.status()
        self.assertEqual(status["paused_until"], NOW + 45)
        self.assertEqual(status["last_error"], "Luma is rate limiting requests. Pausing 45s.")
        self.assertEqual(status["failed"], 0)

        pauses = []
        with self.assertLogs("events.sync", "WARNING"):
            for _ in range(5):
                self.worker._execute(task)
                pauses.append(self.worker._paused_until - NOW)
        self.assertEqual(pauses, [90, 180, 300, 300, 300])
        self.assertEqual(list(self.worker._queue), ["a"])

    def test_rate_limited_keeps_a_more_urgent_queued_request(self):
        self.errors["a"] = RateLimited("slow down")
        self.worker.enqueue(["a"], USER)
        queued = self.worker._queue["a"]
        with self.assertLogs("events.sync", "WARNING"):
            self.worker._execute(Task(SCHEDULED, 99, "a"))
        self.assertIs(self.worker._queue["a"], queued)

    def test_failures_are_recorded_with_backoff(self):
        self.feeds["b"] = feed("b", label="Feed b", failures=2)
        self.errors["b"] = LumaError(500, "oops")
        with self.assertLogs("events.sync", "WARNING"):
            self.worker._execute(Task(NEW, 1, "b"))
        self.assertEqual(self.failures, [("b", "Luma 500: oops", failure_backoff(3))])
        status = self.worker.status()
        self.assertEqual((status["failed"], status["last_error"]), (1, "Feed b: Luma 500: oops"))
        self.assertNotIn("b", self.worker._queue, "failed feeds wait for their retry time instead")

    def test_failure_messages_fall_back_to_the_exception_name(self):
        self.errors["a"] = ValueError()
        with self.assertLogs("events.sync", "WARNING"):
            self.worker._execute(Task(NEW, 1, "a"))
        self.assertEqual(self.failures[0][1], "ValueError")

    def test_a_failing_failure_recorder_is_contained(self):
        self.errors["a"] = LumaError(None, "unreachable")

        def broken(*args):
            raise RuntimeError("disk full")

        self.worker._on_failure = broken
        with self.assertLogs("events.sync", "WARNING") as logs:
            self.worker._execute(Task(NEW, 1, "a"))
        self.assertTrue(any("could not record failure" in line for line in logs.output))
        self.assertEqual(self.worker.status()["failed"], 1)

    def test_vanished_feeds_are_skipped(self):
        self.worker._execute(Task(USER, 1, "gone"))
        self.assertEqual(self.ran, [])
        self.assertEqual(self.worker.status()["done"], 0)

    def test_failure_backoff_doubles_up_to_four_hours(self):
        self.assertEqual([failure_backoff(n) for n in (0, 1, 2, 3, 4, 10)], [600, 600, 1200, 2400, 4800, 4 * HOUR])

    def test_thread_runs_queued_work_and_stops(self):
        done = threading.Event()
        worker = Worker("thread", run=lambda f, p: done.set(), due=lambda now: [], feed=self.feeds.get,
                        on_failure=lambda *a: None, spacing=0, fast_spacing=0)
        worker.start()
        try:
            worker.enqueue(["a"], USER)
            self.assertTrue(done.wait(2))
        finally:
            worker.stop(timeout=2)
        self.assertFalse(worker._thread.is_alive())


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = make_store(self, self.clock)
        self.settings = make_settings(temp_dir(self))
        self.luma, self.partiful, self.agihouse = FakeLuma(), FakePartiful(), FakeAgiHouse()
        self.sync = Sync(self.store, self.settings, luma=self.luma, partiful=self.partiful, agihouse=self.agihouse,
                         clock=self.clock)

    def feed_keys(self) -> list[str]:
        return [f.key for f in self.store.feeds()]

    def calendar_ids(self) -> list[str]:
        return sorted(c["id"] for c in self.store.calendars())

    def queued(self, source: str) -> dict[str, int]:
        return {k: t.priority for k, t in self.sync.workers[source]._queue.items()}

    def listed(self) -> dict[str, str]:
        return {r["event_id"]: r["calendar_id"] for r in self.store.listings(iso(-1))}


class ReconcileTest(SyncTestCase):
    def test_nothing_configured_still_has_agi_house(self):
        self.assertEqual(self.sync.reconcile(), ["agihouse"])
        self.assertEqual(self.feed_keys(), ["agihouse"])
        self.assertEqual(self.calendar_ids(), ["agihouse"])
        self.assertEqual(self.sync.reconcile(), [])
        self.assertEqual(self.sync.workers["luma"]._spacing, 15)
        self.assertEqual(self.sync.workers["luma"]._fast_spacing, 2)
        self.assertEqual(set(self.sync.status()), {"luma", "partiful", "agihouse"})

    def test_luma_sources(self):
        self.store.upsert_calendar(calendar(CAL, "Alpha"), "link")
        self.assertEqual(self.sync.reconcile(), ["agihouse", KEY])
        cal_feed = self.store.feed(KEY)
        self.assertEqual((cal_feed.source, cal_feed.kind, cal_feed.calendar_id, cal_feed.label), ("luma", "calendar", CAL, "Alpha"))

        self.store.set_secret("luma_session", {"session_key": "sess"})
        self.assertEqual(self.sync.reconcile(), ["luma:following", "luma:mine"])
        self.assertIn("luma-mine", self.calendar_ids())
        self.assertEqual(self.store.feed("luma:mine").calendar_id, "luma-mine")
        self.assertIsNone(self.store.feed("luma:following").calendar_id)
        self.assertIsNone(self.store.feed("luma:luma-mine"), "only cal- calendars get calendar feeds")

        self.store.set_secret("luma_ics", {"url": ICS_URL})
        self.assertEqual(self.sync.reconcile(), ["luma:ics"])
        self.store.delete_secret("luma_session")
        self.sync.reconcile()
        self.assertEqual(self.feed_keys(), ["agihouse", KEY, "luma:ics"])
        self.assertIn("luma-mine", self.calendar_ids(), "the iCal feed still lists under it")

        self.store.delete_secret("luma_ics")
        self.sync.reconcile()
        self.assertEqual(self.calendar_ids(), ["agihouse", CAL])
        self.store.remove_calendar(CAL)
        self.sync.reconcile()
        self.assertEqual(self.feed_keys(), ["agihouse"])

    def test_reconcile_without_changes_leaves_the_data_version_alone(self):
        self.store.set_secret("luma_session", {"session_key": "sess"})
        self.store.save_partiful_account({"uid": "u1", "refresh_token": "r"})
        self.sync.reconcile()
        version = self.store.data_version()
        self.assertEqual(self.sync.reconcile(), [])
        self.assertEqual(self.store.data_version(), version)

    def test_calendar_renames_update_feed_labels(self):
        self.store.upsert_calendar(calendar(CAL, "Alpha"), "link")
        self.sync.reconcile()
        self.store.upsert_calendar(calendar(CAL, "Alpha Club"), "followed")
        self.sync.reconcile()
        self.assertEqual(self.store.feed(KEY).label, "Alpha Club")

    def test_partiful_sources(self):
        self.store.save_partiful_account({"uid": "u1", "name": "Jordan", "refresh_token": "r"})
        self.store.save_partiful_account({"uid": "u2", "refresh_token": "r"})
        self.assertEqual(self.sync.reconcile(), ["agihouse", "partiful:u1:following", "partiful:u1:mine",
                                                 "partiful:u2:following", "partiful:u2:mine"])
        self.assertEqual(self.store.feed("partiful:u1:mine").label, "Partiful · Jordan · my events")
        self.assertEqual(self.store.feed("partiful:u2:following").label, "Partiful · u2 · people I follow")
        self.assertEqual(self.store.feed("partiful:u1:mine").calendar_id, "partiful")
        self.assertEqual(self.store.feed("partiful:u1:following").calendar_id, "partiful-following")
        self.assertEqual(self.calendar_ids(), ["agihouse", "partiful", "partiful-following"])

        self.store.set_secret("partiful_feed", {"url": FEED_URL})
        self.store.delete_partiful_account("u1")
        self.store.delete_partiful_account("u2")
        self.assertEqual(self.sync.reconcile(), ["partiful:feed"])
        self.assertEqual(self.feed_keys(), ["agihouse", "partiful:feed"])
        self.assertEqual(self.calendar_ids(), ["agihouse", "partiful"])

        self.store.delete_secret("partiful_feed")
        self.sync.reconcile()
        self.assertEqual(self.calendar_ids(), ["agihouse"])
        self.assertEqual(self.feed_keys(), ["agihouse"])

    def test_sources_changed_queues_new_feeds_first(self):
        self.sync.reconcile()
        self.store.upsert_calendar(calendar(CAL), "link")
        self.sync.sources_changed(refresh=["agihouse", "luma:missing"])
        self.assertEqual(self.queued("luma"), {KEY: NEW})
        self.assertEqual(self.queued("agihouse"), {"agihouse": NEW})
        self.assertEqual(self.queued("partiful"), {})

    def test_refresh_queues_a_source_for_the_user(self):
        self.store.upsert_calendar(calendar(CAL), "link")
        self.store.upsert_calendar(calendar("cal-bbbbbbbbbbbb"), "link")
        self.assertEqual(self.sync.refresh("luma"), 2)
        self.assertEqual(self.queued("luma"), {KEY: USER, "luma:cal-bbbbbbbbbbbb": USER})
        self.assertEqual(self.queued("agihouse"), {})
        self.assertEqual(self.sync.refresh(), 3)
        self.assertEqual(self.queued("agihouse"), {"agihouse": USER})


class ScheduleTest(SyncTestCase):
    def test_is_due(self):
        cases = [
            (feed(failures=1, retry_at=NOW + 10, last_ok_at=NOW - 9 * HOUR), False),
            (feed(failures=1, retry_at=NOW - 10, last_ok_at=NOW), True),
            (feed(failures=3), True),
            (feed(), True),
            (feed(last_attempt_at=NOW - 100), False),
            (feed(last_attempt_at=NOW - 300), True),
            (feed(last_ok_at=NOW - 4 * HOUR + 1), False),
            (feed(last_ok_at=NOW - 4 * HOUR), True),
            (feed(kind="luma-mine", last_ok_at=NOW - HOUR + 1), False),
            (feed(kind="luma-mine", last_ok_at=NOW - HOUR), True),
            (feed(kind="partiful-following", last_ok_at=NOW - HOUR), True),
            (feed(kind="agihouse", last_ok_at=NOW - 1.5 * HOUR), False),
            (feed(kind="agihouse", last_ok_at=NOW - 2 * HOUR), True),
            (feed(kind="mystery", last_ok_at=NOW - 4 * HOUR), True),
        ]
        for f, expected in cases:
            with self.subTest(feed=f):
                self.assertEqual(self.sync.is_due(f, NOW), expected)

    def test_refresh_intervals_come_from_settings(self):
        settings = make_settings(temp_dir(self), EVENTS_LUMA_INTERVAL="60")
        sync = Sync(self.store, settings, luma=self.luma, partiful=self.partiful, agihouse=self.agihouse, clock=self.clock)
        self.assertTrue(sync.is_due(feed(last_ok_at=NOW - 60), NOW))

    def test_due_order_is_personal_then_never_pulled_then_stalest(self):
        for cal_id in ("cal-aaaaaaaaaaaa", "cal-bbbbbbbbbbbb", "cal-cccccccccccc", "cal-dddddddddddd"):
            self.store.upsert_calendar(calendar(cal_id), "link")
        self.store.set_secret("luma_session", {"session_key": "sess"})
        self.sync.reconcile()
        for key, hours_ago in (("luma:cal-aaaaaaaaaaaa", 10), ("luma:cal-bbbbbbbbbbbb", 5), ("luma:cal-dddddddddddd", 1),
                               ("luma:mine", 2)):
            self.clock.now = NOW - hours_ago * HOUR
            self.store.record_success(key)
        self.clock.now = NOW
        due = [f.key for f in self.sync._due_for("luma")(NOW)]
        self.assertEqual(due, ["luma:following", "luma:mine", "luma:cal-cccccccccccc", "luma:cal-aaaaaaaaaaaa",
                               "luma:cal-bbbbbbbbbbbb"])
        self.assertEqual([f.key for f in self.sync._due_for("agihouse")(NOW)], ["agihouse"])


class PullTest(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.store.upsert_calendar(calendar(CAL, "Alpha"), "link")
        self.sync.reconcile()

    def run_feed(self, key: str) -> None:
        self.sync._run(self.store.feed(key), SCHEDULED)

    def sign_in(self) -> None:
        self.store.set_secret("luma_session", {"session_key": "sess"})
        self.sync.reconcile()

    def test_luma_calendar_uses_the_session(self):
        self.sign_in()
        self.luma.events[CAL] = [listing("evt-1")]
        self.run_feed(KEY)
        self.assertEqual(self.luma.calls, [("calendar_events", CAL, "sess")])
        self.assertEqual(self.listed(), {"evt-1": CAL})
        cal_feed = self.store.feed(KEY)
        self.assertEqual((cal_feed.last_attempt_at, cal_feed.last_ok_at, cal_feed.item_count), (NOW, NOW, 1))

    def test_luma_401_drops_the_session_and_retries_without_it(self):
        self.sign_in()

        def events(session_key):
            if session_key:
                raise LumaError(401, "expired")
            return [listing("evt-1")]

        self.luma.events[CAL] = events
        with self.assertLogs("events.sync", "WARNING"):
            self.run_feed(KEY)
        self.assertEqual(self.luma.calls, [("calendar_events", CAL, "sess"), ("calendar_events", CAL, None)])
        self.assertIsNone(self.store.get_secret("luma_session"))
        self.assertIn("Luma signed this app out", self.store.get_meta("luma_session_notice"))
        self.assertIsNone(self.store.feed("luma:following"), "session feeds are reconciled away")
        self.assertNotIn("luma-mine", self.calendar_ids())
        self.assertEqual(self.listed(), {"evt-1": CAL})

    def test_luma_429_raises_rate_limited(self):
        self.luma.events[CAL] = LumaError(429, "slow down")
        with self.assertRaises(RateLimited):
            self.run_feed(KEY)
        self.sign_in()

        def events(session_key):
            raise LumaError(401 if session_key else 429, "no")

        self.luma.events[CAL] = events
        with self.assertLogs("events.sync", "WARNING"), self.assertRaises(RateLimited):
            self.run_feed(KEY)
        self.assertEqual([c[2] for c in self.luma.calls[-2:]], ["sess", None], "the retry after a 401 is rate limited too")

    def test_other_luma_errors_propagate_and_keep_the_session(self):
        self.luma.events[CAL] = LumaError(401, "private calendar")
        with self.assertRaises(LumaError):
            self.run_feed(KEY)
        self.sign_in()
        self.luma.events[CAL] = LumaError(500, "oops")
        with self.assertRaises(LumaError):
            self.run_feed(KEY)
        self.assertIsNotNone(self.store.get_secret("luma_session"))

    def test_followed_calendars_become_feeds_and_are_queued(self):
        self.sign_in()
        self.luma.following_result = [calendar("cal-followed0001", "Followed")]
        self.run_feed("luma:following")
        self.assertEqual(self.store.calendar("cal-followed0001")["origins"], ["followed"])
        self.assertEqual(self.queued("luma"), {"luma:cal-followed0001": NEW})
        following = self.store.feed("luma:following")
        self.assertEqual((following.item_count, following.last_ok_at), (1, NOW))

        self.luma.following_result = []
        self.run_feed("luma:following")
        self.assertIsNone(self.store.calendar("cal-followed0001"), "unfollowed calendars go away")
        self.assertIsNone(self.store.feed("luma:cal-followed0001"))
        self.assertIsNotNone(self.store.calendar(CAL), "calendars added by link stay")

    def test_following_and_registrations_drop_a_rejected_session(self):
        for key in ("luma:following", "luma:mine"):
            with self.subTest(feed=key):
                self.sign_in()
                self.luma.following_result = self.luma.mine_result = LumaError(401, "expired")
                with self.assertLogs("events.sync", "WARNING"):
                    self.run_feed(key)
                self.assertIsNone(self.store.get_secret("luma_session"))

    def test_session_feeds_do_nothing_without_a_session(self):
        for kind in ("luma-following", "luma-mine"):
            self.sync._run(feed(f"luma:{kind}", kind=kind), SCHEDULED)
        self.sync._run(feed("luma:ics", kind="luma-ics"), SCHEDULED)
        self.sync._run(feed("partiful:feed", source="partiful", kind="partiful-feed"), SCHEDULED)
        self.sync._run(feed("partiful:ghost:mine", source="partiful", kind="partiful-mine"), SCHEDULED)
        self.assertEqual((self.luma.calls, self.partiful.calls), ([], []))

    def test_registrations_and_personal_feed(self):
        self.sign_in()
        self.store.set_secret("luma_ics", {"url": ICS_URL})
        self.sync.reconcile()
        self.luma.mine_result = [listing("evt-mine", going_status="registered")]
        self.luma.feed_result = [listing("evt-ics", going_status="registered")]
        self.run_feed("luma:mine")
        self.run_feed("luma:ics")
        self.assertEqual(self.luma.calls, [("my_events", "sess"), ("personal_feed", ICS_URL)])
        self.assertEqual(self.listed(), {"evt-mine": "luma-mine", "evt-ics": "luma-mine"})
        self.luma.mine_result = LumaError(429, "slow down")
        with self.assertRaises(RateLimited):
            self.run_feed("luma:mine")

    def test_partiful_account_pulls(self):
        self.store.save_partiful_account({"uid": "u1", "refresh_token": "r1"})
        self.sync.reconcile()
        self.partiful.token_result = lambda account: dict(account, id_token="tok", name="Jordan", expires_at=NOW + 3600)
        self.partiful.events_result = {
            "getMyUpcomingEventsForHomePage": [listing("pf-1", source="partiful", going_status="going")],
            "getMyFollowedEvents": [listing("pf-2", source="partiful")]}
        self.run_feed("partiful:u1:mine")
        self.run_feed("partiful:u1:following")
        account = self.store.partiful_accounts()[0]
        self.assertEqual((account["id_token"], account["name"], account["expires_at"]), ("tok", "Jordan", NOW + 3600))
        self.assertEqual(self.store.feed("partiful:u1:mine").label, "Partiful · Jordan · my events", "labels follow the name")
        self.assertEqual(self.listed(), {"pf-1": "partiful", "pf-2": "partiful-following"})
        self.assertEqual([c for c in self.partiful.calls if c[0] == "events"],
                         [("events", "u1", "getMyUpcomingEventsForHomePage"), ("events", "u1", "getMyFollowedEvents")])

    def test_unchanged_partiful_tokens_are_not_saved(self):
        self.store.save_partiful_account({"uid": "u1", "refresh_token": "r1", "id_token": "tok", "expires_at": NOW + 3600})
        self.sync.reconcile()
        with mock.patch.object(self.store, "save_partiful_account") as save:
            self.run_feed("partiful:u1:mine")
        save.assert_not_called()

    def test_partiful_login_errors_propagate(self):
        self.store.save_partiful_account({"uid": "u1", "refresh_token": "r1"})
        self.sync.reconcile()
        self.partiful.token_result = PartifulError("the Partiful login expired", login_expired=True)
        with self.assertRaises(PartifulError):
            self.run_feed("partiful:u1:mine")

    def test_partiful_ical_and_agi_house(self):
        self.store.set_secret("partiful_feed", {"url": FEED_URL})
        self.sync.reconcile()
        self.partiful.feed_result = [listing("pf-3", source="partiful")]
        self.agihouse.result = [listing("agi-1", source="agihouse")]
        self.run_feed("partiful:feed")
        self.run_feed("agihouse")
        self.assertEqual(self.partiful.calls, [("feed", FEED_URL)])
        self.assertEqual(self.listed(), {"pf-3": "partiful", "agi-1": "agihouse"})

    def test_unknown_feed_kinds_are_errors(self):
        with self.assertRaisesRegex(ValueError, "unknown feed kind"):
            self.sync._run(feed("x", kind="mystery"), SCHEDULED)

    def test_worker_failures_back_the_feed_off(self):
        self.luma.events[CAL] = LumaError(500, "oops")
        with self.assertLogs("events.sync", "WARNING"):
            self.sync.workers["luma"]._execute(Task(USER, 0, KEY))
        cal_feed = self.store.feed(KEY)
        self.assertEqual((cal_feed.failures, cal_feed.retry_at, cal_feed.last_error), (1, NOW + 600, "Luma 500: oops"))
        self.assertFalse(self.sync.is_due(cal_feed, NOW + 599))
        self.assertTrue(self.sync.is_due(cal_feed, NOW + 600))

    def test_worker_requeues_rate_limited_pulls_without_a_failure(self):
        self.luma.events[CAL] = LumaError(429, "slow down")
        with self.assertLogs("events.sync", "WARNING"):
            self.sync.workers["luma"]._execute(Task(USER, 0, KEY))
        self.assertEqual(self.queued("luma"), {KEY: USER})
        self.assertEqual(self.store.feed(KEY).failures, 0)


EVT = "evt-PrivateEvent001"
EVT_KEY = f"luma:{EVT}"


class LinkedEventTest(SyncTestCase):
    def test_an_event_added_by_link_shows_at_once_and_stays_current(self):
        self.sync.link_event(listing(EVT, name="Studio opening (save the date)", start=iso(24 * 50)))
        self.assertEqual(self.listed(), {EVT: "luma-links"})
        self.assertIn("luma-links", self.calendar_ids())
        link_feed = self.store.feed(EVT_KEY)
        self.assertEqual((link_feed.kind, link_feed.calendar_id), ("luma-event", "luma-links"))
        self.assertEqual(self.queued("luma"), {}, "the link brought the event, so nothing waits to be pulled")
        self.assertFalse(self.sync.is_due(link_feed, self.clock()))
        self.assertTrue(self.sync.is_due(link_feed, self.clock() + self.settings.luma_interval))

        # A pull brings the latest details, asking with the session so your RSVP comes along.
        self.store.set_secret("luma_session", {"session_key": "sess-1"})
        self.luma.single_events[EVT] = listing(EVT, name="Opening", start=iso(24 * 50), going_status="approved")
        self.sync._run(link_feed, SCHEDULED)
        self.assertEqual(self.luma.calls[-1], ("event", EVT, "sess-1"))
        self.assertEqual(self.store.linked_events()[0]["name"], "Opening")

    def test_a_rejected_session_is_dropped_and_the_event_pulled_without_it(self):
        self.sync.link_event(listing(EVT, start=iso(24)))
        self.store.set_secret("luma_session", {"session_key": "sess-1"})

        def answer(session_key):
            if session_key:
                raise LumaError(401, "signed out")
            return listing(EVT, name="Pulled without a session", start=iso(24))
        self.luma.single_events[EVT] = answer
        self.sync._run(self.store.feed(EVT_KEY), SCHEDULED)
        self.assertIsNone(self.store.get_secret("luma_session"))
        self.assertEqual([c[2] for c in self.luma.calls if c[0] == "event"], ["sess-1", None])
        self.assertEqual(self.store.linked_events()[0]["name"], "Pulled without a session")

    def test_a_pull_never_brings_back_a_removed_link(self):
        self.sync.link_event(listing(EVT, start=iso(24)))
        self.assertTrue(self.store.remove_linked_event(EVT))
        self.store.update_linked_event(listing(EVT, name="Pulled while being removed", start=iso(24)))
        self.assertEqual(self.store.linked_events(), [])
        self.sync.reconcile()
        self.assertIsNone(self.store.feed(EVT_KEY))
        self.assertEqual(self.listed(), {})
        self.assertNotIn("luma-links", self.calendar_ids(), "the calendar goes with its last event")

    def test_an_old_event_added_by_link_ages_out_with_its_feed(self):
        self.sync.link_event(listing(EVT, start=iso(24), end=iso(26)))
        self.clock.advance(70 * 86400)
        self.assertEqual(self.store.prune(), 1)
        self.assertEqual(self.store.linked_events(), [])
        self.sync.reconcile()
        self.assertIsNone(self.store.feed(EVT_KEY))


class LifecycleTest(SyncTestCase):
    def test_start_pulls_due_feeds_and_stop_ends_the_threads(self):
        self.agihouse.result = [listing("agi-1", source="agihouse")]
        self.sync.start()
        try:
            deadline = time.monotonic() + 2
            while not self.listed() and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            self.sync.stop()
        self.assertEqual(self.listed(), {"agi-1": "agihouse"})
        self.assertTrue(all(not w._thread.is_alive() for w in self.sync.workers.values()))
        self.sync._housekeeping.join(1)
        self.assertFalse(self.sync._housekeeping.is_alive())


if __name__ == "__main__":
    unittest.main()

"""Background sync. One worker per upstream pulls that upstream's feeds one at a time.

Each worker keeps a de-duplicated priority queue of feed keys:

- USER: the user pressed refresh (short pauses)
- NEW: a source was just added or changed (short pauses)
- SCHEDULED: a feed's last good pull is older than its refresh interval (long pauses for Luma)

Requests are never dropped: asking for a feed that is already queued only raises its priority.
Every pull is stored as soon as it finishes, so the app fills in while a pass runs, and a restart
resumes from each feed's own timestamps. A 429 from Luma pauses the Luma worker and retries.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import net
from . import preview as preview_mod
from . import reader as reader_mod
from .config import Settings
from .sources import agihouse as agihouse_src
from .sources import instagram as instagram_src
from .sources import luma as luma_src
from .sources import partiful as partiful_src
from .sources import sundry as sundry_src
from .store import Feed, Store
from .timeutil import to_iso

log = logging.getLogger(__name__)

USER, NEW, SCHEDULED = 0, 1, 2
CONFIG_FEED = "luma:config"
SUNDRY_FEED = "sundry"
MEDIA_DIR = "media"
# How many linked pages one pull reads at most; the rest wait for the next pull.
PREVIEWS_PER_PULL = 150
PREVIEW_SAVE_EVERY = 8
# One page that will not load is that page's trouble; this many in a row from one site is the site's.
PREVIEW_MISSES_IN_A_ROW = 4
# Instagram: only posts this recent are read, an account stays followed this long after Sundry Mag last
# featured it, and a post Claude could not be asked about is retried this many pulls.
INSTAGRAM_POST_DAYS = 45
INSTAGRAM_FOLLOW_DAYS = 60
INSTAGRAM_READ_ATTEMPTS = 3
CONFIG_NEVER_STALE = 10 * 365 * 86400
RATE_LIMIT_PAUSES = (45, 90, 180, 300)
IDLE_CHECK_S = 60
LUMA_MINE = {"id": "luma-mine", "source": "luma", "name": "Luma · my registrations", "slug": None, "avatar_url": None,
             "tint_color": "#f0c040", "url": "https://luma.com/home", "description": None}


def linked_feed(event_id: str) -> str:
    """The feed that keeps an event added by link current."""
    return f"luma:{event_id}"


class RateLimited(Exception):
    pass


def failure_backoff(failures: int) -> float:
    """10 min, 20 min, 40 min … capped at 4 h."""
    return min(4 * 3600, 600 * 2 ** max(0, failures - 1))


@dataclass(order=True)
class Task:
    priority: int
    seq: int
    key: str = field(compare=False)


class Worker:
    """Runs one upstream's feeds in priority order with pauses between requests."""

    def __init__(self, name: str, *, run: Callable[[Feed, int], None], due: Callable[[float], list[Feed]],
                 feed: Callable[[str], Feed | None], on_failure: Callable[[Feed, str, float], None],
                 spacing: float, fast_spacing: float, clock: Callable[[], float] = time.time):
        self.name = name
        self._run_feed = run
        self._due = due
        self._feed = feed
        self._on_failure = on_failure
        self._spacing = spacing
        self._fast_spacing = fast_spacing
        self._clock = clock
        self._cond = threading.Condition()
        self._queue: dict[str, Task] = {}
        self._seq = itertools.count()
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._last_request_at = 0.0
        self._paused_until = 0.0
        self._rate_limit_strikes = 0
        self._current: str | None = None
        self._run_done = 0
        self._run_failed = 0
        self._run_started_at: float | None = None
        self._last_run_finished_at: float | None = None
        self._last_error: str | None = None

    # ---------- control ----------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=f"sync-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout)

    def enqueue(self, keys: list[str], priority: int) -> None:
        with self._cond:
            for key in keys:
                existing = self._queue.get(key)
                if existing is None or priority < existing.priority:
                    self._queue[key] = Task(priority, next(self._seq), key)
            self._cond.notify_all()

    def status(self) -> dict:
        with self._cond:
            queued = len(self._queue)
            busy = self._current is not None or queued > 0
            return {
                "name": self.name, "busy": busy, "current": self._current,
                "done": self._run_done, "failed": self._run_failed,
                "total": self._run_done + self._run_failed + queued + (1 if self._current else 0),
                "queued_by_user": sum(1 for t in self._queue.values() if t.priority == USER),
                "started_at": self._run_started_at, "last_finished_at": self._last_run_finished_at,
                "paused_until": self._paused_until if self._paused_until > self._clock() else None,
                "last_error": self._last_error,
            }

    # ---------- loop ----------

    def _spacing_for(self, task: Task) -> float:
        return self._fast_spacing if task.priority < SCHEDULED else self._spacing

    def _next(self) -> Task | None:
        with self._cond:
            if self._stopping:
                return None
            now = self._clock()
            if not self._queue:
                try:
                    due = self._due(now)
                except Exception:
                    log.exception("%s: planning failed", self.name)
                    due = []
                for feed in due:
                    self._queue.setdefault(feed.key, Task(SCHEDULED, next(self._seq), feed.key))
            if not self._queue:
                if self._run_started_at is not None:
                    self._last_run_finished_at = now
                    self._run_started_at = None
                    self._run_done = self._run_failed = 0
                self._cond.wait(IDLE_CHECK_S)
                return None
            task = min(self._queue.values())
            ready_at = max(self._paused_until, self._last_request_at + self._spacing_for(task))
            if ready_at > now:
                # Wake early if something more urgent arrives; the loop re-evaluates.
                self._cond.wait(min(ready_at - now, IDLE_CHECK_S))
                return None
            del self._queue[task.key]
            if self._run_started_at is None:
                self._run_started_at = now
            feed = self._feed(task.key)
            self._current = feed.label if feed else task.key
            return task

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._stopping:
                    return
            try:
                task = self._next()
                if task is not None:
                    self._execute(task)
            except Exception:  # never let the worker die
                log.exception("%s: unexpected error", self.name)
                time.sleep(5)

    def _execute(self, task: Task) -> None:
        feed = self._feed(task.key)
        try:
            if feed is None:
                return
            self._run_feed(feed, task.priority)
            self._rate_limit_strikes = 0
            with self._cond:
                self._run_done += 1
        except RateLimited as e:
            pause = RATE_LIMIT_PAUSES[min(self._rate_limit_strikes, len(RATE_LIMIT_PAUSES) - 1)]
            self._rate_limit_strikes += 1
            log.warning("%s: rate limited on %s; pausing %ss", self.name, task.key, pause)
            with self._cond:
                self._paused_until = self._clock() + pause
                self._last_error = f"{e} Pausing {pause}s."
                existing = self._queue.get(task.key)
                if existing is None or task.priority < existing.priority:
                    self._queue[task.key] = task
        except Exception as e:  # upstream or parsing failure: back off this feed only
            failures = (feed.failures if feed else 0) + 1
            message = str(e) or e.__class__.__name__
            log.warning("%s: %s failed (%s): %s", self.name, task.key, failures, message)
            if feed is not None:
                try:
                    self._on_failure(feed, message, failure_backoff(failures))
                except Exception:
                    log.exception("%s: could not record failure", self.name)
            with self._cond:
                self._run_failed += 1
                self._last_error = f"{feed.label if feed else task.key}: {message}"
        finally:
            with self._cond:
                self._last_request_at = self._clock()
                self._current = None


class Sync:
    """Owns the workers and knows how to pull each kind of feed."""

    def __init__(self, store: Store, settings: Settings, *, luma: luma_src.LumaClient | None = None,
                 partiful: partiful_src.PartifulClient | None = None, agihouse: agihouse_src.AgiHouseClient | None = None,
                 sundry: sundry_src.SundryClient | None = None, previews: preview_mod.PreviewClient | None = None,
                 instagram: instagram_src.InstagramClient | None = None, reader: reader_mod.ClaudeReader | None = None,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.settings = settings
        self.luma = luma or luma_src.LumaClient()
        self.partiful = partiful or partiful_src.PartifulClient()
        self.agihouse = agihouse or agihouse_src.AgiHouseClient()
        self.sundry = sundry or sundry_src.SundryClient()
        self.previews = previews or preview_mod.PreviewClient(settings.state_dir / MEDIA_DIR)
        self.instagram = instagram or instagram_src.InstagramClient()
        self.reader = reader or reader_mod.ClaudeReader(settings.claude, settings.claude_model, settings.state_dir / MEDIA_DIR)
        self.clock = clock
        self._reconcile_lock = threading.Lock()
        intervals = {
            "calendar": settings.luma_interval, "luma-following": settings.personal_interval,
            "luma-mine": settings.personal_interval, "luma-ics": settings.personal_interval,
            "partiful-mine": settings.partiful_interval, "partiful-following": settings.partiful_interval,
            "partiful-feed": settings.partiful_interval, "agihouse": settings.agihouse_interval,
            "sundry": settings.sundry_interval, "instagram": settings.instagram_interval,
            # Resolved once; start() makes it due again whenever the configured list changes.
            "luma-config": CONFIG_NEVER_STALE,
            "luma-event": settings.luma_interval,
        }
        self.intervals = intervals
        self.workers: dict[str, Worker] = {}
        for source, spacing in (("luma", settings.luma_spacing), ("partiful", 1.0), ("agihouse", 1.0), ("sundry", 1.0),
                                ("instagram", settings.instagram_spacing)):
            # Instagram gets no faster for being asked by hand: a burst over every account is what it refuses.
            fast = spacing if source == "instagram" else min(spacing, settings.manual_spacing)
            self.workers[source] = Worker(source, run=self._run, due=self._due_for(source), feed=store.feed,
                                          on_failure=self._record_failure, spacing=spacing, fast_spacing=fast, clock=clock)
        self._housekeeping: threading.Thread | None = None
        self._stopping = threading.Event()

    # ---------- lifecycle ----------

    def start(self) -> None:
        self.reconcile()
        self._apply_configured_list()
        for worker in self.workers.values():
            worker.start()
        self._housekeeping = threading.Thread(target=self._housekeep, name="sync-housekeeping", daemon=True)
        self._housekeeping.start()

    def _apply_configured_list(self) -> None:
        """A changed EVENTS_LUMA_CALENDARS is resolved again at once; an unchanged list keeps its
        schedule, including the backoff of a link that could not be resolved."""
        configured = json.dumps(list(self.settings.luma_calendars))
        if self.store.get_meta("luma_config_tokens") != configured:
            if self.store.feed(CONFIG_FEED):
                self.store.reset_feed(CONFIG_FEED)
            self.store.set_meta("luma_config_tokens", configured)

    def stop(self) -> None:
        self._stopping.set()
        for worker in self.workers.values():
            worker.stop()

    def _housekeep(self) -> None:
        while not self._stopping.wait(6 * 3600):
            try:
                removed = self.store.prune()
                if removed:
                    log.info("pruned %s old listings", removed)
                    self.reconcile()  # drops the feeds of events added by link that aged out
                self.previews.prune(self.store.prune_previews() | self.store.prune_instagram(), self.clock() - 86400)
            except Exception:
                log.exception("housekeeping failed")

    # ---------- what should exist ----------

    def reconcile(self) -> list[str]:
        """Make the feeds match the configured sources. Returns keys of feeds that were just created."""
        with self._reconcile_lock:
            store = self.store
            before = {f.key for f in store.feeds()}
            wanted: dict[str, dict] = {}
            for cal in store.calendars():
                if cal["source"] == "luma" and cal["id"].startswith("cal-"):
                    wanted[f"luma:{cal['id']}"] = {"source": "luma", "kind": "calendar", "calendar_id": cal["id"],
                                                   "label": cal["name"] or cal["id"]}
            session = store.get_secret("luma_session")
            ics_cfg = store.get_secret("luma_ics")
            if session or ics_cfg:
                store.upsert_calendar(LUMA_MINE, "builtin")
            else:
                store.remove_calendar(LUMA_MINE["id"], "builtin")
            if session:
                wanted["luma:following"] = {"source": "luma", "kind": "luma-following", "calendar_id": None,
                                            "label": "Luma · followed calendars"}
                wanted["luma:mine"] = {"source": "luma", "kind": "luma-mine", "calendar_id": LUMA_MINE["id"],
                                       "label": "Luma · my registrations"}
            if ics_cfg:
                wanted["luma:ics"] = {"source": "luma", "kind": "luma-ics", "calendar_id": LUMA_MINE["id"],
                                      "label": "Luma · personal iCal feed"}
            accounts = store.partiful_accounts()
            feed_cfg = store.get_secret("partiful_feed")
            for cal, needed in ((partiful_src.MINE_CALENDAR, bool(accounts or feed_cfg)),
                                (partiful_src.FOLLOWING_CALENDAR, bool(accounts))):
                if needed:
                    store.upsert_calendar(cal, "builtin")
                else:
                    store.remove_calendar(cal["id"], "builtin")
            for account in accounts:
                who = account.get("name") or account["uid"]
                wanted[f"partiful:{account['uid']}:mine"] = {"source": "partiful", "kind": "partiful-mine",
                                                             "calendar_id": partiful_src.MINE_CALENDAR["id"],
                                                             "label": f"Partiful · {who} · my events"}
                wanted[f"partiful:{account['uid']}:following"] = {"source": "partiful", "kind": "partiful-following",
                                                                  "calendar_id": partiful_src.FOLLOWING_CALENDAR["id"],
                                                                  "label": f"Partiful · {who} · people I follow"}
            if feed_cfg:
                wanted["partiful:feed"] = {"source": "partiful", "kind": "partiful-feed",
                                           "calendar_id": partiful_src.MINE_CALENDAR["id"], "label": "Partiful · iCal link"}
            if self.settings.agihouse:
                store.upsert_calendar(agihouse_src.CALENDAR, "builtin")
                wanted["agihouse"] = {"source": "agihouse", "kind": "agihouse", "calendar_id": "agihouse",
                                      "label": "AGI House"}
            else:
                store.remove_calendar(agihouse_src.CALENDAR["id"], "builtin")
            if self.settings.sundry:
                store.upsert_calendar(sundry_src.CALENDAR, "builtin")
                wanted[SUNDRY_FEED] = {"source": "sundry", "kind": "sundry", "calendar_id": sundry_src.CALENDAR["id"],
                                       "label": "Sundry Mag · Cape Town"}
            else:
                store.remove_calendar(sundry_src.CALENDAR["id"], "builtin")
            if self.instagram_on:
                for cal in store.calendars():
                    if cal["source"] == "instagram":
                        wanted[cal["id"]] = {"source": "instagram", "kind": "instagram", "calendar_id": cal["id"],
                                             "label": f"Instagram · {cal['name'] or cal['id']}"}
            linked = store.linked_events()
            if linked:
                store.upsert_calendar(luma_src.LINKED_CALENDAR, "builtin")
            else:
                store.remove_calendar(luma_src.LINKED_CALENDAR["id"], "builtin")
            for link in linked:
                wanted[linked_feed(link["event_id"])] = {"source": "luma", "kind": "luma-event",
                                                         "calendar_id": luma_src.LINKED_CALENDAR["id"],
                                                         "label": f"Luma · {link['name'] or link['event_id']}"}
            if self.settings.luma_calendars:
                wanted[CONFIG_FEED] = {"source": "luma", "kind": "luma-config", "calendar_id": None,
                                       "label": "Luma · calendars from configuration"}
            else:
                store.set_origin_calendars("config", [])

            for key in before - set(wanted):
                store.delete_feed(key)
            created = []
            for key, spec in wanted.items():
                try:
                    store.ensure_feed(key, **spec)
                except sqlite3.IntegrityError:
                    continue  # its calendar was removed a moment ago
                if key not in before:
                    created.append(key)
            return sorted(created)

    @property
    def instagram_on(self) -> bool:
        """Accounts are followed only when their posts can be read into events."""
        return self.settings.instagram and self.reader.available

    def link_event(self, listing: dict) -> None:
        """Add an event by link and show it at once; its feed keeps it current from then on."""
        self.store.add_linked_event(listing)
        key = linked_feed(listing["id"])
        created = self.reconcile()
        self.store.replace_listings(key, [listing])
        # Sources added just before this one (calendars pasted alongside it) still go to the front.
        self._enqueue([k for k in created if k != key], NEW)

    def sources_changed(self, *, refresh: list[str] | None = None) -> None:
        """Call after the user adds or removes a source. New feeds (and ``refresh`` keys) go to the front."""
        new = self.reconcile()
        self._enqueue(new + list(refresh or []), NEW)

    def refresh(self, source: str | None = None) -> int:
        """The user asked for fresh data. Returns how many feeds were queued."""
        self.reconcile()
        feeds = [f for f in self.store.feeds() if source in (None, f.source)]
        self._enqueue([f.key for f in feeds], USER)
        return len(feeds)

    def _enqueue(self, keys: list[str], priority: int) -> None:
        by_source: dict[str, list[str]] = {}
        for key in keys:
            feed = self.store.feed(key)
            if feed:
                by_source.setdefault(feed.source, []).append(key)
        for source, source_keys in by_source.items():
            self.workers[source].enqueue(source_keys, priority)

    def status(self) -> dict:
        return {name: worker.status() for name, worker in self.workers.items()}

    # ---------- scheduling ----------

    def is_due(self, feed: Feed, now: float) -> bool:
        if feed.failures > 0:
            return feed.retry_at is None or now >= feed.retry_at
        if feed.last_ok_at is None:
            # Never pulled. If an attempt started and never finished (crash), wait a little.
            return feed.last_attempt_at is None or now - feed.last_attempt_at >= 300
        return now - feed.last_ok_at >= self.intervals.get(feed.kind, self.settings.luma_interval)

    def _due_for(self, source: str) -> Callable[[float], list[Feed]]:
        def due(now: float) -> list[Feed]:
            feeds = [f for f in self.store.feeds(source) if self.is_due(f, now)]
            # Personal feeds first, then never-pulled, then the stalest.
            return sorted(feeds, key=lambda f: (f.kind == "calendar", f.last_ok_at is not None, f.last_ok_at or 0))
        return due

    # ---------- pulling ----------

    def _record_failure(self, feed: Feed, message: str, retry_in: float) -> None:
        self.store.record_failure(feed.key, message, retry_in)

    def _run(self, feed: Feed, priority: int) -> None:
        self.store.mark_attempt(feed.key)
        handler = {
            "calendar": self._pull_luma_calendar, "luma-following": self._pull_luma_following,
            "luma-config": self._pull_luma_config, "luma-event": self._pull_luma_event,
            "luma-mine": self._pull_luma_mine, "luma-ics": self._pull_luma_ics,
            "partiful-mine": self._pull_partiful, "partiful-following": self._pull_partiful,
            "partiful-feed": self._pull_partiful_feed, "agihouse": self._pull_agihouse,
            "sundry": self._pull_sundry, "instagram": self._pull_instagram,
        }.get(feed.kind)
        if handler is None:
            raise ValueError(f"unknown feed kind {feed.kind}")
        handler(feed)

    def _luma_session(self) -> str | None:
        session = self.store.get_secret("luma_session")
        return session.get("session_key") if session else None

    def _luma_call(self, fn: Callable[[], object]) -> object:
        try:
            return fn()
        except luma_src.LumaError as e:
            if e.rate_limited:
                raise RateLimited("Luma is rate limiting requests.") from None
            raise

    def _drop_luma_session(self) -> None:
        log.warning("Luma rejected the saved session; forgetting it")
        self.store.delete_secret("luma_session")
        self.store.set_meta("luma_session_notice", "Luma signed this app out. Paste a fresh session cookie to keep syncing your follows.")
        self.reconcile()

    def _pull_luma_config(self, feed: Feed) -> None:
        """Follow the calendars named in EVENTS_LUMA_CALENDARS. A cal- id stands on its own, so it is
        followed even when Luma cannot be reached; its name fills in on its first pull. A link or slug
        must be resolved, and one that fails is retried with the feed's backoff."""
        tokens = [(luma_src.link_tokens(t) or [t])[0] for t in self.settings.luma_calendars]
        try:
            resolved = json.loads(self.store.get_meta("luma_config_resolved") or "{}")
        except ValueError:
            resolved = {}
        failed = []
        try:
            for token in tokens:
                if token in resolved:
                    continue
                try:
                    resolved[token] = self._luma_call(lambda: self.luma.resolve_calendar(token))
                except (ValueError, luma_src.LumaError) as e:
                    if token.startswith("cal-"):
                        resolved[token] = {"id": token, "source": "luma", "name": token, "slug": None, "avatar_url": None,
                                           "tint_color": None, "url": f"{luma_src.SITE}/{token}", "description": None}
                    else:
                        failed.append(f"{token} ({e})")
        finally:
            # Keep what was resolved even when Luma rate-limits us part way; the retry resumes.
            resolved = {t: cal for t, cal in resolved.items() if t in tokens}
            self.store.set_meta("luma_config_resolved", json.dumps(resolved))
        self.store.set_origin_calendars("config", list(resolved.values()))
        self._enqueue(self.reconcile(), NEW)
        if failed:
            raise ValueError("could not resolve " + "; ".join(failed))
        self.store.record_success(feed.key, len(resolved))

    def _pull_luma_calendar(self, feed: Feed) -> None:
        calendar_id = feed.calendar_id or feed.key.split(":", 1)[1]
        key = self._luma_session()
        try:
            events = self._luma_call(lambda: self.luma.calendar_events(calendar_id, key))
        except luma_src.LumaError as e:
            if not (key and e.unauthorized):
                raise
            self._drop_luma_session()
            events = self._luma_call(lambda: self.luma.calendar_events(calendar_id, None))
        self.store.replace_listings(feed.key, events)  # type: ignore[arg-type]
        # The calendar's own events carry its current name and picture (a calendar added by id has neither).
        own = next((e["presenter"] for e in events if (e.get("presenter") or {}).get("id") == calendar_id), None)  # type: ignore[union-attr]
        if own:
            self.store.update_calendar_details(own)

    def _pull_luma_event(self, feed: Feed) -> None:
        event_id = feed.key.split(":", 1)[1]
        key = self._luma_session()
        try:
            listing = self._luma_call(lambda: self.luma.event(event_id, key))
        except luma_src.LumaError as e:
            if not (key and e.unauthorized):
                raise
            self._drop_luma_session()
            listing = self._luma_call(lambda: self.luma.event(event_id, None))
        self.store.replace_listings(feed.key, [listing])
        self.store.update_linked_event(listing)

    def _pull_luma_following(self, feed: Feed) -> None:
        key = self._luma_session()
        if not key:
            return
        try:
            calendars = self._luma_call(lambda: self.luma.following(key))
        except luma_src.LumaError as e:
            if e.unauthorized:
                self._drop_luma_session()
                return
            raise
        had = sum(1 for c in self.store.calendars() if "followed" in c["origins"])
        if not calendars and had >= 3:
            raise ValueError(f"Luma returned no followed calendars (had {had}); keeping them until it does")
        self.store.set_origin_calendars("followed", calendars)  # type: ignore[arg-type]
        self.store.record_success(feed.key, len(calendars))  # type: ignore[arg-type]
        self._enqueue(self.reconcile(), NEW)

    def _pull_luma_mine(self, feed: Feed) -> None:
        key = self._luma_session()
        if not key:
            return
        try:
            events = self._luma_call(lambda: self.luma.my_events(key))
        except luma_src.LumaError as e:
            if e.unauthorized:
                self._drop_luma_session()
                return
            raise
        self.store.replace_listings(feed.key, events)  # type: ignore[arg-type]

    def _pull_luma_ics(self, feed: Feed) -> None:
        cfg = self.store.get_secret("luma_ics")
        if not cfg:
            return
        self.store.replace_listings(feed.key, self.luma.personal_feed(cfg["url"]))

    def _pull_partiful(self, feed: Feed) -> None:
        _, uid, which = feed.key.split(":", 2)
        account = next((a for a in self.store.partiful_accounts() if a["uid"] == uid), None)
        if account is None:
            return
        fresh = self.partiful.id_token(account)
        if fresh is not account:
            self.store.save_partiful_account(fresh)
            if fresh.get("name") and fresh.get("name") != account.get("name"):
                self.reconcile()  # feed labels include the account name
        name = "getMyUpcomingEventsForHomePage" if which == "mine" else "getMyFollowedEvents"
        self.store.replace_listings(feed.key, self.partiful.events(fresh, name))

    def _pull_partiful_feed(self, feed: Feed) -> None:
        cfg = self.store.get_secret("partiful_feed")
        if not cfg:
            return
        self.store.replace_listings(feed.key, self.partiful.feed(cfg["url"]))

    def _pull_agihouse(self, feed: Feed) -> None:
        self.store.replace_listings(feed.key, self.agihouse.events())

    def _pull_sundry(self, feed: Feed) -> None:
        """A subscriber post read without a working session comes back as its opening only. That is
        still stored the first time, but it never replaces a fuller week already indexed."""
        session = self.store.get_secret("substack_session")
        pull = self.sundry.events((session or {}).get("sid"))
        now = to_iso(self.store.clock())
        upcoming = sum(1 for ev in pull.events if ev["start_at"] >= now)
        self.store.set_meta("sundry_locked", json.dumps(pull.locked))
        if pull.locked and upcoming < self.store.upcoming_count(feed.key):
            raise ValueError("Substack sent only the opening of “" + pull.locked[0] + "”, which is for subscribers; "
                             "keeping the events already indexed")
        self._store_with_previews(feed, pull.events)
        self._read_previews(feed, pull.events, now)
        self._follow_organisers(pull.events)

    def _store_with_previews(self, feed: Feed, events: list[dict]) -> None:
        saved = self.store.previews(ev["url"] for ev in events)
        self.store.replace_listings(feed.key, [preview_mod.apply(ev, saved.get(ev["url"])) for ev in events])

    def _read_previews(self, feed: Feed, events: list[dict], now: str) -> None:
        """Fetch the picture, organiser and words of each coming event's linked page, once per page.
        The listings are stored again as these arrive, so the app fills in while the pull runs."""
        yesterday = to_iso(self.store.clock() - 86400)
        coming = [ev["url"] for ev in events if ev["start_at"] >= yesterday and ev["url"] != sundry_src.SITE]
        due = self.store.previews_due(coming)[:PREVIEWS_PER_PULL]
        blocked: set[str] = set()
        misses: dict[str, int] = {}  # failures in a row, by site
        fresh = 0
        for url in due:
            host = preview_mod.host_of(url)
            if host in blocked:
                continue
            if self._stopping.wait(self.settings.preview_spacing):
                break
            try:
                self.store.save_preview(url, self.previews.get(url))
                fresh += 1
                misses[host] = 0
                continue_reading = True
            except preview_mod.PreviewError as e:
                misses[host] = misses.get(host, 0) + 1
                continue_reading = not e.blocked and misses[host] < PREVIEW_MISSES_IN_A_ROW
                if not e.blocked:
                    self.store.fail_preview(url, str(e))
                if not continue_reading:
                    log.warning("sundry: %s is turning requests away (%s); its other pages wait for the next pull", host, e)
            except Exception as e:  # one odd page must not cost the others their pictures
                log.warning("sundry: could not read %s: %s", net.redact(url), e)
                self.store.fail_preview(url, str(e) or e.__class__.__name__)
                continue_reading = True
            if not continue_reading:
                blocked.add(host)
            if fresh and fresh % PREVIEW_SAVE_EVERY == 0:
                self._store_with_previews(feed, events)
        if fresh:
            self._store_with_previews(feed, events)

    # ---------- Instagram accounts ----------

    def _follow_organisers(self, events: list[dict]) -> None:
        """Follow the Instagram accounts behind the events Sundry Mag lists, for as long as it keeps featuring
        them, unless the user removed the account."""
        if not self.instagram_on:
            self.store.set_origin_calendars("sundry", [])
            return
        now = self.store.clock()
        try:
            seen = json.loads(self.store.get_meta("instagram_seen") or "{}")
            dismissed = set(json.loads(self.store.get_meta("instagram_dismissed") or "[]"))
        except ValueError:
            seen, dismissed = {}, set()
        for found in self.store.previews(ev["url"] for ev in events).values():
            author = found.get("author") or {}
            username = str(author.get("id") or "").removeprefix("instagram:")
            if str(author.get("id") or "").startswith("instagram:") and instagram_src.USERNAME_RE.match(username):
                seen[username] = {"name": author.get("name"), "at": now}
        seen = {name: entry for name, entry in seen.items() if now - entry.get("at", 0) <= INSTAGRAM_FOLLOW_DAYS * 86400}
        self.store.set_meta("instagram_seen", json.dumps(seen))
        self.store.set_origin_calendars("sundry", [instagram_src.calendar(name, entry.get("name"))
                                                   for name, entry in sorted(seen.items()) if name not in dismissed])
        self._enqueue(self.reconcile(), NEW)

    def _pull_instagram(self, feed: Feed) -> None:
        """Save the account's new posts, have Claude read the ones not read yet, and list the events they announce.
        Posts fall off Instagram's list of six, so the listing is built from every saved post."""
        username = feed.key.split(":", 1)[1]
        page = self.instagram.account(username)
        now = self.store.clock()
        avatar = self.previews.keep_image(page["avatar_url"]) if page.get("avatar_url") else None
        self.store.update_calendar_details({"id": feed.calendar_id, "name": page.get("name"), "avatar_url": avatar})
        known = {post["code"] for post in self.store.instagram_posts(username)}
        recent = to_iso(now - INSTAGRAM_POST_DAYS * 86400)
        for post in page["posts"]:
            if post["code"] not in known and post["taken_at"] >= recent:
                image = self.previews.keep_image(post["image_url"]) if post.get("image_url") else None
                self.store.add_instagram_post(username, post, image)
        account = {"username": username, "name": page.get("name") or (self.store.calendar(feed.calendar_id or "") or {}).get("name"),
                   "avatar": avatar or (self.store.calendar(feed.calendar_id or "") or {}).get("avatar_url")}
        posts = self.store.instagram_posts(username)
        unread = [p for p in posts if p["events"] is None and p["attempts"] < INSTAGRAM_READ_ATTEMPTS][:reader_mod.MAX_POSTS]
        failure = None
        if unread:
            today = datetime.fromtimestamp(now, sundry_src.SAST).date()
            self.store.count_instagram_attempt(p["code"] for p in unread)
            try:
                readings = self.reader.read(account, unread, today)
            except reader_mod.ReaderError as e:
                failure = e  # the events already read are still listed below
            else:
                for post in unread:
                    self.store.set_instagram_reading(post["code"], readings.get(post["code"], []))
                posts = self.store.instagram_posts(username)
        listings = [listing for post in posts for listing in instagram_listings(account, post)]
        if failure is None or listings:
            self.store.replace_listings(feed.key, listings)
        if failure is not None:
            raise ValueError(f"could not read @{username}'s new posts: {failure}")


def instagram_listings(account: dict, post: dict) -> list[dict]:
    """The events one read post announces, as listings in Cape Town."""
    listings = []
    url = f"{instagram_src.SITE}/p/{post['code']}/"
    for event in post.get("events") or []:
        try:
            day = date.fromisoformat(event["date"])
        except (KeyError, TypeError, ValueError):
            continue
        midnight = datetime(day.year, day.month, day.day, tzinfo=sundry_src.SAST)

        def at(clock: str | None) -> datetime | None:
            return midnight + timedelta(hours=int(clock[:2]), minutes=int(clock[3:])) if clock else None

        start, end = at(event.get("start")), at(event.get("end"))
        all_day = start is None
        if all_day:
            start, end = midnight, midnight + timedelta(days=1)
        elif end is not None and end <= start:
            end += timedelta(days=1)
        location = sundry_src.empty_location("offline")
        location.update({"venue": event.get("venue") or account.get("name"), "address": event.get("address"),
                         "city": "Cape Town", "region": "Western Cape", "country": "ZA"})
        price = event.get("price") or ""
        notes = [event.get("summary"), f"Cost: {price}" if price and not sundry_src.PLAIN_COST_RE.fullmatch(price) else None]
        identity = f"{post['code']}|{event['date']}|{event['title'].lower()}"
        listings.append({
            "id": "ig-" + hashlib.sha1(identity.encode()).hexdigest()[:16], "source": "instagram", "name": event["title"],
            "url": url, "start_at": to_iso(start), "end_at": to_iso(end) if end else None, "all_day": all_day,
            "timezone": sundry_src.TIMEZONE, "cover_url": post.get("image"), "location": location,
            "presenter": {"id": instagram_src.calendar_id(account["username"]), "name": account.get("name") or f"@{account['username']}",
                          "avatar_url": account.get("avatar"), "url": f"{instagram_src.SITE}/{account['username']}/",
                          "description": None},
            "hosts": [], "tags": preview_mod.hashtags(post.get("caption")), "ticket": sundry_src.parse_cost(price),
            "guest_count": None, "going_status": None, "description": "\n\n".join(n for n in notes if n) or None, "links": [],
        })
    return listings


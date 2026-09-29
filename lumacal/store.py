"""Every read and write of app state. Nothing outside this module writes SQL.

Vocabulary:

- A *calendar* is something the user sees and can mute: a Luma calendar, "Partiful", "AGI House".
  It stays while at least one *origin* claims it (followed via session, bookmarklet import, added
  by link, or built in).
- A *feed* is one unit the sync workers pull, e.g. one Luma calendar or one Partiful account's
  followed events. Each feed shows its listings under one calendar.
- A *listing* is one event as one feed reported it. The same event can be listed by several
  feeds; ``catalog`` merges them.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .db import Database
from .sources import scrub
from .timeutil import to_iso

ORIGINS = ("followed", "import", "link", "builtin")
HISTORY_KEEP_DAYS = 60  # past events kept for the month view before pruning


@dataclass(frozen=True)
class Feed:
    key: str
    source: str
    kind: str
    calendar_id: str | None
    label: str
    last_attempt_at: float | None
    last_ok_at: float | None
    last_error: str | None
    failures: int
    retry_at: float | None
    item_count: int | None

    def to_dict(self) -> dict:
        return {
            "key": self.key, "source": self.source, "kind": self.kind, "calendar_id": self.calendar_id,
            "label": self.label, "last_attempt_at": self.last_attempt_at, "last_ok_at": self.last_ok_at,
            "last_error": self.last_error, "failures": self.failures, "retry_at": self.retry_at,
            "item_count": self.item_count,
        }


@dataclass(frozen=True)
class ReplaceResult:
    changed: bool
    new_event_ids: list[str]


class Store:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time):
        self.db = db
        self.clock = clock

    # ---------- meta and change tracking ----------

    def get_meta(self, key: str) -> str | None:
        row = self.db.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value))

    def data_version(self) -> int:
        return int(self.get_meta("data_version") or 0)

    @staticmethod
    def _bump(conn) -> None:
        """Mark that anything the event list depends on changed. Call inside a write transaction."""
        conn.execute("INSERT INTO meta (key, value) VALUES ('data_version', '1') "
                     "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")

    # ---------- secrets ----------

    def get_secret(self, key: str) -> dict | None:
        row = self.db.query_one("SELECT value FROM secrets WHERE key = ?", (key,))
        if not row:
            return None
        try:
            value = json.loads(row["value"])
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def set_secret(self, key: str, value: dict) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO secrets (key, value, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                         (key, json.dumps(value), self.clock()))

    def delete_secret(self, key: str) -> bool:
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM secrets WHERE key = ?", (key,)).rowcount > 0

    # ---------- preferences ----------

    def prefs(self) -> dict:
        out = {}
        for row in self.db.query("SELECT key, value FROM prefs"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except ValueError:
                continue
        return out

    def set_pref(self, key: str, value: object) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO prefs (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (key, json.dumps(value)))
            self._bump(conn)

    # ---------- Partiful accounts ----------

    def partiful_accounts(self) -> list[dict]:
        return [dict(row) for row in self.db.query("SELECT * FROM partiful_accounts ORDER BY added_at")]

    def save_partiful_account(self, account: dict) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO partiful_accounts (uid, name, refresh_token, id_token, expires_at, added_at) "
                "VALUES (:uid, :name, :refresh_token, :id_token, :expires_at, :added_at) "
                "ON CONFLICT(uid) DO UPDATE SET name = COALESCE(excluded.name, partiful_accounts.name), "
                "refresh_token = excluded.refresh_token, id_token = excluded.id_token, expires_at = excluded.expires_at",
                {"uid": account["uid"], "name": account.get("name"), "refresh_token": account["refresh_token"],
                 "id_token": account.get("id_token"), "expires_at": account.get("expires_at") or 0,
                 "added_at": account.get("added_at") or self.clock()})

    def delete_partiful_account(self, uid: str) -> bool:
        """Forget one Partiful login together with the feeds and listings pulled with it."""
        prefix = f"partiful:{uid}:"
        with self.db.transaction() as conn:
            gone = conn.execute("DELETE FROM partiful_accounts WHERE uid = ?", (uid,)).rowcount > 0
            keys = [row["key"] for row in conn.execute("SELECT key FROM feeds WHERE source = 'partiful'")
                    if row["key"].startswith(prefix)]
            conn.executemany("DELETE FROM feeds WHERE key = ?", [(key,) for key in keys])
            self._bump(conn)
            return gone

    # ---------- calendars ----------

    def calendars(self) -> list[dict]:
        rows = self.db.query(
            "SELECT c.*, GROUP_CONCAT(o.origin) AS origin_list FROM calendars c "
            "LEFT JOIN calendar_origins o ON o.calendar_id = c.id GROUP BY c.id ORDER BY c.name COLLATE NOCASE")
        out = []
        for row in rows:
            item = {key: row[key] for key in ("id", "source", "name", "slug", "avatar_url", "tint_color", "url",
                                              "description", "added_at")}
            item["origins"] = sorted(set((row["origin_list"] or "").split(","))) if row["origin_list"] else []
            out.append(item)
        return out

    def calendar(self, calendar_id: str) -> dict | None:
        return next((c for c in self.calendars() if c["id"] == calendar_id), None)

    def upsert_calendar(self, cal: dict, origin: str) -> bool:
        """Add or refresh a calendar and record who claims it. Returns True when the calendar is new.

        Writes (and bumps the data version) only when something actually changed."""
        assert origin in ORIGINS, origin
        cal = scrub(cal)  # type: ignore[assignment]
        fields = {"source": cal["source"], "name": cal.get("name") or "", "slug": cal.get("slug"),
                  "avatar_url": cal.get("avatar_url"), "tint_color": cal.get("tint_color"), "url": cal.get("url"),
                  "description": cal.get("description")}
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM calendars WHERE id = ?", (cal["id"],)).fetchone()
            if row is None:
                conn.execute("INSERT INTO calendars (id, source, name, slug, avatar_url, tint_color, url, description, added_at) "
                             "VALUES (:id, :source, :name, :slug, :avatar_url, :tint_color, :url, :description, :now)",
                             {"id": cal["id"], "now": self.clock(), **fields})
                changed = True
            else:
                # Keep what we know when a sparser source (an old import, a bare id) omits a field.
                merged = {k: (v if v not in (None, "") else row[k]) for k, v in fields.items()}
                changed = any(merged[k] != row[k] for k in merged)
                if changed:
                    conn.execute("UPDATE calendars SET source = :source, name = :name, slug = :slug, avatar_url = :avatar_url, "
                                 "tint_color = :tint_color, url = :url, description = :description WHERE id = :id",
                                 {"id": cal["id"], **merged})
            claimed = conn.execute("INSERT OR IGNORE INTO calendar_origins (calendar_id, origin) VALUES (?, ?)",
                                   (cal["id"], origin)).rowcount > 0
            if changed or claimed:
                self._bump(conn)
            return row is None

    def set_origin_calendars(self, origin: str, cals: Iterable[dict]) -> tuple[list[str], list[str]]:
        """Make ``cals`` the complete set claimed by ``origin``. Returns (added ids, removed ids).

        Calendars that lose their last origin are deleted with their feeds and listings."""
        assert origin in ORIGINS, origin
        cals = list(cals)
        wanted = {c["id"] for c in cals}
        with self.db.transaction() as conn:
            before = {row["id"] for row in conn.execute("SELECT id FROM calendars")}
            claimed = {row["calendar_id"] for row in conn.execute(
                "SELECT calendar_id FROM calendar_origins WHERE origin = ?", (origin,))}
            for cal in cals:
                self.upsert_calendar(cal, origin)
            dropped = 0
            for calendar_id in claimed - wanted:
                dropped += conn.execute("DELETE FROM calendar_origins WHERE calendar_id = ? AND origin = ?",
                                        (calendar_id, origin)).rowcount
            if dropped:
                self._drop_orphans(conn)
                self._bump(conn)
            after = {row["id"] for row in conn.execute("SELECT id FROM calendars")}
        return sorted(after - before), sorted(before - after)

    def remove_calendar(self, calendar_id: str, origin: str | None = None) -> bool:
        """Drop one origin's claim, or every claim. Returns True when the calendar is gone."""
        with self.db.transaction() as conn:
            if origin is None:
                dropped = conn.execute("DELETE FROM calendar_origins WHERE calendar_id = ?", (calendar_id,)).rowcount
            else:
                dropped = conn.execute("DELETE FROM calendar_origins WHERE calendar_id = ? AND origin = ?",
                                       (calendar_id, origin)).rowcount
            if dropped:
                self._drop_orphans(conn)
                self._bump(conn)
            return conn.execute("SELECT 1 FROM calendars WHERE id = ?", (calendar_id,)).fetchone() is None

    @staticmethod
    def _drop_orphans(conn) -> None:
        conn.execute("DELETE FROM calendars WHERE id NOT IN (SELECT calendar_id FROM calendar_origins)")

    # ---------- feeds ----------

    def ensure_feed(self, key: str, *, source: str, kind: str, calendar_id: str | None, label: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO feeds (key, source, kind, calendar_id, label) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET source = excluded.source, kind = excluded.kind, "
                "calendar_id = excluded.calendar_id, label = excluded.label",
                (key, source, kind, calendar_id, label))

    def delete_feed(self, key: str) -> None:
        with self.db.transaction() as conn:
            if conn.execute("DELETE FROM feeds WHERE key = ?", (key,)).rowcount:
                self._bump(conn)

    def feeds(self, source: str | None = None) -> list[Feed]:
        if source:
            rows = self.db.query("SELECT * FROM feeds WHERE source = ? ORDER BY key", (source,))
        else:
            rows = self.db.query("SELECT * FROM feeds ORDER BY key")
        return [Feed(**dict(row)) for row in rows]

    def feed(self, key: str) -> Feed | None:
        row = self.db.query_one("SELECT * FROM feeds WHERE key = ?", (key,))
        return Feed(**dict(row)) if row else None

    def mark_attempt(self, key: str) -> None:
        self.db.execute("UPDATE feeds SET last_attempt_at = ? WHERE key = ?", (self.clock(), key))

    def record_failure(self, key: str, error: str, retry_in: float) -> None:
        now = self.clock()
        self.db.execute("UPDATE feeds SET last_error = ?, failures = failures + 1, retry_at = ? WHERE key = ?",
                        (error[:500], now + retry_in, key))

    def record_success(self, key: str, count: int | None = None) -> None:
        """For feeds that produce no listings of their own, like the followed-calendars list."""
        self.db.execute("UPDATE feeds SET last_ok_at = ?, last_error = NULL, failures = 0, retry_at = NULL, "
                        "item_count = COALESCE(?, item_count) WHERE key = ?", (self.clock(), count, key))

    # ---------- listings ----------

    def replace_listings(self, feed_key: str, events: list[dict]) -> ReplaceResult:
        """Store a feed's fresh pull. Upcoming listings it no longer reports are removed; past ones are
        kept as history so the month view can still show them."""
        now = self.clock()
        now_iso = to_iso(now)
        # Feeds such as personal iCal links repeat old events forever; past the history window
        # they would be pruned and re-added on every pull.
        oldest = to_iso(now - HISTORY_KEEP_DAYS * 86400)
        rows = {}
        for ev in events:
            if ev.get("id") and ev.get("start_at") and (ev.get("end_at") or ev["start_at"]) >= oldest:
                rows[ev["id"]] = (ev, json.dumps(ev, sort_keys=True, separators=(",", ":")))
        with self.db.transaction() as conn:
            feed = conn.execute("SELECT calendar_id, last_ok_at FROM feeds WHERE key = ?", (feed_key,)).fetchone()
            if feed is None:
                raise KeyError(feed_key)
            first_pull = feed["last_ok_at"] is None
            calendar_id = feed["calendar_id"] or feed_key
            before = {row["event_id"]: row["data"] for row in conn.execute(
                "SELECT event_id, data FROM listings WHERE feed_key = ? AND (start_at >= ? OR event_id IN "
                f"({','.join('?' * len(rows)) or 'NULL'}))", (feed_key, now_iso, *rows))}
            after = {event_id: data for event_id, (_, data) in rows.items()}
            changed = before != after
            if changed:
                conn.execute("DELETE FROM listings WHERE feed_key = ? AND start_at >= ?", (feed_key, now_iso))
                conn.executemany(
                    "INSERT OR REPLACE INTO listings (feed_key, event_id, calendar_id, start_at, end_at, data, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(feed_key, event_id, calendar_id, ev["start_at"], ev.get("end_at"), data, now)
                     for event_id, (ev, data) in rows.items()])
            seen = {row["event_id"] for row in conn.execute(
                f"SELECT event_id FROM event_seen WHERE event_id IN ({','.join('?' * len(rows)) or 'NULL'})", tuple(rows))}
            new_ids = [event_id for event_id in rows if event_id not in seen]
            conn.executemany("INSERT INTO event_seen (event_id, first_seen_at, announced_at) VALUES (?, ?, ?)",
                             [(event_id, now, None if first_pull else now) for event_id in new_ids])
            conn.execute("UPDATE feeds SET last_ok_at = ?, last_error = NULL, failures = 0, retry_at = NULL, "
                         "item_count = ? WHERE key = ?", (now, len(rows), feed_key))
            if changed or new_ids:
                self._bump(conn)
        return ReplaceResult(changed=changed or bool(new_ids), new_event_ids=new_ids)

    def listings(self, since_iso: str) -> list[dict]:
        """Listings starting at or after ``since_iso`` (plus ones still running), oldest first."""
        rows = self.db.query(
            "SELECT l.event_id, l.calendar_id, l.feed_key, f.kind AS feed_kind, f.source AS feed_source, l.data, "
            "s.first_seen_at, s.announced_at FROM listings l JOIN feeds f ON f.key = l.feed_key "
            "LEFT JOIN event_seen s ON s.event_id = l.event_id "
            "WHERE l.start_at >= ? OR (l.end_at IS NOT NULL AND l.end_at >= ?) ORDER BY l.start_at",
            (since_iso, since_iso))
        out = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except ValueError:
                continue
            out.append({"event_id": row["event_id"], "calendar_id": row["calendar_id"], "feed_key": row["feed_key"],
                        "feed_kind": row["feed_kind"], "feed_source": row["feed_source"], "data": data,
                        "first_seen_at": row["first_seen_at"], "announced_at": row["announced_at"]})
        return out

    # ---------- RSVPs known by id ----------

    def replace_going(self, origin: str, statuses: dict[str, str]) -> None:
        now = self.clock()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM going WHERE origin = ?", (origin,))
            conn.executemany("INSERT INTO going (event_id, origin, status, updated_at) VALUES (?, ?, ?, ?)",
                             [(event_id, origin, status, now) for event_id, status in statuses.items()])
            self._bump(conn)

    def going(self) -> dict[str, str]:
        return {row["event_id"]: row["status"] or "going" for row in self.db.query("SELECT event_id, status FROM going")}

    # ---------- the user's own marks ----------

    def set_mark(self, event_id: str, *, starred: bool | None = None, hidden: bool | None = None) -> dict:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT starred, hidden FROM marks WHERE event_id = ?", (event_id,)).fetchone()
            current = {"starred": bool(row["starred"]), "hidden": bool(row["hidden"])} if row else {"starred": False, "hidden": False}
            if starred is not None:
                current["starred"] = starred
            if hidden is not None:
                current["hidden"] = hidden
            if current["starred"] or current["hidden"]:
                conn.execute("INSERT INTO marks (event_id, starred, hidden, updated_at) VALUES (?, ?, ?, ?) "
                             "ON CONFLICT(event_id) DO UPDATE SET starred = excluded.starred, hidden = excluded.hidden, "
                             "updated_at = excluded.updated_at",
                             (event_id, int(current["starred"]), int(current["hidden"]), self.clock()))
            else:
                conn.execute("DELETE FROM marks WHERE event_id = ?", (event_id,))
            self._bump(conn)
        return current

    def marks(self) -> dict[str, dict]:
        return {row["event_id"]: {"starred": bool(row["starred"]), "hidden": bool(row["hidden"])}
                for row in self.db.query("SELECT event_id, starred, hidden FROM marks")}

    # ---------- housekeeping ----------

    def prune(self, keep_days: float = HISTORY_KEEP_DAYS) -> int:
        """Forget events that ended more than ``keep_days`` ago. Returns the number of listings removed."""
        cutoff = to_iso(self.clock() - keep_days * 86400)
        with self.db.transaction() as conn:
            removed = conn.execute("DELETE FROM listings WHERE COALESCE(end_at, start_at) < ?", (cutoff,)).rowcount
            conn.execute("DELETE FROM event_seen WHERE event_id NOT IN (SELECT event_id FROM listings) AND first_seen_at < ?",
                         (self.clock() - keep_days * 86400,))
            conn.execute("DELETE FROM going WHERE event_id NOT IN (SELECT event_id FROM listings) AND updated_at < ?",
                         (self.clock() - keep_days * 86400,))
            if removed:
                self._bump(conn)
            return removed

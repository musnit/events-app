"""The database is one shared SQLite connection behind a lock, with numbered schema migrations.

The app is small and mostly reads, so a single serialized connection is simpler and safer than a
pool. SQLite's own locking still protects the file if another process (the sqlite3 CLI, a backup)
opens it.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Each entry moves the schema up one version. Never edit a released entry; append a new one.
MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

    -- Credentials and private feed URLs. Values are JSON.
    CREATE TABLE secrets (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at REAL NOT NULL
    );

    CREATE TABLE partiful_accounts (
        uid TEXT PRIMARY KEY,
        name TEXT,
        refresh_token TEXT NOT NULL,
        id_token TEXT,
        expires_at REAL NOT NULL DEFAULT 0,
        added_at REAL NOT NULL
    );

    -- Calendars shown in the app. A calendar stays while at least one origin claims it.
    CREATE TABLE calendars (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        slug TEXT,
        avatar_url TEXT,
        tint_color TEXT,
        url TEXT,
        description TEXT,
        added_at REAL NOT NULL
    );
    CREATE TABLE calendar_origins (
        calendar_id TEXT NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
        origin TEXT NOT NULL,  -- followed | import | link | builtin
        PRIMARY KEY (calendar_id, origin)
    );

    -- A feed is one thing the sync workers pull; its listings are replaced on each good pull.
    CREATE TABLE feeds (
        key TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        kind TEXT NOT NULL,
        calendar_id TEXT REFERENCES calendars(id) ON DELETE CASCADE,
        label TEXT NOT NULL,
        last_attempt_at REAL,
        last_ok_at REAL,
        last_error TEXT,
        failures INTEGER NOT NULL DEFAULT 0,
        retry_at REAL,
        item_count INTEGER
    );

    CREATE TABLE listings (
        feed_key TEXT NOT NULL REFERENCES feeds(key) ON DELETE CASCADE,
        event_id TEXT NOT NULL,
        calendar_id TEXT NOT NULL,
        start_at TEXT NOT NULL,
        end_at TEXT,
        data TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY (feed_key, event_id)
    );
    CREATE INDEX listings_start ON listings(start_at);
    CREATE INDEX listings_event ON listings(event_id);

    -- When each event first appeared. announced_at stays NULL for events that arrived with
    -- their feed's first pull, because those are not news.
    CREATE TABLE event_seen (
        event_id TEXT PRIMARY KEY,
        first_seen_at REAL NOT NULL,
        announced_at REAL
    );

    -- RSVPs known only by id, such as the snapshot the Luma bookmarklet sends.
    CREATE TABLE going (
        event_id TEXT NOT NULL,
        origin TEXT NOT NULL,
        status TEXT,
        updated_at REAL NOT NULL,
        PRIMARY KEY (event_id, origin)
    );

    CREATE TABLE marks (
        event_id TEXT PRIMARY KEY,
        starred INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL
    );

    CREATE TABLE prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]


class Database:
    """A single SQLite connection shared by every thread, serialized with a re-entrant lock."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            directory = Path(self.path).parent
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            # Create the file private before SQLite opens it; journals inherit its mode.
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self._depth = 0

    def migrate(self) -> int:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
                with self.transaction() as conn:
                    for statement in _statements(script):
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {number}")
                version = number
            return version

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block atomically. Nested calls join the outer transaction."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> None:
        with self.transaction() as conn:
            conn.execute(sql, params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _statements(script: str) -> list[str]:
    """Split a migration script into statements. Comments go first, since they may contain semicolons;
    migrations must not put "--" or ";" inside string literals."""
    code = "\n".join(line.split("--", 1)[0] for line in script.splitlines())
    return [statement.strip() for statement in code.split(";") if statement.strip()]

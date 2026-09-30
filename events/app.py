"""Start-up: settings, database, one-time legacy import, sync workers and the HTTP server."""
from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

from . import __version__, legacy
from .catalog import Catalog
from .config import Settings
from .db import Database
from .store import Store
from .sync import Sync
from .web import App, make_server

log = logging.getLogger("events")

DB_NAME = "events.db"


def adopt_old_database(state_dir: Path) -> None:
    """Version 2.0 called its database lumacal.db. Rename it (and its WAL files) once, while closed."""
    old, new = state_dir / "lumacal.db", state_dir / DB_NAME
    if old.is_file() and not new.exists():
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{old}{suffix}")
            if source.exists():
                source.rename(f"{new}{suffix}")
        log.info("renamed %s to %s", old.name, new.name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    adopt_old_database(settings.state_dir)
    db = Database(settings.state_dir / DB_NAME)
    db.migrate()
    store = Store(db)
    sync = Sync(store, settings)
    if settings.legacy_dir:
        legacy.migrate(store, sync, settings.legacy_dir, settings.state_dir)
    app = App(settings, store, sync, Catalog(store))
    server = make_server(app, settings.host, settings.port)

    def shut_down(signum: int, _frame: object) -> None:
        log.info("received %s, stopping", signal.Signals(signum).name)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shut_down)
    signal.signal(signal.SIGINT, shut_down)
    if settings.sync_enabled:
        sync.start()
    else:
        sync.reconcile()
        log.info("background sync is off (EVENTS_SYNC=0)")
    if not (settings.web_dir / "index.html").is_file():
        log.warning("no built web app at %s; run: npm --prefix web ci && npm --prefix web run build", settings.web_dir)
    host, port = server.server_address[:2]
    log.info("events %s listening on %s:%s (state in %s)", __version__, host, port, settings.state_dir)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        sync.stop()
        server.server_close()
        db.close()
        log.info("stopped")

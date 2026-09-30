#!/usr/bin/env python3
"""Start the server from a checkout: ``python3 server.py`` (the same as ``python3 -m events``).

Settings are EVENTS_* environment variables; see README.md. From a checkout the built web app in
web/dist is served, and state goes to EVENTS_STATE_DIR (default ~/.local/state/events).
"""
from events.app import main

if __name__ == "__main__":
    main()

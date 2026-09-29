#!/usr/bin/env python3
"""Entry point. Kept at the repository root so the service command stays `python3 server.py`.

Environment: HOST and PORT (default 127.0.0.1:8771), LUMACAL_DATA_DIR (default ./data),
LUMACAL_WEB_DIR (default ./web/dist), LUMACAL_SYNC=0 to run without background sync.
See AGENTS.md for the rest.
"""
from lumacal.app import main

if __name__ == "__main__":
    main()

"""The events app gathers the events of the Luma calendars you follow, your Partiful events and AGI House
into one calendar.

The package is split by responsibility:

- ``config``      runtime settings from the environment
- ``db``/``store`` SQLite persistence (events, sources, secrets, preferences)
- ``sources``     one client per upstream (Luma, Partiful, AGI House)
- ``sync``        background workers that pull each upstream on a schedule
- ``catalog``     turns stored listings into the event list the web app shows
- ``web``         the HTTP server, JSON API and static files
"""

__version__ = "2.1.0"

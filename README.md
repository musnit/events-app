# luma-cal

Every upcoming event from the Luma calendars you follow, your Partiful invites and events from people you
follow, and AGI House, in one calendar. Events are sorted into vibes (party, meet people, learn, build,
get moving, chill, food, games…) and topics (AI, crypto, startups…), and you can filter by time of day,
neighbourhood, crowd size, price and more. Every view and filter has its own URL, and it installs on a phone.

A small Python server (standard library only) syncs the upstreams in the background into SQLite and serves a
React/TypeScript web app.

```sh
npm --prefix web ci && npm --prefix web run build
python3 server.py            # http://127.0.0.1:8771 (HOST/PORT override)
```

Then open the Sources page and run the Luma and Partiful bookmarklets. See `AGENTS.md` for how it works,
how to test it and how it is hosted.

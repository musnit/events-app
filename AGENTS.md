# events — agent context

One calendar of every upcoming event across the Luma calendars musnit follows, musnit's Partiful events
and AGI House. Luma's public API only covers calendars you *manage*, and Partiful has none, so the
app reads the same internal endpoints their websites use. Everything upstream is unofficial.
README.md is the user-facing documentation; this file is for working on the code.

## Layout

- `server.py` — checkout entry point (`python3 server.py`, the same as `python3 -m events`).
- `events/` — backend, Python 3.11+ **standard library only**.
  - `config.py` `EVENTS_*` settings (table in README.md) · `db.py` SQLite + numbered migrations · `store.py` all SQL
  - `sources/` one client per upstream (`luma.py`, `partiful.py`, `agihouse.py`) → listing dicts (shape in `sources/__init__.py`)
  - `sync.py` background workers · `catalog.py` merges listings into the event list · `categorize.py` vibes/topics
  - `areas.py` Bay Area zones · `ics.py` iCalendar read/write · `web.py` HTTP/API/static · `legacy.py` v1 import
- `web/` — frontend: React 19 + TypeScript + Vite, plain CSS, no router or UI libraries.
  - `src/lib/` pure logic (routes, filters, dates, formatting, API client, bookmarklets, PWA)
  - `src/state/` data store (polling), router, toasts · `src/components/` · `src/views/` one file per screen
  - `tests/` node:test unit tests · `scripts/ui-check.mjs` headless-Chromium screenshots and layout audit
- `tests/` — backend unittest suite (offline; upstreams are faked).
- State lives in `EVENTS_STATE_DIR` (default `~/.local/state/events`; on devbox the gitignored `data/`, 0700):
  `events.db` (events, sources, **secrets**, prefs) and `legacy/` (v1 files after import).
- Nix: `package.nix` (offline build running both test suites), `module.nix` (`services.events`),
  `flake.nix` + `standalone-test.nix` (the module's VM test).
- `scripts/check.sh` runs the non-Nix checks.

## Commands

```sh
python3 -m unittest discover -s tests -t .      # backend tests
npm --prefix web ci                              # frontend deps (pinned, lockfile committed)
npm --prefix web test                            # frontend unit tests
npm --prefix web run build                       # type-check + build into web/dist (the server serves it)
scripts/check.sh                                 # all of the above
nix build                                        # the Nix package; its build runs both test suites
nix flake check                                  # the package and a VM test of module.nix
EVENTS_SYNC=0 EVENTS_STATE_DIR=data python3 server.py   # serve without background sync (UI work)
npm --prefix web run dev                         # Vite dev server on 127.0.0.1:5173, proxies /api to $EVENTS_API or :8771
BROWSER_BIN=… BASE_URL=http://127.0.0.1:8771/ npm --prefix web run check:ui   # screenshots in /tmp/events-ui
python3 -m events.categorize data/events.db [vibes|topics]                    # categorization coverage report
```

After changing `web/package-lock.json`, update `npmDepsHash` in `package.nix`: set it to `lib.fakeHash`,
run `nix build`, and copy the hash it reports.

On this box a Chromium lives in the Nix store (`ls -d /nix/store/*-chromium-1*/bin/chromium`). Emoji need a
font: point `FONTCONFIG_FILE` at a config that adds Noto Color Emoji, or chips show boxes in screenshots.

## Data model

- **calendar**: what the user sees and can mute (a Luma calendar, "Partiful · my events", "AGI House").
  Kept while any *origin* claims it: `followed` (session sync), `import` (bookmarklet; the import is the
  complete followed set, so re-running it also drops unfollowed calendars), `link`, `config`
  (`EVENTS_LUMA_CALENDARS`), `builtin`. The API refuses to remove `followed` and `config` calendars.
- **feed**: one unit a worker pulls (`luma:cal-…`, `luma:evt-…`, `luma:following`, `luma:mine`, `luma:ics`,
  `luma:config`, `partiful:<uid>:mine|following`, `partiful:feed`, `agihouse`). Holds sync health and backoff.
- **linked event**: a single Luma event added by link (`linked_events`), private ones included. Its feed
  `luma:evt-…` lists it under the builtin calendar `luma-links` and refreshes it like a calendar; the catalog
  marks it `linked`, and the web app's area filter leaves it in. It goes when removed or when its listing ages out.
  `luma:config` resolves the configured links once (meta `luma_config_resolved`); it runs again only when the
  configured list changes (meta `luma_config_tokens`, compared at start) or after a failure's backoff.
- **listing**: an event as one feed reported it. A good pull replaces the feed's *upcoming* listings;
  past ones stay as history (pruned after 60 days). The catalog merges listings by event id, folds
  cross-source duplicates (same title ±3 h), applies RSVPs (`going`), marks (star/hide) and mutes.
- `event_seen.announced_at` is set only for events that appear after a feed's first pull, which drives
  the "New since your last visit" badge (the client keeps the visit reference in localStorage).

## Sync

One worker thread per upstream with a de-duplicated priority queue: user refresh > newly added source >
schedule. Nothing is dropped; asking again only raises priority. Each pull is stored immediately, so the
UI fills in during a pass and restarts resume from per-feed timestamps. Intervals (each an `EVENTS_*`
setting): Luma calendars 4 h, 15 s apart; user-triggered pulls 2 s apart; personal feeds, Partiful 1 h; AGI House 2 h.
A 429 pauses the Luma worker (45 s → 5 min) and retries; other failures back off per feed (10 min → 4 h)
and keep the feed's previous listings. A Luma 401 forgets the session and retries without it.

## Luma internals (api.luma.com, undocumented)

- `GET /calendar/get-items?calendar_api_id=cal-…&period=future` public, paginated (`pagination_cursor`/`next_cursor`).
  Entries carry `event`, `calendar` (presenter, with `description_short`), `hosts`, `tags`, `ticket_info`,
  `guest_count`, `registration_availability`, `guest_info` (only with a session).
- `GET /home/get-following-calendars`, `GET /home/get-events?period=future` need the `luma.auth-session-key` cookie.
- Email-code sign-in is blocked by Cloudflare Turnstile (`auth/additional-verification-required`), so it was
  removed. Sessions come from a pasted cookie (desktop). The cookie is HttpOnly, so the bookmarklet cannot
  capture it; it sends the followed calendars and a snapshot of RSVPs instead. The personal iCal link
  (Settings → Calendar Syncing) keeps RSVPs current.
- Links resolve by loading the luma.com page and reading `__NEXT_DATA__`: `pageProps.initialData.kind` is `event`
  or `calendar`, and an event page's `initialData.data` has the same shape as a get-items entry.
- `GET /event/get?event_api_id=evt-…` is public and answers for private events too (anyone with the link can see
  them); `get-items` lists only a calendar's public events.
- If syncing breaks, re-pull luma.com's JS chunks and grep for `get-following-calendars`, `home/get-events`.

## Partiful

The bookmarklet reads the Firebase refresh token from partiful.com's IndexedDB. The server mints ID tokens
(`securetoken.googleapis.com`, public web key) and calls `getMyFollowedEvents` and
`getMyUpcomingEventsForHomePage` on `api.partiful.com`. Several accounts can be connected. Upload images
live in a private bucket, so covers go through `partiful.imgix.net`. The personal iCal link
(`webcal://calendars.partiful.com/getCalendar?id=…`) is an alternative with invites/RSVPs only.

## Categories

`categorize.py` scores keyword rules over title (weight 3), tags (2), presenter name + listing calendars (2),
presenter description (1) and hosts (0.75); a category needs 3. Up to three vibes and three topics per event;
if no vibe reaches 3 the best weak signal (≥1.5) wins, with talk-shaped titles counting toward "Learn".
Room holds ("HOLD – …", "Private Event", "[Hold]") are dropped. Bump `RULES_VERSION` when rules change.

## Web app

Every screen is a URL (`web/src/lib/routes.ts`): `/`, `/agenda/2026-10-03`, `/day/2026-10-03`,
`/month/2026-10`, `/event/<id>`, `/saved`, `/calendars`, `/calendars/<id>`, `/sources[/luma|partiful|…]`.
Filters live in the query (`?vibe=party,social&topic=ai&time=evening&weekend=1&zone=sf&q=…`). An event opened
from a list is drawn over it (history state `background`); opened directly it is a page. The server returns
index.html for extensionless paths and rewrites `<base href>` from `X-Forwarded-Prefix`, so the app also
works under a path prefix. It is an installable PWA; `public/sw.js` never caches anything (portal sign-in
and private data), it only shows an offline page.

## API

`GET /api/events` (catalog; ETag/304, gzip) · `GET /api/status` (sources, workers, feeds, prefs) ·
`POST /api/sync {source?}` · `PUT /api/prefs {muted_calendars?, area?}` · `PUT /api/events/<id>/mark {starred?, hidden?}` ·
`GET /api/events/<id>/ics` · `GET /feed.ics?scope=mine|all` ·
`POST /api/luma/import {payload}` · `POST /api/luma/links {text}` (calendars and events) · `DELETE /api/luma/calendars/<id>` ·
`DELETE /api/luma/events/<id>` ·
`PUT|DELETE /api/luma/session` · `PUT|DELETE /api/luma/ics` · `POST /api/partiful/import {payload}` ·
`DELETE /api/partiful/accounts/<uid>` · `PUT|DELETE /api/partiful/feed` · `GET /api/health`.
Writes must be same-origin JSON (Sec-Fetch-Site/Origin checked); the portal in front handles sign-in.

## Hosting

- **devbox (current)**: prototype `events` → https://events.devbox.musnitzky.com/ (portal sign-in required).
  It runs `python3 server.py` from the main checkout `/home/lab/events-app` as user unit
  `prototype-events.service`, with `EVENTS_LISTEN` built from the helper's `HOST`/`PORT` and
  `EVENTS_STATE_DIR=/home/lab/events-app/data`. To deploy: `git -C /home/lab/events-app pull --ff-only`,
  `npm --prefix /home/lab/events-app/web ci && npm --prefix /home/lab/events-app/web run build`,
  `prototype restart events`; check `prototype logs events`. Never point it at a T3 worktree.
- **agent-lab (later, for the NAS, once the app matures)**: this repository stays the app's only home. Never
  copy the app into agent-lab, even though the lab's AGENTS.md puts the programs it hosts in
  `public/modules/<app>/`; the owner does not want a duplicated codebase. The lab pins this repo as a flake
  input instead (with `inputs.nixpkgs.follows = "nixpkgs"`), imports `nixosModules.default`, and keeps only its
  wiring in agent-lab: the endpoint, the door entry, a `lab.state` entry, the NAS's `enable`, and a lab VM test.
  The wiring and test written for an earlier copy are in this repo's history at `f1b684a` (`default.nix`,
  `test.nix`); port them into agent-lab. Three constraints shape that PR:
  - This repo is private, and the NAS reads GitHub only through its host key, a deploy key scoped to agent-lab
    (GitHub refuses one deploy key on two repos). Before the move the owner either makes this repo public
    (`github:musnit/events-app`; its history holds no state or credential files) or gives the box a read-only
    credential for it. The devbox's Nix already fetches `git+https://github.com/musnit/events-app` through
    git's GitHub login, so local CI works either way.
  - It belongs in the lab layer (`lab/`), not the shareable stack (`public/`), because the repo is private
    and the app is personal.
  - Only the NAS should import the module. A module every host imports makes every box fetch the input, and
    the other boxes cannot read the repo. mkLab has no per-host modules yet, so this needs a small stack
    change to hand a module to one host.

## Gotchas

- `events.db` holds the Luma session, Partiful refresh tokens and private feed URLs. Never commit `data/`.
- This repository is public. Tests and fixtures use made-up ids, names, venues, images and links (`evt-Fixture…`,
  `cal-FixtureBrainBay`), never data captured from the owner's accounts or a private event's link: a fixture
  recorded with a session shows which events the owner registered for.
- Merge PRs locally: `git merge --no-ff` the PR branch into an up-to-date `main` and push it; GitHub then marks
  the PR merged. Every merge method on GitHub (merge, squash, rebase) writes the merging account's own email into
  public history and refuses a noreply address in its place. Local commits use the noreply address in this
  checkout's git config. Turning on "Keep my email addresses private" for the account would make `gh pr merge` safe.
- The API never returns secrets; feed URLs come back masked.
- Calendar apps cannot subscribe to `/feed.ics` because the portal requires sign-in; it is a download.
- Frontend TypeScript must stay erasable (no enums/namespaces) and import with `.ts` extensions, because
  `node --test` runs the lib modules directly.

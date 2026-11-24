# events — agent context

One calendar of every upcoming event across the Luma calendars musnit follows, musnit's Partiful events,
AGI House and, for Cape Town, Sundry Mag's weekly list. Luma's public API only covers calendars you *manage*, and Partiful has none, so the
app reads the same internal endpoints their websites use. Everything upstream is unofficial.
README.md is the user-facing documentation; this file is for working on the code.

## Layout

- `server.py` — checkout entry point (`python3 server.py`, the same as `python3 -m events`).
- `events/` — backend, Python 3.11+ **standard library only**.
  - `config.py` `EVENTS_*` settings (table in README.md) · `db.py` SQLite + numbered migrations · `store.py` all SQL
  - `sources/` one client per upstream (`luma.py`, `partiful.py`, `agihouse.py`, `sundry.py`, `instagram.py`) → listing dicts (shape in `sources/__init__.py`)
  - `sync.py` background workers · `catalog.py` merges listings into the event list · `categorize.py` vibes/topics
  - `reader.py` runs Claude Code to read Instagram posts into events · `preview.py` reads a linked page's picture, organiser and words (Open Graph, schema.org) · `areas.py` Bay Area zones, Cape Town · `ics.py` iCalendar read/write · `web.py` HTTP/API/static · `legacy.py` v1 import
- `web/` — frontend: React 19 + TypeScript + Vite, plain CSS, no router or UI libraries.
  - `src/lib/` pure logic (routes, filters, dates, formatting, API client, bookmarklets, PWA)
  - `src/state/` data store (polling), router, toasts · `src/components/` · `src/views/` one file per screen
  - `tests/` node:test unit tests · `scripts/ui-check.mjs` headless-Chromium screenshots and layout audit
- `tests/` — backend unittest suite (offline; upstreams are faked).
- State lives in `EVENTS_STATE_DIR` (default `~/.local/state/events`; on devbox the gitignored `data/`, 0700):
  `events.db` (events, sources, **secrets**, prefs), `media/` (pictures saved from linked pages) and `legacy/` (v1 files after import).
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

- **calendar**: what the user sees and can mute (a Luma calendar, "Partiful · my events", "AGI House", "Sundry Mag").
  Kept while any *origin* claims it: `followed` (session sync), `import` (bookmarklet; the import is the
  complete followed set, so re-running it also drops unfollowed calendars), `link`, `config`
  (`EVENTS_LUMA_CALENDARS`), `builtin`. The API refuses to remove `followed` and `config` calendars.
- **feed**: one unit a worker pulls (`luma:cal-…`, `luma:evt-…`, `luma:following`, `luma:mine`, `luma:ics`,
  `luma:config`, `partiful:<uid>:mine|following`, `partiful:feed`, `agihouse`, `sundry`, `instagram:<account>`). Holds sync health and backoff.
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
setting): Luma calendars 4 h, 15 s apart; user-triggered pulls 2 s apart; personal feeds, Partiful 1 h; AGI House 2 h;
Sundry Mag 6 h; Instagram accounts 12 h, 20 s apart (a sync asked for by hand is no faster).
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

## Sundry Mag (sundrymag.substack.com)

- "What to Do In Cape Town This Week" is posted each Monday. `GET /api/v1/archive?sort=new` lists posts and
  `GET /api/v1/posts/<slug>` returns one with `body_html`, `audience` and `wordcount`.
- A post is a dated heading per day and a list item per event: the title links to the organiser's own post
  (mostly Instagram, then Quicket), with nested "⏰ Time", "📍 Location" and "💸 Cost" lines. `parse_post` reads
  the first clock time of the Time line (South Africa is UTC+2 all year) and the first rand amount of the Cost
  line; what they cannot hold goes into the listing's `description`, and the location and cost links into `links`.
- Recent posts have `audience: only_subscribers` (free). Without the `substack.sid` cookie of a subscribed
  account `body_html` is only the opening, while `wordcount` still counts the whole post; that is how
  `is_cut_short` tells. The pull stores a cut post the first time, never over a fuller week, and records the
  cut titles in meta `sundry_locked` for the Cape Town tab and the Sources page.
- The session is secret `substack_session` (`PUT|DELETE /api/sundry/session`); the API checks that it unlocks
  the newest post before saving it.
- After storing the week, the pull reads the page each coming event links to (`preview.py`), one every
  `EVENTS_PREVIEW_SPACING` seconds, and stores the listings again as pictures arrive. A page is read once: its
  preview is kept in `link_previews` by address (a failure waits 6 h, doubling to a week) and merged into the
  listing on every later pull. Instagram gives a non-browser client the post's Open Graph tags (picture, account,
  caption) but a browser user agent only a script shell, so keep `net.USER_AGENT` un-browser-like. Its image
  addresses are signed and expire, which is why the bytes are saved under `media/<sha256>.jpg` and served from
  `/media/<name>`. A post without a picture means Instagram is turning the box away: the pull stops asking
  Instagram and tries again next time. Quicket, Luma and Moments carry a schema.org `Event` with the organiser
  and the venue's address. Every fetched address, redirects included, must resolve to a public IP.
- Each pull reads the two newest weekly posts. A listing's id is a hash of its link and its date, so a weekly
  series that reuses one Instagram post is a new event each week.

## Instagram accounts

- An account is a calendar `instagram:<account>` (source `instagram`) with a feed of the same key. Origin `link`
  is an account added in Sources; origin `sundry` is the account behind an event Sundry Mag listed (taken from the
  link previews' `author.id`), kept while meta `instagram_seen` shows it featured in the last 60 days. Removing an
  account in the app adds it to meta `instagram_dismissed`, so Sundry does not bring it back.
- `GET instagram.com/<account>/embed/` needs no sign-in. A script in it holds `"contextJSON":"…"`, a JSON string
  whose `context` has `username`, `full_name`, `profile_pic_url` and `graphql_media`: the six newest posts
  (`shortcode`, `taken_at_timestamp`, `display_url`, `edge_media_to_caption`, and for a carousel
  `edge_sidecar_to_children` with each slide's `display_url`). The profile page itself and
  `api/v1/users/web_profile_info` give a signed-out client nothing.
- A post is not an event. Posts from the last 45 days are saved in `instagram_posts` with their picture, and
  `reader.py` asks Claude which announce an event, once per post (three tries). It runs the Claude Code program
  (`EVENTS_CLAUDE`, signed in as the service's user, no API key): `claude --print --json-schema … --system-prompt …`
  with the captions on stdin and the pictures copied into a temporary working directory. Captions are strangers'
  text, so the run has `--tools Read --restricted --safe-mode --strict-mcp-config --permission-prompts none` (it can
  read those pictures and do nothing else), and `parse_answer` keeps only well-formed events dated from yesterday
  to 120 days ahead. Each account's listings are rebuilt from every saved post, since posts leave Instagram's six.
- A carousel's slides (twelve at most) are saved in `instagram_posts.slides` and all given to the reader: a weekly gig
  guide is a slide per day with the gigs written on it, and nothing in its caption. Each event answers with the
  `picture` it was read from, which becomes its cover. One run takes at most `MAX_POSTS` posts and `MAX_PICTURES`
  pictures (`reader.batch`); a pull runs until nothing is unread. `slides` is NULL for a post saved before slides
  were kept; such a post is read again when Instagram still lists it and it turns out to be a carousel.
- Followed accounts get feeds only while a reader is available (`Sync.instagram_on`); without one the accounts
  stay as calendars and nothing is polled.
- A weekly list and the organiser's account often announce the same post: the catalog folds listings from
  different sources that share a `url` and start within 20 hours, keeping the copy of the higher-ranked source.
- Sundry and the accounts retell one event in different words, and a guide account lists gigs the venues announce
  too. For events starting within 3 hours (`catalog._same_title`): Sundry and an account, or two different accounts,
  also fold when one title is the other with more said; two accounts fold on a like title as well. Neither happens
  when both copies name venues with no word in common (an open mic at two bars). Among Instagram's events the copy
  from the post announcing fewer events is kept, so the organiser's own post wins over a guide's entry.

## Categories

`categorize.py` scores keyword rules over title (weight 3), tags (2), presenter name + listing calendars (2),
presenter description (1) and hosts (0.75); a category needs 3. Up to three vibes and three topics per event;
if no vibe reaches 3 the best weak signal (≥1.5) wins, with talk-shaped titles counting toward "Learn".
Room holds ("HOLD – …", "Private Event", "[Hold]") are dropped. Bump `RULES_VERSION` when rules change.

## Web app

Every screen is a URL (`web/src/lib/routes.ts`): `/`, `/agenda/2026-10-03`, `/day/2026-10-03`,
`/month/2026-10`, `/event/<id>`, `/saved`, `/calendars`, `/calendars/<id>`, `/sources[/luma|partiful|…]`, `/cape-town`.
Filters live in the query (`?vibe=party,social&topic=ai&time=evening&weekend=1&zone=sf&q=…`). The Cape Town
tab is the agenda with its area fixed (`ViewLocation.area`, read through `useArea`); links that leave it for a day
or a later date carry `?area=cape-town`, and the shell keeps the tab lit for those. Cape Town is a *destination*
(`lib/event-time.ts`): its events are bucketed and shown in Cape Town's time whatever the browser's zone, and its
area filter is strict, so an event added by link elsewhere does not leak in. An event opened
from a list is drawn over it (history state `background`); opened directly it is a page. The server returns
index.html for extensionless paths and rewrites `<base href>` from `X-Forwarded-Prefix`, so the app also
works under a path prefix. It is an installable PWA; `public/sw.js` never caches anything (portal sign-in
and private data), it only shows an offline page.

## API

`GET /api/events` (catalog; ETag/304, gzip) · `GET /api/status` (sources, workers, feeds, prefs) ·
`POST /api/sync {source?}` · `PUT /api/prefs {muted_calendars?, area?}` · `PUT /api/events/<id>/mark {starred?, hidden?}` ·
`GET /api/events/<id>/ics` · `GET /feed.ics?scope=mine|all` · `GET /media/<name>` (saved pictures) ·
`POST /api/luma/import {payload}` · `POST /api/luma/links {text}` (calendars and events) · `DELETE /api/luma/calendars/<id>` ·
`DELETE /api/luma/events/<id>` ·
`PUT|DELETE /api/luma/session` · `PUT|DELETE /api/luma/ics` · `POST /api/partiful/import {payload}` ·
`PUT|DELETE /api/sundry/session {session}` · `POST /api/instagram/accounts {text}` · `DELETE /api/instagram/accounts/<account>` ·
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
  `public/modules/<app>/`; the owner does not want a duplicated codebase. The repo is public, so the lab pins it
  as a flake input with no credential: `github:musnit/events-app`, with `inputs.nixpkgs.follows = "nixpkgs"`.
  Only the lab's wiring lives in agent-lab, in the lab layer (`lab/`) rather than the shareable stack, because the
  app is personal:
  - `lab/flake.nix` takes the input and hands `nixosModules.default` to every host through mkLab's `extraModules`;
  - a lab module adds the endpoint, the door entry and a `lab.state` entry;
  - the NAS host sets `enable`;
  - a VM test under `lab/tests/` is listed in the lab flake's checks.

  The wiring and test written for an earlier copy are in this repo's history at `f1b684a` (`default.nix`,
  `test.nix`); port them into agent-lab.

## Gotchas

- `events.db` holds the Luma session, the Substack session, Partiful refresh tokens and private feed URLs. Never commit `data/`.
- Tests set `EVENTS_CLAUDE` empty (`tests/helpers.py`), so none can start the real Claude Code program; keep it so.
- Migration 3 creates a `research_collections` table and migration 4 drops it. Both stay: a deployed database has run 3.
  The feature (a Mumbai trip's research notes) is in history at `10fe1f8`.
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

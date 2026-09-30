---
title: Find events from the calendars you follow
description: See the upcoming events from the Luma calendars you follow, your Partiful invites and AGI House in one calendar, sorted by vibe and topic.
---

Events gathers every upcoming event from the Luma calendars you follow,
your Partiful invites and the events of people you follow there, and AGI
House into one calendar. The same event listed in two places appears once.
Each event is sorted into vibes (party, meet people, learn, build, get
moving, chill, food, games…) and topics (AI, crypto, startups…). Filter
by those, by time of day, weekend, Bay Area neighbourhood, crowd size and
price, or search. Star events to save them, hide ones you don't want, and
mute whole calendars. Events that appeared since your last visit are
marked. Every view, date and filter has its own URL: `/`,
`/agenda/2026-10-03`, `/day/…`, `/month/2026-10`, `/event/<id>`, `/saved`,
`/calendars`, `/sources` and `?vibe=party&topic=ai&time=evening`. It
installs on a phone as an app.

## Connect your calendars

Luma's public API covers only calendars you manage, and Partiful has no
API, so the app reads the endpoints their own websites use. Connect them
on the **Sources** page:

- To import the Luma calendars you follow, drag the Luma bookmarklet to
  your bookmarks bar, open luma.com while signed in and click it. It sends
  the calendars you follow and the events you registered for. Run it
  again after following new calendars; calendars you unfollowed drop out.
- Your personal Luma iCal link (Luma → Settings → Calendar Syncing) keeps
  your RSVPs current between imports. On a computer you can instead paste
  your Luma session cookie, and the app then reads your follows and RSVPs
  itself.
- The Partiful bookmarklet, clicked on partiful.com while signed in,
  connects your account: your invites, your RSVPs and the events of people
  you follow. You can connect several accounts. Partiful's iCal link is an
  alternative that carries invites and RSVPs only.
- To follow any Luma calendar without a Luma account, paste its link under
  **Add single calendars by link**.

The deployment can also name calendars that are always followed
(`EVENTS_LUMA_CALENDARS`, `services.events.lumaCalendars`,
`lab.events.lumaCalendars`). They work without any account, even before
anyone opens the app, and the app cannot remove them. AGI House's public
events are included unless turned off.

Sources refresh in the background: Luma calendars every 4 hours, your
personal Luma and Partiful feeds hourly and AGI House every 2 hours.
**Sync** on the Sources page pulls one source now, and **Sync everything
now** pulls them all. The page shows when each feed last worked, and its
error when it did not.

## Authentication and access

The app has no sign-in of its own, so it binds to loopback behind a
reverse proxy that authenticates every request. In the lab that proxy is
the door, the box's front web server: it sends every visitor through the
portal sign-in and admits the admins alone. Set
`lab.proxy.apps.events.groups` to admit a directory group as well. The
app has no per-person accounts. It shows one person's calendars, and
everyone it admits sees and changes the same connections and choices.

Writes must come from the app's own pages: the server refuses
cross-site requests and anything but JSON. **Export** on the Sources
page downloads an `.ics` file of your plan (events you starred or are
going to) or of everything. Calendar apps can import the file but cannot
subscribe to it, because they cannot pass the portal sign-in.

## State and credentials

Everything lives in one SQLite database, `events.db`, in the state
directory: events, calendars, stars, preferences and the credentials you
connect. Those are the Luma session cookie, Partiful refresh tokens and
private iCal links. Its directory is readable by the service alone
(mode 0700). The API never returns a credential, and iCal links come back
masked. Removing a connection on the Sources page deletes its
credential. In the lab the database is `lab.events.dataDir`
(`/persist/events`), encrypted and backed up with the rest of the box's
state. A new box starts empty: run the bookmarklets once against it, or
restore the directory from its backup.

## Limits

- Everything upstream is unofficial and can change without notice. When a
  source breaks, its feed shows the error and keeps the events it last had.
- Luma answers bursts with HTTP 429. The app spaces calendar pulls 15
  seconds apart and pauses when asked to slow down, so a first pull of a
  hundred calendars takes about half an hour. Events appear as each
  calendar arrives.
- Luma's email-code sign-in is behind a bot check, so the app cannot sign
  in to Luma itself. It uses the bookmarklet, a pasted cookie or the
  iCal link.
- Past events are kept for 60 days, then pruned.

## Configuration

The program reads its settings from the environment, with the same names
in every runtime:

| Variable | Default | Meaning |
| --- | --- | --- |
| `EVENTS_LISTEN` | `127.0.0.1:8771` | address:port to bind (`[::1]:8771`, `:8771` for every interface) |
| `EVENTS_STATE_DIR` | `$XDG_STATE_HOME/events` or `~/.local/state/events` | the database's directory |
| `EVENTS_WEB_DIR` | `web/dist` beside the code | the built web app (the Nix package sets its own) |
| `EVENTS_LUMA_CALENDARS` | *(none)* | Luma calendars always followed: links, slugs or `cal-` ids, separated by spaces or commas |
| `EVENTS_AGIHOUSE` | `1` | `0` leaves AGI House out |
| `EVENTS_SYNC` | `1` | `0` serves the database without pulling anything |
| `EVENTS_LUMA_INTERVAL` | `14400` | seconds between pulls of each Luma calendar |
| `EVENTS_PERSONAL_INTERVAL` | `3600` | seconds between pulls of your Luma follows, registrations and iCal link |
| `EVENTS_PARTIFUL_INTERVAL` | `3600` | seconds between Partiful pulls |
| `EVENTS_AGIHOUSE_INTERVAL` | `7200` | seconds between AGI House pulls |
| `EVENTS_LUMA_SPACING` | `15` | seconds between scheduled Luma requests |
| `EVENTS_MANUAL_SPACING` | `2` | seconds between Luma requests you asked for |
| `EVENTS_LEGACY_DIR` | *(none)* | import a version 1 install's JSON files from here, once |

The server answers with the app at every extensionless path, and with
`/api/health`, the JSON API under `/api/` and `/feed.ics?scope=mine|all`.

## Run it

A checkout runs with Python 3.11+ and Node 22.12+:

```sh
npm --prefix web ci && npm --prefix web run build
python3 server.py        # http://127.0.0.1:8771, or python3 -m events
```

With Nix, `nix run .` runs the package, whose build runs both test
suites, and `nix flake check` also boots the NixOS module in a VM. On
NixOS, import the module:

```nix
imports = [ inputs.events.nixosModules.default ];
services.events = {
  enable = true;
  lumaCalendars = [ "https://luma.com/genai-sf" ];
};
```

The unit runs as its own `events` account in a systemd sandbox. It keeps its
database in `/var/lib/events` (`stateDir`), and `environment` passes any
other `EVENTS_*` setting.

## Files

```
events/               the server, written with Python's standard library only
web/                  the web app in React, TypeScript and Vite, with its npm lockfile
tests/                the server's tests, which fake every upstream and run offline
server.py             the entry point that runs the server from a checkout
package.nix           the Nix build, offline, which runs both test suites
module.nix            the NixOS module services.events, a sandboxed systemd unit
standalone-test.nix   the VM test of module.nix alone (the flake's vm check)
flake.nix             the standalone flake
default.nix           the lab's wiring of lab.events to an endpoint, the door and state
test.nix              the lab's VM test, which agent-lab's flake check runs
```

`default.nix` and `test.nix` are evaluated only inside agent-lab, where
this directory is `public/modules/events/`.

## Develop

```sh
python3 -m unittest discover -s tests -t .   # server tests
npm --prefix web test                        # web unit tests
npm --prefix web run build                   # type-check and build web/dist
npm --prefix web run dev                     # Vite on 127.0.0.1:5173; proxies /api to $EVENTS_API or :8771
EVENTS_SYNC=0 python3 server.py              # serve the database without pulling (UI work)
```

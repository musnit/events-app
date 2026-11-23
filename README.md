# Events

Events gathers every upcoming event from the Luma calendars you follow,
your Partiful invites and the events of people you follow there, AGI
House and Sundry Mag's weekly Cape Town list into one calendar. The same event listed in two places appears once.
Each event is sorted into vibes (party, meet people, learn, build, get
moving, chill, food, games…) and topics (AI, crypto, startups…). Filter
by those, by time of day, weekend, Bay Area neighbourhood, crowd size and
price, or search. Star events to save them, hide ones you don't want, and
mute whole calendars. Events that appeared since your last visit are
marked. Every view, date and filter has its own URL: `/`,
`/agenda/2026-10-03`, `/day/…`, `/month/2026-10`, `/event/<id>`, `/saved`,
`/calendars`, `/sources` and `?vibe=party&topic=ai&time=evening`. It
installs on a phone as an app.

The **Cape Town** tab at `/cape-town` is the same list for Cape Town alone:
every event from your calendars that takes place there, and the week's
events from Sundry Mag. Its dates and times are Cape Town's, wherever you
open the app, and the same filters apply. **Cape Town** is also a choice
under **Where**, which makes it the area the other views show.

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
  **Add calendars or events by link**. An event link adds just that event,
  private ones included: Luma lists only public events on a calendar, so a
  private event otherwise reaches the app only through your own
  registrations. An event added by link shows whichever area you choose,
  except Cape Town, which shows only what happens there.
- [Sundry Mag](https://sundrymag.substack.com/) posts "What to Do In Cape
  Town This Week" every Monday, and the app reads each event's day, time,
  place and cost from it without any account. Recent posts are for
  subscribers, which is free; without a subscriber's session Substack
  sends only the opening of the post, and the app says so. Each event links
  to the organiser's own post, usually on Instagram or Quicket, and the app
  reads that page's link preview for the picture, the organiser and their
  description. It keeps a copy of the picture, because Instagram's image
  links expire. To read the whole
  week, copy the `substack.sid` cookie from a signed-in desktop browser
  into **Sources → Cape Town**.

- Instagram accounts are followed from **Sources → Cape Town**. Twice a day
  the app reads each account's six newest posts from Instagram's public
  embed page, and Claude reads every new post and its picture to tell
  whether it announces an event and when and where it is; those become
  events in the Cape Town tab. The accounts behind the events Sundry Mag
  lists are followed automatically, for two months after it last featured
  them, and you can add or remove any account. This needs the Claude Code
  program on the server, signed in; it uses that sign-in and no API key.

The deployment can also name calendars that are always followed
(`EVENTS_LUMA_CALENDARS`, or `services.events.lumaCalendars` in the NixOS
module). They work without any account, even before
anyone opens the app, and the app cannot remove them. AGI House's public
events and Sundry Mag's list are included unless turned off.

Sources refresh in the background: Luma calendars every 4 hours, your
personal Luma and Partiful feeds hourly, AGI House every 2 hours and
Sundry Mag every 6.
**Sync** on the Sources page pulls one source now, and **Sync everything
now** pulls them all. The page shows when each feed last worked, and its
error when it did not.

The database is `events.db` in the state directory, and the pictures saved
from linked pages are in `media/` beside it.

## Authentication and access

The app has no sign-in of its own, so it binds to loopback behind a
reverse proxy that authenticates every request. On the devbox that proxy
is the portal sign-in, which admits the box's administrators. The app has
no per-person accounts. It shows one person's calendars, and
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
credential. A new install starts empty: run the bookmarklets once
against it, or restore the directory from a backup.

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
- Sundry Mag's list is written by hand. The app reads the first clock time
  of each line; "also Sunday" or "R80 at the door" is shown in words on the
  event's page, and an event listed without a time shows as all day.
- Events from Instagram posts are Claude's reading of a caption and a
  poster. It leaves out what it cannot date, and it can still misread; the
  event's page links to the post. Instagram shows only an account's six
  newest posts, so an account that posts more than that between two pulls
  loses the older ones.
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
| `EVENTS_SUNDRY` | `1` | `0` leaves Sundry Mag's Cape Town list out |
| `EVENTS_SYNC` | `1` | `0` serves the database without pulling anything |
| `EVENTS_LUMA_INTERVAL` | `14400` | seconds between pulls of each Luma calendar |
| `EVENTS_PERSONAL_INTERVAL` | `3600` | seconds between pulls of your Luma follows, registrations and iCal link |
| `EVENTS_PARTIFUL_INTERVAL` | `3600` | seconds between Partiful pulls |
| `EVENTS_AGIHOUSE_INTERVAL` | `7200` | seconds between AGI House pulls |
| `EVENTS_SUNDRY_INTERVAL` | `21600` | seconds between Sundry Mag pulls |
| `EVENTS_PREVIEW_SPACING` | `3` | seconds between the linked pages read for pictures |
| `EVENTS_INSTAGRAM` | `1` | `0` follows no Instagram accounts |
| `EVENTS_INSTAGRAM_INTERVAL` | `43200` | seconds between pulls of one Instagram account |
| `EVENTS_INSTAGRAM_SPACING` | `20` | seconds between Instagram accounts |
| `EVENTS_CLAUDE` | `claude` | the Claude Code program that reads posts: a name on `PATH` (or in `~/.local/bin`) or a path; empty for none |
| `EVENTS_CLAUDE_MODEL` | `opus` | the model Claude Code is asked for |
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
```

## Develop

```sh
python3 -m unittest discover -s tests -t .   # server tests
npm --prefix web test                        # web unit tests
npm --prefix web run build                   # type-check and build web/dist
npm --prefix web run dev                     # Vite on 127.0.0.1:5173; proxies /api to $EVENTS_API or :8771
EVENTS_SYNC=0 python3 server.py              # serve the database without pulling (UI work)
```

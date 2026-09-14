# luma-cal — agent context

One calendar view of every upcoming event across the Luma calendars musnit follows, plus his
Partiful events. Luma's public API only covers calendars you *manage* (and needs Luma Plus), so
this app signs in the way luma.com does and reads the site's internal endpoints. Partiful has no
API at all, but each account gets a personal iCalendar feed, which we poll.

## Stack (dependency-free)

- `server.py` — stdlib `http.server`, binds `127.0.0.1:8771`. Serves `web/` and a JSON API.
  Sees paths at `/` (nginx strips `/events/`). Background thread refreshes every 4 h with 15 s between Luma calendar pulls (a full pass takes ~28 min); the refresh button uses 2 s spacing. Luma rate-limits bursts (429), and the app backs off and keeps a calendar's previous events when a pull fails.
- `web/` — vanilla JS single page: month grid + list view, per-calendar colours and filters,
  "going only" toggle, event popover linking to Luma/Partiful.
- `cache.json` (gitignored) — last successful pull. `feed.ics` is generated from it so the
  merged view can be subscribed to from Google/Apple Calendar.
- `.session.json` (gitignored, 0600) — Luma session key + email. `.partiful.json` — feed URL.

## How Luma sources are configured (phone-friendly first)

1. **Calendar links** (default): musnit pastes luma.com calendar links; `resolve_luma_link` loads the
   page's `__NEXT_DATA__` and finds the calendar object. Stored in `.luma_calendars.json`.
2. **Bookmarklet**: run on luma.com, it fetches `/home/get-following-calendars` with the browser's
   cookies and redirects to `/events/#import=<base64 json>`; the frontend posts it to `/api/luma/import`.
3. **Session cookie** (desktop only): paste `luma.auth-session-key`; then the followed list and
   registered events sync automatically. The email-code flow is blocked by Luma's Turnstile check.
4. **Personal iCal feed** (optional): Settings → Calendar Syncing link; marks registered events ✓.
   Accepts the raw ics/get URL, webcal://, or the Google add-by-URL link (`cid=`).

## Luma internals used (api.luma.com, undocumented, may change)

- Email-code sign-in is blocked by a Cloudflare Turnstile check (`auth/additional-verification-required`), so the normal path is pasting the `luma.auth-session-key` cookie from a logged-in browser (`POST /api/auth/session`). Kept for reference: `POST /auth/email/send-sign-in-code {email}` then `POST /auth/email/sign-in-with-code
  {email, code}` → `Set-Cookie: luma.auth-session-key=…`. If the response has
  `step: "two_factor"`, `POST /auth/sign-in-with-two-factor`.
- `GET /home/get-following-calendars` (cookie) → calendars he follows. Response shape was
  guessed from the redirect target; `normalize_calendar` accepts bare or wrapped objects.
- `GET /calendar/get-items?calendar_api_id=cal-…&period=future&pagination_limit=50`
  (public, paginated with `pagination_cursor`/`next_cursor`) → events per calendar.
- `GET /home/get-events?period=future` (cookie) → events he registered for; marks `going`.
- A 401 on refresh wipes `.session.json` so the UI asks to sign in again.

## Partiful

Feed URL looks like `webcal://calendars.partiful.com/getCalendar?id=…`. The user gets it from
any event page → calendar icon → Google Calendar → Copy Link. `fetch_partiful` converts
webcal→https, parses VEVENTs (TZID, UTC and all-day DATE forms), drops events older than a day.

## API

`GET /api/state` · `POST /api/auth/{send-code,verify,two-factor,signout}` ·
`POST /api/partiful {url}` (empty url removes) · `POST /api/refresh` · `GET /feed.ics`.

## Hosting

- systemd user service `luma-cal.service` (`deploy/luma-cal.service`). Restart after editing
  the server: `systemctl --user restart luma-cal`.
- Route `https://clawd.musnitzky.com/events/` via the agentlab-nginx container behind Authelia.
  Route file `deploy/events.conf` → `/home/ubuntu/agentlab/services/nginx/routes/events.conf`,
  then `docker exec agentlab-nginx nginx -t && docker exec agentlab-nginx nginx -s reload`.
- Port 8771 (8765/8766/8770 are property-comps, furnishing, home-ops).

## Gotchas

- Everything on the Luma side is unofficial. If refreshes start failing, re-pull luma.com's
  JS chunks and grep for `send-sign-in-code`, `home/get-events`, `get-following-calendars`.
- The frontend has no service worker, so there is no cache to bump.
- Never commit `.session.json`, `.partiful.json` or `cache.json`.

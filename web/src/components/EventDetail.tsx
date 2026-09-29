import { useMemo, useState } from "react";
import { apiUrl } from "../lib/api.ts";
import { localDayOf, parseDayKey } from "../lib/dates.ts";
import { EMPTY_FILTERS, isNew } from "../lib/filters.ts";
import {
  eventZoneNote, fmtDayLong, fmtPrice, fmtTimeRange, goingLabel, googleCalendarUrl, hasEnded, isHappeningNow, mapsUrl, SOURCE_LABEL,
} from "../lib/format.ts";
import { imgUrl } from "../lib/images.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { data, useData } from "../state/data.ts";
import { toast } from "../state/toasts.ts";
import { Avatar } from "./Avatar.tsx";
import { Icon } from "./Icon.tsx";
import { Link } from "./Link.tsx";

function overlaps(a: EventItem, b: EventItem): boolean {
  const end = (e: EventItem) => Date.parse(e.end_at || e.start_at) || Date.parse(e.start_at) + 2 * 3600_000;
  return Date.parse(a.start_at) < end(b) && Date.parse(b.start_at) < end(a);
}

async function share(ev: EventItem): Promise<void> {
  const url = new URL(href({ name: "event", id: ev.id }), document.baseURI).toString();
  if (navigator.share) {
    try {
      await navigator.share({ title: ev.name, url: ev.url || url });
      return;
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
    }
  }
  try {
    await navigator.clipboard.writeText(ev.url || url);
    toast("Link copied");
  } catch {
    toast("Could not copy the link", "error");
  }
}

export function EventDetail({ ev }: { ev: EventItem }) {
  const { catalog, newSince } = useData();
  const [imageBroken, setImageBroken] = useState(false);
  const calendars = ev.calendar_ids.map((id) => catalog?.calendarsById.get(id)).filter((c) => !!c);
  const presenterCalendar = ev.presenter?.id ? catalog?.calendarsById.get(ev.presenter.id) : undefined;
  const vibes = ev.vibes.map((id) => catalog?.vibeById.get(id)).filter((c) => !!c);
  const topics = ev.topics.map((id) => catalog?.topicById.get(id)).filter((c) => !!c);
  const zone = catalog?.zones.find((z) => z.id === ev.zone)?.label;
  const date = parseDayKey(localDayOf(ev.start_at));
  const price = fmtPrice(ev.ticket);
  const zoneNote = eventZoneNote(ev);
  const maps = mapsUrl(ev);
  const cover = !imageBroken ? imgUrl(ev.cover_url, 640) : undefined;
  const conflicts = useMemo(
    () => (catalog?.events ?? []).filter((o) => o.id !== ev.id && (o.going || o.starred) && overlaps(o, ev)),
    [catalog, ev],
  );
  const muted = calendars.length > 0 && calendars.every((c) => c.muted);
  const loc = ev.location;
  const hostPeople = ev.hosts.filter((h) => h.name !== ev.presenter?.name);

  return (
    <article className="event-detail" data-vibe={ev.vibes[0] || "none"}>
      {cover && (
        <div className="detail-cover">
          <img src={cover} alt="" referrerPolicy="no-referrer" onError={() => setImageBroken(true)} />
        </div>
      )}
      <div className="detail-badges">
        {isHappeningNow(ev) && <span className="badge live">Happening now</span>}
        {hasEnded(ev) && <span className="badge">Ended</span>}
        {ev.going && <span className="badge going"><Icon name="check" size={13} />{goingLabel(ev.going_status)}</span>}
        {ev.starred && <span className="badge saved"><Icon name="star" filled size={13} />Saved</span>}
        {isNew(ev, newSince) && <span className="badge new">New since your last visit</span>}
      </div>
      <h1 className="detail-title">{ev.name}</h1>

      <div className="detail-actions">
        <a className="button primary" href={ev.url} target="_blank" rel="noopener noreferrer">
          {ev.going ? "Open" : "RSVP"} on {SOURCE_LABEL[ev.source]} <Icon name="external" size={16} />
        </a>
        <button type="button" className={`button${ev.starred ? " on" : ""}`} aria-pressed={ev.starred}
          onClick={() => void data.mark(ev.id, { starred: !ev.starred })}>
          <Icon name="star" filled={ev.starred} size={18} /> {ev.starred ? "Saved" : "Save"}
        </button>
        <button type="button" className="button" onClick={() => void share(ev)}>
          <Icon name="share" size={18} /> Share
        </button>
      </div>

      <dl className="detail-facts">
        <div>
          <dt><Icon name="clock" /><span className="sr-only">When</span></dt>
          <dd>
            <Link to={href({ name: "day", date: localDayOf(ev.start_at) })}>{date ? fmtDayLong(date) : ""}</Link>
            <div>{fmtTimeRange(ev)}{zoneNote && <span className="muted"> · {zoneNote} there</span>}</div>
            <div className="fact-links">
              <a href={googleCalendarUrl(ev)} target="_blank" rel="noopener noreferrer">Add to Google Calendar</a>
              <a href={apiUrl(`api/events/${encodeURIComponent(ev.id)}/ics`)} download>Download .ics</a>
            </div>
          </dd>
        </div>
        <div>
          <dt><Icon name={loc.type === "online" ? "link" : "pin"} /><span className="sr-only">Where</span></dt>
          <dd>
            {loc.type === "online" ? <div>Online</div> : (
              <>
                {loc.venue && <div className="strong">{loc.venue}</div>}
                {loc.address && <div>{loc.address}</div>}
                {!loc.address && !loc.venue && <div>{loc.city || "Address shown to guests on the event page"}</div>}
                {(() => {
                  // Neighbourhood and area, unless the address already says so.
                  const shown = `${loc.venue ?? ""} ${loc.address ?? ""} ${!loc.address && !loc.venue ? loc.city ?? "" : ""}`.toLowerCase();
                  const extra = [loc.neighborhood, zone].filter((x): x is string => !!x && !shown.includes(x.toLowerCase()));
                  return extra.length ? <div className="muted">{extra.join(" · ")}</div> : null;
                })()}
                {maps && <div className="fact-links"><a href={maps} target="_blank" rel="noopener noreferrer">Open in Maps</a></div>}
              </>
            )}
          </dd>
        </div>
        {(price || ev.ticket?.approval || ev.ticket?.spots_left != null) && (
          <div>
            <dt><Icon name="ticket" /><span className="sr-only">Tickets</span></dt>
            <dd>
              {price && <div className="strong">{price}</div>}
              {ev.ticket?.approval && <div className="muted">Registration needs approval</div>}
              {ev.ticket?.spots_left != null && ev.ticket.spots_left > 0 && ev.ticket.spots_left < 50 && (
                <div className="muted">{ev.ticket.spots_left} spots left</div>
              )}
            </dd>
          </div>
        )}
        {ev.guest_count ? (
          <div>
            <dt><Icon name="users" /><span className="sr-only">Guests</span></dt>
            <dd>{ev.guest_count.toLocaleString()} going</dd>
          </div>
        ) : null}
      </dl>

      {conflicts.length > 0 && (
        <div className="callout">
          <strong>Overlaps with your plans:</strong>
          <ul>
            {conflicts.slice(0, 4).map((c) => (
              <li key={c.id}>
                <Link to={href({ name: "event", id: c.id })} options={{ replace: true, background: history.state?.background ?? null }}>{c.name}</Link>
                {" "}<span className="muted">({fmtTimeRange(c)}{c.going ? ", going" : ", saved"})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(vibes.length > 0 || topics.length > 0) && (
        <section className="detail-section">
          <h2>Vibe</h2>
          <div className="chips">
            {vibes.map((v) => (
              <Link key={v.id} className="chip" to={href({ name: "agenda", from: null }, { ...EMPTY_FILTERS, vibes: [v.id] })} title={v.hint}>
                {v.emoji} {v.label}
              </Link>
            ))}
            {topics.map((t) => (
              <Link key={t.id} className="chip" to={href({ name: "agenda", from: null }, { ...EMPTY_FILTERS, topics: [t.id] })} title={t.hint}>
                {t.emoji} {t.label}
              </Link>
            ))}
          </div>
        </section>
      )}

      {(ev.presenter?.name || hostPeople.length > 0) && (
        <section className="detail-section">
          <h2>Hosted by</h2>
          <ul className="people">
            {ev.presenter?.name && (
              <li>
                <Avatar src={ev.presenter.avatar_url} name={ev.presenter.name} size={40} square />
                <div>
                  {presenterCalendar
                    ? <Link to={href({ name: "calendar", id: presenterCalendar.id })} className="strong">{ev.presenter.name}</Link>
                    : ev.presenter.url
                      ? <a href={ev.presenter.url} target="_blank" rel="noopener noreferrer" className="strong">{ev.presenter.name}</a>
                      : <span className="strong">{ev.presenter.name}</span>}
                  {ev.presenter.description && <div className="muted clamp-2">{ev.presenter.description}</div>}
                </div>
              </li>
            )}
            {hostPeople.slice(0, 8).map((h, i) => (
              <li key={`${h.name}-${i}`}>
                <Avatar src={h.avatar_url} name={h.name} size={32} />
                <span>{h.name}</span>
              </li>
            ))}
            {hostPeople.length > 8 && <li className="muted">and {hostPeople.length - 8} more</li>}
          </ul>
        </section>
      )}

      {ev.tags.length > 0 && (
        <section className="detail-section">
          <h2>Tags</h2>
          <div className="chips">{ev.tags.map((t) => <span key={t} className="chip static">{t}</span>)}</div>
        </section>
      )}

      <section className="detail-section">
        <h2>Listed on</h2>
        <ul className="listed-on">
          {calendars.map((c) => (
            <li key={c.id}>
              <Link to={href({ name: "calendar", id: c.id })}>{c.name}</Link>
              {c.muted && <span className="muted"> (muted)</span>}
            </li>
          ))}
          {ev.also_on.map((o) => (
            <li key={o.url}><a href={o.url} target="_blank" rel="noopener noreferrer">{SOURCE_LABEL[o.source]}</a></li>
          ))}
        </ul>
      </section>

      <div className="detail-quiet-actions">
        <button type="button" className="button subtle" onClick={() => void data.mark(ev.id, { hidden: !ev.hidden })}>
          <Icon name={ev.hidden ? "eye" : "eyeOff"} size={18} /> {ev.hidden ? "Unhide event" : "Hide this event"}
        </button>
        {calendars.length === 1 && (
          <button type="button" className="button subtle" onClick={() => void data.setMuted(calendars[0].id, !muted)}>
            <Icon name="bellOff" size={18} /> {muted ? `Unmute ${calendars[0].name}` : `Mute ${calendars[0].name}`}
          </button>
        )}
      </div>
      {ev.first_seen_at && <p className="muted small">First seen here {new Date(ev.first_seen_at).toLocaleDateString()}</p>}
    </article>
  );
}

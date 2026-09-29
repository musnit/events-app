import { memo, useState, type ReactNode } from "react";
import { isNew } from "../lib/filters.ts";
import { eventZoneNote, fmtPrice, fmtTimeRange, hasEnded, isHappeningNow, whereLine } from "../lib/format.ts";
import { imgUrl } from "../lib/images.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { data, useData } from "../state/data.ts";
import { currentRelativeUrl } from "../state/router.ts";
import { Avatar } from "./Avatar.tsx";
import { Icon } from "./Icon.tsx";
import { Link } from "./Link.tsx";

/** The people and groups behind an event: the presenting calendar first, then up to two hosts. */
export function hostLine(ev: EventItem, calendarName?: string): { names: string; avatars: { src: string | null; name: string }[] } {
  const lead = ev.presenter?.name || calendarName || "";
  const people = ev.hosts.filter((h) => h.name.trim().toLowerCase() !== lead.trim().toLowerCase());
  const shown = people.slice(0, 2);
  const names = [lead, ...shown.map((h) => h.name)].filter(Boolean).join(" · ") + (people.length > 2 ? ` +${people.length - 2}` : "");
  const avatars = [
    ...(lead ? [{ src: ev.presenter?.avatar_url ?? null, name: lead }] : []),
    ...shown.map((h) => ({ src: h.avatar_url, name: h.name })),
  ];
  return { names, avatars };
}

function Cover({ ev, size }: { ev: EventItem; size: number }) {
  const [broken, setBroken] = useState(false);
  const src = !broken ? imgUrl(ev.cover_url, size) : undefined;
  if (src) {
    return <img className="cover" src={src} alt="" width={size} height={size} loading="lazy" decoding="async"
      referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
  }
  return (
    <div className="cover placeholder" aria-hidden="true">
      {ev.presenter?.avatar_url ? <Avatar src={ev.presenter.avatar_url} name={ev.presenter.name || ev.name} size={44} /> : <span>{ev.name.slice(0, 1)}</span>}
    </div>
  );
}

function Badges({ ev, newSince }: { ev: EventItem; newSince: number | null }): ReactNode {
  const price = fmtPrice(ev.ticket);
  return (
    <>
      {isHappeningNow(ev) && <span className="badge live">Now</span>}
      {ev.going && <span className="badge going"><Icon name="check" size={13} />Going</span>}
      {isNew(ev, newSince) && <span className="badge new">New</span>}
      {price && <span className={`badge price${ev.ticket?.sold_out ? " soldout" : ""}`}>{price}</span>}
    </>
  );
}

interface Props {
  ev: EventItem;
  /** Show the date as well as the time (lists spanning many days show it in the group header instead). */
  showDate?: boolean;
  dateLabel?: string;
}

export const EventCard = memo(function EventCard({ ev, showDate, dateLabel }: Props) {
  const { catalog, newSince } = useData();
  const primary = ev.vibes[0];
  const calendarName = ev.calendar_ids.map((id) => catalog?.calendarsById.get(id)?.name).find(Boolean);
  const { names, avatars } = hostLine(ev, calendarName);
  const zoneNote = eventZoneNote(ev);
  const where = whereLine(ev);
  const ended = hasEnded(ev);
  const categories = [
    ...ev.vibes.slice(0, 1).map((id) => catalog?.vibeById.get(id)),
    ...ev.topics.slice(0, 1).map((id) => catalog?.topicById.get(id)),
  ].filter(Boolean);

  return (
    <article className={`event-card${ended ? " ended" : ""}${ev.hidden || ev.muted ? " dimmed" : ""}`} data-vibe={primary || "none"}>
      <Cover ev={ev} size={96} />
      <div className="event-body">
        <div className="event-when">
          <span>{showDate && dateLabel ? `${dateLabel} · ` : ""}{fmtTimeRange(ev)}</span>
          {zoneNote && <span className="zone-note">({zoneNote})</span>}
        </div>
        <h3 className="event-title">
          <Link to={href({ name: "event", id: ev.id })} options={{ background: currentRelativeUrl() }} className="stretched">
            {ev.name}
          </Link>
        </h3>
        {names && (
          <div className="event-hosts">
            <span className="avatar-stack">{avatars.map((a, i) => <Avatar key={i} src={a.src} name={a.name} size={20} />)}</span>
            <span className="truncate">{names}</span>
          </div>
        )}
        <div className="event-meta">
          <span className="truncate"><Icon name={ev.location.type === "online" ? "link" : "pin"} size={14} />{where}</span>
          {ev.guest_count ? <span className="nowrap"><Icon name="users" size={14} />{ev.guest_count.toLocaleString()}</span> : null}
        </div>
        <div className="event-tags">
          <Badges ev={ev} newSince={newSince} />
          {categories.map((c) => c && <span key={c.id} className="tag">{c.emoji} {c.label}</span>)}
        </div>
      </div>
      <button
        type="button"
        className={`star-button${ev.starred ? " on" : ""}`}
        aria-pressed={ev.starred}
        aria-label={ev.starred ? `Remove ${ev.name} from saved` : `Save ${ev.name}`}
        title={ev.starred ? "Saved" : "Save"}
        onClick={() => void data.mark(ev.id, { starred: !ev.starred })}
      >
        <Icon name="star" filled={ev.starred} size={20} />
      </button>
    </article>
  );
});

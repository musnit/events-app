import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { dayKey, parseDayKey, type DayKey } from "../lib/dates.ts";
import { groupByDay } from "../lib/event-groups.ts";
import { fmtDayShort, relativeDay } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { EventCard } from "./EventCard.tsx";
import { Link } from "./Link.tsx";

const PAGE = 40;

/** Events grouped under sticky day headings. Renders in pages as you scroll, so long lists stay fast. */
export function EventList({ events, resetKey, empty, dayLinks = true, ongoingBefore }: { events: EventItem[]; resetKey: string; empty: ReactNode; dayLinks?: boolean; ongoingBefore?: DayKey }) {
  const [limit, setLimit] = useState(PAGE);
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => setLimit(PAGE), [resetKey]);

  useEffect(() => {
    const node = sentinel.current;
    if (!node) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setLimit((n) => n + PAGE);
    }, { rootMargin: "800px 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [limit, events.length]);

  const groups = useMemo(() => groupByDay(events.slice(0, limit), ongoingBefore), [events, limit, ongoingBefore]);
  const today = dayKey(new Date());

  if (!events.length) return <>{empty}</>;
  return (
    <div className="event-list">
      {groups.map((group) => {
        const ongoing = group.key === "ongoing";
        const date = parseDayKey(group.key);
        const label = ongoing ? "Ongoing" : relativeDay(group.key);
        const full = ongoing ? label : date ? fmtDayShort(date) : group.key;
        return (
          <section key={group.key} className="day-group" aria-label={label === full ? full : `${label}, ${full}`}>
            <h2 className={`day-heading${group.key === today ? " today" : ""}`}>
              {dayLinks && !ongoing ? <Link to={href({ name: "day", date: group.key })}>{label}</Link> : <span>{label}</span>}
              {label !== full && <span className="day-date">{full}</span>}
              <span className="day-count">{group.events.length}</span>
            </h2>
            <div className="cards">
              {group.events.map((ev) => <EventCard key={ev.id} ev={ev} showDate={ongoing}
                dateLabel={ongoing ? `Started ${fmtDayShort(new Date(ev.start_at))}` : undefined} />)}
            </div>
          </section>
        );
      })}
      {limit < events.length && (
        <div ref={sentinel} className="list-more">
          <button type="button" className="button" onClick={() => setLimit((n) => n + PAGE * 2)}>
            Show more ({(events.length - limit).toLocaleString()} left)
          </button>
        </div>
      )}
    </div>
  );
}

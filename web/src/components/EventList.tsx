import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { dayKey, localDayOf, parseDayKey } from "../lib/dates.ts";
import { fmtDayShort, relativeDay } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { EventCard } from "./EventCard.tsx";
import { Link } from "./Link.tsx";

const PAGE = 40;

interface Group {
  key: string;
  events: EventItem[];
}

export function groupByDay(events: EventItem[]): Group[] {
  const groups: Group[] = [];
  let current: Group | null = null;
  for (const ev of events) {
    const key = localDayOf(ev.start_at);
    if (!current || current.key !== key) {
      current = { key, events: [] };
      groups.push(current);
    }
    current.events.push(ev);
  }
  return groups;
}

/** Events grouped under sticky day headings. Renders in pages as you scroll, so long lists stay fast. */
export function EventList({ events, resetKey, empty, dayLinks = true }: { events: EventItem[]; resetKey: string; empty: ReactNode; dayLinks?: boolean }) {
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

  const groups = useMemo(() => groupByDay(events.slice(0, limit)), [events, limit]);
  const today = dayKey(new Date());

  if (!events.length) return <>{empty}</>;
  return (
    <div className="event-list">
      {groups.map((group) => {
        const date = parseDayKey(group.key);
        const label = relativeDay(group.key);
        const full = date ? fmtDayShort(date) : group.key;
        return (
          <section key={group.key} className="day-group" aria-label={label === full ? full : `${label}, ${full}`}>
            <h2 className={`day-heading${group.key === today ? " today" : ""}`}>
              {dayLinks ? <Link to={href({ name: "day", date: group.key })}>{label}</Link> : <span>{label}</span>}
              {label !== full && <span className="day-date">{full}</span>}
              <span className="day-count">{group.events.length}</span>
            </h2>
            <div className="cards">
              {group.events.map((ev) => <EventCard key={ev.id} ev={ev} />)}
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

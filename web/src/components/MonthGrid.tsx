import { useMemo } from "react";
import { dayKey, localDayOf, monthGrid } from "../lib/dates.ts";
import { fmtTime } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { currentRelativeUrl } from "../state/router.ts";
import { Link } from "./Link.tsx";

const WEEKDAYS = Array.from({ length: 7 }, (_, i) =>
  new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(new Date(2026, 1, 1 + i)));
const PER_DAY = 4;

/** Month grid. Wide screens list a few events per day; phones show dots and link to the day. */
export function MonthGrid({ month, events, filtersQuery }: { month: Date; events: EventItem[]; filtersQuery: string }) {
  const days = useMemo(() => monthGrid(month), [month]);
  const byDay = useMemo(() => {
    const map = new Map<string, EventItem[]>();
    for (const ev of events) {
      const key = localDayOf(ev.start_at);
      const list = map.get(key);
      if (list) list.push(ev);
      else map.set(key, [ev]);
    }
    return map;
  }, [events]);
  const today = dayKey(new Date());
  const weeks = days.length / 7;

  return (
    <div className="month-grid">
      <div className="month-row weekdays" aria-hidden="true">
        {WEEKDAYS.map((d) => <div key={d} className="weekday">{d}</div>)}
      </div>
      {Array.from({ length: weeks }, (_, w) => {
        const week = days.slice(w * 7, w * 7 + 7);
        // Weeks already over with nothing in them shrink, so the weeks ahead get the space.
        const past = dayKey(week[6]) < today && week.every((d) => !byDay.get(dayKey(d))?.length);
        return (
        <div className={`month-row${past ? " past" : ""}`} key={w}>
          {week.map((d) => {
            const key = dayKey(d);
            const list = byDay.get(key) ?? [];
            const outside = d.getMonth() !== month.getMonth();
            const dayHref = href({ name: "day", date: key }) + (filtersQuery ? `?${filtersQuery}` : "");
            const label = `${new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(d)}, ${list.length} ${list.length === 1 ? "event" : "events"}`;
            return (
              <div key={key} className={`month-day${outside ? " outside" : ""}${key === today ? " today" : ""}${list.length ? " has-events" : ""}`}>
                <Link to={dayHref} className="day-number" aria-label={label}>{d.getDate()}</Link>
                <ul className="month-events">
                  {list.slice(0, PER_DAY).map((ev) => (
                    <li key={ev.id} data-vibe={ev.vibes[0] || "none"} className={ev.going ? "going" : ev.starred ? "starred" : ""}>
                      <Link to={href({ name: "event", id: ev.id })} options={{ background: currentRelativeUrl() }} title={ev.name}>
                        <span className="time">{ev.all_day ? "" : fmtTime(ev.start_at)}</span> {ev.name}
                      </Link>
                    </li>
                  ))}
                  {list.length > PER_DAY && (
                    <li className="more"><Link to={dayHref}>+{list.length - PER_DAY} more</Link></li>
                  )}
                </ul>
                {list.length > 0 && (
                  <div className="month-dots" aria-hidden="true">
                    {list.slice(0, 3).map((ev) => <span key={ev.id} className="dot" data-vibe={ev.vibes[0] || "none"} />)}
                    {list.length > 3 && <span className="dot-more">{list.length}</span>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        );
      })}
    </div>
  );
}

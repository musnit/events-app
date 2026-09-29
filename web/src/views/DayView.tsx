import { useCallback } from "react";
import { EventCard } from "../components/EventCard.tsx";
import { Icon } from "../components/Icon.tsx";
import { Link } from "../components/Link.tsx";
import { Page } from "../components/Page.tsx";
import { addDays, dayKey, localDayOf, monthKey, parseDayKey } from "../lib/dates.ts";
import { fmtDayLong, fmtDayShort, relativeDay } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { useData } from "../state/data.ts";
import { useView } from "../state/view.ts";
import { useFilteredEvents, useMediaQuery } from "../state/hooks.ts";
import { LoadingCards, NoEvents } from "./common.tsx";

/** One day's events, including ones still running from the day before. */
export function DayView({ date }: { date: string }) {
  const { route, filters } = useView();
  const { catalog, loading } = useData();
  const day = parseDayKey(date)!;
  const next = addDays(day, 1);
  const scope = useCallback((ev: EventItem) => {
    const start = Date.parse(ev.start_at);
    const end = ev.end_at ? Date.parse(ev.end_at) : start + 2 * 3600_000;
    return start < next.getTime() && end > day.getTime() && (localDayOf(ev.start_at) === date || end - day.getTime() > 3 * 3600_000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);
  const { events, counts } = useFilteredEvents(filters, { scope });
  const label = relativeDay(date);
  const prev = dayKey(addDays(day, -1));
  const following = dayKey(next);
  const isToday = date === dayKey(new Date());
  const roomy = useMediaQuery("(min-width: 560px)");

  return (
    <Page
      eyebrow={label !== fmtDayShort(day) ? label : undefined}
      title={roomy ? fmtDayLong(day) : fmtDayShort(day)}
      filterable={{ route, filters, counts, resultCount: events.length }}
      controls={
        <div className="date-nav">
          <Link className="icon-button" to={href({ name: "day", date: prev }, filters)} aria-label={`Previous day, ${fmtDayShort(addDays(day, -1))}`}>
            <Icon name="chevronLeft" />
          </Link>
          {!isToday && <Link className="button small" to={href({ name: "day", date: dayKey(new Date()) }, filters)}>Today</Link>}
          <Link className="icon-button" to={href({ name: "day", date: following }, filters)} aria-label={`Next day, ${fmtDayShort(next)}`}>
            <Icon name="chevronRight" />
          </Link>
        </div>
      }
    >
      {loading && !catalog ? <LoadingCards count={4} /> : events.length ? (
        <div className="event-list">
          <div className="cards">{events.map((ev) => <EventCard key={ev.id} ev={ev} />)}</div>
          <p className="list-footer">
            <Link to={href({ name: "month", month: monthKey(day) }, filters)}>See {new Intl.DateTimeFormat(undefined, { month: "long" }).format(day)}</Link>
            {" · "}
            <Link to={href({ name: "agenda", from: date }, filters)}>Everything from this day on</Link>
          </p>
        </div>
      ) : (
        <NoEvents route={route} filters={filters} what={`events on ${label === "Today" || label === "Tomorrow" ? label.toLowerCase() : "this day"}`} />
      )}
    </Page>
  );
}

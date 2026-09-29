import { useCallback } from "react";
import { EventList } from "../components/EventList.tsx";
import { Link } from "../components/Link.tsx";
import { Page } from "../components/Page.tsx";
import { addDays, dayKey, parseDayKey, startOfDay, weekendStart, type DayKey } from "../lib/dates.ts";
import { filtersToParams } from "../lib/filters.ts";
import { fmtDayLong, hasEnded } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { useData } from "../state/data.ts";
import { useView } from "../state/view.ts";
import { useFilteredEvents, useNow } from "../state/hooks.ts";
import { navigate } from "../state/router.ts";
import { LoadingCards, NoEvents } from "./common.tsx";

/** Upcoming events from today (or from a chosen date), grouped by day. */
export function AgendaView({ from }: { from: DayKey | null }) {
  const { route, filters } = useView();
  const { catalog, loading } = useData();
  const now = useNow();
  const fromDate = from ? parseDayKey(from) : null;
  const start = fromDate ? fromDate.getTime() : now;
  const scope = useCallback(
    (ev: EventItem) => (fromDate ? Date.parse(ev.start_at) >= start : !hasEnded(ev, start)),
    // fromDate is derived from `from`
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [from, start],
  );
  const { events, counts } = useFilteredEvents(filters, { scope });
  const today = startOfDay(new Date(now));
  const weekend = weekendStart(today);
  const jumps = [
    { label: "Today", to: href({ name: "agenda", from: null }, { ...filters, weekend: false }), on: !from && !filters.weekend },
    { label: "Tomorrow", to: href({ name: "day", date: dayKey(addDays(today, 1)) }, filters), on: false },
    { label: "This weekend", to: href({ name: "agenda", from: dayKey(weekend) }, { ...filters, weekend: true }), on: from === dayKey(weekend) && filters.weekend },
    { label: "Next week", to: href({ name: "agenda", from: dayKey(addDays(today, (8 - today.getDay()) % 7 || 7)) }, { ...filters, weekend: false }), on: false },
  ];

  return (
    <Page
      title={fromDate ? `From ${fmtDayLong(fromDate)}` : "Upcoming"}
      filterable={{ route, filters, counts, resultCount: events.length }}
    >
      <div className="jump-row" role="navigation" aria-label="Jump to">
        {jumps.map((j) => (
          <Link key={j.label} to={j.to} className={`chip${j.on ? " on" : ""}`} aria-current={j.on ? "page" : undefined}>{j.label}</Link>
        ))}
        <label className="chip date-chip">
          <span>Pick a date</span>
          <input type="date" value={from ?? ""} min={dayKey(addDays(today, -30))} aria-label="Show events from date"
            onChange={(e) => e.target.value && navigate(href({ name: "agenda", from: e.target.value }, filters))} />
        </label>
      </div>
      {loading && !catalog ? <LoadingCards /> : (
        <EventList events={events} resetKey={`${from}|${filtersToParams(filters)}`} empty={<NoEvents route={route} filters={filters} />} />
      )}
    </Page>
  );
}

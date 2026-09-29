import { useCallback, useMemo } from "react";
import { EventList } from "../components/EventList.tsx";
import { Icon } from "../components/Icon.tsx";
import { Link } from "../components/Link.tsx";
import { MonthGrid } from "../components/MonthGrid.tsx";
import { Page } from "../components/Page.tsx";
import { addDays, addMonths, monthGrid, monthKey, parseMonthKey } from "../lib/dates.ts";
import { filtersToParams } from "../lib/filters.ts";
import { fmtMonth } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { useView } from "../state/view.ts";
import { useFilteredEvents, useMediaQuery, useNow } from "../state/hooks.ts";
import { hasEnded } from "../lib/format.ts";

export function MonthView({ month }: { month: string }) {
  const { route, filters } = useView();
  const first = parseMonthKey(month)!;
  const grid = monthGrid(first);
  const from = grid[0].getTime();
  const to = addDays(grid[grid.length - 1], 1).getTime();
  const scope = useCallback((ev: EventItem) => {
    const t = Date.parse(ev.start_at);
    return t >= from && t < to;
  }, [from, to]);
  const { events, counts } = useFilteredEvents(filters, { scope });
  const inMonth = events.filter((ev) => new Date(ev.start_at).getMonth() === first.getMonth()).length;
  const thisMonth = monthKey(new Date());
  // Phones show dots in the grid, so the month's events are listed underneath it.
  const narrow = useMediaQuery("(max-width: 719px)");
  const now = useNow();
  const listed = useMemo(
    () => (narrow ? events.filter((ev) => new Date(ev.start_at).getMonth() === first.getMonth() && !hasEnded(ev, now)) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [narrow, events, month, now],
  );

  return (
    <Page
      title={fmtMonth(first)}
      eyebrow={`${inMonth.toLocaleString()} ${inMonth === 1 ? "event" : "events"}`}
      className="month-page"
      filterable={{ route, filters, counts, resultCount: inMonth }}
      controls={
        <div className="date-nav">
          <Link className="icon-button" to={href({ name: "month", month: monthKey(addMonths(first, -1)) }, filters)} aria-label="Previous month">
            <Icon name="chevronLeft" />
          </Link>
          {month !== thisMonth && <Link className="button small" to={href({ name: "month", month: thisMonth }, filters)}>Today</Link>}
          <Link className="icon-button" to={href({ name: "month", month: monthKey(addMonths(first, 1)) }, filters)} aria-label="Next month">
            <Icon name="chevronRight" />
          </Link>
        </div>
      }
    >
      <MonthGrid month={first} events={events} filtersQuery={filtersToParams(filters).toString()} />
      {narrow && listed.length > 0 && (
        <div className="month-list">
          <EventList events={listed} resetKey={`${month}|${filtersToParams(filters)}`} empty={null} />
        </div>
      )}
    </Page>
  );
}

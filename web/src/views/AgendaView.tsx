import { useCallback } from "react";
import { EventList } from "../components/EventList.tsx";
import { Link } from "../components/Link.tsx";
import { Page } from "../components/Page.tsx";
import { agendaIncludes } from "../lib/agenda.ts";
import { addDays, dayKey, parseDayKey, startOfDay, weekendStart, type DayKey } from "../lib/dates.ts";
import { filtersToParams } from "../lib/filters.ts";
import { fmtDayLong, fmtDayShort } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { useData } from "../state/data.ts";
import { useView } from "../state/view.ts";
import { useFilteredEvents, useNow } from "../state/hooks.ts";
import { navigate } from "../state/router.ts";
import { LoadingCards, NoEvents } from "./common.tsx";

/** Opens the browser's calendar for the date field laid invisibly over the "Pick a date" chip. A mouse
 * click only puts the cursor in a date field's text, so desktop browsers need showPicker(). A tap on a
 * phone or tablet already opens the device's own picker, so a coarse pointer keeps that. */
function openCalendar(input: HTMLInputElement): void {
  if (!window.matchMedia("(pointer: fine)").matches) return;
  try {
    input.showPicker();
  } catch {
    // The browser lacks showPicker() or refused it; the focused field still takes a typed date.
  }
}

/** Upcoming events from today (or from a chosen date), grouped by day. */
export function AgendaView({ from }: { from: DayKey | null }) {
  const { route, filters } = useView();
  const { catalog, loading } = useData();
  const now = useNow();
  const fromDate = from ? parseDayKey(from) : null;
  const scope = useCallback(
    (ev: EventItem) => agendaIncludes(ev, from, now),
    [from, now],
  );
  const { events, counts } = useFilteredEvents(filters, { scope });
  const today = startOfDay(new Date(now));
  const weekend = weekendStart(today);
  const nextWeek = dayKey(addDays(today, (8 - today.getDay()) % 7 || 7));
  const jumps = [
    { label: "Today", to: href({ name: "agenda", from: null }, { ...filters, weekend: false }), on: !from && !filters.weekend },
    { label: "Tomorrow", to: href({ name: "day", date: dayKey(addDays(today, 1)) }, filters), on: false },
    { label: "This weekend", to: href({ name: "agenda", from: dayKey(weekend) }, { ...filters, weekend: true }), on: from === dayKey(weekend) && filters.weekend },
    { label: "Next week", to: href({ name: "agenda", from: nextWeek }, { ...filters, weekend: false }), on: from === nextWeek && !filters.weekend },
  ];
  // A date chosen with the picker shows on its chip, unless one of the jumps above already names it.
  const picked = fromDate && !jumps.some((j) => j.on) ? fromDate : null;

  return (
    <Page
      title={fromDate ? `From ${fmtDayLong(fromDate)}` : "Upcoming"}
      filterable={{ route, filters, counts, resultCount: events.length }}
    >
      <div className="jump-row" role="navigation" aria-label="Jump to">
        {jumps.map((j) => (
          <Link key={j.label} to={j.to} className={`chip${j.on ? " on" : ""}`} aria-current={j.on ? "page" : undefined}>{j.label}</Link>
        ))}
        <label className={`chip date-chip${picked ? " on" : ""}`}>
          <span>{picked ? fmtDayShort(picked) : "Pick a date"}</span>
          <input type="date" value={from ?? ""} min={dayKey(addDays(today, -30))} max={dayKey(addDays(today, 730))}
            aria-label="Show events from a date"
            onClick={(e) => openCalendar(e.currentTarget)}
            onChange={(e) => {
              // Typing fires on every complete date, including a year still being typed, so only a date
              // inside the field's range moves the page.
              const input = e.currentTarget;
              if (input.value && input.validity.valid) navigate(href({ name: "agenda", from: input.value }, filters));
            }} />
        </label>
      </div>
      {loading && !catalog ? <LoadingCards /> : (
        <EventList events={events} resetKey={`${from}|${filtersToParams(filters)}`}
          ongoingBefore={fromDate ? undefined : dayKey(today)} empty={<NoEvents route={route} filters={filters} />} />
      )}
    </Page>
  );
}

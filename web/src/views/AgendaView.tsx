import { useCallback } from "react";
import { EventList } from "../components/EventList.tsx";
import { Link } from "../components/Link.tsx";
import { Page } from "../components/Page.tsx";
import { agendaIncludes } from "../lib/agenda.ts";
import { addDays, dayKey, parseDayKey, startOfDay, weekendStart, type DayKey } from "../lib/dates.ts";
import { areaToday } from "../lib/event-time.ts";
import { Icon } from "../components/Icon.tsx";
import { filtersToParams } from "../lib/filters.ts";
import { fmtDayLong, fmtDayShort, LOCAL_TZ } from "../lib/format.ts";
import { href, type Route } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { useData } from "../state/data.ts";
import { useView, useViewArea } from "../state/view.ts";
import { useArea, useFilteredEvents, useNow } from "../state/hooks.ts";
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

/** Why the Cape Town tab may be thin: this week's list is for Sundry Mag subscribers. */
function CapeTownLocked() {
  const { status } = useData();
  if (!status?.sundry.enabled || !status.sundry.locked.length) return null;
  return (
    <div className="callout">
      <Icon name="alert" size={16} /> Sundry Mag's list for this week is for subscribers, so only its first day is here.{" "}
      <Link to={href({ name: "sources", section: "sundry" })}>Add your Substack session</Link> to see the whole week.
    </div>
  );
}

function CapeTownSources() {
  const { status } = useData();
  return (
    <p className="list-footer">
      Cape Town events come from your calendars
      {status?.sundry.enabled && <>, from <a href="https://sundrymag.substack.com/" target="_blank" rel="noopener noreferrer">Sundry Mag</a>'s weekly list</>}
      {status?.instagram.enabled && status.instagram.reader && <> and from the <Link to={href({ name: "sources", section: "sundry" })}>Instagram accounts</Link> you follow</>}.
      {LOCAL_TZ !== "Africa/Johannesburg" && " Times are Cape Town's (SAST)."}
    </p>
  );
}

/** Upcoming events from today (or from a chosen date), grouped by day. The Cape Town tab is the same
 * list for Cape Town alone. */
export function AgendaView({ from }: { from: DayKey | null }) {
  const view = useView();
  const { route } = view;
  const pinned = useViewArea();
  const { catalog, loading } = useData();
  // Links that leave this tab for a day or a later date say which area they came from.
  const filters = pinned ? { ...view.filters, area: pinned } : view.filters;
  const home: Route = pinned === "cape-town" ? { name: "cape-town" } : { name: "agenda", from: null };
  const now = useNow();
  const fromDate = from ? parseDayKey(from) : null;
  const scope = useCallback(
    (ev: EventItem) => agendaIncludes(ev, from, now),
    [from, now],
  );
  const { events, counts } = useFilteredEvents(view.filters, { scope });
  const preferredArea = useArea();
  const today = startOfDay(areaToday(filters.area ?? preferredArea, now));
  const weekend = weekendStart(today);
  const nextWeek = dayKey(addDays(today, (8 - today.getDay()) % 7 || 7));
  const jumps = [
    { label: "Today", to: href(home, { ...filters, weekend: false }), on: !from && !filters.weekend },
    { label: "Tomorrow", to: href({ name: "day", date: dayKey(addDays(today, 1)) }, filters), on: false },
    { label: "This weekend", to: href({ name: "agenda", from: dayKey(weekend) }, { ...filters, weekend: true }), on: from === dayKey(weekend) && filters.weekend },
    { label: "Next week", to: href({ name: "agenda", from: nextWeek }, { ...filters, weekend: false }), on: from === nextWeek && !filters.weekend },
  ];
  // A date chosen with the picker shows on its chip, unless one of the jumps above already names it.
  const picked = fromDate && !jumps.some((j) => j.on) ? fromDate : null;

  return (
    <Page
      title={fromDate ? `From ${fmtDayLong(fromDate)}` : pinned === "cape-town" ? "Cape Town" : "Upcoming"}
      eyebrow={pinned === "cape-town" ? "Upcoming in" : undefined}
      filterable={{ route, filters: view.filters, counts, resultCount: events.length }}
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
      {pinned === "cape-town" && <CapeTownLocked />}
      {loading && !catalog ? <LoadingCards /> : (
        <EventList events={events} resetKey={`${from}|${filtersToParams(filters)}`}
          ongoingBefore={fromDate ? undefined : dayKey(today)} empty={<NoEvents route={route} filters={view.filters} />} />
      )}
      {pinned === "cape-town" && <CapeTownSources />}
    </Page>
  );
}

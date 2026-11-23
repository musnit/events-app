import { dayKey, isWeekend, localDayOf, parseDayKey, timeOfDay, type TimeOfDay } from "./dates.ts";
import type { AreaChoice, EventItem } from "./types.ts";

/** A destination keeps its own clock: its dates and times read the same wherever you plan from.
 * South Africa has no daylight saving, so a fixed offset is exact. */
interface Destination { timeZone: string; offsetMinutes: number; abbreviation: string }
const DESTINATIONS: Record<string, Destination> = {
  "cape-town": { timeZone: "Africa/Johannesburg", offsetMinutes: 120, abbreviation: "SAST" },
};

export type EventClock = Pick<EventItem, "start_at"> & Partial<Pick<EventItem, "end_at" | "area">>;

export function eventDestination(ev: EventClock): Destination | undefined {
  return ev.area ? DESTINATIONS[ev.area] : undefined;
}

/** The destination area an event belongs to, as an area choice for links that should stay there. */
export function destinationArea(ev: EventClock): AreaChoice | null {
  return eventDestination(ev) ? (ev.area as AreaChoice) : null;
}

/** The destination every one of these events shares, if they do. */
export function sharedDestination(events: EventClock[]): AreaChoice | null {
  const first = events.length ? destinationArea(events[0]) : null;
  return first && events.every((ev) => ev.area === first) ? first : null;
}

/** Wall-clock time at the destination, read through the UTC accessors. */
function shifted(zone: Destination, ms: number): Date {
  return new Date(ms + zone.offsetMinutes * 60_000);
}

export function eventDayOf(ev: EventClock, iso = ev.start_at): string {
  const zone = eventDestination(ev);
  return zone ? shifted(zone, Date.parse(iso)).toISOString().slice(0, 10) : localDayOf(iso);
}

export function eventHour(ev: EventClock, iso = ev.start_at): number {
  const zone = eventDestination(ev);
  return zone ? shifted(zone, Date.parse(iso)).getUTCHours() : new Date(iso).getHours();
}

export function eventTimeOfDay(ev: EventClock): TimeOfDay {
  if (!eventDestination(ev)) return timeOfDay(ev.start_at);
  const hour = eventHour(ev);
  return hour >= 5 && hour < 12 ? "morning" : hour >= 12 && hour < 17 ? "afternoon" : hour >= 17 && hour < 21 ? "evening" : "late";
}

export function eventIsWeekend(ev: EventClock): boolean {
  if (!eventDestination(ev)) return isWeekend(ev.start_at);
  const weekday = new Date(`${eventDayOf(ev)}T12:00:00Z`).getUTCDay();
  return weekday === 0 || weekday === 6 || (weekday === 5 && eventHour(ev) >= 17);
}

export function eventDayStart(ev: EventClock, key: string): number {
  const zone = eventDestination(ev);
  return zone ? Date.parse(`${key}T00:00:00Z`) - zone.offsetMinutes * 60_000 : parseDayKey(key)!.getTime();
}

export function eventOnDay(ev: EventClock, key: string): boolean {
  const start = Date.parse(ev.start_at);
  const end = ev.end_at ? Date.parse(ev.end_at) : start + 2 * 3600_000;
  const midnight = eventDayStart(ev, key);
  const date = parseDayKey(key)!;
  date.setDate(date.getDate() + 1);
  const next = eventDayStart(ev, dayKey(date));
  return start < next && end > midnight && (eventDayOf(ev) === key || end - midnight > 3 * 3600_000);
}

/** A local Date holding the selected area's calendar date, for labels and date navigation. */
export function areaToday(area: string, now = Date.now()): Date {
  return DESTINATIONS[area] ? parseDayKey(eventDayOf({ start_at: new Date(now).toISOString(), area: area as EventItem["area"] }))! : new Date(now);
}

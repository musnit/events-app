import { dayKey, isWeekend, localDayOf, parseDayKey, timeOfDay, type TimeOfDay } from "./dates.ts";
import type { EventItem } from "./types.ts";

/** Mumbai is a destination calendar: its dates stay in IST even while planning from SF. */
export type EventClock = Pick<EventItem, "start_at"> & Partial<Pick<EventItem, "end_at" | "area" | "research" | "source">>;
export function eventTimeZone(ev: EventClock): string | undefined {
  return ev.area === "mumbai" || ev.research?.city.toLowerCase() === "mumbai" ? "Asia/Kolkata" : undefined;
}

export function eventDayOf(ev: EventClock, iso = ev.start_at): string {
  if (!eventTimeZone(ev)) return localDayOf(iso);
  // India uses UTC+05:30 year-round. UTC accessors avoid the viewer's DST rules.
  return new Date(Date.parse(iso) + 330 * 60_000).toISOString().slice(0, 10);
}

export function eventHour(ev: EventClock, iso = ev.start_at): number {
  return eventTimeZone(ev) ? new Date(Date.parse(iso) + 330 * 60_000).getUTCHours() : new Date(iso).getHours();
}

export function eventTimeOfDay(ev: EventClock): TimeOfDay {
  if (!eventTimeZone(ev)) return timeOfDay(ev.start_at);
  const hour = eventHour(ev);
  return hour >= 5 && hour < 12 ? "morning" : hour >= 12 && hour < 17 ? "afternoon" : hour >= 17 && hour < 21 ? "evening" : "late";
}

export function eventIsWeekend(ev: EventClock): boolean {
  if (!eventTimeZone(ev)) return isWeekend(ev.start_at);
  const weekday = new Date(`${eventDayOf(ev)}T12:00:00Z`).getUTCDay();
  return weekday === 0 || weekday === 6 || (weekday === 5 && eventHour(ev) >= 17);
}

export function eventDayStart(ev: EventClock, key: string): number {
  return eventTimeZone(ev) ? Date.parse(`${key}T00:00:00+05:30`) : parseDayKey(key)!.getTime();
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

export function eventHasExactTime(ev: EventClock): boolean {
  return (ev.source !== undefined && ev.source !== "research") || !ev.research || ev.research.schedule_status === "confirmed";
}

/** A local Date representing the selected area's calendar date, for labels and date navigation. */
export function areaToday(area: string, now = Date.now()): Date {
  return area === "mumbai" ? parseDayKey(eventDayOf({ start_at: new Date(now).toISOString(), area: "mumbai" }))! : new Date(now);
}

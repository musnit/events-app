import { type DayKey } from "./dates.ts";
import { eventDayOf, type EventClock } from "./event-time.ts";

/** Group events in start order, optionally collecting earlier starts under Ongoing. */
export function groupByDay<T extends EventClock>(events: T[], ongoingBefore?: DayKey): { key: string; events: T[] }[] {
  const grouped = new Map<string, T[]>();
  for (const ev of events) {
    const day = eventDayOf(ev);
    const key = ongoingBefore && day < ongoingBefore ? "ongoing" : day;
    const current = grouped.get(key);
    if (current) current.push(ev);
    else grouped.set(key, [ev]);
  }
  return [...grouped].sort(([a], [b]) => a === "ongoing" ? -1 : b === "ongoing" ? 1 : a.localeCompare(b)).map(([key, events]) => ({ key, events }));
}

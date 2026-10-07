import { localDayOf, type DayKey } from "./dates.ts";

/** Group events in start order, optionally collecting earlier starts under Ongoing. */
export function groupByDay<T extends { start_at: string }>(events: T[], ongoingBefore?: DayKey): { key: string; events: T[] }[] {
  const groups: { key: string; events: T[] }[] = [];
  for (const ev of events) {
    const day = localDayOf(ev.start_at);
    const key = ongoingBefore && day < ongoingBefore ? "ongoing" : day;
    let current = groups.at(-1);
    if (!current || current.key !== key) {
      current = { key, events: [] };
      groups.push(current);
    }
    current.events.push(ev);
  }
  return groups;
}

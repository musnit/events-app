import { parseDayKey, type DayKey } from "./dates.ts";
import { hasEnded } from "./format.ts";
import type { EventItem } from "./types.ts";

/** Today includes ongoing events; an explicit date also allows browsing ended events. */
export function agendaIncludes(ev: Pick<EventItem, "start_at" | "end_at">, from: DayKey | null, now: number): boolean {
  const chosen = from ? parseDayKey(from) : null;
  return chosen ? Date.parse(ev.start_at) >= chosen.getTime() : !hasEnded(ev, now);
}

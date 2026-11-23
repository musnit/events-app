import { type DayKey } from "./dates.ts";
import { hasEnded } from "./format.ts";
import { eventDayOf, type EventClock } from "./event-time.ts";

/** Today includes ongoing events; an explicit date also allows browsing ended events. */
export function agendaIncludes(ev: EventClock & { end_at: string | null }, from: DayKey | null, now: number): boolean {
  return from ? eventDayOf(ev) >= from : !hasEnded(ev, now);
}

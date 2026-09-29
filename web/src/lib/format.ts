import { addDays, dayKey, daysBetween, parseDayKey, startOfDay } from "./dates.ts";
import type { EventItem, Source, Ticket } from "./types.ts";

const timeWithMinutes = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const timeOnTheHour = new Intl.DateTimeFormat(undefined, { hour: "numeric" });
const dayLong = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" });
const dayLongYear = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
const dayShort = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
const weekdayName = new Intl.DateTimeFormat(undefined, { weekday: "long" });
const monthYear = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });
const monthShort = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
export const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

export function fmtTime(iso: string): string {
  const d = new Date(iso);
  return (d.getMinutes() === 0 ? timeOnTheHour : timeWithMinutes).format(d);
}

export function fmtTimeRange(ev: Pick<EventItem, "start_at" | "end_at" | "all_day">): string {
  if (ev.all_day) return "All day";
  const start = fmtTime(ev.start_at);
  if (!ev.end_at) return start;
  const end = new Date(ev.end_at);
  const days = daysBetween(new Date(ev.start_at), end);
  // A late-night event ending before 6 am the next day still reads as one evening.
  if (days === 0 || (days === 1 && end.getHours() < 6)) return `${start} – ${fmtTime(ev.end_at)}`;
  return `${start} – ${monthShort.format(end)}, ${fmtTime(ev.end_at)}`;
}

export function fmtDayLong(d: Date, today = new Date()): string {
  return (d.getFullYear() === today.getFullYear() ? dayLong : dayLongYear).format(d);
}

export function fmtDayShort(d: Date): string {
  return dayShort.format(d);
}

export function fmtMonth(d: Date): string {
  return monthYear.format(d);
}

/** "Today", "Tomorrow", a weekday name within the coming week, or a short date. */
export function relativeDay(key: string, today = new Date()): string {
  const d = parseDayKey(key);
  if (!d) return key;
  const diff = daysBetween(today, d);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  if (diff > 1 && diff < 7) return weekdayName.format(d);
  return fmtDayShort(d);
}

/** When an event happens in another time zone, its local wall-clock time, e.g. "7:00 PM EDT". */
export function eventZoneNote(ev: Pick<EventItem, "start_at" | "timezone" | "all_day">): string | null {
  if (ev.all_day || !ev.timezone || ev.timezone === LOCAL_TZ) return null;
  try {
    const d = new Date(ev.start_at);
    const theirs = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: ev.timezone, timeZoneName: "short" }).format(d);
    const ours = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(d);
    const strip = (s: string) => s.replace(/\s\S+$/, "");
    return strip(theirs) === strip(ours) ? null : theirs;
  } catch {
    return null;
  }
}

export function fmtPrice(ticket: Ticket | null): string | null {
  if (!ticket) return null;
  if (ticket.sold_out) return ticket.waitlist ? "Waitlist" : "Sold out";
  if (ticket.free) return "Free";
  if (ticket.price_cents == null) return null;
  const money = (cents: number) =>
    new Intl.NumberFormat(undefined, { style: "currency", currency: (ticket.currency || "usd").toUpperCase(), maximumFractionDigits: cents % 100 ? 2 : 0 }).format(cents / 100);
  if (ticket.max_price_cents && ticket.max_price_cents > ticket.price_cents) return `${money(ticket.price_cents)}–${money(ticket.max_price_cents)}`;
  return money(ticket.price_cents);
}

export const SOURCE_LABEL: Record<Source, string> = { luma: "Luma", partiful: "Partiful", agihouse: "AGI House" };

export function goingLabel(status: string | null): string {
  switch (status) {
    case "pending_approval": return "Pending approval";
    case "waitlist": return "On the waitlist";
    case "host": case "hosting": return "Hosting";
    default: return "Going";
  }
}

export function isHappeningNow(ev: Pick<EventItem, "start_at" | "end_at">, now = Date.now()): boolean {
  const start = Date.parse(ev.start_at);
  const end = ev.end_at ? Date.parse(ev.end_at) : start + 2 * 3600_000;
  return start <= now && now < end;
}

export function hasEnded(ev: Pick<EventItem, "start_at" | "end_at">, now = Date.now()): boolean {
  const end = ev.end_at ? Date.parse(ev.end_at) : Date.parse(ev.start_at) + 2 * 3600_000;
  return end <= now;
}

export function whereLine(ev: EventItem): string {
  const loc = ev.location;
  if (loc.type === "online") return "Online";
  const place = loc.venue || loc.neighborhood;
  const city = loc.city && !(place || "").includes(loc.city) ? loc.city : null;
  return [place, city].filter(Boolean).join(", ") || (loc.address ? loc.address.split(",").slice(0, 2).join(",") : "") || "Location on the event page";
}

export function mapsUrl(ev: EventItem): string | null {
  const loc = ev.location;
  if (loc.type === "online") return null;
  const query = loc.lat != null && loc.lng != null
    ? `${loc.lat},${loc.lng}`
    : [loc.venue, loc.address || loc.city].filter(Boolean).join(", ");
  return query ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}` : null;
}

/** A Google Calendar "add event" link, which works from any browser without signing in here. */
export function googleCalendarUrl(ev: EventItem): string {
  const stamp = (iso: string) => new Date(iso).toISOString().replace(/[-:]|\.\d{3}/g, "");
  let dates: string;
  if (ev.all_day) {
    const start = startOfDay(new Date(ev.start_at));
    const end = ev.end_at ? addDays(startOfDay(new Date(ev.end_at)), 1) : addDays(start, 1);
    dates = `${dayKey(start).replaceAll("-", "")}/${dayKey(end).replaceAll("-", "")}`;
  } else {
    dates = `${stamp(ev.start_at)}/${stamp(ev.end_at || new Date(Date.parse(ev.start_at) + 2 * 3600_000).toISOString())}`;
  }
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: ev.name,
    dates,
    details: ev.url,
    location: ev.location.type === "online" ? "Online" : [ev.location.venue, ev.location.address || ev.location.city].filter(Boolean).join(", "),
  });
  return `https://calendar.google.com/calendar/render?${params}`;
}

export function pluralize(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

export function timeAgo(epochSeconds: number | null, now = Date.now()): string {
  if (!epochSeconds) return "never";
  const s = Math.max(0, Math.round(now / 1000 - epochSeconds));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

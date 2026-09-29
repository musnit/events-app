// Event filters. They live in the URL query so every filtered view can be bookmarked and shared.

import { isWeekend, timeOfDay, type TimeOfDay } from "./dates.ts";
import type { AreaChoice, CalendarItem, EventItem } from "./types.ts";

export interface Filters {
  q: string;
  vibes: string[];
  topics: string[];
  times: TimeOfDay[];
  weekend: boolean;
  /** null means "use the saved preference". */
  area: AreaChoice | null;
  zones: string[];
  sizes: string[];
  going: boolean;
  free: boolean;
  open: boolean;
  fresh: boolean;
  hidden: boolean;
}

export const EMPTY_FILTERS: Filters = {
  q: "", vibes: [], topics: [], times: [], weekend: false, area: null, zones: [], sizes: [],
  going: false, free: false, open: false, fresh: false, hidden: false,
};

export const TIMES: { id: TimeOfDay; label: string; hint: string }[] = [
  { id: "morning", label: "Morning", hint: "5 am – noon" },
  { id: "afternoon", label: "Afternoon", hint: "noon – 5 pm" },
  { id: "evening", label: "Evening", hint: "5 – 9 pm" },
  { id: "late", label: "Late", hint: "9 pm onward" },
];

export const AREA_CHOICES: { id: AreaChoice; label: string }[] = [
  { id: "bay", label: "Bay Area" },
  { id: "bay-online", label: "Bay Area + online" },
  { id: "all", label: "Everywhere" },
];

const TIME_IDS = new Set<string>(TIMES.map((t) => t.id));
const AREA_IDS = new Set<string>(AREA_CHOICES.map((a) => a.id));
const ID_RE = /^[a-z0-9-]{1,40}$/;

function list(value: string | null): string[] {
  if (!value) return [];
  return [...new Set(value.split(",").map((s) => s.trim()).filter((s) => ID_RE.test(s)))];
}

export function filtersFromParams(params: URLSearchParams): Filters {
  const flag = (name: string) => params.get(name) === "1";
  const area = params.get("area");
  return {
    q: (params.get("q") || "").slice(0, 200),
    vibes: list(params.get("vibe")),
    topics: list(params.get("topic")),
    times: list(params.get("time")).filter((t): t is TimeOfDay => TIME_IDS.has(t)),
    weekend: flag("weekend"),
    area: area && AREA_IDS.has(area) ? (area as AreaChoice) : null,
    zones: list(params.get("zone")),
    sizes: list(params.get("size")),
    going: flag("going"),
    free: flag("free"),
    open: flag("open"),
    fresh: flag("new"),
    hidden: flag("hidden"),
  };
}

export function filtersToParams(f: Filters): URLSearchParams {
  const params = new URLSearchParams();
  const setList = (name: string, values: string[]) => values.length && params.set(name, values.join(","));
  const setFlag = (name: string, on: boolean) => on && params.set(name, "1");
  if (f.q.trim()) params.set("q", f.q.trim());
  setList("vibe", f.vibes);
  setList("topic", f.topics);
  setList("time", f.times);
  setFlag("weekend", f.weekend);
  if (f.area) params.set("area", f.area);
  setList("zone", f.zones);
  setList("size", f.sizes);
  setFlag("going", f.going);
  setFlag("free", f.free);
  setFlag("open", f.open);
  setFlag("new", f.fresh);
  setFlag("hidden", f.hidden);
  return params;
}

/** How many filters (besides search) are narrowing the list. */
export function activeFilterCount(f: Filters): number {
  return f.vibes.length + f.topics.length + f.times.length + f.zones.length + f.sizes.length +
    [f.weekend, f.going, f.free, f.open, f.fresh, f.hidden, f.area !== null].filter(Boolean).length;
}

export function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
}

export interface MatchContext {
  area: AreaChoice;
  /** Events announced after this moment (ms) count as new; null disables the "new" filter. */
  newSince: number | null;
  calendars: Map<string, CalendarItem>;
}

const searchCache = new WeakMap<EventItem, string>();

export function searchText(ev: EventItem, calendars: Map<string, CalendarItem>): string {
  let text = searchCache.get(ev);
  if (text === undefined) {
    const loc = ev.location;
    text = [
      ev.name, ev.presenter?.name, ...ev.hosts.map((h) => h.name), ...ev.tags, loc.venue, loc.address, loc.city,
      loc.neighborhood, ...ev.calendar_ids.map((id) => calendars.get(id)?.name),
    ].filter(Boolean).join(" \u0000 ").toLowerCase();
    searchCache.set(ev, text);
  }
  return text;
}

export function isNew(ev: EventItem, newSince: number | null): boolean {
  return newSince != null && ev.announced_at != null && Date.parse(ev.announced_at) > newSince;
}

export function inArea(ev: EventItem, area: AreaChoice): boolean {
  if (area === "all") return true;
  if (ev.area === "bay") return true;
  return area === "bay-online" && ev.area === "online";
}

type Facet = "vibes" | "topics" | "times" | "zones" | "sizes";

/** Whether an event passes the filters. ``skip`` ignores one facet, for counting that facet's options. */
export function matchEvent(ev: EventItem, f: Filters, ctx: MatchContext, skip?: Facet): boolean {
  if (!f.hidden && (ev.hidden || ev.muted)) return false;
  if (!inArea(ev, f.area ?? ctx.area)) return false;
  if (f.going && !ev.going) return false;
  if (f.free && !ev.ticket?.free) return false;
  if (f.open && ev.ticket?.sold_out) return false;
  if (f.fresh && !isNew(ev, ctx.newSince)) return false;
  if (f.weekend && !isWeekend(ev.start_at)) return false;
  if (skip !== "vibes" && f.vibes.length && !f.vibes.some((v) => ev.vibes.includes(v))) return false;
  if (skip !== "topics" && f.topics.length && !f.topics.some((t) => ev.topics.includes(t))) return false;
  if (skip !== "times" && f.times.length && (ev.all_day || !f.times.includes(timeOfDay(ev.start_at)))) return false;
  if (skip !== "zones" && f.zones.length && !(ev.zone && f.zones.includes(ev.zone))) return false;
  if (skip !== "sizes" && f.sizes.length && !(ev.size && f.sizes.includes(ev.size))) return false;
  if (f.q.trim()) {
    const text = searchText(ev, ctx.calendars);
    for (const word of f.q.toLowerCase().split(/\s+/).filter(Boolean)) if (!text.includes(word)) return false;
  }
  return true;
}

export type FacetCounts = Record<Facet, Record<string, number>>;

/** For each facet option, how many events would show if it were picked (given the other filters). */
export function facetCounts(events: EventItem[], f: Filters, ctx: MatchContext): FacetCounts {
  const counts: FacetCounts = { vibes: {}, topics: {}, times: {}, zones: {}, sizes: {} };
  const bump = (facet: Facet, key: string | null) => {
    if (key) counts[facet][key] = (counts[facet][key] || 0) + 1;
  };
  for (const ev of events) {
    if (matchEvent(ev, f, ctx, "vibes")) ev.vibes.forEach((v) => bump("vibes", v));
    if (matchEvent(ev, f, ctx, "topics")) ev.topics.forEach((t) => bump("topics", t));
    if (!ev.all_day && matchEvent(ev, f, ctx, "times")) bump("times", timeOfDay(ev.start_at));
    if (matchEvent(ev, f, ctx, "zones")) bump("zones", ev.zone);
    if (matchEvent(ev, f, ctx, "sizes")) bump("sizes", ev.size);
  }
  return counts;
}

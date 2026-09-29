// Every screen has a URL. Paths are relative to the app root (the document's <base href>).
//
//   ""                      upcoming events from today          "agenda/2026-10-03"  upcoming from a date
//   "day/2026-10-03"        one day                             "month/2026-10"      month grid
//   "event/evt-abc"         one event                           "saved"              going + starred
//   "calendars"             every calendar                      "calendars/cal-abc"  one calendar
//   "sources"               connect Luma/Partiful, sync status  "sources/luma"       …one section
//
// List views also carry filters in the query string (see filters.ts).

import { parseDayKey, parseMonthKey, type DayKey } from "./dates.ts";
import { filtersToParams, type Filters } from "./filters.ts";

export type SourcesSection = "sync" | "luma" | "partiful" | "export" | "app";
export const SOURCES_SECTIONS: SourcesSection[] = ["sync", "luma", "partiful", "export", "app"];

export type Route =
  | { name: "agenda"; from: DayKey | null }
  | { name: "day"; date: DayKey }
  | { name: "month"; month: string }
  | { name: "event"; id: string }
  | { name: "saved" }
  | { name: "calendars" }
  | { name: "calendar"; id: string }
  | { name: "sources"; section: SourcesSection | null }
  | { name: "notFound"; path: string };

/** Views whose content the filters narrow. */
export const FILTERED_VIEWS = new Set<Route["name"]>(["agenda", "day", "month", "saved", "calendar"]);

const SAFE_ID = /^[A-Za-z0-9_.:-]{1,120}$/;

export function parseRoute(path: string): Route {
  const parts = path.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean).map((p) => {
    try {
      return decodeURIComponent(p);
    } catch {
      return p;
    }
  });
  const [head, arg, ...rest] = parts;
  const notFound: Route = { name: "notFound", path };
  if (rest.length) return notFound;
  switch (head) {
    case undefined:
      return { name: "agenda", from: null };
    case "agenda":
      if (!arg) return { name: "agenda", from: null };
      return parseDayKey(arg) ? { name: "agenda", from: arg } : notFound;
    case "day":
      return arg && parseDayKey(arg) ? { name: "day", date: arg } : notFound;
    case "month":
      return arg && parseMonthKey(arg) ? { name: "month", month: arg } : notFound;
    case "event":
      return arg && SAFE_ID.test(arg) ? { name: "event", id: arg } : notFound;
    case "saved":
      return arg ? notFound : { name: "saved" };
    case "calendars":
      if (!arg) return { name: "calendars" };
      return SAFE_ID.test(arg) ? { name: "calendar", id: arg } : notFound;
    case "sources":
      if (!arg) return { name: "sources", section: null };
      return (SOURCES_SECTIONS as string[]).includes(arg) ? { name: "sources", section: arg as SourcesSection } : notFound;
    default:
      return notFound;
  }
}

export function routePath(route: Route): string {
  switch (route.name) {
    case "agenda": return route.from ? `agenda/${route.from}` : "";
    case "day": return `day/${route.date}`;
    case "month": return `month/${route.month}`;
    case "event": return `event/${encodeURIComponent(route.id)}`;
    case "saved": return "saved";
    case "calendars": return "calendars";
    case "calendar": return `calendars/${encodeURIComponent(route.id)}`;
    case "sources": return route.section ? `sources/${route.section}` : "sources";
    case "notFound": return route.path.replace(/^\/+/, "");
  }
}

/** A relative URL for a route; filters ride along on views they apply to. */
export function href(route: Route, filters?: Filters): string {
  const path = routePath(route);
  const query = filters && FILTERED_VIEWS.has(route.name) ? filtersToParams(filters).toString() : "";
  // "./" keeps the root link relative to <base href> instead of the current page.
  return (path || "./") + (query ? `?${query}` : "");
}

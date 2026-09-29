import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { facetCounts, matchEvent, type FacetCounts, type Filters, type MatchContext } from "../lib/filters.ts";
import type { AreaChoice, EventItem } from "../lib/types.ts";
import { useData, type Catalog } from "./data.ts";

/** The current time, refreshed every ``everyMs`` so "today" and "happening now" stay right. */
export function useNow(everyMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);
  return now;
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (listener) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", listener);
      return () => media.removeEventListener("change", listener);
    },
    () => window.matchMedia(query).matches,
  );
}

export const WIDE_QUERY = "(min-width: 960px)";

export function useArea(): AreaChoice {
  const { status } = useData();
  return status?.prefs.area ?? "bay";
}

export function useMatchContext(catalog: Catalog | null, area?: AreaChoice): MatchContext {
  const preferred = useArea();
  const { newSince } = useData();
  const calendars = catalog?.calendarsById;
  const effective = area ?? preferred;
  return useMemo(() => ({ area: effective, newSince, calendars: calendars ?? new Map() }), [effective, newSince, calendars]);
}

export interface FilterScope {
  /** Only consider events for which this returns true. Pass a stable function (useCallback). */
  scope?: (ev: EventItem) => boolean;
  /** Override the saved area preference, e.g. to show a calendar's events everywhere. */
  area?: AreaChoice;
}

/** Events passing the filters (in start order) plus facet counts for the filter chips. */
export function useFilteredEvents(filters: Filters, { scope, area }: FilterScope = {}) {
  const { catalog } = useData();
  const ctx = useMatchContext(catalog, area);
  return useMemo(() => {
    const pool = catalog ? (scope ? catalog.events.filter(scope) : catalog.events) : [];
    const events = pool.filter((ev) => matchEvent(ev, filters, ctx));
    const counts: FacetCounts = facetCounts(pool, filters, ctx);
    return { events, counts, total: pool.length, ctx };
  }, [catalog, filters, ctx, scope]);
}

// The app's data is the event catalog and sync status, polled from the API. Components read it through
// useData(); mutations update optimistically and roll back on failure.

import { useSyncExternalStore } from "react";
import { api, ApiError, fetchEvents, fetchStatus, Offline, SessionExpired } from "../lib/api.ts";
import type { AreaChoice, CalendarItem, Category, EventItem, EventsPayload, Status } from "../lib/types.ts";
import { startVisit, touchVisit } from "../lib/visits.ts";
import { toast } from "./toasts.ts";

export interface Catalog {
  events: EventItem[];
  byId: Map<string, EventItem>;
  calendars: CalendarItem[];
  calendarsById: Map<string, CalendarItem>;
  vibes: Category[];
  topics: Category[];
  vibeById: Map<string, Category>;
  topicById: Map<string, Category>;
  sizes: { id: string; label: string; hint: string }[];
  zones: { id: string; label: string }[];
  generatedAt: string;
}

export interface DataState {
  catalog: Catalog | null;
  status: Status | null;
  /** Nothing loaded yet and a request is in flight. */
  loading: boolean;
  error: string | null;
  sessionExpired: boolean;
  offline: boolean;
  /** Events announced after this moment get a "new" badge. */
  newSince: number;
}

const FAST_POLL_MS = 3_000;
const SLOW_POLL_MS = 45_000;
const HIDDEN_POLL_MS = 5 * 60_000;
// While a long sync runs the catalog changes every few seconds; refetching it at most this often
// keeps phones from downloading it over and over.
const BUSY_REFETCH_MS = 20_000;

function buildCatalog(payload: EventsPayload): Catalog {
  return {
    events: payload.events,
    byId: new Map(payload.events.map((e) => [e.id, e])),
    calendars: payload.calendars,
    calendarsById: new Map(payload.calendars.map((c) => [c.id, c])),
    vibes: payload.taxonomy.vibes,
    topics: payload.taxonomy.topics,
    vibeById: new Map(payload.taxonomy.vibes.map((c) => [c.id, c])),
    topicById: new Map(payload.taxonomy.topics.map((c) => [c.id, c])),
    sizes: payload.taxonomy.sizes,
    zones: payload.zones,
    generatedAt: payload.generated_at,
  };
}

class DataStore {
  private state: DataState;
  private listeners = new Set<() => void>();
  private etag: string | null = null;
  private dataVersion: number | null = null;
  private timer: number | undefined;
  private started = false;
  private inFlight: Promise<void> | null = null;
  private lastEventsFetch = 0;

  constructor() {
    this.state = {
      catalog: null, status: null, loading: true, error: null, sessionExpired: false,
      offline: typeof navigator !== "undefined" && navigator.onLine === false, newSince: startVisit(),
    };
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.state;

  private set(patch: Partial<DataState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  start(): void {
    if (this.started) return;
    this.started = true;
    window.addEventListener("online", () => {
      this.set({ offline: false });
      void this.poll();
    });
    window.addEventListener("offline", () => this.set({ offline: true }));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        touchVisit();
        void this.poll();
      }
    });
    window.setInterval(() => document.visibilityState === "visible" && touchVisit(), 60_000);
    void this.poll();
  }

  /** Poll now, then keep polling: fast while a sync runs, slowly otherwise. */
  poll = (): Promise<void> => {
    if (this.inFlight) return this.inFlight;
    window.clearTimeout(this.timer);
    this.inFlight = this.pollOnce().finally(() => {
      this.inFlight = null;
      const busy = Object.values(this.state.status?.workers ?? {}).some((w) => w.busy);
      const delay = document.visibilityState !== "visible" ? HIDDEN_POLL_MS : busy ? FAST_POLL_MS : SLOW_POLL_MS;
      if (!this.state.sessionExpired) this.timer = window.setTimeout(() => void this.poll(), delay);
    });
    return this.inFlight;
  };

  private async pollOnce(): Promise<void> {
    try {
      const status = await fetchStatus();
      const busy = Object.values(status.workers).some((w) => w.busy);
      const changed = status.data_version !== this.dataVersion || !this.state.catalog;
      const tooSoon = busy && this.state.catalog && this.dataVersion !== null && Date.now() - this.lastEventsFetch < BUSY_REFETCH_MS;
      this.set({ status, offline: false });
      if (changed && !tooSoon) {
        this.lastEventsFetch = Date.now();
        const result = await fetchEvents(this.etag);
        if (result) {
          this.etag = result.etag;
          this.set({ catalog: buildCatalog(result.payload) });
        }
        this.dataVersion = status.data_version;
      }
      this.set({ loading: false, error: null });
    } catch (e) {
      if (e instanceof SessionExpired) this.set({ sessionExpired: true, loading: false });
      else if (e instanceof Offline) this.set({ offline: true, loading: false });
      else this.set({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  }

  private updateEvent(id: string, patch: Partial<EventItem>): EventItem | null {
    const catalog = this.state.catalog;
    const before = catalog?.byId.get(id);
    if (!catalog || !before) return null;
    const after = { ...before, ...patch };
    const events = catalog.events.map((e) => (e.id === id ? after : e));
    this.set({ catalog: { ...catalog, events, byId: new Map(catalog.byId).set(id, after) } });
    return before;
  }

  async mark(id: string, change: { starred?: boolean; hidden?: boolean }): Promise<void> {
    const before = this.updateEvent(id, change);
    try {
      await api.mark(id, change);
      this.dataVersion = null; // the server bumped its version; pick up the canonical copy next poll
    } catch (e) {
      if (before) this.updateEvent(id, { starred: before.starred, hidden: before.hidden });
      this.fail("Could not save that", e);
    }
  }

  async setMuted(calendarId: string, muted: boolean): Promise<void> {
    const status = this.state.status;
    const current = new Set(status?.prefs.muted_calendars ?? this.state.catalog?.calendars.filter((c) => c.muted).map((c) => c.id) ?? []);
    if (muted) current.add(calendarId);
    else current.delete(calendarId);
    const catalog = this.state.catalog;
    if (catalog) {
      const calendars = catalog.calendars.map((c) => (c.id === calendarId ? { ...c, muted } : c));
      const mutedSet = current;
      const events = catalog.events.map((e) =>
        e.calendar_ids.includes(calendarId) ? { ...e, muted: e.calendar_ids.every((c) => mutedSet.has(c)) } : e);
      this.set({ catalog: { ...catalog, calendars, calendarsById: new Map(calendars.map((c) => [c.id, c])), events, byId: new Map(events.map((e) => [e.id, e])) } });
    }
    try {
      await api.prefs({ muted_calendars: [...current] });
      this.dataVersion = null;
      await this.poll();
    } catch (e) {
      this.fail("Could not change that calendar", e);
      this.dataVersion = null;
      await this.poll();
    }
  }

  async setArea(area: AreaChoice): Promise<void> {
    const status = this.state.status;
    if (status) this.set({ status: { ...status, prefs: { ...status.prefs, area } } });
    try {
      await api.prefs({ area });
    } catch (e) {
      this.fail("Could not save your area", e);
    }
  }

  /** Call after changing sources: it refreshes soon and keeps polling quickly while the sync starts. */
  async afterSourcesChanged(): Promise<void> {
    this.dataVersion = null;
    await this.poll();
  }

  private fail(what: string, e: unknown): void {
    if (e instanceof SessionExpired) this.set({ sessionExpired: true });
    else toast(`${what}: ${e instanceof ApiError || e instanceof Error ? e.message : String(e)}`, "error");
  }
}

export const data = new DataStore();

export function useData(): DataState {
  return useSyncExternalStore(data.subscribe, data.getSnapshot);
}

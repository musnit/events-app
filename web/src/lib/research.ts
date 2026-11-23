import type { ResearchCollection, ResearchEntry, ResearchKind, ResearchPriority } from "./types.ts";
import { parseDayKey } from "./dates.ts";

export const MUMBAI_COLLECTION = "mumbai-devcon-2026";
export const MUMBAI_ZONE = "Asia/Kolkata";
export const KIND_LABELS: Record<ResearchKind, string> = {
  talk: "Talks & workshops", "side-event": "Side events", "music-art": "Music & art", "local-scene": "Local scene", place: "Places",
};
export const PRIORITY_LABELS: Record<ResearchPriority, string> = {
  essential: "Don't miss", recommended: "Recommended", alternative: "Good alternative", watchlist: "Watchlist", skip: "I'd skip", reference: "Reference",
};
export const RESEARCH_VIEWS = [
  { id: "curated", label: "The shortlist" }, { id: "all", label: "All research" },
  { id: "watchlist", label: "Watchlist" }, { id: "skip", label: "I'd skip" }, { id: "places", label: "Places & scene" },
] as const;
export type ResearchView = typeof RESEARCH_VIEWS[number]["id"];
export interface ResearchFilters { q: string; day: string; kind: string; priority: string; view: ResearchView }

export function researchFilters(params: URLSearchParams): ResearchFilters {
  const view = params.get("view");
  return {
    q: (params.get("q") ?? "").slice(0, 300), day: params.get("date") ?? "",
    kind: params.get("kind") ?? "", priority: params.get("priority") ?? "",
    view: RESEARCH_VIEWS.some((v) => v.id === view) ? view as ResearchView : "curated",
  };
}

export function researchSearchText(entry: ResearchEntry): string {
  return [entry.title, entry.verdict, entry.summary, entry.venue, entry.address, entry.city, entry.access,
    entry.date_label, ...entry.tags, ...entry.conflicts,
    ...entry.people.flatMap((p) => [p.name, p.role, p.assessment]),
    ...entry.sections.flatMap((s) => [s.title, s.body]), ...entry.sources.map((s) => s.label),
  ].filter(Boolean).join(" \n ").toLowerCase();
}

export function researchDay(entry: ResearchEntry): string | null {
  if (!entry.start_at || !Number.isFinite(Date.parse(entry.start_at))) return entry.local_date && parseDayKey(entry.local_date) ? entry.local_date : null;
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: MUMBAI_ZONE, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(entry.start_at));
  const part = (name: string) => parts.find((p) => p.type === name)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

/** A multi-day event appears on each local day it occupies; a midnight end is exclusive. */
export function researchOnDay(entry: ResearchEntry, day: string): boolean {
  const first = researchDay(entry);
  if (!first || !parseDayKey(day)) return false;
  if (!entry.start_at || !entry.end_at || !Number.isFinite(Date.parse(entry.end_at)) || Date.parse(entry.end_at) <= Date.parse(entry.start_at)) return first === day;
  const last = researchDay({ ...entry, start_at: new Date(Date.parse(entry.end_at) - 1).toISOString() })!;
  return first <= day && day <= last;
}

/** Destination scope is deliberate, even if another city's entry is accidentally linked in. */
export function mumbaiEntries(collections: ResearchCollection[]): ResearchEntry[] {
  return collections.filter((c) => c.id === MUMBAI_COLLECTION && c.city.toLowerCase() === "mumbai")
    .flatMap((c) => c.entries).filter((e) => e.city.trim().toLowerCase() === "mumbai");
}

export function matchesResearch(entry: ResearchEntry, f: ResearchFilters): boolean {
  if (entry.city.trim().toLowerCase() !== "mumbai") return false;
  if (f.view === "curated" && (!(["essential", "recommended", "alternative"] as string[]).includes(entry.priority) || entry.schedule_status === "stale")) return false;
  if ((f.view === "watchlist" || f.view === "skip") && entry.priority !== f.view) return false;
  if (f.view === "places" && entry.kind !== "place" && entry.kind !== "local-scene") return false;
  if (f.day && (f.day === "undated" ? researchDay(entry) !== null : !researchOnDay(entry, f.day))) return false;
  if (f.kind && entry.kind !== f.kind) return false;
  if (f.priority && entry.priority !== f.priority) return false;
  const haystack = researchSearchText(entry);
  return f.q.toLowerCase().split(/\s+/).filter(Boolean).every((word) => haystack.includes(word));
}

export function sortResearch(a: ResearchEntry, b: ResearchEntry): number {
  const rank: Record<ResearchPriority, number> = { essential: 0, recommended: 1, alternative: 2, watchlist: 3, reference: 4, skip: 5 };
  return rank[a.priority] - rank[b.priority] || (a.start_at ?? "z").localeCompare(b.start_at ?? "z") || a.title.localeCompare(b.title);
}

export function researchDateLabel(iso: string): string {
  return Number.isFinite(Date.parse(iso))
    ? new Intl.DateTimeFormat("en-GB", { timeZone: MUMBAI_ZONE, day: "numeric", month: "short", year: "numeric" }).format(new Date(iso))
    : iso;
}

/** Never turn a date placeholder into a supposedly exact local time. */
export function researchWhen(entry: ResearchEntry): string {
  if (entry.schedule_status !== "confirmed" || !entry.start_at || !Number.isFinite(Date.parse(entry.start_at))) {
    return entry.date_label || "Date to be confirmed";
  }
  const date = new Intl.DateTimeFormat("en-GB", { timeZone: MUMBAI_ZONE, weekday: "short", day: "numeric", month: "short" }).format(new Date(entry.start_at));
  const time = (iso: string) => new Intl.DateTimeFormat("en-GB", { timeZone: MUMBAI_ZONE, hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
  let end = "";
  if (entry.end_at && Number.isFinite(Date.parse(entry.end_at))) {
    const nextDay = researchDay({ ...entry, start_at: entry.end_at }) !== researchDay(entry);
    end = ` – ${nextDay ? researchDateLabel(entry.end_at) + ", " : ""}${time(entry.end_at)}`;
  }
  return `${date} · ${time(entry.start_at)}${end} IST`;
}

export function canExportResearch(entry: ResearchEntry): boolean {
  return ["essential", "recommended", "alternative"].includes(entry.priority) && entry.schedule_status === "confirmed" && !!entry.start_at && !!entry.end_at &&
    Number.isFinite(Date.parse(entry.start_at)) && Number.isFinite(Date.parse(entry.end_at)) && Date.parse(entry.end_at) > Date.parse(entry.start_at);
}

export function safeResearchUrl(url: string | null): string | undefined {
  if (!url) return undefined;
  try { const parsed = new URL(url); return ["https:", "http:"].includes(parsed.protocol) ? parsed.href : undefined; }
  catch { return undefined; }
}

export function researchMapsUrl(entry: ResearchEntry): string | undefined {
  const known = (value: string | null) => value && !/undisclosed|to be (confirmed|announced)|\b(?:tbc|tba|tbd)\b|secret (?:location|venue)/i.test(value) ? value : null;
  const venue = known(entry.venue);
  const address = known(entry.address);
  if (!venue && !address) return undefined;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([venue, address, entry.city].filter(Boolean).join(", "))}`;
}

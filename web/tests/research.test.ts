import assert from "node:assert/strict";
import { test } from "node:test";
import { canExportResearch, matchesResearch, MUMBAI_COLLECTION, mumbaiEntries, researchDay, researchFilters, researchMapsUrl, researchWhen, safeResearchUrl } from "../src/lib/research.ts";
import type { ResearchCollection, ResearchEntry } from "../src/lib/types.ts";

function entry(patch: Partial<ResearchEntry> = {}): ResearchEntry {
  return {
    id: "fixture-open-lab", title: "Open Laboratory", kind: "talk", priority: "recommended",
    verdict: "A hands-on research session.", summary: "Try working tools together.",
    start_at: "2026-11-04T04:00:00Z", end_at: "2026-11-04T05:00:00Z", date_label: "4 November, morning",
    schedule_status: "confirmed", timezone: "Asia/Kolkata", venue: "Fixture Lab", address: null,
    city: "Mumbai", country: "India", url: "https://example.com/lab", access: "Registration required",
    people: [{ name: "Ada Fixture", role: "Researcher", assessment: "Works on public-interest ocean sensors." }],
    sections: [{ title: "Limitations", body: "No independently audited benchmarks yet." }],
    sources: [{ label: "Public laboratory notebook", url: "https://example.com/notebook" }], tags: ["science"],
    conflicts: ["Clashes with the coastal workshop."], ...patch,
  };
}

function collection(entries: ResearchEntry[], id = MUMBAI_COLLECTION): ResearchCollection {
  return { id, title: "Fixture guide", city: "Mumbai", timezone: "Asia/Kolkata", checked_at: "2026-10-27", intro: "", notes: [], itinerary: [], entries };
}
const defaults = researchFilters(new URLSearchParams());

test("Mumbai collection cannot include another city's research or another collection", () => {
  const mumbai = entry();
  const sf = entry({ id: "fixture-sf", city: "San Francisco" });
  assert.deepEqual(mumbaiEntries([collection([mumbai, sf]), collection([entry({ id: "other" })], "other-guide")]), [mumbai]);
  assert.equal(matchesResearch(sf, { ...defaults, view: "all" }), false);
});

test("shortlist excludes stale claims, watchlist and skips but all research preserves them", () => {
  assert.equal(matchesResearch(entry(), defaults), true);
  assert.equal(matchesResearch(entry({ priority: "alternative" }), defaults), true);
  for (const candidate of [entry({ priority: "skip" }), entry({ priority: "watchlist" }), entry({ schedule_status: "stale" })]) {
    assert.equal(matchesResearch(candidate, defaults), false);
    assert.equal(matchesResearch(candidate, { ...defaults, view: "all" }), true);
  }
  assert.equal(matchesResearch(entry({ kind: "place", start_at: null }), { ...defaults, view: "places" }), true);
});

test("research search finds assessments, caveats, access and conflicts, not only title", () => {
  const ev = entry();
  for (const q of ["ada sensors", "audited benchmarks", "registration", "coastal", "laboratory notebook"]) {
    assert.equal(matchesResearch(ev, { ...defaults, q }), true, q);
  }
  assert.equal(matchesResearch(ev, { ...defaults, q: "ada trading" }), false);
});

test("Mumbai dates and times do not change when viewer is in California", () => {
  const ev = entry(); // Nov 3 evening in California; Nov 4 morning in Mumbai.
  assert.equal(researchDay(ev), "2026-11-04");
  assert.match(researchWhen(ev), /Wed,? 4 Nov · 09:30 – 10:30 IST/);
  assert.equal(matchesResearch(ev, { ...defaults, day: "2026-11-04" }), true);
  assert.equal(matchesResearch(ev, { ...defaults, day: "2026-11-03" }), false);
});

test("overnight events identify the actual next Mumbai date", () => {
  const ev = entry({ start_at: "2026-11-04T17:30:00Z", end_at: "2026-11-04T21:00:00Z" });
  assert.match(researchWhen(ev), /23:00 – 5 Nov 2026, 02:30 IST/);
});

test("known Mumbai dates remain filterable when the event clock is unconfirmed", () => {
  const ev = entry({ start_at: null, end_at: null, local_date: "2026-11-04", schedule_status: "unverified", date_label: "4 November; time to be confirmed" });
  assert.equal(researchDay(ev), "2026-11-04");
  assert.equal(matchesResearch(ev, { ...defaults, day: "2026-11-04" }), true);
  assert.equal(matchesResearch(ev, { ...defaults, day: "undated" }), false);
  assert.equal(researchWhen(ev), "4 November; time to be confirmed");
  assert.equal(canExportResearch(ev), false);
  assert.equal(researchDay({ ...ev, local_date: "2026-02-30" }), null);
  assert.equal(researchDay(entry({ local_date: "2026-11-03" })), "2026-11-04", "real timestamps take precedence");
});

test("date filters include every occupied Mumbai day of multi-day events, with exclusive midnight ends", () => {
  const ev = entry({ start_at: "2026-11-03T10:00:00+05:30", end_at: "2026-11-06T22:00:00+05:30" });
  for (const day of ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]) assert.equal(matchesResearch(ev, { ...defaults, day }), true);
  for (const day of ["2026-11-02", "2026-11-07"]) assert.equal(matchesResearch(ev, { ...defaults, day }), false);
  assert.equal(matchesResearch({ ...ev, end_at: "2026-11-05T18:30:00Z" }, { ...defaults, day: "2026-11-06" }), false);
  assert.equal(matchesResearch({ ...ev, end_at: "2026-11-05T18:31:00Z" }, { ...defaults, day: "2026-11-06" }), true);
  const dateOnly = entry({ start_at: null, end_at: null, local_date: "2026-11-04", schedule_status: "unverified" });
  assert.equal(matchesResearch(dateOnly, { ...defaults, day: "2026-11-04" }), true);
  assert.equal(matchesResearch(dateOnly, { ...defaults, day: "2026-11-05" }), false);
});

test("undisclosed venues do not turn into misleading map destinations", () => {
  for (const venue of ["Undisclosed venue", "Mumbai venue TBC", "Secret location", "To be announced"]) assert.equal(researchMapsUrl(entry({ venue, address: null })), undefined);
  assert.equal(researchMapsUrl(entry({ venue: "TBC", address: "TBA" })), undefined);
  const url = new URL(researchMapsUrl(entry({ venue: "Undisclosed venue", address: "123 Fixture Road" }))!);
  assert.equal(url.searchParams.get("query"), "123 Fixture Road, Mumbai");
  assert.match(researchMapsUrl(entry())!, /Fixture%20Lab/);
});

test("uncertain schedules never gain invented midnight times or calendar exports", () => {
  for (const schedule_status of ["approximate", "unverified", "unscheduled", "stale"] as const) {
    const ev = entry({ schedule_status, start_at: "2026-11-04T00:00:00Z" });
    assert.equal(researchWhen(ev), "4 November, morning");
    assert.equal(canExportResearch(ev), false);
  }
  assert.equal(canExportResearch(entry({ end_at: null })), false);
  assert.equal(canExportResearch(entry({ end_at: "invalid" })), false);
  assert.equal(canExportResearch(entry({ end_at: "2026-11-03T00:00:00Z" })), false);
  assert.equal(canExportResearch(entry()), true);
  for (const priority of ["watchlist", "skip", "reference"] as const) assert.equal(canExportResearch(entry({ priority })), false);
});

test("undated places remain filterable and bad source protocols cannot become links", () => {
  assert.equal(matchesResearch(entry({ kind: "place", start_at: null }), { ...defaults, day: "undated" }), true);
  assert.equal(safeResearchUrl("javascript:alert(1)"), undefined);
  assert.equal(safeResearchUrl("data:text/html,test"), undefined);
  assert.equal(safeResearchUrl("https://example.com/source"), "https://example.com/source");
});

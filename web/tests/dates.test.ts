// Runs with TZ=America/Los_Angeles (see package.json).
import assert from "node:assert/strict";
import { test } from "node:test";
import { addDays, dayKey, isWeekend, localDayOf, monthGrid, parseDayKey, parseMonthKey, timeOfDay, weekendStart } from "../src/lib/dates.ts";
import { fmtTimeRange, googleCalendarUrl, relativeDay } from "../src/lib/format.ts";
import { readImportHash } from "../src/lib/bookmarklets.ts";

test("day keys parse and reject impossible dates", () => {
  assert.equal(dayKey(parseDayKey("2026-10-03")!), "2026-10-03");
  assert.equal(parseDayKey("2026-02-29"), null);
  assert.equal(parseDayKey("2028-02-29")?.getDate(), 29);
  assert.equal(parseMonthKey("2026-00"), null);
  assert.equal(parseMonthKey("2026-12")?.getMonth(), 11);
});

test("adding days is safe across daylight-saving changes", () => {
  // US clocks go back on 2026-11-01.
  assert.equal(dayKey(addDays(parseDayKey("2026-10-31")!, 1)), "2026-11-01");
  assert.equal(dayKey(addDays(parseDayKey("2026-11-01")!, 1)), "2026-11-02");
});

test("month grids cover whole weeks, Sunday first", () => {
  const grid = monthGrid(parseMonthKey("2026-10")!);
  assert.equal(grid.length % 7, 0);
  assert.equal(grid[0].getDay(), 0);
  assert.equal(dayKey(grid[0]), "2026-09-27");
  assert.equal(dayKey(grid[grid.length - 1]), "2026-10-31");
});

test("local day, time of day and weekend follow the viewer's zone", () => {
  // 03:30Z on Saturday is Friday 8:30 pm in San Francisco.
  assert.equal(localDayOf("2026-10-03T03:30:00Z"), "2026-10-02");
  assert.equal(timeOfDay("2026-10-03T03:30:00Z"), "evening");
  assert.equal(timeOfDay("2026-10-03T06:30:00Z"), "late");
  assert.equal(isWeekend("2026-10-03T03:30:00Z"), true);
  assert.equal(isWeekend("2026-10-01T03:30:00Z"), false);
  assert.equal(dayKey(weekendStart(parseDayKey("2026-09-29")!)), "2026-10-02");
  assert.equal(dayKey(weekendStart(parseDayKey("2026-10-04")!)), "2026-10-04");
});

test("relative day labels", () => {
  const today = parseDayKey("2026-09-29")!;
  assert.equal(relativeDay("2026-09-29", today), "Today");
  assert.equal(relativeDay("2026-09-30", today), "Tomorrow");
  assert.equal(relativeDay("2026-10-02", today), "Friday");
  assert.match(relativeDay("2026-10-20", today), /Oct/);
});

test("time ranges read naturally", () => {
  assert.equal(fmtTimeRange({ start_at: "2026-10-02T02:00:00Z", end_at: "2026-10-02T04:30:00Z", all_day: false }), "7 PM – 9:30 PM");
  // A party ending after midnight still reads as one evening.
  assert.equal(fmtTimeRange({ start_at: "2026-10-03T04:00:00Z", end_at: "2026-10-03T09:00:00Z", all_day: false }), "9 PM – 2 AM");
  assert.match(fmtTimeRange({ start_at: "2026-09-29T16:00:00Z", end_at: "2026-09-30T23:30:00Z", all_day: false }), /Sep 30/);
  assert.equal(fmtTimeRange({ start_at: "2026-10-02T07:00:00Z", end_at: null, all_day: true }), "All day");
});

test("Google Calendar links carry UTC times", () => {
  const url = new URL(googleCalendarUrl({
    id: "evt-1", source: "luma", name: "Hack night", url: "https://luma.com/x", start_at: "2026-10-02T02:00:00Z",
    end_at: "2026-10-02T04:00:00Z", all_day: false, timezone: null, cover_url: null,
    location: { type: "offline", venue: "Hall", address: "1 Main St", city: null, neighborhood: null, region: null, country: null, lat: null, lng: null },
    presenter: null, hosts: [], tags: [], ticket: null, guest_count: null, going: false, going_status: null, calendar_ids: [],
    first_seen_at: null, announced_at: null, also_on: [], area: "bay", zone: null, vibes: [], topics: [], size: null,
    starred: false, hidden: false, muted: false,
  }));
  assert.equal(url.searchParams.get("dates"), "20261002T020000Z/20261002T040000Z");
  assert.equal(url.searchParams.get("location"), "Hall, 1 Main St");
});

test("bookmarklet hand-offs decode, including non-ASCII text", () => {
  const payload = { calendars: [{ api_id: "cal-1", name: "Café ☕" }], going: ["evt-1"] };
  const encoded = Buffer.from(JSON.stringify(payload), "utf8").toString("base64");
  assert.deepEqual(readImportHash(`#import=${encodeURIComponent(encoded)}`), { kind: "luma", payload });
  assert.equal(readImportHash("#pfimport=not-base64!!")?.kind ?? null, null);
  assert.equal(readImportHash("#something"), null);
});

// The test process is in California; Mumbai's destination calendar must stay in IST.
import assert from "node:assert/strict";
import { test } from "node:test";
import { agendaIncludes } from "../src/lib/agenda.ts";
import { dayKey } from "../src/lib/dates.ts";
import { groupByDay } from "../src/lib/event-groups.ts";
import { areaToday, eventDayOf, eventIsWeekend, eventOnDay, eventTimeOfDay } from "../src/lib/event-time.ts";
import { fmtTimeRange } from "../src/lib/format.ts";

const mumbai = { start_at: "2026-11-04T04:00:00Z", end_at: "2026-11-04T05:00:00Z", area: "mumbai" as const, all_day: false };

test("Mumbai agenda, day and month buckets use the same IST date while Bay behavior stays local", () => {
  assert.equal(eventDayOf(mumbai), "2026-11-04");
  assert.equal(eventOnDay(mumbai, "2026-11-04"), true);
  assert.equal(eventOnDay(mumbai, "2026-11-03"), false);
  assert.equal(agendaIncludes(mumbai, "2026-11-04", 0), true);
  assert.deepEqual(groupByDay([mumbai]).map((g) => g.key), ["2026-11-04"]);
  const bay = { ...mumbai, area: "bay" as const };
  assert.equal(eventDayOf(bay), "2026-11-03");
  assert.equal(eventOnDay(bay, "2026-11-03"), true);
  assert.equal(agendaIncludes(bay, "2026-11-04", 0), false);
});

test("Mumbai time facets, weekend boundaries and display use IST", () => {
  assert.equal(eventTimeOfDay(mumbai), "morning");
  assert.match(fmtTimeRange(mumbai), /9:30 AM – 10:30 AM IST/);
  assert.equal(eventIsWeekend({ ...mumbai, start_at: "2026-11-06T11:29:00Z" }), false);
  assert.equal(eventIsWeekend({ ...mumbai, start_at: "2026-11-06T11:30:00Z" }), true);
  assert.equal(eventTimeOfDay({ ...mumbai, area: "bay" }), "evening");
});

test("overnight Mumbai events do not acquire a false previous day and show next-day end", () => {
  const overnight = { ...mumbai, start_at: "2026-11-04T18:00:00Z", end_at: "2026-11-04T23:00:00Z" };
  assert.equal(eventOnDay(overnight, "2026-11-04"), true);
  assert.equal(eventOnDay(overnight, "2026-11-05"), true);
  assert.equal(eventOnDay(overnight, "2026-11-03"), false);
  assert.match(fmtTimeRange(overnight), /Nov 5, 4:30 AM IST/);
  assert.equal(dayKey(areaToday("mumbai", Date.parse("2026-11-04T19:00:00Z"))), "2026-11-05");
  assert.equal(dayKey(areaToday("bay", Date.parse("2026-11-04T19:00:00Z"))), "2026-11-04");
});

test("mixed destination groups never emit duplicate day headings", () => {
  const events = [mumbai, { ...mumbai, area: "bay" as const }, mumbai];
  const groups = groupByDay(events);
  assert.deepEqual(groups.map((g) => g.key), ["2026-11-03", "2026-11-04"]);
  assert.equal(groups[1].events.length, 2);
});

// Runs with TZ=America/Los_Angeles (see package.json).
import assert from "node:assert/strict";
import { test } from "node:test";
import { agendaIncludes } from "../src/lib/agenda.ts";
import { dayKey } from "../src/lib/dates.ts";
import { groupByDay } from "../src/lib/event-groups.ts";

const now = Date.parse("2026-10-07T12:00:00-07:00");

test("Today keeps events from earlier days in one Ongoing section", () => {
  const events = [];
  for (const date of ["2026-10-03", "2026-10-05", "2026-10-06"]) {
    const event = {
      start_at: `${date}T10:00:00-07:00`, end_at: "2026-10-11T18:00:00-07:00",
    };
    assert.equal(agendaIncludes(event, null, now), true);
    events.push(event);
  }
  assert.deepEqual(groupByDay(events, "2026-10-07"), [{ key: "ongoing", events }]);
  assert.equal(agendaIncludes({ start_at: events[0].start_at, end_at: "2026-10-07T12:00:00-07:00" }, null, now), false);
});

test("Ongoing ends at local midnight; today's and future starts keep their date headings", () => {
  const end_at = "2026-10-08T07:00:00Z";
  const events = [
    { start_at: "2026-10-07T06:59:59Z", end_at },
    { start_at: "2026-10-07T07:00:00Z", end_at },
    { start_at: "2026-10-08T17:00:00Z", end_at: null },
  ];
  assert.deepEqual(groupByDay(events, "2026-10-07").map(g => g.key), ["ongoing", "2026-10-07", "2026-10-08"]);
  assert.deepEqual(groupByDay(events).map(g => g.key), ["2026-10-06", "2026-10-07", "2026-10-08"]);
  assert.deepEqual(groupByDay([], "2026-10-07"), []);
  assert.deepEqual(groupByDay(events.slice(1), "2026-10-07").map(g => g.key), ["2026-10-07", "2026-10-08"]);
});

test("Today keeps today's running events and future events, excluding ones that have ended", () => {
  const start_at = "2026-10-07T11:00:00-07:00";
  assert.equal(agendaIncludes({ start_at, end_at: "2026-10-07T13:00:00-07:00" }, null, now), true);
  assert.equal(agendaIncludes({ start_at, end_at: "2026-10-07T12:00:00-07:00" }, null, now), false);
  assert.equal(agendaIncludes({ start_at, end_at: null }, null, now), true);
  assert.equal(agendaIncludes({ start_at: "2026-10-07T09:00:00-07:00", end_at: null }, null, now), false);
  assert.equal(agendaIncludes({ start_at: "2026-10-08T10:00:00-07:00", end_at: null }, null, now), true);
});

test("an explicit date still includes ended events from that date onward", () => {
  const event = { start_at: "2026-10-03T10:00:00-07:00", end_at: "2026-10-03T12:00:00-07:00" };
  assert.equal(agendaIncludes(event, "2026-10-03", now), true);
  assert.equal(agendaIncludes(event, "2026-10-04", now), false);
});

test("an open Today view moves still-running events into Ongoing after midnight, including DST days", () => {
  for (const [start_at, before, after] of [
    ["2026-10-06T20:00:00-07:00", "2026-10-06T23:59:00-07:00", "2026-10-07T00:01:00-07:00"],
    ["2026-11-01T20:00:00-08:00", "2026-11-01T23:59:00-08:00", "2026-11-02T00:01:00-08:00"],
  ]) {
    const event = { start_at, end_at: "2026-11-03T10:00:00-08:00" };
    assert.equal(agendaIncludes(event, null, Date.parse(before)), true);
    assert.equal(agendaIncludes(event, null, Date.parse(after)), true);
    assert.equal(groupByDay([event], dayKey(new Date(before)))[0].key, dayKey(new Date(before)));
    assert.equal(groupByDay([event], dayKey(new Date(after)))[0].key, "ongoing");
  }
});

// The test process is in California; Cape Town's calendar must stay on Cape Town's clock (SAST, UTC+2).
import assert from "node:assert/strict";
import { test } from "node:test";
import { agendaIncludes } from "../src/lib/agenda.ts";
import { dayKey } from "../src/lib/dates.ts";
import { groupByDay } from "../src/lib/event-groups.ts";
import { areaToday, destinationArea, eventDayOf, eventIsWeekend, eventOnDay, eventTimeOfDay, sharedDestination } from "../src/lib/event-time.ts";
import { eventLinkLabel, fmtPrice, fmtTimeRange } from "../src/lib/format.ts";

// 00:30 on Wednesday 4 November in Cape Town, which is still Tuesday afternoon in California.
const capeTown = { start_at: "2026-11-03T22:30:00Z", end_at: "2026-11-03T23:30:00Z", area: "cape-town" as const, all_day: false };

test("agenda, day and month buckets use Cape Town's date while the Bay Area stays local", () => {
  assert.equal(eventDayOf(capeTown), "2026-11-04");
  assert.equal(eventOnDay(capeTown, "2026-11-04"), true);
  assert.equal(eventOnDay(capeTown, "2026-11-03"), false);
  assert.equal(agendaIncludes(capeTown, "2026-11-04", 0), true);
  assert.deepEqual(groupByDay([capeTown]).map((g) => g.key), ["2026-11-04"]);
  const bay = { ...capeTown, area: "bay" as const };
  assert.equal(eventDayOf(bay), "2026-11-03");
  assert.equal(eventOnDay(bay, "2026-11-03"), true);
  assert.equal(agendaIncludes(bay, "2026-11-04", 0), false);
});

test("time of day, the weekend and the shown times use Cape Town's clock", () => {
  const evening = { ...capeTown, start_at: "2026-11-04T16:15:00Z", end_at: "2026-11-04T18:15:00Z" };
  assert.equal(eventTimeOfDay(evening), "evening");
  assert.match(fmtTimeRange(evening), /6:15 PM – 8:15 PM SAST/);
  assert.match(fmtTimeRange({ ...evening, end_at: null }), /^6:15 PM SAST$/);
  assert.equal(eventIsWeekend({ ...capeTown, start_at: "2026-11-06T14:59:00Z" }), false, "Friday 4:59 pm");
  assert.equal(eventIsWeekend({ ...capeTown, start_at: "2026-11-06T15:00:00Z" }), true, "Friday 5 pm");
  assert.equal(eventTimeOfDay({ ...evening, area: "bay" }), "morning");
});

test("an overnight event belongs to the day it starts and shows the date it ends", () => {
  const overnight = { ...capeTown, start_at: "2026-11-04T19:00:00Z", end_at: "2026-11-05T00:00:00Z" };
  assert.equal(eventOnDay(overnight, "2026-11-04"), true);
  assert.equal(eventOnDay(overnight, "2026-11-05"), false, "two hours past midnight is not the next day's event");
  assert.equal(eventOnDay({ ...overnight, end_at: "2026-11-05T02:00:00Z" }, "2026-11-05"), true);
  assert.match(fmtTimeRange(overnight), /9:00 PM – Nov 5, 2:00 AM SAST/);
  assert.equal(dayKey(areaToday("cape-town", Date.parse("2026-11-04T22:30:00Z"))), "2026-11-05");
  assert.equal(dayKey(areaToday("bay", Date.parse("2026-11-04T22:30:00Z"))), "2026-11-04");
});

test("groups of mixed areas never repeat a day heading", () => {
  const events = [capeTown, { ...capeTown, area: "bay" as const }, capeTown];
  const groups = groupByDay(events);
  assert.deepEqual(groups.map((g) => g.key), ["2026-11-03", "2026-11-04"]);
  assert.equal(groups[1].events.length, 2);
});

test("a destination is named only when every event shares it", () => {
  assert.equal(destinationArea(capeTown), "cape-town");
  assert.equal(destinationArea({ ...capeTown, area: "bay" }), null);
  assert.equal(sharedDestination([capeTown, capeTown]), "cape-town");
  assert.equal(sharedDestination([capeTown, { ...capeTown, area: "bay" }]), null);
  assert.equal(sharedDestination([]), null);
});

test("rand prices and the main button of a weekly-list event", () => {
  const ticket = { free: false, price_cents: 8000, max_price_cents: null, currency: "zar", sold_out: false, spots_left: null,
    approval: false, waitlist: false, availability: null };
  assert.equal(fmtPrice(ticket), "R80");
  assert.equal(fmtPrice({ ...ticket, price_cents: 9950 }), "R99.5");
  assert.equal(fmtPrice({ ...ticket, price_cents: 150000 }), "R1,500");
  assert.equal(fmtPrice({ ...ticket, free: true, price_cents: null }), "Free");
  const label = (url: string) => eventLinkLabel({ source: "sundry", url, going: false });
  assert.equal(label("https://www.instagram.com/p/FixtureRun01/"), "See it on Instagram");
  assert.equal(label("https://www.quicket.co.za/events/100001-fixture-film/"), "See it on Quicket");
  assert.equal(label("https://tickets.fixture.test/late"), "Open the event page");
  assert.equal(label("not a url"), "Open the event page");
  assert.equal(eventLinkLabel({ source: "luma", url: "https://luma.com/fixture", going: true }), "Open on Luma");
});

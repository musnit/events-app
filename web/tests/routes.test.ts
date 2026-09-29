import assert from "node:assert/strict";
import { test } from "node:test";
import { EMPTY_FILTERS, filtersFromParams, filtersToParams } from "../src/lib/filters.ts";
import { href, parseRoute, routePath, type Route } from "../src/lib/routes.ts";

test("every route survives a round trip through its path", () => {
  const routes: Route[] = [
    { name: "agenda", from: null },
    { name: "agenda", from: "2026-10-03" },
    { name: "day", date: "2026-02-28" },
    { name: "month", month: "2026-10" },
    { name: "event", id: "evt-AbC123" },
    { name: "event", id: "pf-x_y-z" },
    { name: "saved" },
    { name: "calendars" },
    { name: "calendar", id: "cal-Q8gsfXqUMvFaFmE" },
    { name: "sources", section: null },
    { name: "sources", section: "partiful" },
  ];
  for (const route of routes) assert.deepEqual(parseRoute(routePath(route)), route);
});

test("invalid dates, ids and unknown paths are not found", () => {
  for (const path of ["day/2026-02-30", "day/tomorrow", "month/2026-13", "agenda/yesterday", "event/", "event/a b",
    "sources/billing", "saved/extra", "calendars/x/y", "nope"]) {
    assert.equal(parseRoute(path).name, "notFound", path);
  }
  assert.deepEqual(parseRoute(""), { name: "agenda", from: null });
  assert.deepEqual(parseRoute("/month/2026-10/"), { name: "month", month: "2026-10" });
});

test("href keeps filters on list views and drops them elsewhere", () => {
  const filters = { ...EMPTY_FILTERS, vibes: ["party"], weekend: true };
  assert.equal(href({ name: "agenda", from: null }, filters), "./?vibe=party&weekend=1");
  assert.equal(href({ name: "day", date: "2026-10-03" }, filters), "day/2026-10-03?vibe=party&weekend=1");
  assert.equal(href({ name: "event", id: "evt-1" }, filters), "event/evt-1");
  assert.equal(href({ name: "sources", section: "luma" }, filters), "sources/luma");
  assert.equal(href({ name: "agenda", from: null }), "./");
});

test("filters round-trip through the query string", () => {
  const filters = {
    ...EMPTY_FILTERS, q: "hack night", vibes: ["build", "party"], topics: ["ai"], times: ["evening" as const],
    weekend: true, area: "all" as const, zones: ["sf"], sizes: ["intimate"], going: true, free: true, open: true, fresh: true, hidden: true,
  };
  assert.deepEqual(filtersFromParams(filtersToParams(filters)), filters);
  assert.equal(filtersToParams(EMPTY_FILTERS).toString(), "");
});

test("junk in the query string is ignored", () => {
  const f = filtersFromParams(new URLSearchParams("vibe=party,<script>,party&time=noon,late&area=moon&going=yes&new=1"));
  assert.deepEqual(f.vibes, ["party"]);
  assert.deepEqual(f.times, ["late"]);
  assert.equal(f.area, null);
  assert.equal(f.going, false);
  assert.equal(f.fresh, true);
});

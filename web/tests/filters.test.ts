// Runs with TZ=America/Los_Angeles (see package.json), so local times below are Pacific.
import assert from "node:assert/strict";
import { test } from "node:test";
import { activeFilterCount, EMPTY_FILTERS, facetCounts, matchEvent, toggle, type MatchContext } from "../src/lib/filters.ts";
import type { EventItem } from "../src/lib/types.ts";

function event(patch: Partial<EventItem>): EventItem {
  return {
    id: "evt-1", source: "luma", name: "Test event", url: "https://luma.com/x", start_at: "2026-10-02T02:00:00Z", end_at: null,
    all_day: false, timezone: "America/Los_Angeles", cover_url: null,
    location: { type: "offline", venue: "The Commons", address: "123 Main St", city: "San Francisco", neighborhood: "SoMa", region: "CA", country: "US", lat: null, lng: null },
    presenter: { id: "cal-a", name: "Builders Club", avatar_url: null, url: null, description: null },
    hosts: [{ name: "Ada Lovelace", avatar_url: null }], tags: ["Networking"], ticket: null, guest_count: 30,
    going: false, going_status: null, calendar_ids: ["cal-a"], first_seen_at: null, announced_at: null, also_on: [],
    area: "bay", zone: "sf", vibes: ["build"], topics: ["ai"], size: "intimate", starred: false, hidden: false, muted: false,
    linked: false, ...patch,
  };
}

const ctx: MatchContext = { area: "bay", newSince: Date.parse("2026-09-29T00:00:00Z"), calendars: new Map() };

test("area preference keeps Bay Area events and optionally online ones", () => {
  const online = event({ area: "online", zone: null });
  const far = event({ area: "elsewhere", zone: null });
  assert.equal(matchEvent(online, EMPTY_FILTERS, ctx), false);
  assert.equal(matchEvent(online, EMPTY_FILTERS, { ...ctx, area: "bay-online" }), true);
  assert.equal(matchEvent(far, EMPTY_FILTERS, { ...ctx, area: "bay-online" }), false);
  assert.equal(matchEvent(far, { ...EMPTY_FILTERS, area: "all" }, ctx), true);
});

test("an event added by its own link shows wherever it is", () => {
  const far = event({ area: "elsewhere", zone: null, linked: true });
  assert.equal(matchEvent(far, EMPTY_FILTERS, ctx), true);
  assert.equal(matchEvent({ ...far, muted: true }, EMPTY_FILTERS, ctx), false, "muting its calendar still hides it");
});

test("vibes and topics match any chosen value, and combine with each other", () => {
  const ev = event({});
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, vibes: ["party", "build"] }, ctx), true);
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, vibes: ["party"] }, ctx), false);
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, vibes: ["build"], topics: ["crypto"] }, ctx), false);
});

test("time of day and weekend use local time", () => {
  // 2026-10-02T02:00Z is Thursday 7 pm in San Francisco.
  const ev = event({});
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, times: ["evening"] }, ctx), true);
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, times: ["morning"] }, ctx), false);
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, weekend: true }, ctx), false);
  const fridayNight = event({ start_at: "2026-10-03T03:00:00Z" });
  assert.equal(matchEvent(fridayNight, { ...EMPTY_FILTERS, weekend: true }, ctx), true);
});

test("search needs every word somewhere in the event", () => {
  const ev = event({});
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, q: "ada soma" }, ctx), true);
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, q: "BUILDERS networking" }, ctx), true);
  assert.equal(matchEvent(ev, { ...EMPTY_FILTERS, q: "ada oakland" }, ctx), false);
});

test("hidden and muted events stay out unless asked for", () => {
  assert.equal(matchEvent(event({ hidden: true }), EMPTY_FILTERS, ctx), false);
  assert.equal(matchEvent(event({ muted: true }), EMPTY_FILTERS, ctx), false);
  assert.equal(matchEvent(event({ muted: true }), { ...EMPTY_FILTERS, hidden: true }, ctx), true);
});

test("going, free, not-sold-out and new filters", () => {
  const free = event({ ticket: { free: true, price_cents: null, max_price_cents: null, currency: null, sold_out: false, spots_left: null, approval: false, waitlist: false, availability: null } });
  const soldOut = event({ ticket: { free: false, price_cents: 2000, max_price_cents: null, currency: "usd", sold_out: true, spots_left: 0, approval: false, waitlist: false, availability: "sold-out" } });
  assert.equal(matchEvent(free, { ...EMPTY_FILTERS, free: true }, ctx), true);
  assert.equal(matchEvent(soldOut, { ...EMPTY_FILTERS, free: true }, ctx), false);
  assert.equal(matchEvent(soldOut, { ...EMPTY_FILTERS, open: true }, ctx), false);
  assert.equal(matchEvent(event({ going: true }), { ...EMPTY_FILTERS, going: true }, ctx), true);
  assert.equal(matchEvent(event({ announced_at: "2026-09-30T10:00:00Z" }), { ...EMPTY_FILTERS, fresh: true }, ctx), true);
  assert.equal(matchEvent(event({ announced_at: "2026-09-01T10:00:00Z" }), { ...EMPTY_FILTERS, fresh: true }, ctx), false);
  assert.equal(matchEvent(event({ announced_at: null }), { ...EMPTY_FILTERS, fresh: true }, ctx), false);
});

test("facet counts ignore their own facet but respect the others", () => {
  const events = [
    event({ id: "a", vibes: ["build"], topics: ["ai"] }),
    event({ id: "b", vibes: ["party"], topics: ["ai"] }),
    event({ id: "c", vibes: ["party"], topics: ["crypto"] }),
  ];
  const counts = facetCounts(events, { ...EMPTY_FILTERS, vibes: ["party"], topics: ["ai"] }, ctx);
  assert.deepEqual(counts.vibes, { build: 1, party: 1 });
  assert.deepEqual(counts.topics, { ai: 1, crypto: 1 });
});

test("helpers", () => {
  assert.deepEqual(toggle(["a", "b"], "a"), ["b"]);
  assert.deepEqual(toggle(["a"], "b"), ["a", "b"]);
  assert.equal(activeFilterCount({ ...EMPTY_FILTERS, vibes: ["a", "b"], going: true, area: "all" }), 4);
  assert.equal(activeFilterCount({ ...EMPTY_FILTERS, q: "x" }), 0);
});

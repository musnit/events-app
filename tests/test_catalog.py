import gzip
import json
import unittest

from events import categorize
from events.catalog import Catalog
from events.sources import agihouse, empty_location, partiful
from events.sources.luma import LINKED_CALENDAR
from events.sync import LUMA_MINE

from .helpers import HOUR, FakeClock, add_feed, iso, listing, make_store

SF = empty_location("offline") | {"venue": "Madrone Art Bar", "address": "500 Fixture St, San Francisco",
                                  "city": "San Francisco", "region": "CA", "country": "US"}


class CatalogTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = make_store(self, self.clock)
        self.catalog = Catalog(self.store, clock=self.clock)
        self.cal_a = add_feed(self.store, "luma:cal-a", calendar_id="cal-a", name="SF AI Club")
        self.cal_b = add_feed(self.store, "luma:cal-b", calendar_id="cal-b", name="Fixture Brain Lectures")
        self.store.upsert_calendar(LUMA_MINE, "builtin")
        self.store.ensure_feed("luma:ics", source="luma", kind="luma-ics", calendar_id="luma-mine", label="iCal")
        self.store.upsert_calendar(agihouse.CALENDAR, "builtin")
        self.store.ensure_feed("agihouse", source="agihouse", kind="agihouse", calendar_id="agihouse", label="AGI House")
        self.store.upsert_calendar(partiful.MINE_CALENDAR, "builtin")
        self.store.ensure_feed("partiful:u1:mine", source="partiful", kind="partiful-mine", calendar_id="partiful",
                               label="Partiful")

    def events(self) -> dict[str, dict]:
        return {e["id"]: e for e in self.catalog.build()["events"]}


class BuildTest(CatalogTestCase):
    def test_same_event_from_several_feeds_merges(self):
        rich = listing("evt-1", name="Scaling Laws Fireside Chat", cover_url="https://img.test/1.png", location=SF,
                       hosts=[{"name": "SF AI Club", "avatar_url": None}], guest_count=120)
        self.store.replace_listings("luma:ics", [listing("evt-1", name="Scaling Laws (feed)", going_status="registered")])
        self.store.replace_listings(self.cal_a, [rich])
        self.store.replace_listings(self.cal_b, [dict(rich, cover_url=None, name="Copy without a cover")])
        ev = self.events()["evt-1"]
        self.assertEqual(ev["name"], "Scaling Laws Fireside Chat", "the calendar feed with a cover supplies details")
        self.assertEqual(ev["cover_url"], "https://img.test/1.png")
        self.assertEqual(ev["calendar_ids"], ["cal-a", "cal-b", "luma-mine"])
        self.assertTrue(ev["going"])
        self.assertEqual(ev["going_status"], "registered")
        self.assertEqual(ev["also_on"], [])

    def test_events_added_by_link_are_marked(self):
        self.store.upsert_calendar(LINKED_CALENDAR, "builtin")
        self.store.ensure_feed("luma:evt-linked", source="luma", kind="luma-event", calendar_id="luma-links",
                               label="Linked")
        self.store.replace_listings("luma:evt-linked", [listing("evt-linked")])
        self.store.replace_listings(self.cal_a, [listing("evt-linked"), listing("evt-plain")])
        events = self.events()
        self.assertTrue(events["evt-linked"]["linked"])
        self.assertEqual(events["evt-linked"]["calendar_ids"], ["cal-a", "luma-links"])
        self.assertFalse(events["evt-plain"]["linked"])

    def test_going_from_the_going_table(self):
        self.store.replace_listings(self.cal_b, [listing("evt-2"), listing("evt-3")])
        self.store.replace_going("luma-import", {"evt-2": "registered"})
        events = self.events()
        self.assertEqual((events["evt-2"]["going"], events["evt-2"]["going_status"]), (True, "registered"))
        self.assertEqual((events["evt-3"]["going"], events["evt-3"]["going_status"]), (False, None))

    def test_cross_source_duplicates_fold_into_the_luma_copy(self):
        start = iso(48)
        self.store.replace_listings(self.cal_a, [listing("evt-hack", name="AI Agents Hackathon", start=start)])
        self.store.replace_listings("agihouse", [
            listing("agi-agents-hackathon", source="agihouse", name="AI Agents Hackathon!", start=iso(48.5),
                    url="https://www.agihouse.org/events/agents-hackathon"),
            listing("agi-later", source="agihouse", name="AI Agents Hackathon", start=iso(52)),
            listing("agi-other", source="agihouse", name="Robotics Demo Night", start=start),
        ])
        self.store.replace_listings("partiful:u1:mine", [
            listing("pf-hack", source="partiful", name="AI agents hackathon", start=iso(49), going_status="going",
                    url="https://partiful.com/e/hack")])
        events = self.events()
        self.assertEqual(sorted(events), ["agi-later", "agi-other", "evt-hack"])
        hack = events["evt-hack"]
        self.assertEqual(hack["also_on"], [{"source": "partiful", "url": "https://partiful.com/e/hack"},
                                           {"source": "agihouse", "url": "https://www.agihouse.org/events/agents-hackathon"}])
        self.assertEqual(hack["calendar_ids"], ["agihouse", "cal-a", "partiful"])
        self.assertTrue(hack["going"], "an RSVP on the folded copy carries over")
        self.assertEqual(hack["going_status"], "going")

    def test_same_source_copies_are_not_folded(self):
        self.store.replace_listings(self.cal_a, [listing("evt-x", name="Founders Dinner")])
        self.store.replace_listings(self.cal_b, [listing("evt-y", name="Founders Dinner")])
        self.assertEqual(sorted(self.events()), ["evt-x", "evt-y"])

    def test_placeholders_are_skipped(self):
        self.store.replace_listings(self.cal_a, [listing("evt-hold", name="HOLD - 2nd Floor Private Rental"),
                                                 listing("evt-tbd", name="Philosophy Event (HOLD) Details to come!"),
                                                 listing("evt-real", name="Philosophy Salon")])
        self.assertEqual(list(self.events()), ["evt-real"])

    def test_muted_calendars(self):
        self.store.replace_listings(self.cal_a, [listing("evt-both")])
        self.store.replace_listings(self.cal_b, [listing("evt-both"), listing("evt-b-only")])
        self.store.set_pref("muted_calendars", ["cal-b", 5])
        payload = self.catalog.build()
        events = {e["id"]: e for e in payload["events"]}
        self.assertTrue(events["evt-b-only"]["muted"])
        self.assertFalse(events["evt-both"]["muted"], "still listed by an unmuted calendar")
        muted = {c["id"]: c["muted"] for c in payload["calendars"]}
        self.assertTrue(muted["cal-b"])
        self.assertFalse(muted["cal-a"])

    def test_derived_fields_and_marks(self):
        self.store.replace_listings(self.cal_b, [listing("evt-talk", name="Why Octopuses Can't Give Lectures", location=SF,
                                                         guest_count=103)])
        self.store.set_mark("evt-talk", starred=True, hidden=True)
        ev = self.events()["evt-talk"]
        self.assertEqual((ev["area"], ev["zone"]), ("bay", "sf"))
        self.assertIn("learn", ev["vibes"])
        self.assertEqual(ev["size"], "medium")
        self.assertTrue(ev["starred"])
        self.assertTrue(ev["hidden"])
        for key in ("topics", "first_seen_at", "announced_at", "calendar_ids", "going", "also_on"):
            self.assertIn(key, ev)

    def test_history_window_and_ordering(self):
        self.store.replace_listings(self.cal_a, [
            listing("evt-b", name="Beta", start=iso(5)), listing("evt-a", name="Alpha", start=iso(5)),
            listing("evt-early", name="Zulu", start=iso(1)),
            listing("evt-last-week", start=iso(-24 * 10)), listing("evt-long-ago", start=iso(-24 * 50))])
        self.assertEqual(list(self.events()), ["evt-last-week", "evt-early", "evt-a", "evt-b"])

    def test_announcements_and_first_seen(self):
        self.store.replace_listings(self.cal_a, [listing("evt-old")])
        self.clock.advance(HOUR)
        self.store.replace_listings(self.cal_a, [listing("evt-old"), listing("evt-new")])
        events = self.events()
        self.assertIsNone(events["evt-old"]["announced_at"])
        self.assertEqual(events["evt-old"]["first_seen_at"], iso(0))
        self.assertEqual(events["evt-new"]["announced_at"], iso(1))

    def test_calendar_summaries(self):
        self.store.replace_listings(self.cal_a, [listing("evt-1"), listing("evt-2", start=iso(-30), end=iso(-29))])
        self.store.record_failure(self.cal_b, "Luma 500: oops", 600)
        self.store.replace_listings("agihouse", [])
        payload = self.catalog.build()
        self.assertEqual(set(payload), {"generated_at", "events", "calendars", "taxonomy", "zones"})
        self.assertEqual(payload["generated_at"], iso(0))
        self.assertEqual(payload["taxonomy"], categorize.taxonomy())
        self.assertEqual([z["id"] for z in payload["zones"]], ["sf", "peninsula", "south-bay", "east-bay", "north-bay"])
        cals = {c["id"]: c for c in payload["calendars"]}
        self.assertEqual(set(cals), {"cal-a", "cal-b", "luma-mine", "agihouse", "partiful"})
        self.assertEqual(cals["cal-a"]["upcoming_count"], 1, "past events are not upcoming")
        self.assertEqual(cals["cal-a"]["last_ok_at"], iso(0))
        self.assertIsNone(cals["cal-a"]["last_error"])
        self.assertEqual(cals["cal-b"]["last_error"], "Luma 500: oops")
        self.assertIsNone(cals["cal-b"]["last_ok_at"])
        self.assertEqual(cals["cal-a"]["origins"], ["link"])
        self.assertIsNone(cals["agihouse"]["last_ok_at"], "only Luma calendar feeds report pull times")


class SnapshotTest(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.store.replace_listings(self.cal_a, [listing("evt-1", name="Käse & Wein 🍷")])
        self.builds = 0
        build = self.catalog.build

        def counting_build():
            self.builds += 1
            return build()

        self.catalog.build = counting_build

    def test_snapshot_is_cached_until_the_data_changes(self):
        first = self.catalog.snapshot()
        self.assertIs(self.catalog.snapshot(), first)
        self.assertEqual(self.builds, 1)
        self.assertEqual(first.version, f"{self.store.data_version()}.{categorize.RULES_VERSION}.{int(self.clock() // 3600)}")
        self.assertRegex(first.etag, r'^"[0-9a-f]{24}"$')
        self.assertEqual(gzip.decompress(first.gzipped), first.body)
        payload = json.loads(first.body)
        self.assertEqual(payload["version"], first.version)
        self.assertIn("Käse & Wein 🍷".encode(), first.body, "UTF-8, not escaped")

        self.store.set_mark("evt-1", starred=True)
        second = self.catalog.snapshot()
        self.assertIsNot(second, first)
        self.assertNotEqual(second.etag, first.etag)
        self.assertEqual(self.builds, 2)

    def test_snapshot_rebuilds_every_hour(self):
        first = self.catalog.snapshot()
        self.clock.advance(HOUR)
        self.assertIsNot(self.catalog.snapshot(), first)

    def test_lone_surrogates_do_not_break_the_snapshot(self):
        self.store.replace_listings(self.cal_b, [listing("evt-bad", name=json.loads('"Launch party \\ud83c"'))])
        events = {e["id"]: e for e in json.loads(self.catalog.snapshot().body.decode("utf-8"))["events"]}
        self.assertTrue(events["evt-bad"]["name"].startswith("Launch party"))


class FeedEventsTest(CatalogTestCase):
    def test_events_for_the_calendar_feed(self):
        self.store.replace_listings(self.cal_a, [
            listing("evt-going", going_status="approved"), listing("evt-starred"), listing("evt-plain"),
            listing("evt-hidden", going_status="approved"), listing("evt-yesterday", start=iso(-20), end=iso(-19)),
            listing("evt-last-week", going_status="approved", start=iso(-24 * 7), end=iso(-24 * 7 + 2))])
        self.store.set_mark("evt-starred", starred=True)
        self.store.set_mark("evt-hidden", hidden=True)
        mine = [e["id"] for e in self.catalog.events_for_feed(starred_or_going=True)]
        everything = [e["id"] for e in self.catalog.events_for_feed(starred_or_going=False)]
        self.assertEqual(sorted(mine), ["evt-going", "evt-starred"])
        self.assertEqual(sorted(everything), ["evt-going", "evt-plain", "evt-starred", "evt-yesterday"])


if __name__ == "__main__":
    unittest.main()

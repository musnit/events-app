"""Made-up editorial collections; never capture the owner's private research or account data."""
import copy
import json
import subprocess
import sys
import unittest

from events import areas
from events.catalog import Catalog
from events.db import Database
from events.research import canonical_url, validate_collections
from events.store import Store

from .helpers import DAY, FakeClock, add_feed, iso, listing, make_store, temp_dir


def entry(entry_id="fixture-research-talk", **changes):
    return {"id": entry_id, "title": "Fixture Public Science Forum", "kind": "talk", "priority": "recommended",
            "verdict": "A discussion worth attending.", "summary": "Open research and public participation.",
            "start_at": iso(24), "end_at": iso(26), "date_label": "Tomorrow, 5:30pm IST",
            "schedule_status": "confirmed", "timezone": "Asia/Kolkata", "venue": "Fixture Hall",
            "address": "1 Example Road, Mumbai, India", "city": "Mumbai", "country": "IN",
            "url": "https://luma.com/fixture-science", "access": "Free registration",
            "people": [{"name": "Example Researcher", "role": "Speaker", "assessment": "Research experience"}],
            "sections": [{"title": "Why attend", "body": "A noncommercial conversation.\nMore detail."}],
            "sources": [{"label": "Organizer", "url": "https://example.test/program"}],
            "tags": ["science", "community"], "conflicts": [], **changes}


def collection(*entries, **changes):
    return {"id": "fixture-mumbai", "title": "Fixture Mumbai research", "city": "Mumbai",
            "timezone": "Asia/Kolkata", "checked_at": iso(), "intro": "Research for a trip.",
            "notes": [{"title": "Evidence", "body": "Dates may change."}],
            "itinerary": [{"date": "2026-09-30", "body": "Visit the forum."}],
            "entries": list(entries or [entry()]), **changes}


class ResearchTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = make_store(self, self.clock)
        self.catalog = Catalog(self.store, self.clock)

    def test_persistence_independent_of_sync_pruning_and_restart(self):
        path = temp_dir(self) / "state" / "events.db"
        db = Database(path)
        db.migrate()
        store = Store(db, self.clock)
        store.import_research(collection())
        feed = add_feed(store, "luma:cal-fixture", calendar_id="cal-fixture")
        store.replace_listings(feed, [listing("evt-fixture")])
        store.replace_listings(feed, [])
        store.prune(keep_days=0)
        db.close()
        reopened = Database(path)
        self.addCleanup(reopened.close)
        reopened.migrate()
        self.assertEqual(Store(reopened).research_collections(), validate_collections(collection()))

    def test_import_is_atomic_and_idempotent_and_never_replaces_other_state(self):
        self.store.import_research(collection())
        self.store.set_mark("fixture-research-talk", starred=True)
        self.store.set_pref("area", "bay")
        self.store.set_secret("fixture_secret", {"token": "must-stay-private"})
        original = self.store.research_collections()
        version = self.store.data_version()
        self.assertEqual(self.store.import_research(collection())["changed"], 0)
        self.assertEqual(self.store.data_version(), version)
        invalid = [collection(intro="Changed"), collection(entry("second-entry", url="javascript:alert(1)"), id="second")]
        with self.assertRaises(ValueError):
            self.store.import_research(invalid)
        self.assertEqual(self.store.research_collections(), original)
        self.assertEqual(self.store.data_version(), version)
        self.store.import_research(collection(intro="Updated analysis"))
        self.assertTrue(self.store.marks()["fixture-research-talk"]["starred"])
        self.assertEqual(self.store.prefs(), {"area": "bay"})
        self.assertNotIn("must-stay-private", self.catalog.snapshot().body.decode())

    def test_cross_collection_ids_unique_and_existing_collection_unchanged(self):
        self.store.import_research(collection())
        with self.assertRaises(ValueError):
            self.store.import_research(collection(id="second-collection"))
        self.assertEqual(len(self.store.research_collections()), 1)
        self.store.import_research(collection(entry("second-entry"), id="second-collection"))
        self.store.import_research(collection(intro="Updated"))
        self.assertEqual(len(self.store.research_collections()), 2)

    def test_confirmed_dates_normalize_and_unknown_dates_stay_out_of_catalog(self):
        scheduled = entry(start_at="2026-09-30T17:30:00+05:30", end_at=None)
        unknown = [entry("fixture-" + status, start_at=None, end_at=None, schedule_status=status,
                         kind="place" if status == "unscheduled" else "side-event")
                   for status in ("unverified", "unscheduled", "stale")]
        self.store.import_research(collection(scheduled, *unknown))
        payload = self.catalog.build()
        self.assertEqual(len(payload["events"]), 1)
        event = payload["events"][0]
        self.assertEqual(event["start_at"], "2026-09-30T12:00:00Z")
        self.assertIsNone(event["end_at"])
        self.assertEqual((event["area"], event["source"]), ("mumbai", "research"))
        self.assertEqual(event["research"]["summary"], scheduled["summary"])
        self.assertEqual(event["collection_ids"], ["fixture-mumbai"])
        self.assertEqual(len(payload["research_collections"][0]["entries"]), 4)
        self.assertTrue(all(e["event_id"] is None for e in payload["research_collections"][0]["entries"][1:]))

    def test_stale_known_dates_are_not_scheduled_and_history_keeps_research(self):
        self.store.import_research(collection(entry(schedule_status="stale")))
        self.assertEqual(self.catalog.build()["events"], [])
        self.store.import_research(collection())
        self.clock.advance(100 * DAY)
        payload = self.catalog.build()
        self.assertEqual(payload["events"], [])
        self.assertEqual(len(payload["research_collections"][0]["entries"]), 1)

    def test_watchlist_skip_reference_stay_collection_only_even_with_dates(self):
        self.store.import_research(collection(*(entry("fixture-" + priority, priority=priority)
                                                for priority in ("watchlist", "skip", "reference"))))
        payload = self.catalog.build()
        self.assertEqual(payload["events"], [])
        self.assertEqual(len(payload["research_collections"][0]["entries"]), 3)

    def test_rejected_research_does_not_remove_existing_upstream_event(self):
        feed = add_feed(self.store, "luma:cal-fixture", calendar_id="cal-fixture")
        self.store.replace_listings(feed, [listing("evt-fixture", url=entry()["url"])])
        self.store.import_research(collection(entry(priority="skip")))
        event = self.catalog.build()["events"][0]
        self.assertEqual(event["id"], "evt-fixture")
        self.assertEqual(event["research"]["priority"], "skip")
        self.assertEqual(len(self.catalog.events_for_feed(starred_or_going=False)), 1)

    def test_known_date_without_clock_does_not_invent_calendar_time(self):
        self.store.import_research(collection(entry(start_at=None, end_at=None, local_date="2026-09-30",
                                                   schedule_status="unverified")))
        payload = self.catalog.build()
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["research_collections"][0]["entries"][0]["local_date"], "2026-09-30")
        with self.assertRaises(ValueError):
            self.store.import_research(collection(entry(local_date="2026-02-30")))

    def test_approximate_and_missing_end_intervals_are_not_in_calendar_exports(self):
        self.store.import_research(collection(entry(schedule_status="approximate"),
                                              entry("fixture-missing-end", end_at=None)))
        self.assertEqual(len(self.catalog.build()["events"]), 2)
        self.assertEqual(self.catalog.events_for_feed(starred_or_going=False), [])

    def test_exact_source_url_merge_preserves_marks_rsvp_and_notes_after_sync(self):
        feed = add_feed(self.store, "luma:cal-fixture", calendar_id="cal-fixture")
        upstream = listing("evt-fixture", name="Updated source title", url="https://lu.ma/fixture-science/?utm_source=feed",
                           going_status="approved", location={"city": "Mumbai", "country": "IN"})
        self.store.replace_listings(feed, [upstream])
        self.store.set_mark("evt-fixture", hidden=True)
        self.store.set_mark("fixture-research-talk", starred=True)
        self.store.import_research(collection())
        event = self.catalog.build()["events"][0]
        self.assertEqual((event["id"], event["source"]), ("evt-fixture", "luma"))
        self.assertTrue(event["starred"] and event["hidden"] and event["going"])
        self.assertEqual(event["research"]["event_id"], "evt-fixture")
        self.assertEqual(event["name"], "Updated source title")
        self.assertEqual(len(self.catalog.build()["events"]), 1)
        self.store.replace_listings(feed, [dict(upstream, name="Changed by source")])
        event = self.catalog.build()["events"][0]
        self.assertEqual(event["name"], "Changed by source")
        self.assertEqual(event["research"]["sections"], entry()["sections"])
        self.store.replace_listings(feed, [])
        fallback = self.catalog.build()["events"][0]
        self.assertEqual(fallback["id"], "fixture-research-talk")
        self.assertTrue(fallback["starred"])

    def test_same_title_in_sf_does_not_merge_with_mumbai_and_sf_stays_unchanged(self):
        feed = add_feed(self.store, "luma:cal-fixture", calendar_id="cal-fixture")
        self.store.replace_listings(feed, [listing("evt-sf", name=entry()["title"], start=iso(24),
                           location={"city": "San Francisco", "country": "US", "region": "CA"})])
        before = self.catalog.build()["events"][0]
        self.store.import_research(collection())
        payload = self.catalog.build()
        self.assertEqual(len(payload["events"]), 2)
        after = next(ev for ev in payload["events"] if ev["id"] == "evt-sf")
        self.assertEqual(before, after)
        self.assertEqual(after["area"], "bay")

    def test_validation_rejects_malformed_dates_urls_ids_and_nested_secrets(self):
        bad = [entry(id="../invalid"), entry(start_at="2026-09-30"), entry(end_at=iso(23)),
               entry(start_at="2026-09-30T12:00:00"), entry(url="https://user:pass@example.test/x"),
               entry(url="javascript:alert(1)"), entry(timezone="Not/AZone"), entry(schedule_status="maybe"),
               entry(start_at=None, end_at=None), entry(sources=[{"label": "x", "url": "file:///tmp/x"}]),
               entry(session_key="secret")]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.store.import_research(collection(item))
        with self.assertRaises(ValueError):
            validate_collections(collection(entry(), copy.deepcopy(entry())))
        self.assertEqual(self.store.research_collections(), [])

    def test_cli_validates_before_creating_state_and_imports(self):
        root = temp_dir(self)
        path, state = root / "fixture.json", root / "state"
        path.write_text(json.dumps(collection(entry(url="file:///tmp/x"))))
        command = [sys.executable, "-m", "events.research", "import", str(path), "--state-dir", str(state)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(state.exists())
        path.write_text(json.dumps(collection()))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"collections": 1, "entries": 1, "changed": 1})


class MumbaiLocationTest(unittest.TestCase):
    def test_explicit_locations_and_coordinates(self):
        for location in ({"city": "Mumbai", "country": "IN"}, {"city": "Navi Mumbai", "region": "Maharashtra"},
                         {"city": "Bandra", "address": "Bandra, Mumbai, India"},
                         {"lat": 19.0607, "lng": 72.8508}, {"address": "1 Example Road, Mumbai, India"}):
            with self.subTest(location=location):
                self.assertEqual(areas.classify({"location": location}), ("mumbai", None))

    def test_india_timezone_and_mumbai_named_sf_venues_are_not_mumbai(self):
        self.assertEqual(areas.classify({"timezone": "Asia/Kolkata", "name": "Mumbai meetup"}), ("elsewhere", None))
        self.assertEqual(areas.classify({"location": {"venue": "Mumbai Cafe", "city": "San Francisco"}}), ("bay", "sf"))
        self.assertEqual(areas.classify({"location": {"type": "online", "city": "Mumbai"}}), ("online", None))

    def test_url_canonicalization_preserves_meaningful_distinctions(self):
        self.assertEqual(canonical_url("https://lu.ma/example/?utm_source=x"), "https://luma.com/example")
        self.assertNotEqual(canonical_url("https://example.test/schedule#talk-one"),
                            canonical_url("https://example.test/schedule#talk-two"))
        self.assertNotEqual(canonical_url("https://example.test/event?id=1"),
                            canonical_url("https://example.test/event?id=2"))

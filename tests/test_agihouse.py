import unittest

from events.sources import agihouse

from .helpers import FakeRequest, response


def raw_event(**fields) -> dict:
    """An event as the agihouse.org events API returns it."""
    ev = {
        "id": "a1b2c3", "slug": "agents-hackathon", "title": "AI Agents  Hackathon", "type": "Hackathon",
        "startTime": "2026-10-10T17:00:00.000Z", "endTime": "2026-10-11T03:00:00.000Z", "timezone": "America/Los_Angeles",
        "coverImageUrl": "https://cdn.agihouse.test/agents.png", "status": "published", "privacy": "public",
        "dateTbd": False,
        "location": {"name": "AGI House SF", "address": "170 St Germain Ave", "city": "San Francisco, CA 94114",
                     "isVirtual": False},
    }
    ev.update(fields)
    return ev


class NormalizeEventTest(unittest.TestCase):
    def test_published_public_event(self):
        ev = agihouse.normalize_event(raw_event())
        self.assertEqual(ev["id"], "agi-agents-hackathon")
        self.assertEqual(ev["source"], "agihouse")
        self.assertEqual(ev["name"], "AI Agents Hackathon")
        self.assertEqual(ev["url"], "https://www.agihouse.org/events/agents-hackathon")
        self.assertEqual((ev["start_at"], ev["end_at"]), ("2026-10-10T17:00:00Z", "2026-10-11T03:00:00Z"))
        self.assertEqual(ev["timezone"], "America/Los_Angeles")
        self.assertEqual(ev["cover_url"], "https://cdn.agihouse.test/agents.png")
        self.assertEqual(ev["location"], {"type": "offline", "venue": "AGI House SF", "address": "170 St Germain Ave",
                                          "city": "San Francisco", "neighborhood": None, "region": "CA", "country": None,
                                          "lat": None, "lng": None})
        self.assertEqual(ev["presenter"]["id"], "agihouse")
        self.assertEqual(ev["tags"], ["Hackathon"])
        self.assertIsNone(ev["going_status"])

    def test_status_privacy_and_tbd_filtering(self):
        self.assertIsNotNone(agihouse.normalize_event(raw_event(status=None, privacy=None)))
        for fields in ({"status": "draft"}, {"status": "cancelled"}, {"privacy": "private"}, {"privacy": "unlisted"},
                       {"dateTbd": True}, {"startTime": None}, {"startTime": "TBD"}, {"slug": None, "id": None}):
            with self.subTest(fields=fields):
                self.assertIsNone(agihouse.normalize_event(raw_event(**fields)))
        self.assertIsNone(agihouse.normalize_event("agents-hackathon"))

    def test_city_and_region_split(self):
        cases = {"San Francisco, CA 94114": ("San Francisco", "CA"), "Palo Alto CA": ("Palo Alto", "CA"),
                 "Hillsborough": ("Hillsborough", None), "CA": (None, "CA"), "": (None, None),
                 "New York, NY": ("New York, NY", None)}
        for city, expected in cases.items():
            with self.subTest(city=city):
                loc = agihouse.normalize_event(raw_event(location={"city": city}))["location"]
                self.assertEqual((loc["city"], loc["region"]), expected)

    def test_virtual_events_and_missing_fields(self):
        ev = agihouse.normalize_event(raw_event(slug=None, title=" ", type=None, coverImageUrl="", timezone=None,
                                                location={"isVirtual": True}))
        self.assertEqual(ev["id"], "agi-a1b2c3", "the id stands in for a missing slug")
        self.assertEqual(ev["name"], "Untitled event")
        self.assertEqual(ev["tags"], [])
        self.assertIsNone(ev["cover_url"])
        self.assertIsNone(ev["timezone"])
        self.assertEqual(ev["location"]["type"], "online")
        self.assertEqual(agihouse.normalize_event(raw_event(location="170 St Germain Ave"))["location"]["type"], "offline")


class ClientTest(unittest.TestCase):
    def test_events_filters_and_normalizes(self):
        body = {"events": [raw_event(), raw_event(slug="secret", privacy="private"), "junk", raw_event(slug="demo-day")]}
        fake = FakeRequest(lambda url, **kw: response(body))
        events = agihouse.AgiHouseClient(fake).events()
        self.assertEqual([e["id"] for e in events], ["agi-agents-hackathon", "agi-demo-day"])
        url, kwargs = fake.calls[0]
        self.assertEqual(url, agihouse.API)
        self.assertEqual(kwargs["headers"]["origin"], "https://www.agihouse.org")

    def test_non_json_answers_raise_a_clear_error(self):
        fake = FakeRequest(lambda url, **kw: response("<html>502 Bad Gateway</html>", content_type="text/html"))
        with self.assertRaisesRegex(ValueError, "AGI House answered with something that is not JSON"):
            agihouse.AgiHouseClient(fake).events()

    def test_unexpected_payloads_yield_nothing(self):
        for body in ([raw_event()], {"events": None}, {}):
            with self.subTest(body=body):
                self.assertEqual(agihouse.AgiHouseClient(FakeRequest(lambda url, **kw: response(body))).events(), [])


if __name__ == "__main__":
    unittest.main()

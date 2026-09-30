import base64
import unittest

from events import net
from events.sources import partiful
from events.sources.partiful import PartifulClient, PartifulError

from .helpers import NOW, FakeClock, FakeRequest, jwt, response

FEED_URL = "https://calendars.partiful.com/getCalendar?id=5tX0pQ"


def api_event(**fields) -> dict:
    """An event object shaped like the ones Partiful's Firebase callables return."""
    ev = {
        "id": "AbC123xyz",
        "title": "  Rooftop   Party ",
        "startDate": "2026-10-10T02:00:00.000Z",
        "endDate": 1791608400000,  # epoch ms
        "timezone": "America/Los_Angeles",
        "location": {"name": "Secret Garden", "address": "123 Valencia St"},
        "hosts": [{"name": "Ann Lee", "avatarUrl": "https://cdn.partiful.test/ann.jpg"}, "Bob", {"displayName": "Cy"}, {}],
        "guest": {"status": "GOING"},
        "guestStatusCounts": {"GOING": 42, "MAYBE": 3},
        "image": {"upload": {"path": "events/AbC123xyz/cover image.png", "contentType": "image/png"}},
    }
    ev.update(fields)
    return ev


class NormalizeEventTest(unittest.TestCase):
    def test_full_event(self):
        ev = partiful.normalize_event(api_event())
        self.assertEqual(ev["id"], "pf-AbC123xyz")
        self.assertEqual(ev["source"], "partiful")
        self.assertEqual(ev["name"], "Rooftop Party")
        self.assertEqual(ev["url"], "https://partiful.com/e/AbC123xyz")
        self.assertEqual((ev["start_at"], ev["end_at"]), ("2026-10-10T02:00:00Z", "2026-10-10T05:00:00Z"))
        self.assertEqual(ev["timezone"], "America/Los_Angeles")
        self.assertEqual(ev["location"]["type"], "offline")
        self.assertEqual(ev["location"]["address"], "Secret Garden")
        self.assertEqual(ev["hosts"], [{"name": "Ann Lee", "avatar_url": "https://cdn.partiful.test/ann.jpg"},
                                       {"name": "Bob", "avatar_url": None}, {"name": "Cy", "avatar_url": None}])
        self.assertEqual(ev["going_status"], "going")
        self.assertEqual(ev["guest_count"], 42)
        self.assertEqual(ev["cover_url"], "https://partiful.imgix.net/events/AbC123xyz/cover%20image.png?w=800&h=800&fit=clip")
        self.assertIsNone(ev["presenter"])
        self.assertIsNone(ev["ticket"])

    def test_going_statuses(self):
        cases = {"GOING": "going", "yes": "yes", "APPROVED": "approved", "host": "host", "HOSTING": "hosting",
                 "MAYBE": None, "DECLINED": None, "INVITED": None, "WAITLIST": None, "": None}
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(partiful.normalize_event(api_event(guest={"status": status}))["going_status"], expected)
        self.assertEqual(partiful.normalize_event(api_event(guest=None, rsvpStatus="GOING"))["going_status"], "going")
        self.assertEqual(partiful.normalize_event(api_event(guest={}, myStatus="host"))["going_status"], "host")

    def test_guest_counts(self):
        for counts, expected in (({"GOING": 0}, None), ({"MAYBE": 5}, None), ({"GOING": "12"}, None), (None, None),
                                 ("42", None), ({"GOING": 7}, 7)):
            with self.subTest(counts=counts):
                self.assertEqual(partiful.normalize_event(api_event(guestStatusCounts=counts))["guest_count"], expected)

    def test_host_fallbacks(self):
        self.assertEqual(partiful.normalize_event(api_event(hosts=None, hostName=" Dana  Park "))["hosts"],
                         [{"name": "Dana Park", "avatar_url": None}])
        self.assertEqual(partiful.normalize_event(api_event(hosts=[], hostNames=["Eve"]))["hosts"],
                         [{"name": "Eve", "avatar_url": None}])
        self.assertEqual(partiful.normalize_event(api_event(hosts={"name": "not a list"}))["hosts"], [])

    def test_location_shapes(self):
        cases = [({"formattedAddress": "1 Market St, San Francisco"}, "1 Market St, San Francisco"),
                 ("  Dolores   Park ", "Dolores Park"), ({}, None), (None, None)]
        for location, expected in cases:
            with self.subTest(location=location):
                ev = partiful.normalize_event(api_event(location=location))
                self.assertEqual(ev["location"]["address"], expected)
                self.assertEqual(ev["location"]["type"], "offline" if expected else "unknown")
        self.assertEqual(partiful.normalize_event(api_event(location=None, locationName="The Lab"))["location"]["address"], "The Lab")

    def test_alternate_keys_and_timestamps(self):
        ev = partiful.normalize_event({"eventId": "x/y z", "name": "Dinner", "startTime": {"_seconds": 1790733600},
                                       "timeZone": "America/New_York", "posterUrl": "https://cdn.test/p.jpg"})
        self.assertEqual(ev["id"], "pf-x/y z")
        self.assertEqual(ev["url"], "https://partiful.com/e/x/y%20z")
        self.assertEqual(ev["start_at"], "2026-09-30T02:00:00Z")
        self.assertEqual(ev["timezone"], "America/New_York")
        self.assertEqual(ev["cover_url"], "https://cdn.test/p.jpg")
        self.assertIsNone(ev["end_at"])
        self.assertEqual(partiful.normalize_event(api_event(title=""))["name"], "Untitled event")
        self.assertIsNone(partiful.normalize_event(api_event(timezone=5))["timezone"])

    def test_missing_id_or_start(self):
        self.assertIsNone(partiful.normalize_event(api_event(id=None)))
        self.assertIsNone(partiful.normalize_event(api_event(startDate="TBD")))
        self.assertIsNone(partiful.normalize_event({}))


class ImageUrlTest(unittest.TestCase):
    def test_variants(self):
        cases = [
            ("https://cdn.partiful.test/a.jpg", "https://cdn.partiful.test/a.jpg"),
            ("", None),
            (None, None),
            (7, None),
            ({"upload": {"path": "u/1/x y.png"}}, "https://partiful.imgix.net/u/1/x%20y.png?w=800&h=800&fit=clip"),
            ({"poster": {"name": "neon party.gif"}}, "https://partiful-posters.imgix.net/neon%20party.gif?fit=max&w=800&h=800"),
            ({"url": "https://cdn.partiful.test/b.jpg"}, "https://cdn.partiful.test/b.jpg"),
            ({"url": "https://firebasestorage.googleapis.com/v0/b/getpartiful.appspot.com/o/x.png"}, None),
            ({"url": "https://firebasestorage.googleapis.com/o/x.png", "image": {"upload": {"path": "y.png"}}},
             "https://partiful.imgix.net/y.png?w=800&h=800&fit=clip"),
            ({"poster": {"url": "https://cdn.partiful.test/poster.jpg"}}, "https://cdn.partiful.test/poster.jpg"),
            ({"upload": {"path": ""}, "url": "https://cdn.partiful.test/c.jpg"}, "https://cdn.partiful.test/c.jpg"),
            ({"upload": "not a dict"}, None),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(partiful.image_url(value), expected)


class FindEventsTest(unittest.TestCase):
    def test_walks_nested_results(self):
        first, second = api_event(), api_event(id="Zz9", title="Brunch", guest={"status": "MAYBE"})
        result = {"sections": [{"title": "Upcoming", "events": [first, {"event": second}]},
                               {"title": "Empty", "items": []}], "meta": {"count": 2}}
        found = []
        partiful.find_events(result, found)
        self.assertEqual([e["id"] for e in found], ["AbC123xyz", "Zz9"])

    def test_requires_title_start_and_id(self):
        found = []
        partiful.find_events([{"title": "x", "startDate": "2026-10-10T02:00:00Z"}, {"id": "a", "startDate": "2026"},
                              {"id": "b", "title": "no start"}], found)
        self.assertEqual(found, [])


class FeedTest(unittest.TestCase):
    FEED = "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//Partiful//EN",
        "BEGIN:VEVENT", "UID:AbC123xyz@partiful.com", "DTSTART:20261010T020000Z", "DTEND:20261010T050000Z",
        "SUMMARY:Rooftop Party | Partiful", "URL:https://partiful.com/e/AbC123xyz?c=invite",
        r"LOCATION:Secret Garden\, Mission District", "STATUS:CONFIRMED", "END:VEVENT",
        "BEGIN:VEVENT", "UID:evt-2@partiful.com", "DTSTART;TZID=America/Los_Angeles:20261012T180000",
        "SUMMARY:Game Night", r"DESCRIPTION:RSVP here: https://partiful.com/e/GameNight_2-x\nSee you!",
        "STATUS:TENTATIVE", "END:VEVENT",
        "BEGIN:VEVENT", "UID:no link here!", "DTSTART;VALUE=DATE:20261020", "SUMMARY:  Offsite  ", "END:VEVENT",
        "BEGIN:VEVENT", "UID:no-start", "SUMMARY:Ghost", "END:VEVENT",
        "END:VCALENDAR",
    ])

    def test_parse_feed_derives_ids_from_links(self):
        party, games, offsite = partiful.parse_feed(self.FEED)
        self.assertEqual(party["id"], "pf-AbC123xyz", "same id as the API copy, so the two merge")
        self.assertEqual(party["url"], "https://partiful.com/e/AbC123xyz")
        self.assertEqual(party["name"], "Rooftop Party")
        self.assertEqual(party["location"]["address"], "Secret Garden, Mission District")
        self.assertEqual(party["going_status"], "going")
        self.assertEqual((party["start_at"], party["end_at"]), ("2026-10-10T02:00:00Z", "2026-10-10T05:00:00Z"))

        self.assertEqual(games["id"], "pf-GameNight_2-x", "the link can come from the description")
        self.assertEqual(games["url"], "https://partiful.com/e/GameNight_2-x")
        self.assertIsNone(games["going_status"], "tentative RSVPs are not going")
        self.assertEqual(games["timezone"], "America/Los_Angeles")
        self.assertEqual(games["start_at"], "2026-10-13T01:00:00Z")

        self.assertEqual(offsite["id"], "pf-nolinkhere")
        self.assertEqual(offsite["url"], "https://partiful.com/events")
        self.assertEqual(offsite["name"], "Offsite")
        self.assertTrue(offsite["all_day"])
        self.assertEqual(offsite["location"]["type"], "unknown")

    def test_parse_feed_rejects_non_calendars(self):
        with self.assertRaises(ValueError):
            partiful.parse_feed("<html>Not found</html>")

    def test_normalize_feed_url(self):
        self.assertEqual(partiful.normalize_feed_url("webcal://calendars.partiful.com/getCalendar?id=5tX0pQ"), FEED_URL)
        self.assertEqual(partiful.normalize_feed_url(f"  {FEED_URL} "), FEED_URL)
        for bad in ("", None, "https://partiful.com/events", "http://calendars.partiful.com/getCalendar?id=1",
                    "https://calendars.partiful.com.evil.example/getCalendar", "calendars.partiful.com/getCalendar"):
            with self.subTest(url=bad):
                with self.assertRaises(ValueError):
                    partiful.normalize_feed_url(bad)

    def test_parse_import_payload(self):
        self.assertEqual(partiful.parse_import_payload({"refresh_token": "  AMf-token ", "uid": "u1"}),
                         {"uid": "u1", "refresh_token": "AMf-token"})
        self.assertEqual(partiful.parse_import_payload({"refresh_token": "t", "uid": 5})["uid"], None)
        for bad in (None, "token", ["t"], {}, {"refresh_token": "   "}, {"refresh_token": 5}):
            with self.subTest(payload=bad):
                with self.assertRaises(ValueError):
                    partiful.parse_import_payload(bad)

    def test_jwt_claims(self):
        self.assertEqual(partiful.jwt_claims(jwt({"user_id": "u1", "name": "Jo"})), {"user_id": "u1", "name": "Jo"})
        listy = "h." + base64.urlsafe_b64encode(b"[1, 2]").decode().rstrip("=") + ".s"
        for token in (None, "", "garbage", "a.%%%.c", "a.bm90IGpzb24.c", listy):
            with self.subTest(token=token):
                self.assertEqual(partiful.jwt_claims(token), {})


class PartifulClientTest(unittest.TestCase):
    ID_TOKEN = jwt({"user_id": "uid-1", "name": "Jordan Rivera", "exp": NOW + 3600})

    def setUp(self):
        self.clock = FakeClock()
        self.answer: object = response({"id_token": self.ID_TOKEN, "refresh_token": "refresh-2", "expires_in": "3600",
                                        "user_id": "uid-1", "token_type": "Bearer"})
        self.fake = FakeRequest(lambda url, **kw: self.answer)
        self.client = PartifulClient(self.fake, self.clock)

    def test_valid_token_is_reused(self):
        account = {"uid": "uid-1", "refresh_token": "refresh-1", "id_token": "tok", "expires_at": NOW + 61}
        self.assertIs(self.client.id_token(account), account)
        self.assertEqual(self.fake.calls, [])

    def test_refreshes_when_expiring_and_caches_the_result(self):
        account = {"uid": "uid-1", "refresh_token": "refresh-1", "id_token": "tok", "expires_at": NOW + 60, "added_at": 5}
        fresh = self.client.id_token(account)
        url, kwargs = self.fake.calls[0]
        self.assertEqual(url, partiful.TOKEN_URL)
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["json_body"], {"grant_type": "refresh_token", "refresh_token": "refresh-1"})
        self.assertEqual(kwargs["headers"]["referer"], "https://partiful.com/")
        self.assertEqual(fresh, {"uid": "uid-1", "refresh_token": "refresh-2", "id_token": self.ID_TOKEN,
                                 "name": "Jordan Rivera", "expires_at": NOW + 3600, "added_at": 5})
        self.assertEqual(account["id_token"], "tok", "the input is not mutated")
        self.assertIs(self.client.id_token(fresh), fresh)
        self.assertEqual(len(self.fake.calls), 1)
        self.clock.advance(3600 - 59)
        self.assertIsNot(self.client.id_token(fresh), fresh)
        self.assertEqual(len(self.fake.calls), 2)

    def test_uid_and_name_fall_back_to_claims_and_account(self):
        self.answer = response({"id_token": jwt({"user_id": "from-claims"})})
        fresh = self.client.id_token({"uid": None, "refresh_token": "r", "name": "Saved Name"})
        self.assertEqual(fresh["uid"], "from-claims")
        self.assertEqual(fresh["name"], "Saved Name")
        self.assertEqual(fresh["refresh_token"], "r", "an absent new refresh token keeps the old one")
        self.assertEqual(fresh["expires_at"], NOW + 3600)

    def test_dead_logins_are_detected(self):
        for marker in partiful.DEAD_LOGIN_MARKERS:
            with self.subTest(marker=marker):
                body = '{"error": {"code": 400, "message": "%s", "status": "INVALID_ARGUMENT"}}' % marker
                self.answer = net.HttpError(400, body, partiful.TOKEN_URL)
                with self.assertRaises(PartifulError) as ctx:
                    self.client.id_token({"uid": "uid-1", "refresh_token": "r"})
                self.assertTrue(ctx.exception.login_expired)
                self.assertIn("bookmarklet", str(ctx.exception))

    def test_other_refresh_failures_are_not_dead_logins(self):
        for answer, message in ((net.HttpError(503, "Service Unavailable", partiful.TOKEN_URL), "token refresh failed (503)"),
                                (net.NetError("could not reach securetoken.googleapis.com"), "could not reach"),
                                (response({"access_token": "x"}), "no ID token"),
                                (response(["unexpected"]), "no ID token")):
            with self.subTest(message=message):
                self.answer = answer
                with self.assertRaises(PartifulError) as ctx:
                    self.client.id_token({"uid": "uid-1", "refresh_token": "r"})
                self.assertFalse(ctx.exception.login_expired)
                self.assertIn(message, str(ctx.exception))

    def test_non_json_answers_are_partiful_errors(self):
        self.answer = response("<html>Wi-Fi login</html>", content_type="text/html")
        with self.assertRaisesRegex(PartifulError, "not JSON") as ctx:
            self.client.id_token({"uid": "uid-1", "refresh_token": "r"})
        self.assertFalse(ctx.exception.login_expired)
        with self.assertRaisesRegex(PartifulError, "getMyFollowedEvents answered with something that is not JSON"):
            self.client.call({"uid": "uid-1", "id_token": "t"}, "getMyFollowedEvents")

    def test_call_posts_to_the_callable(self):
        self.answer = response({"result": {"data": [api_event()]}})
        account = {"uid": "uid-1", "id_token": "tok-1"}
        self.assertEqual(self.client.call(account, "getMyFollowedEvents", {"limit": 5}), {"data": [api_event()]})
        url, kwargs = self.fake.calls[0]
        self.assertEqual(url, "https://api.partiful.com/getMyFollowedEvents")
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["json_body"], {"data": {"params": {"limit": 5}, "userId": "uid-1"}})
        self.assertEqual(kwargs["headers"]["authorization"], "Bearer tok-1")
        self.answer = response({"result": None})
        self.assertIsNone(self.client.call(account, "getMyFollowedEvents"))
        # A different shape means the API changed; failing keeps the feed's events instead of wiping them.
        for shape in (["not", "a", "dict"], {"data": []}):
            with self.subTest(shape=shape):
                self.answer = response(shape)
                with self.assertRaises(partiful.PartifulError):
                    self.client.call(account, "getMyFollowedEvents")

    def test_call_errors(self):
        for answer, expired in ((net.HttpError(401, "{}", "https://api.partiful.com/x"), True),
                                (net.HttpError(500, "{}", "https://api.partiful.com/x"), False),
                                (net.NetError("reset"), False)):
            with self.subTest(answer=answer):
                self.answer = answer
                with self.assertRaises(PartifulError) as ctx:
                    self.client.call({"uid": "u", "id_token": "t"}, "getMyUpcomingEventsForHomePage")
                self.assertEqual(ctx.exception.login_expired, expired)

    def test_events_merge_duplicates_and_keep_going(self):
        self.answer = response({"result": {"sections": [
            {"events": [api_event(guest={"status": "MAYBE"}), api_event(id="Zz9", title="Brunch", startDate="nope")]},
            {"events": [api_event(guest={"status": "GOING"}), api_event(id="Yy8", title="Picnic", guest=None)]}]}})
        events = self.client.events({"uid": "uid-1", "id_token": "t"}, "getMyUpcomingEventsForHomePage")
        self.assertEqual([(e["id"], e["going_status"]) for e in events], [("pf-AbC123xyz", "going"), ("pf-Yy8", None)])

    def test_feed(self):
        self.answer = response(FeedTest.FEED, content_type="text/calendar; charset=utf-8")
        self.assertEqual(len(self.client.feed(FEED_URL)), 3)
        self.assertEqual(self.fake.calls[0][0], FEED_URL)
        self.answer = net.HttpError(404, "", FEED_URL)
        with self.assertRaisesRegex(ValueError, "could not read the feed"):
            self.client.feed(FEED_URL)


if __name__ == "__main__":
    unittest.main()

"""Sundry Mag's weekly post, read from made-up posts in the shape Substack serves."""
import json
import unittest
from datetime import date

from events import net
from events.catalog import Catalog
from events.sources import sundry
from events.sync import SUNDRY_FEED, Sync

from .helpers import (FakeAgiHouse, FakeClock, FakeLuma, FakePartiful, FakeRequest, FakeSundry, make_settings, make_store, response,
                      temp_dir)

PUBLISHED = date(2026, 9, 28)  # the tests' clock reads Tuesday 2026-09-29, 12:00 UTC


def item(title: str, link: str | None, time: str, place: str, cost: str) -> str:
    head = f'<a href="{link}">{title}</a>' if link else title
    return (f"<li><p>{head}</p><ul><li><p>⏰ Time: {time}</p></li><li><p>📍 Location: {place}</p></li>"
            f"<li><p>💸 Cost: {cost}</p></li></ul></li>")


def body(*days: tuple[str, list[str]]) -> str:
    cover = '<div class="captioned-image-container"><figure><a href="https://cdn.fixture.test/cover.png"></a></figure></div>'
    lists = "".join(f"<h3>{heading}</h3><ul>{''.join(items)}</ul>" for heading, items in days)
    return cover + lists + "<p>Did this list miss something? Comment below!</p>"


WEEK = body(
    ("Monday, September 28, 2026", [
        item("Fixture Run Club: Harbour Loop", "https://www.instagram.com/p/FixtureRun01/?igsh=abc&amp;img_index=2",
             "6:00PM", '<a href="https://share.google/FixtureMap01">Fixture Quay Cafe</a>', "Free Entry"),
    ]),
    ("Tuesday, September 29, 2026", [
        item("Fixture Film Club: <em>Made-Up Classic</em> (1959)", "https://www.quicket.co.za/events/100001-fixture-film/#/",
             "6:15-8:15PM", '<a href="https://share.google/FixtureMap02">Fixture House</a>',
             '<a href="https://www.quicket.co.za/events/100001-fixture-film/?ref=organiser-profile#/">R40</a>'),
        item("Fixture Late Session", "https://www.instagram.com/p/FixtureLate1/", "9:00PM-2:00AM",
             '<a href="https://www.instagram.com/fixture_basement/?hl=en">Fixture Basement</a>',
             '<a href="https://tickets.fixture.test/late">From R80</a>'),
    ]),
    ("Saturday, October 3rd", [
        item("Fixture Makers Market", None, "10:00AM-4:00PM (also Sunday)", "Fixture Yard", "R20, Kids Free"),
        item("Fixture Supper Club", "https://supper.fixture.test/", "By Reservation", "Fixture Kitchen", "By Approval"),
    ]),
)


def post(slug: str = "fixture-week", **fields) -> dict:
    values = {"slug": slug, "title": "What to Do In Cape Town This Week: Week of September 28-October 4, 2026",
              "post_date": "2026-09-28T05:00:00.000Z", "audience": "everyone", "wordcount": 80, "body_html": WEEK}
    values.update(fields)
    return values


def by_name(events: list[dict]) -> dict[str, dict]:
    return {ev["name"]: ev for ev in events}


class ParsePostTest(unittest.TestCase):
    def setUp(self):
        self.events = by_name(sundry.parse_post(WEEK, PUBLISHED))

    def test_every_item_becomes_a_cape_town_listing(self):
        self.assertEqual(len(self.events), 5)
        run = self.events["Fixture Run Club: Harbour Loop"]
        self.assertEqual(run["source"], "sundry")
        self.assertRegex(run["id"], r"^sundry-[0-9a-f]{16}$")
        self.assertEqual(run["url"], "https://www.instagram.com/p/FixtureRun01/", "tracking values are dropped")
        self.assertEqual((run["start_at"], run["end_at"], run["all_day"]), ("2026-09-28T16:00:00Z", None, False))
        self.assertEqual(run["timezone"], "Africa/Johannesburg")
        self.assertEqual(run["location"], {"type": "offline", "venue": "Fixture Quay Cafe", "address": None, "city": "Cape Town",
                                           "neighborhood": None, "region": "Western Cape", "country": "ZA", "lat": None, "lng": None})
        self.assertTrue(run["ticket"]["free"])
        self.assertEqual(run["links"], [{"label": "Map", "url": "https://share.google/FixtureMap01"}])
        self.assertIsNone(run["description"])

    def test_ranges_prices_and_links(self):
        film = self.events["Fixture Film Club: Made-Up Classic (1959)"]
        self.assertEqual((film["start_at"], film["end_at"]), ("2026-09-29T16:15:00Z", "2026-09-29T18:15:00Z"))
        self.assertEqual(film["url"], "https://www.quicket.co.za/events/100001-fixture-film/")
        self.assertEqual((film["ticket"]["free"], film["ticket"]["price_cents"], film["ticket"]["currency"]), (False, 4000, "zar"))
        self.assertEqual(film["links"], [{"label": "Map", "url": "https://share.google/FixtureMap02"}],
                         "a ticket link that is the event's own page is not repeated")
        late = self.events["Fixture Late Session"]
        self.assertEqual((late["start_at"], late["end_at"]), ("2026-09-29T19:00:00Z", "2026-09-30T00:00:00Z"))
        self.assertEqual(late["links"], [{"label": "Tickets", "url": "https://tickets.fixture.test/late"},
                                         {"label": "Venue", "url": "https://www.instagram.com/fixture_basement/"}])
        self.assertEqual(late["description"], "Cost: From R80")

    def test_what_the_fields_cannot_hold_is_kept_in_words(self):
        market = self.events["Fixture Makers Market"]
        self.assertEqual(market["start_at"], "2026-10-03T08:00:00Z", "a heading without a year takes the post's")
        self.assertEqual(market["url"], sundry.SITE)
        self.assertEqual(market["description"], "Time: 10:00AM-4:00PM (also Sunday)\nCost: R20, Kids Free")
        supper = self.events["Fixture Supper Club"]
        self.assertEqual((supper["all_day"], supper["start_at"], supper["end_at"]),
                         (True, "2026-10-02T22:00:00Z", "2026-10-03T22:00:00Z"))
        self.assertEqual(supper["description"], "Time: By Reservation\nCost: By Approval")
        self.assertTrue(supper["ticket"]["approval"])

    def test_same_link_on_two_days_is_two_events_and_a_repeat_is_one(self):
        again = item("Fixture Run Club: Harbour Loop", "https://www.instagram.com/p/FixtureRun01/", "6:00PM", "Fixture Quay Cafe", "Free")
        events = sundry.parse_post(body(("Monday, September 28, 2026", [again, again]), ("Monday, October 5, 2026", [again])), PUBLISHED)
        self.assertEqual([e["start_at"] for e in events], ["2026-09-28T16:00:00Z", "2026-10-05T16:00:00Z"])
        self.assertEqual(len({e["id"] for e in events}), 2)

    def test_nothing_before_a_dated_heading_and_no_crash_on_junk(self):
        self.assertEqual(sundry.parse_post("<ul>" + item("Fixture Orphan", None, "6PM", "Somewhere", "Free") + "</ul>", PUBLISHED), [])
        self.assertEqual(sundry.parse_post("", PUBLISHED), [])
        self.assertEqual(sundry.parse_post("<h3>Sponsors</h3><ul><li><p>Thanks</p></li></ul>", PUBLISHED), [])

    def test_a_december_post_can_reach_into_january(self):
        events = sundry.parse_post(body(("Friday, January 1", [item("Fixture New Year Swim", None, "7:00AM", "Fixture Beach", "Free")])),
                                   date(2026, 12, 28))
        self.assertEqual(events[0]["start_at"], "2027-01-01T05:00:00Z")


class ParseTimeTest(unittest.TestCase):
    def test_clock_times(self):
        cases = {
            "7:00PM": (19 * 60, None), "10:00AM": (600, None), "6:15-8:15PM": (1095, 1215), "11:00-1:00PM": (660, 780),
            "9:30-11:00AM": (570, 660), "10:00-2:00AM": (22 * 60, 120), "9:00-12:00AM": (21 * 60, 0),
            "10:00AM-5:00PM": (600, 1020), "5:30PM-7:30": (1050, 1170), "10:00AM – 12:00PM": (600, 720),
            "18:00-20:00": (1080, 1200), "6:30-8:30": (1110, 1230), "7:00PM for 7:30PM": (19 * 60, None),
            "Doors at 6:00PM, Show at 8:00PM": (18 * 60, None), "Performances @ 6:30 & 7:30PM": (1110, None),
            "8:00PM - Late": (20 * 60, None), "From 1:00PM (Also Saturday & Sunday)": (780, None), "6PM": (18 * 60, None),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(sundry.parse_time(text)[:2], expected)

    def test_no_clock_time(self):
        for text in ("", "Various, Sept 4-6th", "By Reservation", "All Day", "Store Hours", "25:00", "13:00PM"):
            with self.subTest(text=text):
                self.assertEqual(sundry.parse_time(text)[:2], (None, None))

    def test_leftover_words(self):
        self.assertEqual(sundry.parse_time("6:15-8:15PM")[2], "")
        self.assertEqual(sundry.parse_time("10:00AM-4:00PM (also Sunday)")[2], "(also Sunday)")


class ParseCostTest(unittest.TestCase):
    def test_costs(self):
        cost = sundry.parse_cost
        self.assertEqual((cost("R1,500")["price_cents"], cost("R 250 per Ticket")["price_cents"], cost("From R99.50")["price_cents"]),
                         (150000, 25000, 9950))
        for text in ("Free", "Free Entry", "FREE", "Free with Registration", "Free Before 9PM, R50 After"):
            self.assertEqual((cost(text)["free"], cost(text)["price_cents"]), (True, None), text)
        self.assertEqual((cost("Donation Based")["free"], cost("Donation Based")["price_cents"]), (False, None))
        self.assertTrue(cost("By Application")["approval"])
        for text in ("", "N/A", "Varies"):
            self.assertIsNone(cost(text))


class SessionTest(unittest.TestCase):
    def test_accepts_the_value_or_the_cookie_header(self):
        value = "s%3AFixtureSessionValue0123456789.abcDEF"
        self.assertEqual(sundry.parse_session(f"  {value} "), value)
        self.assertEqual(sundry.parse_session(f"substack.sid={value}"), value)
        self.assertEqual(sundry.parse_session(f"ajs_id=1; substack.sid={value}; other=2"), value)
        for bad in ("", "short", "has spaces in the middle of it all", "<script>alert(1)</script>xxxxxxxxxxxx"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sundry.parse_session(bad)


def substack(posts: dict[str, dict], archive: list[dict] | None = None):
    listed = archive if archive is not None else [{"slug": p["slug"], "title": p["title"], "post_date": p["post_date"]}
                                                  for p in posts.values()]

    def handler(url: str, **kwargs):
        if url.endswith("/api/v1/archive"):
            return response({"posts": listed})
        slug = url.rsplit("/", 1)[1]
        return response(posts[slug]) if slug in posts else net.HttpError(404, "not found", url)
    return FakeRequest(handler)


class ClientTest(unittest.TestCase):
    def test_reads_the_two_newest_weekly_posts_with_the_session(self):
        older = post("fixture-older", post_date="2026-09-21T05:00:00.000Z",
                     body_html=body(("Monday, September 21, 2026", [item("Fixture Quiz", None, "7:00PM", "Fixture Pub", "R50")])))
        archive = [{"slug": "fixture-week", "title": post()["title"], "post_date": post()["post_date"]},
                   {"slug": "fixture-guide", "title": "5 Made-Up Vintage Shops in Cape Town", "post_date": "2026-09-25T05:00:00.000Z"},
                   {"slug": "fixture-older", "title": "Things to Do In Cape Town This Week: September 21-27", "post_date": older["post_date"]},
                   {"slug": "fixture-oldest", "title": "What to Do In Cape Town This Week: September 14-20", "post_date": "2026-09-14T05:00:00.000Z"}]
        fake = substack({"fixture-week": post(), "fixture-older": older}, archive)
        pull = sundry.SundryClient(fake).events("FIXTURE-SID")
        self.assertEqual(len(pull.events), 6)
        self.assertEqual(pull.locked, [])
        self.assertEqual([url for url, _ in fake.calls], [sundry.SITE + "/api/v1/archive", sundry.SITE + "/api/v1/posts/fixture-week",
                                                          sundry.SITE + "/api/v1/posts/fixture-older"])
        self.assertTrue(all(kw["headers"]["cookie"] == "substack.sid=FIXTURE-SID" for _, kw in fake.calls))

    def test_without_a_session_no_cookie_is_sent_and_a_cut_post_is_reported(self):
        opening = body(("Monday, September 28, 2026", [item("Fixture Run Club", None, "6:00PM", "Fixture Quay", "Free")]))
        fake = substack({"fixture-week": post(audience="only_subscribers", wordcount=1000, body_html=opening)})
        pull = sundry.SundryClient(fake).events()
        self.assertEqual([e["name"] for e in pull.events], ["Fixture Run Club"])
        self.assertEqual(pull.locked, [post()["title"]])
        self.assertTrue(all("cookie" not in kw["headers"] for _, kw in fake.calls))

    def test_a_subscriber_post_read_in_full_is_not_locked(self):
        full = post(audience="only_subscribers", wordcount=len(sundry.re.sub(r"<[^>]+>", " ", WEEK).split()))
        self.assertFalse(sundry.is_cut_short(full))
        self.assertFalse(sundry.is_cut_short(post(wordcount=5000)), "a public post is never cut")

    def test_clear_errors(self):
        cases = [(substack({}, []), "no weekly Cape Town post"),
                 (substack({"fixture-week": post(body_html="<p>Back next week!</p>")}), "found no events"),
                 (FakeRequest(lambda url, **kw: response("<html>busy</html>", content_type="text/html")), "not JSON"),
                 (FakeRequest(lambda url, **kw: net.HttpError(429, "slow down", url)), "Substack answered 429"),
                 (substack({"fixture-week": post(post_date=None)}, [{"slug": "fixture-week", "title": post()["title"]}]), "no date")]
        for fake, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(sundry.SundryError, message):
                sundry.SundryClient(fake).events()


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = make_store(self, self.clock)
        self.root = temp_dir(self)
        self.week = sundry.parse_post(WEEK, PUBLISHED)
        self.source = FakeSundry(self.week)

    def sync(self, **env: str) -> Sync:
        return Sync(self.store, make_settings(self.root, **{"EVENTS_SUNDRY": "1", **env}), luma=FakeLuma(), partiful=FakePartiful(),
                    agihouse=FakeAgiHouse(), sundry=self.source, clock=self.clock)

    def pull(self, sync: Sync) -> None:
        sync._run(self.store.feed(SUNDRY_FEED), 0)

    def test_enabled_adds_the_calendar_and_feed_and_disabled_removes_them(self):
        self.assertIn(SUNDRY_FEED, self.sync().reconcile())
        self.assertEqual(self.store.calendar("sundry")["name"], "Sundry Mag · Cape Town this week")
        self.assertEqual(self.store.feed(SUNDRY_FEED).kind, "sundry")
        self.sync(EVENTS_SUNDRY="0").reconcile()
        self.assertIsNone(self.store.calendar("sundry"))
        self.assertIsNone(self.store.feed(SUNDRY_FEED))

    def test_pull_stores_the_week_in_the_cape_town_area(self):
        sync = self.sync()
        sync.reconcile()
        self.store.set_secret("substack_session", {"sid": "FIXTURE-SID"})
        self.pull(sync)
        self.assertEqual(self.source.sessions, ["FIXTURE-SID"])
        events = Catalog(self.store, self.clock).build()["events"]
        self.assertEqual(len(events), 5)
        self.assertEqual({(e["area"], e["zone"], tuple(e["calendar_ids"])) for e in events}, {("cape-town", None, ("sundry",))})
        self.assertEqual(self.store.get_meta("sundry_locked"), "[]")
        self.assertEqual(self.store.feed(SUNDRY_FEED).item_count, 5)

    def test_a_cut_post_is_stored_the_first_time_but_never_replaces_a_fuller_week(self):
        sync = self.sync()
        sync.reconcile()
        opening = [e for e in self.week if e["start_at"] < "2026-09-30"]
        self.source.pull = sundry.Pull(opening, ["Fixture week"])
        self.pull(sync)
        self.assertEqual(self.store.feed(SUNDRY_FEED).item_count, 3)
        self.assertEqual(json.loads(self.store.get_meta("sundry_locked")), ["Fixture week"])
        self.source.pull = sundry.Pull(self.week)
        self.pull(sync)
        self.assertEqual(self.store.upcoming_count(SUNDRY_FEED), 4, "Tuesday evening onward, seen from Tuesday noon")
        self.source.pull = sundry.Pull(opening, ["Fixture week"])
        with self.assertRaisesRegex(ValueError, "only the opening of “Fixture week”"):
            self.pull(sync)
        self.assertEqual(self.store.upcoming_count(SUNDRY_FEED), 4)
        self.assertEqual(json.loads(self.store.get_meta("sundry_locked")), ["Fixture week"])

    def test_the_default_setting_is_on_with_a_six_hour_interval(self):
        from events.config import Settings

        settings = Settings.from_env({"EVENTS_STATE_DIR": str(self.root)})
        self.assertEqual((settings.sundry, settings.sundry_interval), (True, 6 * 3600))
        self.assertEqual(self.sync().intervals["sundry"], 6 * 3600)

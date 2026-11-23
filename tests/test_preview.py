"""Link previews, read from made-up pages in the shape Instagram and Quicket serve them."""
import json
import os
import stat
import unittest

from events import preview
from events.preview import PreviewClient, PreviewError

from .helpers import response, temp_dir

JPEG = b"\xff\xd8\xff\xe0" + b"fixture-jpeg" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-png" * 20
POST = "https://www.instagram.com/p/FixturePost01/"


def instagram_page(caption: str = "Every Monday at Fixture Basement\n📍 1 Example Road\n\n#OpenMic #capetown #openmic",
                   image: str | None = "https://cdn.fixture.test/post.jpg?sig=1&amp;oe=2") -> str:
    picture = f'<meta property="og:image" content="{image}" />' if image else ""
    return ('<html><head><meta charset="utf-8">'
            '<meta content="Fixture Basement (@fixture_basement) • Instagram photo" name="twitter:title" />'
            f'<meta property="og:title" content="Fixture Basement on Instagram: &quot;{caption}&quot;" />'
            f'<meta property="og:url" content="https://www.instagram.com/fixture_basement/p/FixturePost01/" />{picture}'
            '<meta property="og:description" content="12 likes - fixture_basement on September 1, 2026" /></head><body></body></html>')


def ticket_page(**event) -> str:
    data = {"@context": "https://schema.org", "@type": "Event", "name": "Fixture Film Night",
            "image": ["//images.fixture.test/100001_300_300.jpeg"],
            "location": {"@type": "Place", "name": "Fixture House",
                         "address": {"@type": "PostalAddress", "streetAddress": "1 Example Road, Cape Town, 8001, South Africa",
                                     "addressLocality": "Cape Town"}},
            "description": "A made-up classic.\r\nDoors at six.\r\n",
            "organizer": {"@type": "Organization", "name": "Fixture Film Club", "url": "https://www.instagram.com/fixture_film_club/"}}
    data.update(event)
    return ('<html><head><meta property="og:image" content="//images.fixture.test/og.jpeg">'
            f'<script type="application/ld+json">{json.dumps(data)}</script></head></html>')


class ParsePageTest(unittest.TestCase):
    def test_instagram_post(self):
        found = preview.parse_page(POST, instagram_page())
        self.assertEqual(found["image_url"], "https://cdn.fixture.test/post.jpg?sig=1&oe=2")
        self.assertEqual(found["author"], {"id": "instagram:fixture_basement", "name": "Fixture Basement",
                                           "url": "https://www.instagram.com/fixture_basement/"})
        self.assertEqual(found["text"], "Every Monday at Fixture Basement\n📍 1 Example Road\n\n#OpenMic #capetown #openmic")
        self.assertEqual(found["tags"], ["openmic", "capetown"])
        self.assertEqual((found["venue"], found["address"]), (None, None))

    def test_instagram_account_from_the_canonical_address_and_long_captions(self):
        page = instagram_page("word " * 600).replace('name="twitter:title"', 'name="other"')
        found = preview.parse_page(POST, page)
        self.assertEqual(found["author"]["id"], "instagram:fixture_basement")
        self.assertLessEqual(len(found["text"]), preview.MAX_TEXT + 1)
        self.assertTrue(found["text"].endswith("…"))

    def test_instagram_sign_in_page_has_nothing(self):
        found = preview.parse_page(POST, "<html><head><title>Instagram</title></head><body>Log in</body></html>")
        self.assertEqual((found["image_url"], found["author"], found["text"]), (None, None, None))

    def test_ticket_page_with_schema_org_event(self):
        found = preview.parse_page("https://www.quicket.co.za/events/100001-fixture-film/", ticket_page())
        self.assertEqual(found["image_url"], "https://images.fixture.test/100001_300_300.jpeg")
        self.assertEqual(found["author"], {"id": "quicket.co.za:fixture film club", "name": "Fixture Film Club",
                                           "url": "https://www.instagram.com/fixture_film_club/"})
        self.assertEqual((found["venue"], found["address"]), ("Fixture House", "1 Example Road, Cape Town, 8001, South Africa"))
        self.assertEqual(found["text"], "A made-up classic.\nDoors at six.")

    def test_schema_org_variants(self):
        url = "https://tickets.fixture.test/e/1"
        graph = '<script type="application/ld+json">{"@graph": [{"@type": "WebSite"}, {"@type": ["Event", "MusicEvent"], "image": "/a.png", ' \
                '"location": {"name": "Fixture Yard", "address": "2 Example Road"}, "organizer": [{"name": " Fixture  Crew "}]}]}</script>'
        found = preview.parse_page(url, graph)
        self.assertEqual((found["image_url"], found["venue"], found["address"]), ("https://tickets.fixture.test/a.png", "Fixture Yard", "2 Example Road"))
        self.assertEqual(found["author"], {"id": "tickets.fixture.test:fixture crew", "name": "Fixture Crew", "url": None})
        parts = preview.parse_page(url, ticket_page(location={"address": {"addressLocality": "Cape Town", "addressCountry": "South Africa"}}))
        self.assertEqual(parts["address"], "Cape Town, South Africa")
        for junk in ('<script type="application/ld+json">{not json</script>', '<script type="application/ld+json">[1, "x", null]</script>', ""):
            self.assertEqual(preview.parse_page(url, junk)["author"], None)

    def test_plain_page_falls_back_to_open_graph(self):
        page = '<meta property="og:image" content="https://cdn.fixture.test/a.png"><meta name="description" content="Bring a friend. #Jazz">'
        found = preview.parse_page("https://club.fixture.test/night", page)
        self.assertEqual((found["image_url"], found["text"], found["tags"], found["author"]),
                         ("https://cdn.fixture.test/a.png", "Bring a friend. #Jazz", ["jazz"], None))
        self.assertIsNone(preview.parse_page("https://club.fixture.test/", '<meta property="og:image" content="javascript:alert(1)">')["image_url"])


class ApplyTest(unittest.TestCase):
    def listing(self, **fields) -> dict:
        ev = {"cover_url": None, "presenter": None, "description": "Cost: From R80", "tags": [],
              "location": {"venue": "Fixture Basement", "address": None, "city": "Cape Town"}}
        ev.update(fields)
        return ev

    def test_fills_only_what_the_listing_lacks(self):
        found = {"image": "media/" + "a" * 32 + ".jpg", "author": {"id": "instagram:x", "name": "Fixture X", "url": "https://www.instagram.com/x/"},
                 "text": "Doors at nine.", "venue": "Elsewhere", "address": "1 Example Road", "tags": ["vinyl"]}
        ev = preview.apply(self.listing(), found)
        self.assertEqual(ev["cover_url"], found["image"])
        self.assertEqual(ev["presenter"], {"id": "instagram:x", "name": "Fixture X", "avatar_url": None,
                                           "url": "https://www.instagram.com/x/", "description": None})
        self.assertEqual(ev["description"], "Cost: From R80\n\nDoors at nine.")
        self.assertEqual(ev["location"], {"venue": "Fixture Basement", "address": "1 Example Road", "city": "Cape Town"})
        self.assertEqual(ev["tags"], ["vinyl"])
        kept = self.listing(cover_url="https://cdn.fixture.test/own.jpg", presenter={"name": "Own"}, tags=["own"], description=None)
        kept["location"]["address"] = "9 Own Street"
        ev = preview.apply(kept, found)
        self.assertEqual((ev["cover_url"], ev["presenter"], ev["tags"], ev["location"]["address"], ev["description"]),
                         ("https://cdn.fixture.test/own.jpg", {"name": "Own"}, ["own"], "9 Own Street", "Doors at nine."))

    def test_no_preview_and_does_not_change_the_original(self):
        original = self.listing()
        self.assertIs(preview.apply(original, None), original)
        preview.apply(original, {"image": "media/x.jpg", "address": "1 Example Road", "tags": ["a"], "text": "t", "author": None})
        self.assertEqual(original, self.listing())


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.media = temp_dir(self) / "state" / "media"
        self.pages: dict[str, object] = {POST: instagram_page(), "https://cdn.fixture.test/post.jpg?sig=1&oe=2": JPEG}
        self.calls: list[tuple[str, int]] = []

    def fetch(self, url: str, max_bytes: int):
        self.calls.append((url, max_bytes))
        page = self.pages.get(url, PreviewError("answered 404"))
        if isinstance(page, BaseException):
            raise page
        return response(page, content_type="image/jpeg" if isinstance(page, bytes) else "text/html; charset=utf-8", url=url)

    def test_reads_the_page_and_keeps_its_picture_privately(self):
        client = PreviewClient(self.media, self.fetch)
        found = client.get(POST)
        self.assertRegex(found["image"], r"^media/[0-9a-f]{32}\.jpg$")
        self.assertEqual(found["author"]["name"], "Fixture Basement")
        self.assertEqual(self.calls, [(POST, preview.MAX_PAGE_BYTES), ("https://cdn.fixture.test/post.jpg?sig=1&oe=2", preview.MAX_IMAGE_BYTES)])
        saved = self.media / found["image"].removeprefix("media/")
        self.assertEqual(saved.read_bytes(), JPEG)
        self.assertEqual((stat.S_IMODE(saved.stat().st_mode), stat.S_IMODE(self.media.stat().st_mode)), (0o600, 0o700))
        self.assertEqual(client.get(POST)["image"], found["image"], "the same picture has the same name")
        self.assertEqual(len(list(self.media.iterdir())), 1)

    def test_instagram_without_a_picture_means_we_are_turned_away(self):
        self.pages[POST] = "<html><title>Instagram</title></html>"
        with self.assertRaises(PreviewError) as raised:
            PreviewClient(self.media, self.fetch).get(POST)
        self.assertTrue(raised.exception.blocked)

    def test_a_picture_that_fails_or_is_not_a_picture_still_leaves_the_words(self):
        for bad in (PreviewError("answered 403"), b"<html>not a picture</html>", b""):
            self.pages["https://cdn.fixture.test/post.jpg?sig=1&oe=2"] = bad
            found = PreviewClient(self.media, self.fetch).get(POST)
            self.assertIsNone(found["image"])
            self.assertTrue(found["text"].startswith("Every Monday"))
        self.assertFalse(self.media.exists())

    def test_other_picture_formats(self):
        client = PreviewClient(self.media, self.fetch)
        self.assertTrue(client._keep(PNG).endswith(".png"))
        self.assertTrue(client._keep(b"RIFF\x00\x00\x00\x00WEBPfixture").endswith(".webp"))
        self.assertTrue(client._keep(b"GIF89afixture").endswith(".gif"))
        self.assertIsNone(client._keep(b"<svg xmlns='http://www.w3.org/2000/svg'/>"), "no scripts in what the app serves")

    def test_prune_removes_only_old_unused_pictures(self):
        client = PreviewClient(self.media, self.fetch)
        used, old, recent = client._keep(JPEG), client._keep(PNG), client._keep(JPEG + b"2")
        for name, age in ((used, 5 * 86400), (old, 5 * 86400), (recent, 60)):
            path = self.media / name.removeprefix("media/")
            os.utime(path, (path.stat().st_atime - age, path.stat().st_mtime - age))
        now = (self.media / recent.removeprefix("media/")).stat().st_mtime + 60
        self.assertEqual(client.prune({used}, now - 86400), 1)
        self.assertEqual({"media/" + p.name for p in self.media.iterdir()}, {used, recent})
        self.assertEqual(PreviewClient(self.media / "missing", self.fetch).prune(set(), now), 0)


class PublicOnlyTest(unittest.TestCase):
    @staticmethod
    def resolver(*addresses: str):
        return lambda host, port, **kw: [(None, None, None, "", (address, port)) for address in addresses]

    def test_public_addresses_pass(self):
        preview.require_public("https://www.instagram.com/p/FixturePost01/", self.resolver("157.240.22.174", "2a03:2880:f20d:e5:face:b00c:0:4420"))

    def test_private_loopback_and_odd_addresses_are_refused(self):
        for url, addresses in (("http://localhost:8771/api/status", ["127.0.0.1"]), ("https://intranet.fixture.test/", ["10.0.0.5"]),
                               ("https://mixed.fixture.test/", ["93.184.216.34", "192.168.1.1"]), ("http://[::1]/", ["::1"]),
                               ("https://metadata.fixture.test/", ["169.254.169.254"]), ("https://v6.fixture.test/", ["fe80::1%eth0"]),
                               ("https://nowhere.fixture.test/", [])):
            with self.subTest(url=url), self.assertRaises(PreviewError):
                preview.require_public(url, self.resolver(*addresses))
        for url in ("file:///etc/passwd", "ftp://files.fixture.test/a", "https://user:pass@site.fixture.test/", "not a url", "https://"):
            with self.subTest(url=url), self.assertRaises(PreviewError):
                preview.require_public(url, self.resolver("93.184.216.34"))

    def test_a_host_that_does_not_resolve(self):
        def missing(host, port, **kw):
            raise OSError("no such host")
        with self.assertRaisesRegex(PreviewError, "could not find"):
            preview.require_public("https://gone.fixture.test/", missing)

    def test_redirects_are_checked_too(self):
        handler = preview._CheckedRedirects()
        with self.assertRaises(PreviewError):
            handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:8771/api/status")

import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from events import net

SECRET_URL = "https://calendars.partiful.com/getCalendar?id=SECRET-TOKEN&x=1#frag"


class FakeUpstream:
    """What urllib.request.urlopen returns, used as a context manager."""

    def __init__(self, body: bytes = b"{}", status: int = 200, content_type: str = "application/json", url: str = ""):
        self.body = io.BytesIO(body)
        self.status = status
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RedactTest(unittest.TestCase):
    def test_query_values_and_fragments_are_dropped(self):
        self.assertEqual(net.redact(SECRET_URL), "https://calendars.partiful.com/getCalendar?id=…&x=…")
        self.assertEqual(net.redact("https://api.luma.com/home/get-events"), "https://api.luma.com/home/get-events")
        self.assertEqual(net.redact("http://[::1"), "<url>")


class RequestTest(unittest.TestCase):
    def test_success(self):
        upstream = FakeUpstream(b'{"ok": true}', url="https://api.luma.com/x?a=1&b=two+words")
        with mock.patch("urllib.request.urlopen", return_value=upstream) as urlopen:
            resp = net.request("https://api.luma.com/x?a=1", params={"b": "two words"}, headers={"accept": "application/json"},
                               json_body={"n": 1}, method="POST", timeout=5)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.luma.com/x?a=1&b=two+words")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {"n": 1})
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("User-agent"), net.USER_AGENT)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)
        self.assertEqual((resp.status, resp.json(), resp.url), (200, {"ok": True}, upstream.url))

    def test_text_uses_the_declared_charset(self):
        upstream = FakeUpstream("Café".encode("latin-1"), content_type="text/calendar; charset=iso-8859-1")
        with mock.patch("urllib.request.urlopen", return_value=upstream):
            self.assertEqual(net.request("https://x.test/feed").text(), "Café")
        self.assertEqual(net.Response(200, Message(), b"", "u").json(), {})

    def test_http_errors_keep_status_and_body_but_hide_the_url_secret(self):
        error = urllib.error.HTTPError(SECRET_URL, 429, "Too Many Requests", Message(), io.BytesIO(b'{"message": "slow"}'))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(net.HttpError) as ctx:
                net.request(SECRET_URL)
        self.assertEqual((ctx.exception.status, ctx.exception.body), (429, '{"message": "slow"}'))
        self.assertNotIn("SECRET-TOKEN", str(ctx.exception))
        self.assertNotIn("SECRET-TOKEN", ctx.exception.url)
        self.assertIsInstance(ctx.exception, net.NetError)

    def test_network_errors_hide_the_url_secret(self):
        for error in (urllib.error.URLError("Name or service not known"), TimeoutError("timed out"),
                      ConnectionResetError("reset")):
            with self.subTest(error=type(error).__name__):
                with mock.patch("urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(net.NetError) as ctx:
                        net.request(SECRET_URL)
                self.assertNotIsInstance(ctx.exception, net.HttpError)
                self.assertIn("calendars.partiful.com", str(ctx.exception))
                self.assertNotIn("SECRET-TOKEN", str(ctx.exception))

    def test_oversized_responses_are_refused(self):
        with mock.patch.object(net, "MAX_BYTES", 10), \
                mock.patch("urllib.request.urlopen", return_value=FakeUpstream(b"x" * 11)):
            with self.assertRaisesRegex(net.NetError, "larger than 10 bytes"):
                net.request(SECRET_URL)


if __name__ == "__main__":
    unittest.main()

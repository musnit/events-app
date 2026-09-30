"""Outbound HTTP: timeouts, one user agent, a size cap, and errors that never echo secret URLs."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) events/2.1"
MAX_BYTES = 25 * 1024 * 1024


class NetError(Exception):
    """The request did not produce a usable HTTP response (DNS, TLS, timeout, reset)."""


class HttpError(NetError):
    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = redact(url)
        super().__init__(f"HTTP {status} from {self.url}: {body[:200]}")


@dataclass
class Response:
    status: int
    headers: Message
    body: bytes
    url: str

    def text(self) -> str:
        charset = self.headers.get_content_charset() or "utf-8"
        return self.body.decode(charset, errors="replace")

    def json(self) -> object:
        return json.loads(self.body) if self.body else {}


def redact(url: str) -> str:
    """Drop query values, which is where personal feeds keep their tokens."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<url>"
    query = "&".join(f"{k}=…" for k, _ in urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def request(url: str, *, method: str = "GET", params: dict | None = None, headers: dict | None = None,
            json_body: object = None, timeout: float = 30) -> Response:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None
    merged = {"user-agent": USER_AGENT}
    merged.update(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        merged["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=merged, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise NetError(f"response from {redact(url)} is larger than {MAX_BYTES} bytes")
            return Response(resp.status, resp.headers, body, resp.url)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(64 * 1024).decode(errors="replace")
        except OSError:
            body = ""
        raise HttpError(e.code, body, url) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise NetError(f"could not reach {redact(url)}: {reason}") from None

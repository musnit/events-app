"""What a linked page says about itself: its picture, who posted it and their words.

A list written by hand (Sundry Mag) links each event to the organiser's own post, mostly on
Instagram, then Quicket. Those pages describe themselves for link previews, with Open Graph tags and
schema.org JSON, and that is all this module reads; it never signs in anywhere. The picture is copied
into the state directory, because Instagram's image addresses stop working after a few days.

The links come from someone else's page, so every address fetched here, redirects included, must
resolve to the public internet.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path

from . import net
from .sources import clean_text

MEDIA_PREFIX = "media/"
MEDIA_NAME_RE = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp|gif)\Z")
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TEXT = 1500
MAX_TAGS = 6
IMAGE_TYPES = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF8", "gif"))
INSTAGRAM_HOSTS = ("instagram.com", "www.instagram.com")
INSTAGRAM_CAPTION_RE = re.compile(r"\A(?P<name>.*?) on Instagram: [\"“](?P<text>.*)[\"”]\s*\Z", re.S)
INSTAGRAM_HANDLE_RE = re.compile(r"\(@([A-Za-z0-9._]{1,30})\)")
HASHTAG_RE = re.compile(r"(?<![\w&])#([A-Za-z][A-Za-z0-9_]{1,29})")


class PreviewError(Exception):
    def __init__(self, message: str, *, blocked: bool = False):
        super().__init__(message)
        # The site is turning this client away for now; asking for more pages will not help.
        self.blocked = blocked


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def require_public(url: str, resolve: Callable[..., list] = socket.getaddrinfo) -> None:
    """Refuse anything but http(s) to a host whose every address is on the public internet."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise PreviewError("not a web address") from None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise PreviewError("not a web address")
    try:
        found = resolve(parts.hostname, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise PreviewError(f"could not find {parts.hostname}") from None
    addresses = {info[4][0] for info in found}
    if not addresses or not all(ipaddress.ip_address(a.split("%", 1)[0]).is_global for a in addresses):
        raise PreviewError(f"{parts.hostname} is not on the public internet")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 (urllib's signature)
        require_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_public(url: str, max_bytes: int, *, timeout: float = 20) -> net.Response:
    """GET a public page or picture, following only redirects that stay public."""
    require_public(url)
    opener = urllib.request.build_opener(_CheckedRedirects)
    req = urllib.request.Request(url, headers={"user-agent": net.USER_AGENT, "accept": "*/*", "accept-language": "en"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise PreviewError(f"{host_of(url)} sent more than {max_bytes} bytes")
            return net.Response(resp.status, resp.headers, body, resp.url)
    except urllib.error.HTTPError as e:
        raise PreviewError(f"{host_of(url)} answered {e.code}", blocked=e.code in (401, 403, 429)) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise PreviewError(f"could not reach {host_of(url)}: {getattr(e, 'reason', e)}") from None


class _Head(HTMLParser):
    """The meta tags and schema.org scripts of a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.linked_data: list[str] = []
        self._script: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): v or "" for k, v in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key and values.get("content") and key not in self.meta:
                self.meta[key] = values["content"]
        elif tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self._script = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.linked_data.append("".join(self._script))
            self._script = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)


def _things(value: object) -> list[dict]:
    """Every object in a schema.org document, however it nests them."""
    if isinstance(value, list):
        return [thing for item in value for thing in _things(item)]
    if isinstance(value, dict):
        return [value, *_things(value.get("@graph"))]
    return []


def _first(value: object) -> object:
    return value[0] if isinstance(value, list) and value else value


def _text(value: object, limit: int = MAX_TEXT) -> str | None:
    """Plain text that keeps its line breaks, cut at a word."""
    if not isinstance(value, str):
        return None
    lines = [clean_text(line) for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(None, 1)[0].rstrip(".,;:") + "…"
    return text or None


def _absolute(url: object, base: str) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    joined = urllib.parse.urljoin(base, url.strip())
    return joined if joined.startswith(("http://", "https://")) else None


def hashtags(text: str | None) -> list[str]:
    found = dict.fromkeys(tag.lower() for tag in HASHTAG_RE.findall(text or ""))
    return list(found)[:MAX_TAGS]


def parse_page(url: str, html: str) -> dict:
    """{"image_url", "author": {"id", "name", "url"} | None, "text", "venue", "address", "tags"} from one page."""
    head = _Head()
    head.feed(html)
    meta = head.meta
    host = host_of(url)
    out: dict = {"image_url": _absolute(meta.get("og:image") or meta.get("twitter:image"), url), "author": None,
                 "text": None, "venue": None, "address": None, "tags": []}
    if host in INSTAGRAM_HOSTS:
        caption = INSTAGRAM_CAPTION_RE.match(meta.get("og:title", ""))
        handle = INSTAGRAM_HANDLE_RE.search(meta.get("twitter:title", ""))
        if not handle:
            # The canonical address names the account: instagram.com/<account>/p/<code>/
            path = urllib.parse.urlsplit(meta.get("og:url", "")).path.strip("/").split("/")
            handle_text = path[0] if len(path) >= 3 and path[1] in ("p", "reel", "tv") else None
        else:
            handle_text = handle.group(1)
        name = clean_text(caption.group("name")) if caption else clean_text(meta.get("twitter:title", "").split("(@")[0])
        if handle_text:
            out["author"] = {"id": f"instagram:{handle_text.lower()}", "name": name or f"@{handle_text}",
                             "url": f"https://www.instagram.com/{handle_text}/"}
        out["text"] = _text(caption.group("text")) if caption else None
        out["tags"] = hashtags(caption.group("text") if caption else None)
        return out
    event = None
    for raw in head.linked_data:
        try:
            things = _things(json.loads(raw))
        except ValueError:
            continue
        event = next((t for t in things if "Event" in str(t.get("@type"))), None)
        if event:
            break
    if event:
        out["image_url"] = _absolute(_first(event.get("image")), url) or out["image_url"]
        organizer = _first(event.get("organizer"))
        if isinstance(organizer, dict) and clean_text(organizer.get("name")):
            name = clean_text(organizer["name"])
            out["author"] = {"id": f"{host.removeprefix('www.')}:{name.lower()}", "name": name,
                             "url": _absolute(organizer.get("url"), url)}
        place = _first(event.get("location"))
        if isinstance(place, dict):
            out["venue"] = clean_text(place.get("name")) or None
            address = place.get("address")
            if isinstance(address, dict):
                address = address.get("streetAddress") or ", ".join(
                    clean_text(address.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry") if clean_text(address.get(k)))
            out["address"] = clean_text(address) or None
        out["text"] = _text(event.get("description"))
    out["text"] = out["text"] or _text(meta.get("og:description") or meta.get("description"))
    out["tags"] = hashtags(out["text"])
    return out


def apply(listing: dict, preview: dict | None) -> dict:
    """A listing with whatever its linked page adds. What the listing already says stays."""
    if not preview:
        return listing
    ev = dict(listing)
    if not ev.get("cover_url") and preview.get("image"):
        ev["cover_url"] = preview["image"]
    author = preview.get("author")
    if not ev.get("presenter") and isinstance(author, dict) and author.get("name"):
        ev["presenter"] = {"id": author.get("id"), "name": author["name"], "avatar_url": None, "url": author.get("url"),
                           "description": None}
    if preview.get("text"):
        ev["description"] = "\n\n".join(part for part in (ev.get("description"), preview["text"]) if part)
    if preview.get("address") and not (ev.get("location") or {}).get("address"):
        ev["location"] = {**ev["location"], "address": preview["address"]}
    if not ev.get("tags") and preview.get("tags"):
        ev["tags"] = list(preview["tags"])
    return ev


class PreviewClient:
    def __init__(self, media_dir: Path, fetch: Callable[[str, int], net.Response] = fetch_public):
        self.media_dir = Path(media_dir)
        self._fetch = fetch

    def get(self, url: str) -> dict:
        """Read one page and keep its picture. The result is ``parse_page``'s, with ``image`` a media path or None."""
        page = self._fetch(url, MAX_PAGE_BYTES)
        found = parse_page(url, page.text())
        if host_of(url) in INSTAGRAM_HOSTS and not found["image_url"]:
            # A real post always has a picture; without one Instagram served its sign-in page instead.
            raise PreviewError("Instagram did not describe the post", blocked=True)
        image = None
        if found["image_url"]:
            try:
                image = self._keep(self._fetch(found["image_url"], MAX_IMAGE_BYTES).body)
            except PreviewError:
                image = None  # the words are still worth having
        return {"image": image, "author": found["author"], "text": found["text"], "venue": found["venue"],
                "address": found["address"], "tags": found["tags"]}

    def _keep(self, body: bytes) -> str | None:
        kind = next((ext for magic, ext in IMAGE_TYPES if body.startswith(magic)), None)
        if kind is None and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
            kind = "webp"
        if kind is None:
            return None
        name = f"{hashlib.sha256(body).hexdigest()[:32]}.{kind}"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.media_dir, 0o700)
        path = self.media_dir / name
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".part")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
            os.replace(temporary, path)
        return MEDIA_PREFIX + name

    def prune(self, keep: set[str], older_than: float) -> int:
        """Delete pictures no listing uses any more. Recent files stay: their preview may not be saved yet."""
        removed = 0
        if not self.media_dir.is_dir():
            return 0
        for path in self.media_dir.iterdir():
            if MEDIA_PREFIX + path.name in keep or not path.is_file():
                continue
            if path.stat().st_mtime < older_than:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

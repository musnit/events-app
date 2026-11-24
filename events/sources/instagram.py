"""An Instagram account's latest posts, from the page Instagram serves for embedding a profile.

``instagram.com/<account>/embed/`` needs no sign-in. Its script holds a ``contextJSON`` string with
the account's name and picture and its six newest posts: shortcode, time, caption, picture and,
for a carousel, each of its slides.
A post is not an event; ``events.reader`` decides which posts announce one.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from .. import net
from ..timeutil import to_iso
from . import clean_text

SITE = "https://www.instagram.com"
USERNAME_RE = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
# Paths on instagram.com that are not accounts.
RESERVED = {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "about", "developer", "legal", "direct"}
CONTEXT_RE = re.compile(r'"contextJSON":"((?:[^"\\]|\\.)*)"')
MAX_CAPTION = 3000
MAX_SLIDES = 12


class InstagramError(Exception):
    pass


def calendar_id(username: str) -> str:
    return f"instagram:{username.lower()}"


def calendar(username: str, name: str | None = None) -> dict:
    username = username.lower()
    return {"id": calendar_id(username), "source": "instagram", "name": name or f"@{username}", "slug": username,
            "avatar_url": None, "tint_color": "#c13584", "url": f"{SITE}/{username}/", "description": None}


def usernames(text: str) -> list[str]:
    """Account names from pasted handles and profile links: "@name", "name", "instagram.com/name/"."""
    found: dict[str, None] = {}
    for token in re.split(r"[\s,;]+", text or ""):
        token = token.strip().strip("<>()\"'")
        link = re.match(r"(?:https?://)?(?:www\.)?instagram\.com/([^/?#]+)", token, re.I)
        name = (link.group(1) if link else token.lstrip("@")).lower()
        looks_like_a_site = "." in name and name.rsplit(".", 1)[1] in ("com", "net", "org", "za")
        if "/" in name or (looks_like_a_site and not link and not token.startswith("@")):
            continue  # some other site's address; an account named like one is written @name.co.za
        if USERNAME_RE.match(name) and name not in RESERVED:
            found[name] = None
    return list(found)


def _picture(address: object) -> str | None:
    return address if isinstance(address, str) and address.startswith("https://") else None


def _edges(value: object) -> list:
    edges = value.get("edges") if isinstance(value, dict) else None
    return edges if isinstance(edges, list) else []


def parse_embed(html: str) -> dict:
    """{"username", "name", "avatar_url", "posts": [{"code", "url", "taken_at", "caption", "image_url", "slide_urls"}]}

    ``slide_urls`` are a carousel's pictures in order, the first being the post's own; other posts have none."""
    match = CONTEXT_RE.search(html or "")
    if not match:
        raise InstagramError("Instagram did not list the account's posts")
    try:
        context = json.loads(json.loads('"' + match.group(1) + '"'))["context"]
    except (ValueError, KeyError, TypeError):
        raise InstagramError("Instagram's account page is not in the shape expected") from None
    if not isinstance(context, dict) or not isinstance(context.get("username"), str):
        raise InstagramError("Instagram's account page is not in the shape expected")
    posts = []
    for item in context.get("graphql_media") or []:
        node = item.get("shortcode_media", item) if isinstance(item, dict) else None
        if not isinstance(node, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{5,20}", str(node.get("shortcode") or "")):
            continue
        try:
            taken_at = to_iso(float(node.get("taken_at_timestamp")))
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        edges = ((node.get("edge_media_to_caption") or {}).get("edges") or [{}])
        caption = ((edges[0] or {}).get("node") or {}).get("text") if isinstance(edges[0], dict) else None
        image = node.get("display_url") or node.get("thumbnail_src")
        slides = [_picture(((edge or {}).get("node") or {}).get("display_url") if isinstance(edge, dict) else None)
                  for edge in _edges(node.get("edge_sidecar_to_children"))]
        posts.append({"code": node["shortcode"], "url": f"{SITE}/p/{node['shortcode']}/", "taken_at": taken_at,
                      "caption": str(caption or "")[:MAX_CAPTION].strip(), "image_url": _picture(image),
                      "slide_urls": [slide for slide in slides if slide][:MAX_SLIDES]})
    avatar = context.get("profile_pic_url")
    return {"username": context["username"].lower(), "name": clean_text(context.get("full_name")) or None,
            "avatar_url": _picture(avatar), "posts": posts}


class InstagramClient:
    def __init__(self, request: Callable[..., net.Response] = net.request):
        self._request = request

    def account(self, username: str) -> dict:
        if not USERNAME_RE.match(username):
            raise InstagramError("not an Instagram account name")
        try:
            page = self._request(f"{SITE}/{username}/embed/", headers={"accept": "text/html", "accept-language": "en"})
        except net.HttpError as e:
            raise InstagramError(f"Instagram answered {e.status} for @{username}") from None
        return parse_embed(page.text())

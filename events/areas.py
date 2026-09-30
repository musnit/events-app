"""Where an event is: Bay Area (and which part), online, elsewhere, or unknown."""
from __future__ import annotations

import math
import re

ZONES = {
    "sf": "San Francisco",
    "peninsula": "Peninsula",
    "south-bay": "South Bay",
    "east-bay": "East Bay",
    "north-bay": "North Bay",
}

# City -> (zone, lat, lng). The coordinates let an event with only a pin find its nearest town.
CITIES: dict[str, tuple[str, float, float]] = {
    "san francisco": ("sf", 37.7749, -122.4194),
    "daly city": ("peninsula", 37.6879, -122.4702), "brisbane": ("peninsula", 37.6808, -122.3999),
    "south san francisco": ("peninsula", 37.6547, -122.4077), "san bruno": ("peninsula", 37.6305, -122.4111),
    "pacifica": ("peninsula", 37.6138, -122.4869), "millbrae": ("peninsula", 37.5985, -122.3872),
    "burlingame": ("peninsula", 37.5841, -122.3661), "hillsborough": ("peninsula", 37.5741, -122.3794),
    "san mateo": ("peninsula", 37.5630, -122.3255), "foster city": ("peninsula", 37.5585, -122.2711),
    "belmont": ("peninsula", 37.5202, -122.2758), "san carlos": ("peninsula", 37.5072, -122.2605),
    "redwood city": ("peninsula", 37.4852, -122.2364), "half moon bay": ("peninsula", 37.4636, -122.4286),
    "atherton": ("peninsula", 37.4613, -122.1977), "menlo park": ("peninsula", 37.4530, -122.1817),
    "east palo alto": ("peninsula", 37.4688, -122.1411), "woodside": ("peninsula", 37.4299, -122.2539),
    "portola valley": ("peninsula", 37.3841, -122.2352), "palo alto": ("peninsula", 37.4419, -122.1430),
    "stanford": ("peninsula", 37.4275, -122.1697), "san gregorio": ("peninsula", 37.3272, -122.3869),
    "mountain view": ("south-bay", 37.3861, -122.0839), "los altos": ("south-bay", 37.3852, -122.1141),
    "sunnyvale": ("south-bay", 37.3688, -122.0363), "santa clara": ("south-bay", 37.3541, -121.9552),
    "cupertino": ("south-bay", 37.3230, -122.0322), "san jose": ("south-bay", 37.3382, -121.8863),
    "campbell": ("south-bay", 37.2872, -121.9500), "los gatos": ("south-bay", 37.2358, -121.9624),
    "saratoga": ("south-bay", 37.2638, -122.0230), "milpitas": ("south-bay", 37.4323, -121.8996),
    "morgan hill": ("south-bay", 37.1305, -121.6544), "santa cruz": ("south-bay", 36.9741, -122.0308),
    "oakland": ("east-bay", 37.8044, -122.2712), "berkeley": ("east-bay", 37.8715, -122.2730),
    "emeryville": ("east-bay", 37.8313, -122.2852), "alameda": ("east-bay", 37.7652, -122.2416),
    "albany": ("east-bay", 37.8869, -122.2978), "el cerrito": ("east-bay", 37.9161, -122.3108),
    "richmond": ("east-bay", 37.9358, -122.3478), "piedmont": ("east-bay", 37.8244, -122.2317),
    "san leandro": ("east-bay", 37.7249, -122.1561), "castro valley": ("east-bay", 37.6941, -122.0864),
    "hayward": ("east-bay", 37.6688, -122.0808), "union city": ("east-bay", 37.5934, -122.0439),
    "fremont": ("east-bay", 37.5485, -121.9886), "newark": ("east-bay", 37.5297, -122.0402),
    "walnut creek": ("east-bay", 37.9101, -122.0652), "lafayette": ("east-bay", 37.8858, -122.1180),
    "orinda": ("east-bay", 37.8771, -122.1797), "concord": ("east-bay", 37.9780, -122.0311),
    "pleasanton": ("east-bay", 37.6624, -121.8747), "dublin": ("east-bay", 37.7022, -121.9358),
    "livermore": ("east-bay", 37.6819, -121.7680), "san ramon": ("east-bay", 37.7799, -121.9780),
    "danville": ("east-bay", 37.8216, -121.9999),
    "sausalito": ("north-bay", 37.8591, -122.4853), "mill valley": ("north-bay", 37.9060, -122.5450),
    "tiburon": ("north-bay", 37.8735, -122.4566), "corte madera": ("north-bay", 37.9255, -122.5275),
    "larkspur": ("north-bay", 37.9341, -122.5353), "san rafael": ("north-bay", 37.9735, -122.5311),
    "novato": ("north-bay", 38.1074, -122.5697), "petaluma": ("north-bay", 38.2324, -122.6367),
    "sonoma": ("north-bay", 38.2919, -122.4580), "napa": ("north-bay", 38.2975, -122.2869),
    "santa rosa": ("north-bay", 38.4404, -122.7141), "healdsburg": ("north-bay", 38.6105, -122.8692),
    "bolinas": ("north-bay", 37.9091, -122.6861), "stinson beach": ("north-bay", 37.9005, -122.6444),
}
ALIASES = {"sf": "san francisco", "s.f.": "san francisco", "san francisco county": "san francisco",
           "marin": "san rafael", "marin county": "san rafael", "east bay": "oakland", "south bay": "san jose",
           "peninsula": "san mateo", "silicon valley": "san jose"}
ZONE_WORDS = {"east bay": "east-bay", "south bay": "south-bay", "peninsula": "peninsula", "north bay": "north-bay",
              "marin": "north-bay"}
BAY_BOX = (36.85, 38.70, -123.15, -121.20)  # lat min, lat max, lng min, lng max
NEAREST_KM = 25
ONLINE_WORDS = re.compile(r"\b(zoom|google meet|online|virtual|livestream|webinar|discord|twitch|youtube live)\b", re.I)
CITY_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(c) for c in CITIES), key=len, reverse=True)) + r")\b", re.I)
BAY_TEXT_RE = re.compile(r"\b(bay area|s\.?f\.?|silicon valley)\b|\bCA\s+9[45]\d{3}\b", re.I)
# ", NJ" / ", NY 10001" and so on: a US state other than California.
OTHER_STATE_RE = re.compile(r",\s*(?!CA\b)(A[KLRZ]|C[OT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|"
                            r"S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b(\s+\d{5})?")
# A country name settles it; a far-away city name only counts when no Bay Area town is named
# ("Chicago Pizza, San Francisco" is still in SF).
# Only as an address component (", Ireland" or the whole field), so Bay venues such as China Basin,
# India Basin or Japan Center stay local.
COUNTRY_RE = re.compile(r"(?:^|,)\s*(ireland|england|scotland|united kingdom|uk|canada|mexico|france|germany|spain|portugal|"
                        r"italy|japan|china|india|singapore|australia|brazil|netherlands|switzerland|sweden|israel|"
                        r"south korea|korea|taiwan|hong kong|uae|nigeria|kenya|south africa)\s*(?:,|$)", re.I)
FAR_CITY_RE = re.compile(r"\b(london|paris|berlin|tokyo|toronto|new york|nyc|brooklyn|boston|chicago|austin|seattle|miami|"
                         r"los angeles|denver|atlanta|washington,? d\.?c\.?)\b", re.I)
PACIFIC = {"America/Los_Angeles", "US/Pacific", "PST8PDT"}
UNINFORMATIVE_ZONES = {"UTC", "Etc/UTC", "GMT", "Etc/GMT", "Z", "Universal", "Zulu"}


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1) * math.cos(math.radians((lat1 + lat2) / 2))
    return 6371 * math.hypot(dlat, dlng)


def _city_zone(city: str | None) -> str | None:
    if not city:
        return None
    key = re.sub(r",?\s*(ca|california)(\s+\d{5})?$", "", city.strip().lower()).strip()
    key = ALIASES.get(key, key)
    if key in CITIES:
        return CITIES[key][0]
    return ZONE_WORDS.get(key)


def classify(ev: dict) -> tuple[str, str | None]:
    """Return (area, zone). area is bay | online | elsewhere | unknown; zone is a ZONES key or None."""
    loc = ev.get("location") or {}
    if loc.get("type") == "online":
        return "online", None
    lat, lng = loc.get("lat"), loc.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        if not (BAY_BOX[0] <= lat <= BAY_BOX[1] and BAY_BOX[2] <= lng <= BAY_BOX[3]):
            return "elsewhere", None
        zone = _city_zone(loc.get("city"))
        if not zone:
            best = min(CITIES.values(), key=lambda c: _km(lat, lng, c[1], c[2]))
            zone = best[0] if _km(lat, lng, best[1], best[2]) <= NEAREST_KM else None
        return "bay", zone
    country = str(loc.get("country") or "").strip().upper()
    region = str(loc.get("region") or "").strip().lower()
    if country and country not in ("US", "USA", "UNITED STATES"):
        return "elsewhere", None
    if region and region not in ("ca", "california"):
        return "elsewhere", None
    zone = _city_zone(loc.get("city"))
    if zone:
        return "bay", zone
    timezone = ev.get("timezone")
    if timezone in UNINFORMATIVE_ZONES:
        timezone = None
    if timezone and timezone not in PACIFIC:
        return "elsewhere", None
    text = " ".join(str(x) for x in (loc.get("venue"), loc.get("address"), loc.get("city"), loc.get("neighborhood")) if x)
    if text:
        if OTHER_STATE_RE.search(text) or COUNTRY_RE.search(text):
            return "elsewhere", None
        if match := CITY_RE.search(text):
            return "bay", CITIES[match.group(1).lower()][0]
        if FAR_CITY_RE.search(text):
            return "elsewhere", None
        if match := BAY_TEXT_RE.search(text):
            return "bay", "sf" if re.fullmatch(r"s\.?f\.?", match.group(0), re.I) else None
        if ONLINE_WORDS.search(text) or text.startswith("http"):
            return "online", None
        if loc.get("city"):
            return "elsewhere", None
    if match := CITY_RE.search(ev.get("name") or ""):
        return "bay", CITIES[match.group(1).lower()][0]
    # Hidden addresses are usually local: keep Pacific-time events in the Bay Area view.
    if timezone in PACIFIC:
        return "bay", None
    return "unknown", None

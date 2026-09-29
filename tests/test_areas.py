import unittest

from lumacal import areas
from lumacal.sources import empty_location


def where(kind: str = "offline", *, name: str = "Meetup", timezone: str | None = None, **location) -> dict:
    loc = empty_location(kind)
    loc.update(location)
    return {"name": name, "timezone": timezone, "location": loc}


class ClassifyTest(unittest.TestCase):
    def assert_area(self, ev: dict, expected: tuple[str, str | None], msg: str | None = None) -> None:
        self.assertEqual(areas.classify(ev), expected, msg)

    def test_san_francisco_by_city(self):
        self.assert_area(where(city="San Francisco", region="CA", country="US"), ("bay", "sf"))
        self.assert_area(where(city="San Francisco, CA"), ("bay", "sf"))
        self.assert_area(where(city="san francisco, california 94110"), ("bay", "sf"))
        self.assert_area(where(city="SF"), ("bay", "sf"))

    def test_city_names_and_aliases_pick_zones(self):
        cases = {"Oakland": "east-bay", "Palo Alto": "peninsula", "Mountain View": "south-bay",
                 "San Rafael": "north-bay", "Marin County": "north-bay", "East Bay": "east-bay",
                 "Silicon Valley": "south-bay", "Newark": "east-bay"}
        for city, zone in cases.items():
            with self.subTest(city=city):
                self.assert_area(where(city=city, region="CA", country="US"), ("bay", zone))

    def test_zone_by_nearest_coordinates(self):
        self.assert_area(where(lat=37.8050, lng=-122.2700), ("bay", "east-bay"))  # downtown Oakland
        self.assert_area(where(lat=37.3304, lng=-121.8860), ("bay", "south-bay"))  # downtown San Jose
        self.assert_area(where(lat=37.4636, lng=-122.4286), ("bay", "peninsula"))  # Half Moon Bay
        # Inside the Bay box but more than 25 km from any known town.
        self.assert_area(where(lat=37.0, lng=-123.1), ("bay", None))
        # A known city beats the pin.
        self.assert_area(where(city="Palo Alto", lat=37.8050, lng=-122.2700), ("bay", "peninsula"))

    def test_outside_the_bay_box(self):
        self.assert_area(where(city="Los Angeles", lat=34.0522, lng=-118.2437), ("elsewhere", None))
        self.assert_area(where(city="San Francisco", lat=40.7128, lng=-74.0060), ("elsewhere", None),
                         "coordinates outrank a mistyped city")

    def test_non_us_country(self):
        self.assert_area(where(city="London", country="GB"), ("elsewhere", None))
        self.assert_area(where(city="Dublin", country="IE"), ("elsewhere", None), "Dublin, CA shares the name")
        self.assert_area(where(city="Oakland", country="United States"), ("bay", "east-bay"))

    def test_non_california_region(self):
        self.assert_area(where(city="Newark", region="NJ", country="US"), ("elsewhere", None))
        self.assert_area(where(city="Richmond", region="Virginia", country="US"), ("elsewhere", None))
        self.assert_area(where(city="Brooklyn", region="NY"), ("elsewhere", None))
        self.assert_area(where(city="Berkeley", region="California"), ("bay", "east-bay"))

    def test_online(self):
        self.assert_area(where("online", city="San Francisco"), ("online", None))
        self.assert_area(where(address="https://zoom.us/j/123456"), ("online", None))
        self.assert_area(where(venue="Zoom webinar"), ("online", None))

    def test_address_text(self):
        self.assert_area(where(address="2 Embarcadero Center, San Francisco"), ("bay", "sf"))
        self.assert_area(where(address="123 Main St, CA 94110"), ("bay", None))
        self.assert_area(where(venue="Somewhere in the Bay Area"), ("bay", None))
        self.assert_area(where(address="Pier 70, SF"), ("bay", "sf"))
        self.assert_area(where(city="Austin"), ("elsewhere", None), "an unknown city is elsewhere")

    def test_pacific_timezone_fallback(self):
        self.assert_area(where("unknown", timezone="America/Los_Angeles"), ("bay", None))
        self.assert_area(where("unknown", timezone="US/Pacific"), ("bay", None))
        self.assert_area(where("unknown", timezone="America/New_York"), ("elsewhere", None))
        self.assert_area(where("unknown", name="Palo Alto Founders Dinner"), ("bay", "peninsula"))

    def test_unknown(self):
        self.assert_area(where("unknown"), ("unknown", None))
        self.assert_area({}, ("unknown", None))
        self.assert_area({"location": None, "name": None}, ("unknown", None))

    def test_address_only_events_elsewhere_are_not_bay(self):
        """Partiful and iCal events carry only an address line, which may name a Bay town's namesake."""
        cases = [("Newark Penn Station, Newark, NJ 07102", "America/New_York"),
                 ("Newark Penn Station, Newark, NJ 07102", None),
                 ("Trinity College, Dublin, Ireland", "Europe/Dublin"),
                 ("Trinity College, Dublin, Ireland", None),
                 ("Howard Smith Wharves, Brisbane QLD, Australia", None),
                 ("Main St, Richmond, VA 23219", None),
                 ("Soho House, London", None),
                 ("2 Embarcadero Center, San Francisco", "America/New_York")]
        for address, timezone in cases:
            with self.subTest(address=address, timezone=timezone):
                self.assert_area(where(address=address, timezone=timezone), ("elsewhere", None))

    def test_bay_addresses_that_mention_far_places(self):
        self.assert_area(where(address="Chicago Pizza, 1 Market St, San Francisco"), ("bay", "sf"))
        self.assert_area(where(address="Brooklyn Bagels, Oakland, CA 94612", timezone="America/Los_Angeles"),
                         ("bay", "east-bay"))
        self.assert_area(where(address="Cañada College, 4200 Farm Hill Blvd, Redwood City"), ("bay", "peninsula"))

    def test_bay_venues_named_after_countries_stay_in_the_bay(self):
        for address, zone in (("China Basin Park, 400 Terry A Francois Blvd, San Francisco", "sf"),
                              ("India Basin Shoreline Park, 900 Innes Ave, San Francisco", "sf"),
                              ("Japan Center East Mall, 1737 Post St, San Francisco", "sf"),
                              ("Ireland's 32, 3920 Geary Blvd, San Francisco", "sf"),
                              ("Congregation Beth Israel, 1630 Bancroft Way, Berkeley", "east-bay")):
            with self.subTest(address=address):
                self.assert_area(where(address=address), ("bay", zone))

if __name__ == "__main__":
    unittest.main()

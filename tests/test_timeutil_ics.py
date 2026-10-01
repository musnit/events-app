import json
import unittest
from datetime import datetime, timedelta, timezone

from events import ics
from events.sources import luma
from events.timeutil import canonical, parse_iso, to_iso

CRLF = "\r\n"

LUMA_FEED = CRLF.join([
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Luma//Calendar//EN",
    "BEGIN:VTIMEZONE",
    "TZID:America/Los_Angeles",
    "BEGIN:DAYLIGHT",
    "DTSTART:20070311T020000",
    "TZOFFSETTO:-0700",
    "END:DAYLIGHT",
    "END:VTIMEZONE",
    "BEGIN:VEVENT",
    "UID:evt-FixtureTalk0001@events.lu.ma",
    "DTSTAMP:20260929T120000Z",
    "DTSTART;TZID=America/Los_Angeles:20260929T190000",
    'DTEND;TZID="America/Los_Angeles":20260929T210000',
    r"SUMMARY:Mind\, AI and the Future of Human Memory\; a talk",
    r"DESCRIPTION:Get up-to-date information at: https://luma.com/fixtalk1\n\nHo",
    " sted by Fixture Brain Bay Area",
    r"LOCATION:Fixture Hall\, 100 Fixture Ave\, San Jose",
    "URL:https://luma.com/fixtalk1",
    "BEGIN:VALARM",
    "ACTION:DISPLAY",
    "DESCRIPTION:Reminder",
    "TRIGGER:-PT1H",
    "END:VALARM",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:abc123@partiful",
    "DTSTART:20261010T020000Z",
    "DTEND:20261010T050000Z",
    "SUMMARY:Game Night | Partiful",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:allday-1",
    "DTSTART;VALUE=DATE:20261010",
    "DTEND;VALUE=DATE:20261012",
    "summary:Offsite",
    "END:VEVENT",
    "END:VCALENDAR",
    "",
])


class TimeutilTest(unittest.TestCase):
    def test_to_iso_converts_to_utc_and_drops_microseconds(self):
        pacific = timezone(timedelta(hours=-7))
        self.assertEqual(to_iso(datetime(2026, 9, 29, 19, 0, 5, 999999, tzinfo=pacific)), "2026-09-30T02:00:05Z")

    def test_to_iso_accepts_epoch_seconds(self):
        self.assertEqual(to_iso(0), "1970-01-01T00:00:00Z")
        self.assertEqual(to_iso(1790000000.75), "2026-09-21T14:13:20Z")

    def test_to_iso_rejects_naive_datetimes(self):
        with self.assertRaises(ValueError):
            to_iso(datetime(2026, 9, 29, 12))

    def test_parse_iso_accepts_z_offsets_and_fractions(self):
        expected = datetime(2026, 9, 30, 2, tzinfo=timezone.utc)
        for text in ("2026-09-30T02:00:00Z", "2026-09-30T02:00:00z", "2026-09-30T02:00:00.000Z",
                     "2026-09-29T19:00:00-07:00", " 2026-09-30T02:00:00+00:00 "):
            with self.subTest(text=text):
                self.assertEqual(parse_iso(text), expected)

    def test_parse_iso_returns_none_for_unusable_input(self):
        for value in (None, "", "2026-09-30T02:00:00", "2026-09-30", "tomorrow", 1790000000, {"seconds": 1}):
            with self.subTest(value=value):
                self.assertIsNone(parse_iso(value))

    def test_canonical_coerces_every_upstream_shape(self):
        cases = {
            "2026-09-29T19:00:00-07:00": "2026-09-30T02:00:00Z",
            "2026-09-30T02:00:00.000Z": "2026-09-30T02:00:00Z",
            1790733600: "2026-09-30T02:00:00Z",  # epoch seconds
            1790733600000: "2026-09-30T02:00:00Z",  # epoch milliseconds
            1790733600.5: "2026-09-30T02:00:00Z",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(canonical(value), expected)
        self.assertEqual(canonical({"seconds": 1790733600, "nanoseconds": 0}), "2026-09-30T02:00:00Z")
        self.assertEqual(canonical({"_seconds": 1790733600}), "2026-09-30T02:00:00Z")

    def test_canonical_returns_none_for_garbage(self):
        for value in (None, True, False, "", "not a date", "2026-09-30T02:00:00", {}, {"seconds": "soon"},
                      [], float("nan"), float("inf"), 10 ** 30):
            with self.subTest(value=value):
                self.assertIsNone(canonical(value))


class IcsReadTest(unittest.TestCase):
    def test_unfold_joins_continuation_lines_for_any_line_ending(self):
        self.assertEqual(ics.unfold("A:one\r\n two\r\n\tthree\r\nB:x"), ["A:onetwothree", "B:x"])
        self.assertEqual(ics.unfold("A:one\n two\rB:x"), ["A:onetwo", "B:x"])

    def test_unescape_handles_every_rfc_escape(self):
        self.assertEqual(ics.unescape(r"a\,b\;c\\d\ne\Nf"), "a,b;c\\d\ne\nf")
        self.assertEqual(ics.unescape("trailing\\"), "trailing\\")

    def test_escape_round_trips_through_unescape(self):
        text = "Talk; with, commas\\backslash\nand a second line\r\nthird"
        escaped = ics.escape(text)
        self.assertNotIn("\n", escaped)
        self.assertEqual(ics.unescape(escaped), text.replace("\r\n", "\n"))
        self.assertEqual(ics.escape(None), "")

    def test_parse_reads_events_and_ignores_nested_components(self):
        events = ics.parse(LUMA_FEED)
        self.assertEqual([e["UID"] for e in events], ["evt-FixtureTalk0001@events.lu.ma", "abc123@partiful", "allday-1"])
        talk = events[0]
        self.assertEqual(ics.unescape(talk["SUMMARY"]), "Mind, AI and the Future of Human Memory; a talk")
        # The folded description is joined, and VALARM's own DESCRIPTION/TRIGGER do not leak into the event.
        self.assertEqual(ics.unescape(talk["DESCRIPTION"]),
                         "Get up-to-date information at: https://luma.com/fixtalk1\n\nHosted by Fixture Brain Bay Area")
        self.assertNotIn("TRIGGER", talk)
        self.assertNotIn("ACTION", talk)
        self.assertEqual(talk["DTSTART__params"], {"TZID": "America/Los_Angeles"})
        self.assertEqual(talk["DTEND__params"], {"TZID": "America/Los_Angeles"}, "quotes around params are removed")
        self.assertEqual(talk["URL"], "https://luma.com/fixtalk1", "values keep their own colons")
        self.assertEqual(events[2]["SUMMARY"], "Offsite", "property names are case-insensitive")

    def test_event_times_for_tzid_utc_and_all_day(self):
        talk, party, offsite = ics.parse(LUMA_FEED)
        self.assertEqual(ics.event_times(talk), ("2026-09-30T02:00:00Z", "2026-09-30T04:00:00Z", False))
        self.assertEqual(ics.event_times(party), ("2026-10-10T02:00:00Z", "2026-10-10T05:00:00Z", False))
        # All-day dates are local midnights, Pacific when no TZID is given.
        self.assertEqual(ics.event_times(offsite), ("2026-10-10T07:00:00Z", "2026-10-12T07:00:00Z", True))

    def test_parse_datetime_variants(self):
        self.assertEqual(ics.parse_datetime("20261010", {"TZID": "Europe/Berlin"}), ("2026-10-09T22:00:00Z", True))
        self.assertEqual(ics.parse_datetime("20261010", {}), ("2026-10-10T07:00:00Z", True), "8 digits read as a date")
        self.assertEqual(ics.parse_datetime("20261210T190000", {"TZID": "America/Los_Angeles"}),
                         ("2026-12-11T03:00:00Z", False), "winter time is UTC-8")
        self.assertEqual(ics.parse_datetime("20261010T020000", {}), ("2026-10-10T02:00:00Z", False),
                         "floating times are read as UTC")
        self.assertEqual(ics.parse_datetime("20261010T190000", {"TZID": "Not/A_Zone"}), ("2026-10-11T02:00:00Z", False),
                         "unknown zones fall back to Pacific")
        self.assertEqual(ics.parse_datetime(" 20261010T020000Z ", {}), ("2026-10-10T02:00:00Z", False))
        for bad in ("", "tomorrow", "2026-10-10", "20261310T000000Z"):
            with self.subTest(value=bad):
                self.assertEqual(ics.parse_datetime(bad, {}), (None, False))

    def test_event_times_without_start_or_end(self):
        self.assertEqual(ics.event_times({}), (None, None, False))
        self.assertEqual(ics.event_times({"DTSTART": "20261010T020000Z"}), ("2026-10-10T02:00:00Z", None, False))

    def test_parse_skips_unterminated_events_and_junk(self):
        self.assertEqual(ics.parse("BEGIN:VEVENT\r\nUID:x\r\n"), [])
        self.assertEqual(ics.parse(""), [])
        self.assertEqual(ics.parse("SUMMARY:outside\r\nBEGIN:VEVENT\r\nno colon here\r\nEND:VEVENT"), [{}])

    def test_looks_like_calendar(self):
        self.assertTrue(ics.looks_like_calendar(LUMA_FEED))
        self.assertTrue(ics.looks_like_calendar("﻿begin:vcalendar\r\n"))
        self.assertFalse(ics.looks_like_calendar("<!doctype html><title>Sign in</title>"))


class IcsBuildTest(unittest.TestCase):
    NOW_ISO = "2026-09-29T12:00:00Z"

    def event(self, **fields) -> dict:
        ev = {"id": "evt-1", "name": "Talk", "start_at": "2026-09-30T02:00:00Z", "end_at": "2026-09-30T04:00:00Z",
              "url": "https://luma.com/fixtalk1", "location": {}, "hosts": [], "presenter": None}
        ev.update(fields)
        return ev

    def build(self, *events: dict, name: str = "My events") -> str:
        return ics.build(list(events), name=name, now_iso=self.NOW_ISO)

    def assert_folded(self, text: str) -> None:
        self.assertTrue(text.endswith(CRLF))
        physical = text[:-2].split(CRLF)
        for line in physical:
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
            self.assertNotIn("\n", line)
            self.assertNotIn("\r", line)
        for line in physical:
            if line.startswith(" "):
                self.assertTrue(line[1:], "continuation lines carry content")

    def test_timed_event_fields(self):
        text = self.build(self.event(name="Mind, AI; and more", location={"venue": "Fixture Hall", "address": "100 Fixture Ave, San Jose"},
                                     hosts=[{"name": "Fixture Brain Bay Area"}, {"name": "Jordan Rivera"}]),
                          name="My events · Luma, Partiful")
        lines = text.split(CRLF)
        self.assertEqual(lines[:4], ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//events//EN", "CALSCALE:GREGORIAN"])
        self.assertIn(r"X-WR-CALNAME:My events · Luma\, Partiful", lines)
        for expected in ("UID:evt-1@luma-cal", "DTSTAMP:20260929T120000Z", "DTSTART:20260930T020000Z",
                         "DTEND:20260930T040000Z", r"SUMMARY:Mind\, AI\; and more", "URL:https://luma.com/fixtalk1"):
            self.assertIn(expected, lines)
        self.assertEqual(lines.count("BEGIN:VEVENT"), 1)
        self.assertEqual(lines[-2:], ["END:VCALENDAR", ""])
        parsed = ics.parse(text)[0]
        self.assertEqual(ics.unescape(parsed["LOCATION"]), "Fixture Hall, 100 Fixture Ave, San Jose")
        self.assertEqual(ics.unescape(parsed["DESCRIPTION"]), "Fixture Brain Bay Area, Jordan Rivera\nhttps://luma.com/fixtalk1")

    def test_end_defaults_to_start_and_events_without_start_are_skipped(self):
        text = self.build(self.event(end_at=None), self.event(id="evt-2", start_at=None))
        self.assertIn("DTEND:20260930T020000Z", text)
        self.assertNotIn("evt-2", text)

    def test_all_day_events_use_local_dates(self):
        text = self.build(self.event(id="evt-day", all_day=True, start_at="2026-10-10T07:00:00Z", end_at=None,
                                     timezone="America/Los_Angeles"),
                          self.event(id="evt-berlin", all_day=True, start_at="2026-10-09T22:00:00Z",
                                     end_at="2026-10-11T22:00:00Z", timezone="Europe/Berlin"))
        self.assertIn("DTSTART;VALUE=DATE:20261010", text)
        self.assertIn("DTEND;VALUE=DATE:20261011", text, "a missing end becomes the next day")
        self.assertIn("DTEND;VALUE=DATE:20261012", text)
        self.assertNotIn("DTSTART:2026", text)

    def test_online_events_without_address_say_online(self):
        self.assertIn("LOCATION:Online", self.build(self.event(location={"type": "online"})))
        self.assertIn("LOCATION:San Jose", self.build(self.event(location={"city": "San Jose"})))
        self.assertNotIn("LOCATION", self.build(self.event(location={"type": "offline"})))

    def test_long_lines_fold_at_75_octets(self):
        name = "A very long title " * 12
        text = self.build(self.event(name=name))
        self.assert_folded(text)
        self.assertIn(CRLF + " ", text)
        self.assertEqual(ics.unescape(ics.parse(text)[0]["SUMMARY"]), name)

    def test_folding_counts_octets_for_multibyte_characters(self):
        name = "Fiesta 🎉 " * 12 + "é" * 50 + "東京ワークショップ" * 5
        text = self.build(self.event(name=name, location={"venue": "Café Ünïcode", "address": "東京都渋谷区 " * 8}))
        self.assert_folded(text)
        parsed = ics.parse(text)[0]
        self.assertEqual(ics.unescape(parsed["SUMMARY"]), name)
        self.assertEqual(ics.unescape(parsed["LOCATION"]), "Café Ünïcode, " + "東京都渋谷区 " * 8)

    def test_fold_boundaries(self):
        self.assertEqual(ics._fold("x" * 75), ["x" * 75])
        self.assertEqual(ics._fold("x" * 76), ["x" * 75, " x"])
        self.assertEqual(ics._fold("x" * 74 + "é"), ["x" * 74, " é"], "a 2-octet character never straddles the limit")
        self.assertEqual(ics._fold(""), [""])
        parts = ics._fold("🎉" * 40)
        self.assertEqual(len(parts[0].encode()), 72)
        self.assertTrue(all(len(p.encode()) <= 75 for p in parts))
        self.assertEqual("".join(p[1:] if i else p for i, p in enumerate(parts)), "🎉" * 40)

    def test_description_names_the_presenting_calendar(self):
        text = self.build(self.event(presenter={"id": "cal-x", "name": "Fixture Brain Lectures", "url": None},
                                     hosts=[{"name": "Jordan Rivera"}]))
        self.assertEqual(ics.unescape(ics.parse(text)[0]["DESCRIPTION"]),
                         "Fixture Brain Lectures\nJordan Rivera\nhttps://luma.com/fixtalk1")

    def test_lone_surrogates_from_upstream_json_are_exportable(self):
        entry = json.loads(r'''{"event": {"api_id": "evt-1", "name": "Launch party \ud83c", "start_at": "2026-09-30T02:00:00Z"},
                                "hosts": [{"name": "DJ \udfff Kai"}]}''')
        ev = luma.normalize_entry(entry)
        text = self.build(ev)
        self.assertIn("SUMMARY:Launch party", text)
        text.encode("utf-8")


if __name__ == "__main__":
    unittest.main()

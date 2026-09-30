import unittest

from events import categorize


def vibes(name: str, **fields) -> list[str]:
    return categorize.classify({"name": name, **fields})[0]


def topics(name: str, **fields) -> list[str]:
    return categorize.classify({"name": name, **fields})[1]


class VibeTest(unittest.TestCase):
    def test_representative_titles_for_each_vibe(self):
        cases = {
            "Sunset Rooftop Party with DJ Sets": "party",
            "Founders Happy Hour & Mixer": "social",
            "Fireside Chat: Scaling Inference": "learn",
            "AI Agents Hackathon": "build",
            "Robotics Demo Night": "showcase",
            "Live Jazz Night at the Gallery": "culture",
            "Saturday Morning Run Club": "active",
            "Sound Bath & Breathwork": "wellness",
            "Omakase Supper Club": "food",
            "Board Game Night": "games",
        }
        self.assertEqual(sorted(cases.values()), sorted(categorize.VIBE_IDS), "one case per vibe")
        for title, vibe in cases.items():
            with self.subTest(title=title):
                self.assertEqual(vibes(title)[0], vibe)

    def test_several_vibes_and_the_cap(self):
        self.assertEqual(vibes("YC Demo Day Watch Party"), ["party", "showcase"])
        many = vibes("Hackathon, Demo Day, Dinner Party, Trivia and Yoga")
        self.assertEqual(len(many), categorize.MAX_PER_KIND)

    def test_weaker_fields_need_two_signals(self):
        run_club = {"name": "Run Club", "description": "Weekly runs from the Ferry Building"}
        self.assertEqual(categorize.classify({"name": "Monthly gathering", "presenter": run_club}), (["social"], []))
        # Tags alone are not enough for a topic; one strong tag is still the best weak vibe.
        self.assertEqual(categorize.classify({"name": "Session 4", "tags": ["AI", "Workshop"]}), (["learn"], []))
        self.assertEqual(categorize.classify({"name": "Session 4", "tags": ["AI", "LLMs"]}, ["AI Tinkerers"])[1], ["ai"])

    def test_calendar_names_count_as_presenter_signals(self):
        ev = {"name": "Monthly Night", "presenter": {"name": None, "description": "For robotics people"}}
        self.assertEqual(categorize.classify(ev)[1], [])
        self.assertEqual(categorize.classify(ev, ["SF Hardware Meetup"])[1], ["hardware"])
        self.assertEqual(categorize.classify({"name": "Monthly Night"}, ["SF Hardware Meetup", "Robotics Club"])[1], [],
                         "the calendar names form one field")

    def test_talk_shaped_titles_fall_back_to_learn(self):
        for title in ("What Should Stay Human?", "AGNTCon 2026", "Minds and Machines w/ Dr. Chen",
                      "Scaling Laws, featuring Jane Smith"):
            with self.subTest(title=title):
                self.assertEqual(vibes(title), ["learn"])

    def test_no_signal_means_no_vibe(self):
        self.assertEqual(vibes("Untitled"), [])
        self.assertEqual(vibes("Q4 Sync"), [])

    def test_specific_vibes_lead_on_ties(self):
        self.assertEqual(vibes("Trail Run & Coffee Meetup")[0], "active")


class TopicTest(unittest.TestCase):
    def test_representative_topics(self):
        cases = {
            "Building with LLM agents": "ai",
            "Crypto Builders Brunch": "crypto",
            "Founders & Investors Breakfast": "startups",
            "Longevity Biotech Salon": "bio",
            "Climate Tech Mixer": "climate",
            "Robotics Demo Night": "hardware",
            "Figma Design Systems Workshop": "design",
            "Rust Developers Meetup": "dev",
            "Quantum Physics for Everyone": "science",
            "Podcast Creators Mixer": "creators",
            "AI Policy Roundtable": "society",
        }
        for title, topic in cases.items():
            with self.subTest(title=title):
                self.assertIn(topic, topics(title))

    def test_whole_words_only(self):
        self.assertNotIn("ai", topics("Mountain Hike to the Summit Trail"))
        self.assertNotIn("ai", topics("Dumplings and Chai"))
        self.assertEqual(topics("Paint & Sip"), [])


class PlaceholderTest(unittest.TestCase):
    def test_room_holds_and_private_bookings(self):
        for title in ("HOLD - 2nd Floor Private Rental", "Philosophy Event (HOLD) Details to come!", "Hold: board room",
                      "[hold] studio", "TBD", "Blocked", "Placeholder for Q4 offsite", "Private Event", "  busy ",
                      "Test"):
            with self.subTest(title=title):
                self.assertTrue(categorize.is_placeholder({"name": title}))

    def test_real_events_are_kept(self):
        for title in ("Household Budgeting 101", "Holden Caulfield Book Club", "Holding Space: a meditation",
                      "Busy Founders Breakfast", "Protest Songs Night", "Private Equity 101", ""):
            with self.subTest(title=title):
                self.assertFalse(categorize.is_placeholder({"name": title}))
        self.assertFalse(categorize.is_placeholder({}))


class SizeAndTaxonomyTest(unittest.TestCase):
    def test_size_boundaries(self):
        cases = {1: "intimate", 39: "intimate", 40: "medium", 149: "medium", 150: "big", 5000: "big"}
        for count, size in cases.items():
            with self.subTest(count=count):
                self.assertEqual(categorize.size_of(count), size)
        for count in (None, 0, -5, "80", 12.5):
            with self.subTest(count=count):
                self.assertIsNone(categorize.size_of(count))

    def test_taxonomy_shape(self):
        tax = categorize.taxonomy()
        self.assertEqual(set(tax), {"version", "vibes", "topics", "sizes"})
        self.assertEqual(tax["version"], categorize.RULES_VERSION)
        self.assertEqual([v["id"] for v in tax["vibes"]], categorize.VIBE_IDS)
        self.assertEqual([t["id"] for t in tax["topics"]], categorize.TOPIC_IDS)
        self.assertEqual([s["id"] for s in tax["sizes"]], ["intimate", "medium", "big"])
        for item in tax["vibes"] + tax["topics"]:
            self.assertEqual(set(item), {"id", "label", "emoji", "hint"})
            self.assertTrue(all(item.values()))
        for size in tax["sizes"]:
            self.assertEqual(set(size), {"id", "label", "hint"})
        self.assertEqual(len(set(categorize.VIBE_IDS + categorize.TOPIC_IDS)), len(categorize.VIBE_IDS) + len(categorize.TOPIC_IDS))

    def test_every_vibe_has_a_specificity_rank(self):
        self.assertEqual(set(categorize.SPECIFICITY), set(categorize.VIBE_IDS))


if __name__ == "__main__":
    unittest.main()

"""
test_rubric.py — scoring engine, in isolation. No workbook, no filesystem, no network.

    python test_rubric.py

Fixtures are two example cards from SPEC.md: a single scorecard (66, Bronze) and a four-sitting
set (mean 89.0, Gold, one excluded).
"""
import unittest

from rubric import Rubric, half_up, load_rubric

# An example single card (66, Bronze)
EXAMPLE_CARD = dict(aroma=8, flavor=12, body=6, complexity=5, balance=8,
                    finish=5, uniqueness=6, drinkability=9, aesthetics=3, value=4)

# An example four-sitting set, the January one excluded
EXAMPLE_SITTINGS = [
    {"date": "2026-09-06", "include_in_average": True,
     "scores": dict(aroma=9, flavor=18, body=9, complexity=9, balance=9,
                    finish=9, uniqueness=8, drinkability=9, aesthetics=4, value=5)},
    {"date": "2026-08-12", "include_in_average": True,
     "scores": dict(aroma=9, flavor=18, body=9, complexity=9, balance=9,
                    finish=10, uniqueness=8, drinkability=9, aesthetics=5, value=5)},
    {"date": "2026-03-04", "include_in_average": True,
     "scores": dict(aroma=8, flavor=17, body=9, complexity=9, balance=9,
                    finish=9, uniqueness=8, drinkability=9, aesthetics=4, value=5)},
    {"date": "2026-01-02", "include_in_average": False, "why": "bottle just opened",
     "scores": dict(aroma=7, flavor=15, body=8, complexity=8, balance=8,
                    finish=8, uniqueness=7, drinkability=8, aesthetics=3, value=4)},
]


class TestHalfUpRounding(unittest.TestCase):
    """Python's round() is banker's rounding and would silently cost a medal."""

    def test_half_up_not_bankers(self):
        self.assertEqual(half_up(88.5), 89)
        self.assertEqual(round(88.5), 88)          # the trap this exists to avoid
        self.assertEqual(half_up(89.5), 90)
        self.assertEqual(half_up(89.4), 89)
        self.assertEqual(half_up(0.5), 1)


class TestRubricShape(unittest.TestCase):
    def setUp(self):
        self.r = load_rubric()

    def test_ten_categories_summing_to_one_hundred(self):
        self.assertEqual(len(self.r.categories), 10)
        self.assertEqual(self.r.max_total, 100)

    def test_flavor_is_worth_twenty_and_value_five(self):
        self.assertEqual(self.r.category("flavor").max, 20)
        self.assertEqual(self.r.category("value").max, 5)
        self.assertEqual(self.r.category("aesthetics").max, 5)

    def test_bands_cover_the_whole_range(self):
        for score, expected in [(100, "Diamond"), (90, "Diamond"), (89, "Gold"), (80, "Gold"),
                                (79, "Silver"), (70, "Silver"), (69, "Bronze"), (60, "Bronze"),
                                (59, "No Medal"), (0, "No Medal")]:
            self.assertEqual(self.r.medal(score), expected, f"score {score}")

    def test_medal_boundary_uses_half_up(self):
        self.assertEqual(self.r.medal(89.5), "Diamond")
        self.assertEqual(self.r.medal(89.4), "Gold")
        self.assertEqual(self.r.medal(79.5), "Gold")


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.r = load_rubric()

    def test_a_good_card_has_no_problems(self):
        self.assertEqual(self.r.validate(EXAMPLE_CARD), [])

    def test_over_max_is_rejected_per_category(self):
        bad = dict(EXAMPLE_CARD, flavor=21)
        self.assertTrue(any("Flavor" in p for p in self.r.validate(bad)))
        bad = dict(EXAMPLE_CARD, value=6)
        self.assertTrue(any("Value" in p for p in self.r.validate(bad)))
        ok = dict(EXAMPLE_CARD, flavor=20)                 # 20 is legal for Flavor
        self.assertEqual(self.r.validate(ok), [])

    def test_missing_and_unknown_categories(self):
        missing = {k: v for k, v in EXAMPLE_CARD.items() if k != "finish"}
        self.assertTrue(any("Finish" in p for p in self.r.validate(missing)))
        self.assertTrue(any("nose" in p for p in self.r.validate(dict(EXAMPLE_CARD, nose=8))))

    def test_non_integers_rejected(self):
        self.assertTrue(self.r.validate(dict(EXAMPLE_CARD, aroma=8.5)))
        self.assertTrue(self.r.validate(dict(EXAMPLE_CARD, aroma=True)))

    def test_total_raises_on_an_invalid_card(self):
        with self.assertRaises(ValueError):
            self.r.total(dict(EXAMPLE_CARD, flavor=99))


class TestTheRealCards(unittest.TestCase):
    def setUp(self):
        self.r = load_rubric()

    def test_buffalo_trace_scores_66_bronze(self):
        self.assertEqual(self.r.total(EXAMPLE_CARD), 66)
        self.assertEqual(self.r.medal(66), "Bronze")

    def test_buffalo_trace_is_four_points_off_silver(self):
        self.assertEqual(self.r.points_to_next_band(66), ("Silver", 4))

    def test_stagg_career_is_89_gold_from_three_of_four(self):
        c = self.r.career(EXAMPLE_SITTINGS)
        self.assertEqual(c["n"], 3)
        self.assertEqual(c["n_excluded"], 1)
        self.assertEqual(c["mean_total"], 89.0)
        self.assertEqual(c["medal"], "Gold")
        self.assertEqual(c["range"], (87, 91))
        self.assertEqual(c["next_band"], ("Diamond", 1))

    def test_excluded_sitting_really_is_excluded(self):
        counted = self.r.career(EXAMPLE_SITTINGS)["mean_total"]
        all_four = self.r.career([dict(s, include_in_average=True) for s in EXAMPLE_SITTINGS])["mean_total"]
        self.assertEqual(counted, 89.0)
        self.assertEqual(all_four, 85.8)
        self.assertNotEqual(counted, all_four)

    def test_per_category_means_and_ranges(self):
        cats = self.r.career(EXAMPLE_SITTINGS)["categories"]
        self.assertEqual(cats["aroma"]["mean"], 8.7)
        self.assertEqual((cats["aroma"]["min"], cats["aroma"]["max_seen"]), (8, 9))
        self.assertTrue(cats["aroma"]["varies"])
        self.assertEqual(cats["body"]["mean"], 9.0)
        self.assertFalse(cats["body"]["varies"])            # 9 every sitting
        self.assertEqual(cats["finish"]["mean"], 9.3)
        self.assertEqual((cats["finish"]["min"], cats["finish"]["max_seen"]), (9, 10))

    def test_exact_category_means_sum_to_the_headline(self):
        c = self.r.career(EXAMPLE_SITTINGS)
        self.assertAlmostEqual(sum(v["mean_exact"] for v in c["categories"].values()),
                               c["mean_total_exact"], places=9)

    def test_displayed_category_means_may_not_add_up_and_we_know_it(self):
        """Ten independent roundings drift. With all four sittings the displayed columns
        sum to 86.0 while the true mean of totals is 85.8. The UI must not print a column total."""
        c = self.r.career([dict(s, include_in_average=True) for s in EXAMPLE_SITTINGS])
        self.assertEqual(c["mean_total"], 85.8)
        self.assertEqual(c["display_sum"], 86.0)
        self.assertFalse(c["display_sum_matches"])

        counted = self.r.career(EXAMPLE_SITTINGS)                    # the 3-sitting case happens to agree
        self.assertTrue(counted["display_sum_matches"])

    def test_single_sitting_needs_no_special_case(self):
        c = self.r.career([{"scores": EXAMPLE_CARD, "include_in_average": True}])
        self.assertEqual(c["n"], 1)
        self.assertEqual(c["mean_total"], 66)
        self.assertEqual(c["range"], (66, 66))
        self.assertFalse(c["categories"]["aroma"]["varies"])

    def test_no_counted_sittings_is_not_a_crash(self):
        c = self.r.career([dict(s, include_in_average=False) for s in EXAMPLE_SITTINGS])
        self.assertEqual(c["n"], 0)
        self.assertIsNone(c["mean_total"])
        self.assertIsNone(c["medal"])


class TestRubricIsConfigDriven(unittest.TestCase):
    """A different scale must work without touching this module."""

    def test_a_hundred_point_five_axis_rubric(self):
        r = Rubric({
            "name": "test", "version": 2, "decimals": 1,
            "categories": [{"key": "a", "label": "A", "max": 25},
                           {"key": "b", "label": "B", "max": 25},
                           {"key": "c", "label": "C", "max": 50}],
            "medals": [{"name": "Top", "min": 80}, {"name": "None", "min": 0}],
        })
        self.assertEqual(r.max_total, 100)
        self.assertEqual(r.total({"a": 20, "b": 20, "c": 45}), 85)
        self.assertEqual(r.medal(85), "Top")
        self.assertEqual(r.medal(79), "None")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""
test_quickentry.py — the phone lane, in isolation.

    python test_quickentry.py

Builds a small workbook with a Quick Entry sheet, drains it, and checks what lands in the journal.
Temp dirs only; the master collection workbook is never opened by this module or its tests.
"""
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

import quickentry
import rubric as rubric_mod
import store

# Synthetic catalog. S-9 and S-10 deliberately share a name — that collision is the whole reason
# matching refuses to guess (SPEC.md §2).
SPIRITS = [
    {"code": "B-1", "display_name": "Example Distillery Single Barrel", "name": "Single Barrel"},
    {"code": "B-2", "display_name": "Sample Co Test Rye", "name": "Test Rye"},
    {"code": "S-9", "display_name": "Twin Co Barrel Pick", "name": "Barrel Pick"},
    {"code": "S-10", "display_name": "Twin Co Barrel Pick", "name": "Barrel Pick"},
]


class QuickCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="quick-test-"))
        self.book = self.tmp / "Whiskey Tastings.xlsx"
        self.j = store.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_book(self, rows, sheet=quickentry.SHEET, headers=None):
        headers = headers or quickentry.HEADERS
        wb = Workbook()
        ws = wb.active
        ws.title = sheet
        ws.append(headers)
        for r in rows:
            ws.append([r.get(h) for h in headers])
        wb.save(self.book)
        wb.close()

    def drain(self):
        return quickentry.drain(self.book, self.j, SPIRITS)


class TestReading(QuickCase):
    def test_blank_rows_are_ignored(self):
        self.write_book([{"display_name": "Example Distillery Single Barrel", "nose": "caramel"},
                         {}, {"display_name": None}])
        rows = quickentry.read_rows(self.book)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["nose"], "caramel")
        self.assertEqual(rows[0]["_row"], 2)

    def test_a_real_date_cell_becomes_an_iso_string(self):
        self.write_book([{"date": datetime(2026, 9, 8), "display_name": "Sample Co Test Rye"}])
        self.assertEqual(quickentry.read_rows(self.book)[0]["date"], "2026-09-08")

    def test_a_missing_workbook_is_not_an_error(self):
        self.assertEqual(quickentry.read_rows(self.tmp / "nope.xlsx"), [])

    def test_a_workbook_without_the_sheet_is_not_an_error(self):
        self.write_book([{"display_name": "x"}], sheet="Something Else")
        self.assertEqual(quickentry.read_rows(self.book), [])


class TestMatching(QuickCase):
    def match(self, text):
        return quickentry.match_spirit(text, SPIRITS)

    def test_a_bottle_code_wins_outright(self):
        self.assertEqual(self.match("B-2"), ("B-2", None))
        self.assertEqual(self.match("  b-2 "), ("B-2", None))

    def test_an_exact_name_matches(self):
        self.assertEqual(self.match("Example Distillery Single Barrel"), ("B-1", None))
        self.assertEqual(self.match("sample co   test rye"), ("B-2", None))

    def test_a_partial_name_matches_when_it_is_unique(self):
        self.assertEqual(self.match("test rye"), ("B-2", None))

    def test_a_shared_name_is_refused_rather_than_guessed(self):
        """Two bottles share this name; picking one would put a score on the wrong bottle."""
        code, problem = self.match("Twin Co Barrel Pick")
        self.assertIsNone(code)
        self.assertIn("ambiguous", problem)
        self.assertIn("S-9", problem)

    def test_an_unknown_name_is_refused(self):
        code, problem = self.match("Something Nobody Owns")
        self.assertIsNone(code)
        self.assertEqual(problem, "no match in the collection")

    def test_an_empty_name_is_refused(self):
        self.assertEqual(self.match("")[1], "no name typed")
        self.assertEqual(self.match(None)[1], "no name typed")

    def test_an_unknown_code_is_refused(self):
        code, problem = self.match("B-999")
        self.assertIsNone(code)
        self.assertIn("B-999", problem)


class TestDraining(QuickCase):
    def test_a_matched_row_becomes_an_unscored_draft(self):
        self.write_book([{"date": "2026-09-08", "display_name": "B-1", "barrel_id": "F664",
                          "nose": "orange peel", "palate": "toffee", "finish": "long",
                          "notes": "worth revisiting"}])
        summary = self.drain()
        self.assertEqual(summary["counts"], {"read": 1, "drained": 1, "unmatched": 0})

        t = self.j.tastings()[0]
        self.assertEqual(t["spirit_id"], "B-1")
        self.assertEqual(t["status"], "draft")
        self.assertEqual(t["entered_from"], "quick-entry")
        self.assertEqual(t["date"], "2026-09-08")
        self.assertEqual(t["barrel_id"], "F664")
        self.assertEqual(t["scores"], {})
        self.assertIsNone(t["total"])
        self.assertIsNone(t["medal"])
        self.assertFalse(t["include_in_average"])

    def test_the_three_impressions_land_on_the_right_categories(self):
        self.write_book([{"display_name": "B-1", "nose": "orange peel", "palate": "toffee",
                          "finish": "long", "notes": "overall thought"}])
        self.drain()
        notes = self.j.tastings()[0]["notes"]
        self.assertEqual(notes["aroma"], "orange peel")
        self.assertEqual(notes["flavor"], "toffee")
        self.assertEqual(notes["finish"], "long")
        self.assertEqual(notes["overall"], "overall thought")

    def test_an_unmatched_row_is_returned_and_nothing_is_written(self):
        self.write_book([{"display_name": "Twin Co Barrel Pick", "nose": "smoke"},
                         {"display_name": "Nobody Owns This", "nose": "peat"}])
        summary = self.drain()
        self.assertEqual(summary["counts"]["drained"], 0)
        self.assertEqual(summary["counts"]["unmatched"], 2)
        self.assertEqual(self.j.tastings(), [], "nothing may reach the journal unmatched")
        self.assertTrue(all(u["problem"] for u in summary["unmatched"]))
        self.assertEqual(summary["unmatched"][0]["nose"], "smoke", "the typing is preserved")

    def test_matched_and_unmatched_rows_are_separated(self):
        self.write_book([{"display_name": "B-1", "nose": "caramel"},
                         {"display_name": "Twin Co Barrel Pick", "nose": "smoke"},
                         {"display_name": "Sample Co Test Rye", "nose": "dill"}])
        summary = self.drain()
        self.assertEqual(summary["counts"], {"read": 3, "drained": 2, "unmatched": 1})
        self.assertEqual({d["spirit_id"] for d in summary["drained"]}, {"B-1", "B-2"})

    def test_a_draft_never_moves_a_career_score(self):
        """An unscored card has no opinion in it yet — rubric.career() would also choke on it."""
        self.write_book([{"display_name": "B-1", "nose": "caramel"}])
        self.drain()
        career = self.j.career("B-1")
        self.assertEqual(career["n"], 0)
        self.assertIsNone(career["mean_total"])
        self.assertEqual(career["n_total"], 1, "but it is visible in the history")

    def test_a_draft_alongside_a_real_score_leaves_the_score_alone(self):
        from test_rubric import EXAMPLE_CARD
        self.j.write_tasting(spirit_id="B-1", scores=EXAMPLE_CARD)
        self.write_book([{"display_name": "B-1", "nose": "caramel"}])
        self.drain()
        career = self.j.career("B-1")
        self.assertEqual(career["n"], 1)
        self.assertEqual(career["mean_total"], 66)

    def test_draining_nothing_is_harmless(self):
        self.write_book([])
        self.assertEqual(self.drain()["counts"], {"read": 0, "drained": 0, "unmatched": 0})
        self.assertEqual(self.j.tastings(), [])


    def test_a_flagged_reason_is_read_back_so_the_ui_can_show_it(self):
        """The reason is written onto the sheet; it has to survive the round trip or the flag is
        invisible in the app."""
        self.write_book(
            [{"display_name": "Nobody Owns This", "nose": "peat", "problem": "no match"}],
            headers=quickentry.HEADERS + ["problem"])
        row = quickentry.read_rows(self.book)[0]
        self.assertEqual(row["problem"], "no match")
        self.assertEqual(row["nose"], "peat")

    def test_a_stale_reason_does_not_block_a_row_that_now_matches(self):
        self.write_book(
            [{"display_name": "B-1", "nose": "caramel", "problem": "no match in the collection"}],
            headers=quickentry.HEADERS + ["problem"])
        summary = self.drain()
        self.assertEqual(summary["counts"]["drained"], 1)
        t = self.j.tastings()[0]
        self.assertEqual(t["spirit_id"], "B-1")
        self.assertNotIn("problem", t["notes"], "the reason is display only")


class TestStoreGuard(QuickCase):
    def test_a_submitted_card_still_may_not_be_unscored(self):
        with self.assertRaises(ValueError):
            self.j.write_tasting(spirit_id="B-1")                       # status defaults submitted
        with self.assertRaises(ValueError):
            self.j.write_tasting(spirit_id="B-1", status="submitted", scores={})

    def test_an_unscored_draft_can_never_be_counted_even_if_asked(self):
        rec = self.j.write_tasting(spirit_id="B-1", status="draft", include_in_average=True)
        self.assertFalse(rec["include_in_average"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

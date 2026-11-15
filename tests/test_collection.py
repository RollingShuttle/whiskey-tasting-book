"""
test_collection.py — proves step 1 works on its own, against the real workbook.

    python tests/test_collection.py
    WHISKEY_MASTER="/some/path/Whiskey Collection.xlsx" python test_collection.py

The most important test here is test_master_is_never_modified: it SHA-256s the 147 MB workbook
before and after a full load. If that ever fails, stop and do not run anything else in this
project — 198 bottle photos are at stake.
"""

# Run straight from the shell, only tests/ is on the path; discovered from the repo root, only
# the root is. Put all three where imports can find them so both ways of running behave alike.
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT), str(ROOT / "tools")]

import hashlib
import json
import os
import tempfile
import unittest

import collection as C

CONFIG = "config.yaml"


def sha256(path):
    d = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


class TestPureHelpers(unittest.TestCase):
    """No file access — these must pass anywhere."""

    def test_parse_code_unpadded(self):
        self.assertEqual(C.parse_code("B-1"), ("B", 1))
        self.assertEqual(C.parse_code("S-209"), ("S", 209))
        self.assertEqual(C.parse_code(" M-23 "), ("M", 23))
        for bad in ("B1", "B-", "X-4", "", None, "B-01a"):
            self.assertIsNone(C.parse_code(bad), f"{bad!r} should not parse")

    def test_codes_sort_numerically_not_lexically(self):
        codes = ["B-2", "B-10", "B-1", "B-100"]
        by_int = sorted(codes, key=lambda c: C.parse_code(c)[1])
        self.assertEqual(by_int, ["B-1", "B-2", "B-10", "B-100"])
        self.assertNotEqual(sorted(codes), by_int)          # the bug this guards against

    def test_num_tolerates_the_real_junk(self):
        for junk in ("NAS", "N/A", "#VALUE!", "", "  ", None, "unknown", "—"):
            self.assertIsNone(C._num(junk), f"{junk!r} should be None")
        self.assertEqual(C._num("101.4"), 101.4)
        self.assertEqual(C._num("$729.99"), 729.99)
        self.assertEqual(C._num("1,250"), 1250.0)
        self.assertEqual(C._num(90), 90.0)
        self.assertIsNone(C._num(True))                     # bools are not proof values


class TestAgainstRealWorkbook(unittest.TestCase):
    coll = None
    before = None

    @classmethod
    def setUpClass(cls):
        import yaml
        with open(CONFIG, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        cls.path = C.resolve_master(cfg)
        if not cls.path.exists():
            raise unittest.SkipTest(f"master workbook not reachable at {cls.path}")
        try:
            cls.before = sha256(cls.path)
        except PermissionError:
            # Excel holds a lock while the workbook is open, and it is open whenever bottles are
            # being deleted from it. That is not a failure of anything; there is simply nothing
            # to check until it is closed.
            raise unittest.SkipTest("the workbook is open in Excel — close it to run these")
        cls.coll = C.load(CONFIG)
        for s in cls.coll.rows:                             # force the second read pass too
            cls.coll.formula_columns(s)

    def test_master_is_never_modified(self):
        self.assertEqual(sha256(self.path), self.before,
                         "THE MASTER WORKBOOK CHANGED. Stop. 198 photos are at risk.")

    def test_row_counts_are_plausible(self):
        """Not an exact match. Bottles and samples are deleted from the workbook as they are
        finished, so the count drifts down by design and pinning it would fail every time you
        emptied a bottle. What still has to hold is that the read found the table at all."""
        for sheet, reference in C.BASELINE.items():
            found = len(self.coll.rows[sheet])
            self.assertGreater(found, reference * C.COLLAPSE_FRACTION,
                               f"{sheet}: {found} rows is a broken read, not a shrinking shelf")
            self.assertLessEqual(found, reference + 500, f"{sheet} row count implausibly high")


    def test_every_code_is_present_unique_and_well_formed(self):
        for sheet, rows in self.coll.rows.items():
            prefix = C.CODE_PREFIX[sheet]
            seen = set()
            for r in rows:
                p = C.parse_code(r["code"])
                self.assertIsNotNone(p, f"{sheet} row {r['_row']} has code {r['code']!r}")
                self.assertEqual(p[0], prefix, f"{sheet} row {r['_row']} wrong prefix")
                self.assertNotIn(r["code"], seen, f"{sheet} duplicate {r['code']}")
                seen.add(r["code"])

    def test_next_code_is_highest_plus_one(self):
        for sheet in self.coll.rows:
            highest = max(C.parse_code(r["code"])[1] for r in self.coll.rows[sheet])
            self.assertEqual(self.coll.next_code(sheet),
                             f"{C.CODE_PREFIX[sheet]}-{highest + 1}")

    def test_first_empty_row_is_inside_the_table_and_unused(self):
        for sheet, tdef in self.coll.tables.items():
            _, r1, _, r2 = tdef.bounds
            row = self.coll.first_empty_row(sheet)
            self.assertIsNotNone(row, f"{sheet} table is full")
            self.assertGreater(row, r1, f"{sheet} first empty row is at or above the header")
            self.assertLessEqual(row, r2, f"{sheet} first empty row is outside the table")
            self.assertNotIn(row, {r["_row"] for r in self.coll.rows[sheet]})

    def test_abv_is_a_formula_on_every_sheet(self):
        """Regression guard. Bottle's ABV is filled-down, not a declared calculated column, so
        Excel will not auto-apply it to a row an outside tool writes."""
        for sheet in ("Bottle", "Miniature", "Sample"):
            fc = self.coll.formula_columns(sheet)
            self.assertIn("ABV", fc, f"{sheet} ABV should be a formula")
            self.assertTrue(fc["ABV"]["uses_a1_refs"],
                            f"{sheet} ABV formula must have its row refs shifted when written")
        self.assertFalse(self.coll.formula_columns("Bottle")["ABV"]["declared"],
                         "Bottle ABV is filled-down, not declared — the writer must copy it")

    def test_a_bottle_survives_the_round_trip(self):
        """Shape check on B-1 without pinning real collection values into the repo: the code
        resolves, string and numeric fields come through, and NAS ages parse to None while their
        label is preserved."""
        bottles = {r["code"]: r for r in self.coll.rows["Bottle"]}
        self.assertIn("B-1", bottles)
        b1 = bottles["B-1"]
        self.assertTrue(b1["distillery"])                   # a non-empty distillery string
        self.assertTrue(b1["name"])
        self.assertIsInstance(b1["proof"], (float, type(None)))
        self.assertIn(b1["status"], {"Opened", "Unopened", None})
        # NAS handling: at least one bottle parses to age None but keeps its original label
        nas = [r for r in self.coll.rows["Bottle"] if r.get("age") is None and r.get("age_label")]
        self.assertTrue(nas, "expected at least one NAS bottle with a preserved age_label")

    def test_snapshot_is_valid_json_and_small_enough_for_a_phone(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "collection.json"
            p.write_text(json.dumps(self.coll.snapshot(), ensure_ascii=False), encoding="utf-8")
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(len(data["spirits"]),
                             sum(len(r) for r in self.coll.rows.values()))
            self.assertLess(p.stat().st_size, 2_000_000, "snapshot too big to sync comfortably")

    def test_no_errors_reported(self):
        self.assertEqual([f"[{i.sheet}] {i.message}" for i in self.coll.errors], [])


class TestDropdowns(unittest.TestCase):
    """The workbook's own Type drop-down is what the phone's Add form offers, so it is read out
    of the file. Synthetic: two sheets whose lists differ by a word, the way the real ones do."""

    HEAD = ["Bottle Code", "Distillery", "Name", "Type", "Region", "Status"]     # columns B..G

    def build(self, lists):
        from openpyxl import Workbook
        from openpyxl.worksheet.datavalidation import DataValidation
        from openpyxl.worksheet.table import Table
        tmp = tempfile.TemporaryDirectory(prefix="dropdown-test-")
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "Whiskey Collection.xlsx"
        wb = Workbook()
        for n, (title, formula) in enumerate(lists.items()):
            ws = wb.active if n == 0 else wb.create_sheet()
            ws.title = title
            for i, h in enumerate(self.HEAD, start=2):
                ws.cell(row=8, column=i, value=h)
            for i, v in enumerate([f"{title[0]}-1", "A Distillery", "One", "Bourbon"], start=2):
                ws.cell(row=9, column=i, value=v)
            ws.add_table(Table(displayName=f"Table{n}", ref="B8:G20"))
            if formula:
                dv = DataValidation(type="list", formula1=formula)
                ws.add_data_validation(dv)
                dv.add("E9:E20")                          # Type
            kept_in_cells = DataValidation(type="list", formula1="$Z$1:$Z$5")
            ws.add_data_validation(kept_in_cells)
            kept_in_cells.add("F9:F20")                   # Region
        wb.save(path)
        wb.close()
        return path

    def test_a_list_is_read_under_its_column_name(self):
        path = self.build({"Bottle": '"Bourbon,Rye, Gin ,Rum"'})
        lists = C._read_dropdowns(path, C._read_table_defs(path))
        self.assertEqual(lists["Bottle"]["Type"], ["Bourbon", "Rye", "Gin", "Rum"])
        self.assertNotIn("Region", lists["Bottle"],
                         "a list kept in cells is not followed: those cells are not ours to read")

    def test_the_sheets_merge_in_reading_order(self):
        """Vodka is on the second sheet's list only, after Gin. It belongs after Gin in the
        merged list, not at the end of it."""
        path = self.build({"Bottle": '"Bourbon,Rye,Gin,Rum"',
                           "Miniature": '"bourbon,Rye,Gin,Vodka,Rum,Sake"'})
        coll = C.Collection(path).load()
        self.assertEqual(coll.options("Type"), ["Bourbon", "Rye", "Gin", "Vodka", "Rum", "Sake"])
        self.assertEqual(coll.snapshot()["types"], coll.options("Type"),
                         "the snapshot is how the list reaches the phone")
        json.dumps(coll.snapshot()["types"])

    def test_a_workbook_with_no_list_offers_none(self):
        path = self.build({"Bottle": None})
        coll = C.Collection(path).load()
        self.assertEqual(coll.options("Type"), [])
        self.assertEqual(coll.snapshot()["types"], [])
        self.assertFalse([i for i in coll.issues if "drop-down" in i.message])

    def test_reading_the_lists_leaves_the_file_alone(self):
        path = self.build({"Bottle": '"Bourbon,Rye"'})
        was = hashlib.sha256(path.read_bytes()).hexdigest()
        C.Collection(path).load()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), was)

    def test_a_list_that_cannot_be_read_does_not_stop_the_collection(self):
        from unittest import mock
        path = self.build({"Bottle": '"Bourbon,Rye"'})
        with mock.patch.object(C, "_read_dropdowns", side_effect=ValueError("not as expected")):
            coll = C.Collection(path).load()
        self.assertEqual(len(coll.rows["Bottle"]), 1, "the bottles are what the load is for")
        self.assertEqual(coll.options("Type"), [])
        self.assertTrue([i for i in coll.issues if i.level == "warn" and "drop-down" in i.message])


if __name__ == "__main__":
    unittest.main(verbosity=2)

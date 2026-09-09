"""
test_app.py — the local server, in isolation.

    python test_app.py

Runs against Flask's test client. The journal is a throwaway temp directory and the collection is
a tiny fixture snapshot, so this touches no OneDrive folder and never opens the 147 MB master. The
snapshot-only hot path is proven by pointing the master override at a path that does not exist and
confirming the spirit list still loads.
"""
import json
import re
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import app as app_mod
import rubric as rubric_mod
import store as store_mod
from test_rubric import EXAMPLE_CARD

# Synthetic fixtures — invented bottles, not the real collection. The server logic under test does
# not care what the values are, only their shape, so nothing real needs to live in the repo.
FIXTURE_SNAPSHOT = {
    "generated_from": "fixture",
    "counts": {"Bottle": 1, "Sample": 1},
    "next_code": {"Bottle": "B-145", "Sample": "S-210"},
    "first_empty_row": {"Bottle": 153, "Sample": 218},
    "spirits": [
        {"_sheet": "Bottle", "_row": 26, "code": "B-18", "distillery": "Example Distillery",
         "name": "Single Barrel", "type": "Bourbon", "region": "America", "age": None,
         "age_label": "NAS", "proof": 100.0, "abv": 50.0, "size_ml": 750.0, "paid": 100.0,
         "conc_ratio": 1.15, "status": "Opened", "rarity": "Uncommon"},
        {"_sheet": "Sample", "_row": 9, "code": "S-1", "distillery": "Sample Co",
         "name": "Test Rye", "type": "Rye", "region": "Scotland", "proof": 120.0,
         "abv": 60.0, "sizeoz": 1.0, "age": 8.0, "conc_ratio": 0.95},
    ],
}


class AppCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="app-test-"))
        self.snap = self.tmp / "collection.json"
        self.snap.write_text(json.dumps(FIXTURE_SNAPSHOT), encoding="utf-8")
        self.app = app_mod.create_app(
            "config.yaml",
            app_folder=str(self.tmp / "journal"),
            snapshot_path=str(self.snap),
            master="Z:/nonexistent/Whiskey Collection.xlsx",   # must never be opened on hot paths
            rollup=str(self.tmp / "Whiskey Tastings.xlsx"),
            backups=str(self.tmp / "backups"),
        )
        self.book = self.tmp / "Whiskey Tastings.xlsx"
        self.c = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, payload):
        return self.c.post("/api/tasting", json=payload)


class TestConfig(AppCase):
    def test_config_is_the_full_rubric(self):
        cfg = self.c.get("/api/config").get_json()
        cats = cfg["rubric"]["categories"]
        self.assertEqual(len(cats), 10)
        self.assertEqual({c["key"]: c["max"] for c in cats}["flavor"], 20)
        self.assertEqual({c["key"]: c["max"] for c in cats}["aesthetics"], 5)
        self.assertEqual(cfg["rubric"]["max_total"], 100)

    def test_question_text_travels_for_the_tooltips(self):
        cats = self.c.get("/api/config").get_json()["rubric"]["categories"]
        aroma = next(c for c in cats if c["key"] == "aroma")
        self.assertIn("nose", aroma["question"].lower())
        self.assertTrue(all(c["question"] for c in cats), "every row needs its ? text")

    def test_bands_and_medal_colors(self):
        cfg = self.c.get("/api/config").get_json()
        bands = {b["name"]: b["min"] for b in cfg["rubric"]["bands"]}
        self.assertEqual(bands["Diamond"], 90)
        self.assertEqual(bands["No Medal"], 0)
        self.assertIn("Diamond", cfg["medal_colors"])


class TestSpirits(AppCase):
    def test_spirit_list_comes_from_the_snapshot(self):
        data = self.c.get("/api/spirits").get_json()
        self.assertEqual(data["count"], 2)
        codes = {s["code"] for s in data["spirits"]}
        self.assertEqual(codes, {"B-18", "S-1"})

    def test_derived_fields_are_added(self):
        spirits = {s["code"]: s for s in self.c.get("/api/spirits").get_json()["spirits"]}
        b = spirits["B-18"]
        self.assertEqual(b["display_name"], "Example Distillery Single Barrel")
        # 750 ml -> 25.36 oz; 100.00 / 25.36 = 3.94/oz
        self.assertAlmostEqual(b["value_per_oz"], round(100.0 / (750 / app_mod.ML_PER_OZ), 2), places=2)
        self.assertIsNone(spirits["S-1"].get("value_per_oz"))   # no paid on a sample

    def test_hot_path_never_needs_the_master(self):
        """The master override points at a missing file; spirits must still load from the snapshot."""
        self.assertEqual(self.c.get("/api/spirits").status_code, 200)

    def test_unknown_spirit_is_404(self):
        self.assertEqual(self.c.get("/api/spirit/B-999").status_code, 404)

    def test_spirit_detail_has_empty_career_before_any_tasting(self):
        d = self.c.get("/api/spirit/B-18").get_json()
        self.assertEqual(d["spirit"]["code"], "B-18")
        self.assertEqual(d["career"]["n"], 0)
        self.assertEqual(d["sittings"], [])


class TestSubmit(AppCase):
    def test_buffalo_trace_scores_66_bronze_and_persists(self):
        r = self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD,
                        "notes": {"aroma": "caramel"}, "overall_notes": "solid daily pour",
                        "date": "2026-09-08"})
        self.assertEqual(r.status_code, 201)
        body = r.get_json()
        self.assertEqual(body["tasting"]["total"], 66)
        self.assertEqual(body["tasting"]["medal"], "Bronze")
        self.assertEqual(body["next_band"], ["Silver", 4])
        # overall note rides under the reserved key so store.py stays a single notes dict
        self.assertEqual(body["tasting"]["notes"]["overall"], "solid daily pour")

        career = self.c.get("/api/spirit/B-18").get_json()["career"]
        self.assertEqual(career["n"], 1)
        self.assertEqual(career["mean_total"], 66)

    def test_invalid_card_is_rejected_and_nothing_is_written(self):
        r = self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=21)})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(any("Flavor" in p for p in r.get_json()["problems"]))
        self.assertEqual(self.c.get("/api/spirit/B-18").get_json()["career"]["n"], 0)

    def test_fractional_score_is_rejected(self):
        r = self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, aroma=8.5)})
        self.assertEqual(r.status_code, 400)

    def test_missing_spirit_id_is_rejected(self):
        self.assertEqual(self._post({"scores": EXAMPLE_CARD}).status_code, 400)

    def test_an_encounter_code_can_be_scored_even_though_it_is_not_in_the_catalog(self):
        r = self._post({"spirit_id": "X-1", "scores": EXAMPLE_CARD,
                        "venue": "Bar", "pour_price": 30, "pour_size_oz": 1.5})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["tasting"]["spirit_id"], "X-1")


class TestSessions(AppCase):
    def _new_session(self, **kw):
        r = self.c.post("/api/session", json=dict({"title": "Thursday flight"}, **kw))
        self.assertEqual(r.status_code, 201)
        return r.get_json()["session"]

    def test_a_flight_is_created_and_listed(self):
        s = self._new_session(location="Home", blind=True)
        self.assertTrue(s["session_id"].startswith("F-"))
        listed = self.c.get("/api/sessions").get_json()["sessions"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["title"], "Thursday flight")
        self.assertEqual(listed[0]["pours"], 0)

    def test_an_empty_flight_starts_at_position_one(self):
        sid = self._new_session()["session_id"]
        d = self.c.get(f"/api/session/{sid}").get_json()
        self.assertEqual(d["pours"], [])
        self.assertEqual(d["next_flight_pos"], 1)

    def test_pours_link_to_the_flight_and_carry_their_spirit(self):
        sid = self._new_session()["session_id"]
        r = self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD,
                        "session_id": sid, "flight_pos": 1})
        self.assertEqual(r.status_code, 201)

        d = self.c.get(f"/api/session/{sid}").get_json()
        self.assertEqual(len(d["pours"]), 1)
        self.assertEqual(d["pours"][0]["flight_pos"], 1)
        self.assertEqual(d["pours"][0]["spirit"]["display_name"],
                         "Example Distillery Single Barrel")
        self.assertEqual(d["next_flight_pos"], 2)
        self.assertEqual(self.c.get("/api/sessions").get_json()["sessions"][0]["pours"], 1)

    def test_pours_come_back_in_flight_order(self):
        sid = self._new_session()["session_id"]
        self._post({"spirit_id": "S-1", "scores": EXAMPLE_CARD, "session_id": sid, "flight_pos": 2})
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD, "session_id": sid, "flight_pos": 1})
        pours = self.c.get(f"/api/session/{sid}").get_json()["pours"]
        self.assertEqual([p["flight_pos"] for p in pours], [1, 2])
        self.assertEqual([p["spirit_id"] for p in pours], ["B-18", "S-1"])

    def test_a_string_flight_pos_is_coerced_to_an_int(self):
        """Sorting pours would break comparing '2' against 1."""
        sid = self._new_session()["session_id"]
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD,
                    "session_id": sid, "flight_pos": "3"})
        pours = self.c.get(f"/api/session/{sid}").get_json()["pours"]
        self.assertEqual(pours[0]["flight_pos"], 3)

    def test_amending_a_flight_writes_a_new_revision(self):
        s = self._new_session()
        r = self.c.post("/api/session", json={"session_id": s["session_id"],
                                              "title": "Barrel picks", "blind": True})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["session"]["revision"], 2)
        listed = self.c.get("/api/sessions").get_json()["sessions"]
        self.assertEqual(len(listed), 1, "only the highest revision is live")
        self.assertEqual(listed[0]["title"], "Barrel picks")
        self.assertTrue(listed[0]["blind"])

    def test_unknown_session_is_404(self):
        self.assertEqual(self.c.get("/api/session/F-20260101-000000-dead").status_code, 404)

    def test_a_standalone_pour_joins_no_flight(self):
        sid = self._new_session()["session_id"]
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})      # no session_id
        self.assertEqual(self.c.get(f"/api/session/{sid}").get_json()["pours"], [])


class TestTableView(AppCase):
    def _journal(self):
        return store_mod.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()

    def _collection(self):
        r = self.c.get("/api/table/collection")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def _by_code(self):
        return {r["code"]: r for r in self._collection()["rows"]}

    def test_collection_table_has_a_row_per_spirit_and_a_column_contract(self):
        d = self._collection()
        self.assertEqual({r["code"] for r in d["rows"]}, {"B-18", "S-1"})
        for c in d["columns"]:
            self.assertTrue(c["key"] and c["label"] and c["type"])
            self.assertIn("default", c)

    def test_an_unscored_spirit_has_no_career(self):
        row = self._by_code()["B-18"]
        self.assertIsNone(row["career_score"])
        self.assertIsNone(row["medal"])
        self.assertEqual(row["n"], 0)

    def test_scoring_fills_in_the_career_columns(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        row = self._by_code()["B-18"]
        self.assertEqual(row["career_score"], 66)
        self.assertEqual(row["medal"], "Bronze")
        self.assertEqual(row["n"], 1)
        self.assertEqual((row["worst"], row["best"]), (66, 66))

    def test_the_two_columns_the_spreadsheet_cannot_do(self):
        """$ / oz and score / $ — SPEC.md §4.3, §10."""
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        row = self._by_code()["B-18"]
        self.assertAlmostEqual(row["value_per_oz"],
                               round(100.0 / (750 / app_mod.ML_PER_OZ), 2), places=2)
        self.assertAlmostEqual(row["score_per_dollar"], round(66 / 100.0, 3), places=3)
        self.assertIsNone(self._by_code()["S-1"]["value_per_oz"])     # no paid on a sample

    def test_career_is_the_mean_of_sittings_not_the_last_one(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})                     # 66
        self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=16)})    # 70
        row = self._by_code()["B-18"]
        self.assertEqual(row["n"], 2)
        self.assertEqual(row["career_score"], 68.0)
        self.assertEqual((row["worst"], row["best"]), (66, 70))

    def test_an_excluded_sitting_is_listed_but_does_not_count(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=20),
                    "include_in_average": False})
        rows = self.c.get("/api/table/tastings").get_json()["rows"]
        self.assertEqual(sorted(r["counted"] for r in rows), ["no", "yes"])
        row = self._by_code()["B-18"]
        self.assertEqual((row["n"], row["n_total"]), (1, 2))
        self.assertEqual(row["career_score"], 66)

    def test_encounters_sit_alongside_owned_bottles(self):
        j = self._journal()
        j.write_encounter(name="Bar Pour", distillery="Somewhere")
        j.assign_encounter_codes()
        by = self._by_code()
        self.assertIn("X-1", by)
        self.assertFalse(by["X-1"]["owned"])
        self.assertTrue(by["B-18"]["owned"])
        self.assertEqual(by["X-1"]["display_name"], "Somewhere Bar Pour")
        self.assertEqual(by["X-1"]["source"], "Encounter")

    def test_a_linked_encounter_stops_being_its_own_row(self):
        j = self._journal()
        e = j.write_encounter(name="Later Bought", distillery="Somewhere")
        j.assign_encounter_codes()
        path = self.tmp / "journal" / "encounters" / f"{e['encounter_uid']}.json"
        rec = json.loads(path.read_text(encoding="utf-8"))
        rec["linked_bottle_code"] = "B-18"
        path.write_text(json.dumps(rec), encoding="utf-8")
        self.assertNotIn(rec["code"], {r["code"] for r in self._collection()["rows"]})

    def test_tastings_table_has_a_row_per_sitting_with_pour_economics(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD,
                    "venue": "Bar", "pour_price": 30, "pour_size_oz": 1.5})
        d = self.c.get("/api/table/tastings").get_json()
        self.assertEqual(len(d["rows"]), 1)
        row = d["rows"][0]
        self.assertEqual(row["total"], 66)
        self.assertEqual(row["medal"], "Bronze")
        self.assertEqual(row["venue"], "Bar")
        self.assertEqual(row["value_per_oz"], 20.0)              # 30 / 1.5
        self.assertEqual(row["display_name"], "Example Distillery Single Barrel")
        self.assertEqual(row["counted"], "yes")

    def test_a_flight_pour_carries_its_session_and_position(self):
        sid = self.c.post("/api/session", json={"title": "F"}).get_json()["session"]["session_id"]
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD,
                    "session_id": sid, "flight_pos": 2})
        row = self.c.get("/api/table/tastings").get_json()["rows"][0]
        self.assertEqual(row["session_id"], sid)
        self.assertEqual(row["flight_pos"], 2)


class TestCompare(AppCase):
    def _cmp(self, query):
        r = self.c.get(f"/api/compare?{query}")
        return r.status_code, r.get_json()

    def test_career_mode_is_the_default(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        self._post({"spirit_id": "S-1", "scores": dict(EXAMPLE_CARD, flavor=18)})
        code, d = self._cmp("codes=B-18,S-1")
        self.assertEqual(code, 200)
        self.assertEqual([i["key"] for i in d["items"]], ["B-18", "S-1"])
        self.assertTrue(all(i["mode"] == "career" for i in d["items"]))
        by = {i["key"]: i for i in d["items"]}
        self.assertEqual(by["B-18"]["total"], 66)
        self.assertEqual(by["S-1"]["total"], 72)
        self.assertEqual(by["B-18"]["label"], "Example Distillery Single Barrel")

    def test_career_scores_are_means_carrying_their_range(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})                    # flavor 12
        self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=16)})   # flavor 16
        item = self._cmp("codes=B-18")[1]["items"][0]
        self.assertEqual(item["n"], 2)
        self.assertEqual(item["total"], 68.0)
        self.assertEqual(item["scores"]["flavor"], 14.0)
        self.assertEqual(item["ranges"]["flavor"], [12, 16])
        self.assertEqual(item["ranges"]["aroma"], [8, 8])

    def test_each_axis_carries_its_own_maximum_and_the_leader(self):
        """Flavor/20 must read at the same visual scale as Balance/10 (SPEC.md §10)."""
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})                   # flavor 12
        self._post({"spirit_id": "S-1", "scores": dict(EXAMPLE_CARD, flavor=18)})   # flavor 18
        axes = {a["key"]: a for a in self._cmp("codes=B-18,S-1")[1]["axes"]}
        self.assertEqual(axes["flavor"]["max"], 20)
        self.assertEqual(axes["aesthetics"]["max"], 5)
        self.assertEqual(axes["flavor"]["leader"], "S-1")
        self.assertEqual(axes["flavor"]["spread"], 6)
        self.assertIsNone(axes["aroma"]["leader"], "a tied axis has no leader")
        self.assertEqual(axes["aroma"]["spread"], 0)

    def test_at_most_four_cards(self):
        self.assertEqual(self._cmp("codes=B-18,S-1,B-18,S-1,B-18")[0], 400)

    def test_nothing_to_compare_is_rejected(self):
        self.assertEqual(self._cmp("")[0], 400)

    def test_unknown_code_is_404(self):
        self.assertEqual(self._cmp("codes=B-999")[0], 404)

    def test_pinning_a_flight_compares_its_pours(self):
        sid = self.c.post("/api/session",
                          json={"title": "Head to head"}).get_json()["session"]["session_id"]
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD,
                    "session_id": sid, "flight_pos": 1})
        self._post({"spirit_id": "S-1", "scores": dict(EXAMPLE_CARD, flavor=18),
                    "session_id": sid, "flight_pos": 2})
        code, d = self._cmp(f"session={sid}")
        self.assertEqual(code, 200)
        self.assertEqual(len(d["items"]), 2)
        self.assertTrue(all(i["mode"] == "sitting" for i in d["items"]))
        self.assertEqual([i["code"] for i in d["items"]], ["B-18", "S-1"])
        self.assertEqual(d["items"][0]["total"], 66)

    def test_unknown_session_is_404(self):
        self.assertEqual(self._cmp("session=F-20260101-000000-dead")[0], 404)

    def test_explicit_sittings_can_be_pinned(self):
        a = self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        b = self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=16)})
        ids = [a.get_json()["tasting"]["tasting_id"], b.get_json()["tasting"]["tasting_id"]]
        code, d = self._cmp(f"tastings={ids[0]},{ids[1]}")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(i["total"] for i in d["items"]), [66, 70])
        self.assertEqual(self._cmp("tastings=T-nope")[0], 404)

    def test_notes_come_from_the_latest_counted_sitting(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD, "date": "2026-01-01",
                    "notes": {"aroma": "old note"}, "overall_notes": "old overall"})
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD, "date": "2026-06-01",
                    "notes": {"aroma": "fresh note"}, "overall_notes": "fresh overall"})
        item = self._cmp("codes=B-18")[1]["items"][0]
        self.assertEqual(item["notes"]["aroma"], "fresh note")
        self.assertEqual(item["overall_note"], "fresh overall")
        self.assertNotIn("overall", item["notes"], "the overall note is not a category")

    def test_an_unscored_spirit_compares_without_crashing(self):
        code, d = self._cmp("codes=B-18,S-1")
        self.assertEqual(code, 200)
        self.assertEqual(d["items"][0]["scores"], {})
        self.assertIsNone(d["items"][0]["total"])
        self.assertEqual(d["items"][0]["n"], 0)
        self.assertIsNone({a["key"]: a for a in d["axes"]}["aroma"]["leader"])


class TestQuickEntry(AppCase):
    """The phone lane, end to end through the server (SPEC.md §1.2)."""

    def write_quick(self, rows):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Quick Entry"
        head = ["date", "display_name", "barrel_id", "nose", "palate", "finish", "notes"]
        ws.append(head)
        for r in rows:
            ws.append([r.get(h) for h in head])
        wb.save(self.book)
        wb.close()

    def test_typed_rows_are_listed_before_draining(self):
        self.write_quick([{"display_name": "B-18", "nose": "caramel"}])
        d = self.c.get("/api/quickentry").get_json()
        self.assertTrue(d["exists"])
        self.assertFalse(d["locked"])
        self.assertEqual(len(d["rows"]), 1)
        self.assertEqual(d["rows"][0]["nose"], "caramel")

    def test_no_workbook_yet_is_not_an_error(self):
        d = self.c.get("/api/quickentry").get_json()
        self.assertFalse(d["exists"])
        self.assertEqual(d["rows"], [])

    def test_draining_creates_drafts_and_clears_the_sheet(self):
        self.write_quick([{"date": "2026-09-08", "display_name": "B-18", "barrel_id": "F664",
                           "nose": "orange peel", "palate": "toffee", "finish": "long",
                           "notes": "revisit"}])
        r = self.c.post("/api/quickentry/drain")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["counts"], {"read": 1, "drained": 1, "unmatched": 0})

        rows = self.c.get("/api/table/tastings").get_json()["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "B-18")
        self.assertIsNone(rows[0]["total"], "a drained row has notes but no score yet")

        # the sheet was rewritten, so the same row cannot be drained twice
        self.assertEqual(self.c.get("/api/quickentry").get_json()["rows"], [])
        again = self.c.post("/api/quickentry/drain").get_json()
        self.assertEqual(again["counts"]["drained"], 0)

    def test_an_unmatched_row_stays_on_the_sheet_with_its_reason(self):
        self.write_quick([{"display_name": "Something Nobody Owns", "nose": "peat"},
                          {"display_name": "B-18", "nose": "caramel"}])
        body = self.c.post("/api/quickentry/drain").get_json()
        self.assertEqual(body["counts"], {"read": 2, "drained": 1, "unmatched": 1})

        left = self.c.get("/api/quickentry").get_json()["rows"]
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["display_name"], "Something Nobody Owns")
        self.assertEqual(left[0]["nose"], "peat", "the typing is not lost")

    def test_a_draft_does_not_move_the_career_score(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        self.write_quick([{"display_name": "B-18", "nose": "caramel"}])
        self.c.post("/api/quickentry/drain")
        row = {r["code"]: r for r in
               self.c.get("/api/table/collection").get_json()["rows"]}["B-18"]
        self.assertEqual(row["career_score"], 66)
        self.assertEqual(row["n"], 1)
        self.assertEqual(row["n_total"], 2, "the draft is visible but not counted")

    def test_draining_is_refused_while_excel_holds_the_workbook(self):
        self.write_quick([{"display_name": "B-18", "nose": "caramel"}])
        lock = self.book.with_name(f"~${self.book.name}")
        lock.write_bytes(b"")
        r = self.c.post("/api/quickentry/drain")
        self.assertEqual(r.status_code, 409)
        self.assertIn("open in Excel", r.get_json()["error"])
        self.assertEqual(self.c.get("/api/table/tastings").get_json()["rows"], [],
                         "nothing may be drained when the rewrite cannot follow")


class TestAnalysis(AppCase):
    """The questions the spreadsheet cannot answer (SPEC.md §4.5)."""

    def _get(self):
        r = self.c.get("/api/analysis")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def test_an_empty_journal_analyses_to_nothing(self):
        d = self._get()
        self.assertEqual(d["points"], [])
        self.assertEqual(d["calibration"], [])
        self.assertEqual(d["counts"], {"scored": 0, "sittings": 0})

    def test_one_point_per_spirit_carrying_the_master_fields(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        d = self._get()
        self.assertEqual(len(d["points"]), 1)
        p = d["points"][0]
        self.assertEqual(p["code"], "B-18")
        self.assertEqual(p["score"], 66)
        self.assertEqual(p["medal"], "Bronze")
        self.assertEqual(p["n"], 1)
        self.assertEqual((p["type"], p["region"]), ("Bourbon", "America"))
        self.assertEqual((p["paid"], p["proof"], p["conc_ratio"]), (100.0, 100.0, 1.15))
        self.assertIsNone(p["age"], "the fixture bottle is NAS")

    def test_points_use_the_career_score_not_the_last_sitting(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})                    # 66
        self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=16)})   # 70
        p = self._get()["points"][0]
        self.assertEqual((p["score"], p["n"]), (68.0, 2))

    def test_group_means_are_sorted_best_first(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})                    # Bourbon 66
        self._post({"spirit_id": "S-1", "scores": dict(EXAMPLE_CARD, flavor=18)})    # Rye 72
        d = self._get()
        self.assertEqual([g["key"] for g in d["by_type"]], ["Rye", "Bourbon"])
        self.assertEqual([g["key"] for g in d["by_region"]], ["Scotland", "America"])
        rye = {g["key"]: g for g in d["by_type"]}["Rye"]
        self.assertEqual((rye["mean"], rye["spirits"], rye["sittings"]), (72, 1, 1))

    def test_calibration_is_one_mean_per_month_over_sittings(self):
        """This series measures the scorer, not the spirit — it is how grade drift shows up."""
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD, "date": "2026-01-15"})
        self._post({"spirit_id": "S-1", "scores": dict(EXAMPLE_CARD, flavor=16),
                    "date": "2026-01-20"})
        self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=18),
                    "date": "2026-02-03"})
        cal = self._get()["calibration"]
        self.assertEqual(cal, [{"month": "2026-01", "mean": 68.0, "n": 2},
                               {"month": "2026-02", "mean": 72.0, "n": 1}])

    def test_an_excluded_sitting_stays_out_of_the_calibration(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD, "date": "2026-01-15"})
        self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=20),
                    "date": "2026-01-16", "include_in_average": False})
        self.assertEqual(self._get()["calibration"],
                         [{"month": "2026-01", "mean": 66.0, "n": 1}])

    def test_axes_say_how_much_data_each_one_has(self):
        """A scatter drawn over two points should not look like a finding."""
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        axes = {a["key"]: a for a in self._get()["axes"]}
        self.assertEqual(axes["paid"]["have"], 1)
        self.assertEqual(axes["conc_ratio"]["have"], 1)
        self.assertEqual(axes["age"]["have"], 0, "the only scored bottle is NAS")
        self.assertEqual(axes["conc_ratio"]["label"], "Conc. Ratio")

    def test_a_spirit_with_only_a_draft_produces_no_point(self):
        j = store_mod.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()
        j.write_tasting(spirit_id="B-18", status="draft", notes={"aroma": "x"})
        d = self._get()
        self.assertEqual(d["points"], [])
        self.assertEqual(d["calibration"], [])

    def test_a_spirit_with_only_excluded_sittings_produces_no_point(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD, "include_in_average": False})
        self.assertEqual(self._get()["points"], [])


class TestPendingApproval(AppCase):
    """The approval gate (SPEC.md §8.5). Runs against a fixture master, never the real one."""

    def setUp(self):
        super().setUp()
        from test_master_write import build_fixture
        self.master = self.tmp / "Whiskey Collection.xlsx"
        build_fixture(self.master)
        self.app = app_mod.create_app(
            "config.yaml",
            app_folder=str(self.tmp / "journal"),
            snapshot_path=str(self.snap),
            master=str(self.master),
            rollup=str(self.tmp / "Whiskey Tastings.xlsx"),
            backups=str(self.tmp / "backups"),
        )
        self.c = self.app.test_client()

    def queue(self, **fields):
        r = self.c.post("/api/pending", json={"sheet": "Bottle", "fields": fields,
                                              "entered_from": "phone"})
        self.assertEqual(r.status_code, 201)
        return r.get_json()["pending"]["pending_uid"]

    def codes(self):
        import collection as collection_mod
        return {r["code"] for r in collection_mod.Collection(self.master).load().rows["Bottle"]}

    def test_a_queued_request_is_listed_and_touches_nothing(self):
        self.queue(Distillery="Gamma Co", Name="Three")
        d = self.c.get("/api/pending").get_json()
        self.assertEqual(len(d["pending"]), 1)
        self.assertEqual(d["pending"][0]["fields"]["Distillery"], "Gamma Co")
        self.assertFalse(d["master"]["locked"])
        self.assertEqual(self.codes(), {"B-1", "B-2"}, "queuing must not write to the master")

    def test_approving_writes_the_row_and_clears_the_queue(self):
        uid = self.queue(Distillery="Gamma Co", Name="Three", Proof=101.4)
        r = self.c.post(f"/api/pending/{uid}/approve")
        self.assertEqual(r.status_code, 200, r.get_json())
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["code"], "B-3")
        self.assertEqual(self.codes(), {"B-1", "B-2", "B-3"})
        self.assertEqual(self.c.get("/api/pending").get_json()["pending"], [])

    def test_the_review_panel_can_correct_the_parse_before_writing(self):
        uid = self.queue(Distillery="Typo Co", Name="Three")
        self.c.post(f"/api/pending/{uid}/approve",
                    json={"fields": {"Distillery": "Corrected Co"}})
        import collection as collection_mod
        rows = {r["code"]: r for r in
                collection_mod.Collection(self.master).load().rows["Bottle"]}
        self.assertEqual(rows["B-3"]["distillery"], "Corrected Co")

    def test_an_encounter_code_is_refused_at_the_gate(self):
        uid = self.queue(**{"Bottle Code": "X-4", "Name": "Bar pour"})
        r = self.c.post(f"/api/pending/{uid}/approve")
        self.assertEqual(r.status_code, 409)
        self.assertIn("X-", r.get_json()["error"])
        self.assertEqual(self.codes(), {"B-1", "B-2"})
        self.assertEqual(len(self.c.get("/api/pending").get_json()["pending"]), 1,
                         "a refused request stays in the queue")

    def test_approving_is_refused_while_excel_holds_the_master(self):
        uid = self.queue(Distillery="Gamma Co", Name="Three")
        self.master.with_name(f"~${self.master.name}").write_bytes(b"")
        r = self.c.post(f"/api/pending/{uid}/approve")
        self.assertEqual(r.status_code, 409)
        self.assertIn("open in Excel", r.get_json()["error"])
        self.assertEqual(self.codes(), {"B-1", "B-2"})

    def test_rejecting_keeps_the_record_out_of_the_master(self):
        uid = self.queue(Distillery="Gamma Co", Name="Three")
        r = self.c.post(f"/api/pending/{uid}/reject", json={"reason": "duplicate"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/pending").get_json()["pending"], [])
        self.assertEqual(self.codes(), {"B-1", "B-2"})
        kept = list((self.tmp / "journal" / "pending" / "rejected").glob("P-*.json"))
        self.assertEqual(len(kept), 1, "a rejection is kept, not dropped")

    def test_unknown_request_is_404(self):
        self.assertEqual(self.c.post("/api/pending/P-nope/approve").status_code, 404)
        self.assertEqual(self.c.post("/api/pending/P-nope/reject").status_code, 404)

    def test_two_queued_requests_take_consecutive_codes(self):
        """Codes are assigned at approval time, so a long-queued request cannot claim a stale one."""
        a = self.queue(Distillery="Gamma Co", Name="Three")
        b = self.queue(Distillery="Delta Co", Name="Four")
        first = self.c.post(f"/api/pending/{a}/approve").get_json()["code"]
        second = self.c.post(f"/api/pending/{b}/approve").get_json()["code"]
        self.assertEqual((first, second), ("B-3", "B-4"))
        self.assertEqual(self.codes(), {"B-1", "B-2", "B-3", "B-4"})


class TestRankingLens(AppCase):
    """Everything a ranking lens needs is served with the rows, so switching lens is instant."""

    def test_the_rubric_says_which_categories_are_flavour(self):
        cats = {c["key"]: c for c in self.c.get("/api/config").get_json()["rubric"]["categories"]}
        self.assertTrue(cats["aroma"]["flavour"])
        self.assertTrue(cats["flavor"]["flavour"])
        self.assertFalse(cats["aesthetics"]["flavour"], "aesthetics is the bottle, not the liquid")
        self.assertFalse(cats["value"]["flavour"], "value is the price, not the liquid")
        flavour_max = sum(c["max"] for c in cats.values() if c["flavour"])
        self.assertEqual(flavour_max, 90)

    def test_collection_rows_carry_per_category_means(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        row = {r["code"]: r for r in
               self.c.get("/api/table/collection").get_json()["rows"]}["B-18"]
        self.assertEqual(row["category_means"]["flavor"], 12)
        self.assertEqual(row["category_means"]["aesthetics"], 3)

    def test_an_unscored_spirit_has_no_means(self):
        row = {r["code"]: r for r in
               self.c.get("/api/table/collection").get_json()["rows"]}["B-18"]
        self.assertEqual(row["category_means"], {})

    def test_the_everything_lens_equals_the_career_score_exactly(self):
        """The §3.6 trap: summing rounded means drifts. These are exact, so it cannot."""
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})                   # 66
        self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=13)})  # 67
        self._post({"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD, flavor=17)})  # 71
        row = {r["code"]: r for r in
               self.c.get("/api/table/collection").get_json()["rows"]}["B-18"]
        lens_total = round(sum(row["category_means"].values()), 1)
        self.assertEqual(lens_total, row["career_score"])

    def test_a_flavour_lens_leaves_out_exactly_the_non_flavour_categories(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        cats = self.c.get("/api/config").get_json()["rubric"]["categories"]
        flavour = [c["key"] for c in cats if c["flavour"]]
        row = {r["code"]: r for r in
               self.c.get("/api/table/collection").get_json()["rows"]}["B-18"]
        subtotal = round(sum(row["category_means"][k] for k in flavour), 1)
        # the example card is 66 with aesthetics 3 and value 4
        self.assertEqual(subtotal, 59)
        self.assertEqual(subtotal + 3 + 4, row["career_score"])

    def test_tasting_rows_carry_their_raw_scores(self):
        self._post({"spirit_id": "B-18", "scores": EXAMPLE_CARD})
        row = self.c.get("/api/table/tastings").get_json()["rows"][0]
        self.assertEqual(row["scores"]["flavor"], 12)
        self.assertEqual(sum(row["scores"].values()), row["total"])


class TestPageAndHealth(AppCase):
    def test_index_is_served(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Whiskey Tasting Book", r.data)

    def test_static_assets_are_served(self):
        self.assertEqual(self.c.get("/static/app.js").status_code, 200)
        self.assertEqual(self.c.get("/static/style.css").status_code, 200)

    def test_the_page_carries_its_own_icon(self):
        """The app window's taskbar button takes its icon from the page, not from the shortcut
        that launched it, so without this it shows a generic browser globe instead."""
        self.assertIn(b'rel="icon"', self.c.get("/").data)
        self.assertEqual(self.c.get("/static/icon.ico").status_code, 200)

    def test_health_reports_the_journal_and_catalog(self):
        h = self.c.get("/api/health").get_json()
        self.assertTrue(h["ok"])
        self.assertEqual(h["catalog"]["spirits"], 2)
        self.assertIn("journal", h["journal_root"])


class TestFinishedBottles(AppCase):
    """Bottles and samples are deleted from the workbook when they are emptied. The row is
    disposable; the reviews of it are not, and they are the reason the journal exists."""

    def journal(self):
        return store_mod.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()

    def refresh_without(self, code):
        """A refresh where the workbook no longer lists `code` — a finished bottle deleted."""
        import collection as collection_mod
        left = [s for s in FIXTURE_SNAPSHOT["spirits"] if s["code"] != code]
        stub = types.SimpleNamespace(
            errors=[], rows={"Bottle": left},
            snapshot=lambda: dict(FIXTURE_SNAPSHOT, spirits=left))
        with mock.patch.object(collection_mod, "load", return_value=stub):
            return self.c.post("/api/refresh")

    def test_the_departure_is_recorded(self):
        r = self.refresh_without("B-18")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["retired"], ["B-18"])
        kept = self.journal().retired()
        self.assertEqual([x["code"] for x in kept], ["B-18"])
        self.assertEqual(kept[0]["fields"]["name"], "Single Barrel",
                         "what the bottle was has to survive the row that described it")

    def test_the_reviews_survive_the_bottle(self):
        j = self.journal()
        j.write_tasting(spirit_id="B-18", scores=dict(EXAMPLE_CARD))
        self.refresh_without("B-18")
        rows = self.c.get("/api/table/collection").get_json()["rows"]
        row = next((r for r in rows if r["code"] == "B-18"), None)
        self.assertIsNotNone(row, "the review vanished with the bottle")
        self.assertEqual(row["n"], 1)
        self.assertIsNotNone(row["career_score"])
        self.assertEqual(row["display_name"], "Example Distillery Single Barrel")

    def test_it_is_no_longer_owned(self):
        j = self.journal()
        j.write_tasting(spirit_id="B-18", scores=dict(EXAMPLE_CARD))
        self.refresh_without("B-18")
        row = next(r for r in self.c.get("/api/table/collection").get_json()["rows"]
                   if r["code"] == "B-18")
        self.assertFalse(row["owned"])
        self.assertEqual(row["source"], "Retired")

    def test_one_deleted_without_ever_being_scored_is_simply_gone(self):
        """Nothing to preserve, so nothing to clutter the table with."""
        self.refresh_without("B-18")
        codes = [r["code"] for r in self.c.get("/api/table/collection").get_json()["rows"]]
        self.assertNotIn("B-18", codes)

    def test_recording_it_twice_keeps_the_first_record(self):
        """Refresh is pressed often. The record nearest to when the bottle was owned is the one
        worth keeping, and journal files are never rewritten."""
        self.refresh_without("B-18")
        first = self.journal().retired()[0]["retired_at"]
        self.refresh_without("B-18")
        again = self.journal().retired()
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["retired_at"], first)

    def test_a_bottle_that_comes_back_is_owned_again(self):
        """Deleted by mistake and typed back in: the live workbook row wins over the record."""
        j = self.journal()
        j.write_tasting(spirit_id="B-18", scores=dict(EXAMPLE_CARD))
        self.refresh_without("B-18")
        rows = self.c.get("/api/table/collection").get_json()["rows"]   # snapshot still lacks it
        self.assertFalse(next(r for r in rows if r["code"] == "B-18")["owned"])
        self.snap.write_text(json.dumps(FIXTURE_SNAPSHOT), encoding="utf-8")
        self.app.config["_catalog"].load() if "_catalog" in self.app.config else None
        fresh = app_mod.create_app(
            "config.yaml", app_folder=str(self.tmp / "journal"), snapshot_path=str(self.snap),
            master="Z:/nonexistent/x.xlsx", rollup=str(self.tmp / "r.xlsx"),
            backups=str(self.tmp / "b")).test_client()
        row = next(r for r in fresh.get("/api/table/collection").get_json()["rows"]
                   if r["code"] == "B-18")
        self.assertTrue(row["owned"], "it is in the workbook again; it is owned again")
        self.assertEqual(row["n"], 1, "and it keeps the review it already had")


class TestWhatThePhoneIsGiven(AppCase):
    """The phone has no server, so anything it cannot work out for itself has to be published."""

    def publish(self):
        import collection as collection_mod
        j = store_mod.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()
        j.write_tasting(spirit_id="B-18", scores=dict(EXAMPLE_CARD), date="2026-05-04")
        j.write_tasting(spirit_id="B-18", scores=dict(EXAMPLE_CARD), date="2026-06-11")
        stub = types.SimpleNamespace(errors=[], rows={"Bottle": FIXTURE_SNAPSHOT["spirits"]},
                                     snapshot=lambda: FIXTURE_SNAPSHOT)
        with mock.patch.object(collection_mod, "load", return_value=stub):
            self.assertEqual(self.c.post("/api/refresh").status_code, 200)
        return json.loads((self.tmp / "journal" / "snapshot" / "careers.json")
                          .read_text(encoding="utf-8"))

    def test_per_category_means_are_published(self):
        """Without them the phone can only compare totals, and comparing whiskies by one number
        is what the ten categories exist to avoid."""
        cats = self.publish()["careers"]["B-18"]["categories"]
        self.assertEqual(set(cats), {c.key for c in rubric_mod.load_rubric().categories})
        self.assertAlmostEqual(cats["flavor"], EXAMPLE_CARD["flavor"], places=2)

    def test_the_calibration_series_is_published(self):
        """One mean per month over every counted sitting. The phone holds its own cards, not the
        journal, so this is the one thing on that screen it cannot derive."""
        series = self.publish()["calibration"]
        self.assertEqual([m["month"] for m in series], ["2026-05", "2026-06"])
        self.assertTrue(all(m["n"] >= 1 for m in series))


class TestStatus(AppCase):
    """All three sheets carry a Status column. Finished and Removed mean the spirit has left the
    collection; Opened and Unopened mean it is still there. Marking a row is better than deleting
    it — the reviews keep something to belong to — so the app has to read the difference."""

    def journal(self):
        return store_mod.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()

    def with_status(self, status, code="B-18"):
        """Rewrite the snapshot so that one spirit carries `status`, and serve from it."""
        spirits = [dict(sp, status=status) if sp["code"] == code else sp
                   for sp in FIXTURE_SNAPSHOT["spirits"]]
        self.snap.write_text(json.dumps(dict(FIXTURE_SNAPSHOT, spirits=spirits)), encoding="utf-8")
        return app_mod.create_app(
            "config.yaml", app_folder=str(self.tmp / "journal"), snapshot_path=str(self.snap),
            master="Z:/nonexistent/x.xlsx", rollup=str(self.tmp / "r.xlsx"),
            backups=str(self.tmp / "b")).test_client()

    def row(self, client, code="B-18"):
        return next(r for r in client.get("/api/table/collection").get_json()["rows"]
                    if r["code"] == code)

    def test_finished_is_not_owned(self):
        self.assertFalse(self.row(self.with_status("Finished"))["owned"])

    def test_removed_is_not_owned(self):
        self.assertFalse(self.row(self.with_status("Removed"))["owned"])

    def test_opened_and_unopened_still_are(self):
        for held in ("Opened", "Unopened"):
            self.assertTrue(self.row(self.with_status(held))["owned"], held)

    def test_the_match_is_forgiving_about_typing(self):
        """These are typed into Excel by hand."""
        for spelling in ("finished", "FINISHED", " Finished "):
            self.assertFalse(self.row(self.with_status(spelling))["owned"], spelling)

    def test_a_blank_status_is_not_assumed_to_be_gone(self):
        """Rows predate the column. Assuming the worst of them would hide real bottles."""
        self.assertTrue(self.row(self.with_status(""))["owned"])

    def test_a_finished_bottle_keeps_its_row_and_its_reviews(self):
        j = self.journal()
        j.write_tasting(spirit_id="B-18", scores=dict(EXAMPLE_CARD))
        row = self.row(self.with_status("Finished"))
        self.assertEqual(row["n"], 1, "the reviews went with the bottle")
        self.assertEqual(row["source"], "Bottle", "it is still a workbook row, not a tombstone")
        self.assertIsNotNone(row["career_score"])

    def test_refresh_dates_the_bottle_being_finished(self):
        """The workbook records no date for it, and "how long did that bottle last" is worth
        keeping. Written once, so it is when the mark was first seen."""
        import collection as collection_mod
        spirits = [dict(sp, status="Finished") if sp["code"] == "B-18" else sp
                   for sp in FIXTURE_SNAPSHOT["spirits"]]
        stub = types.SimpleNamespace(errors=[], rows={"Bottle": spirits},
                                     snapshot=lambda: dict(FIXTURE_SNAPSHOT, spirits=spirits))
        with mock.patch.object(collection_mod, "load", return_value=stub):
            r = self.c.post("/api/refresh")
        self.assertEqual(r.status_code, 200)
        self.assertIn("B-18", r.get_json()["retired"])
        rec = next(x for x in self.journal().retired() if x["code"] == "B-18")
        self.assertEqual(rec["reason"], "marked Finished")
        self.assertTrue(rec["retired_at"])

    def test_an_unrecognised_status_is_reported(self):
        """A typo would otherwise leave an empty bottle counted as owned for ever, silently."""
        import collection as collection_mod
        coll = collection_mod.Collection.__new__(collection_mod.Collection)
        coll.rows = {"Bottle": [{"code": "B-1", "_row": 9, "status": "Finsihed"}]}
        coll.issues = []
        coll.first_empty_row = lambda sheet: 99
        coll._check()
        self.assertTrue(any("not recognised" in i.message for i in coll.issues),
                        "a misspelled status passed without comment")


class TestCodesAreNeverReissued(AppCase):
    """The quiet danger in deleting rows. next_code is highest-present + 1, so finishing the
    newest bottle and deleting it would free its code for the next one — and every review of the
    bottle that held it would silently become a review of a different whiskey."""

    def journal(self):
        return store_mod.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()

    def bottles(self, numbers):
        return types.SimpleNamespace(
            errors=[],
            rows={"Bottle": [{"code": "B-%d" % n, "_row": 8 + n} for n in numbers]},
            snapshot=lambda: dict(FIXTURE_SNAPSHOT, spirits=[
                {"_sheet": "Bottle", "_row": 8 + n, "code": "B-%d" % n} for n in numbers]))

    def refresh_with(self, numbers):
        import collection as collection_mod
        with mock.patch.object(collection_mod, "load", return_value=self.bottles(numbers)):
            return self.c.post("/api/refresh")

    def test_the_mark_is_taken_when_the_workbook_is_read(self):
        self.refresh_with([1, 2, 3])
        self.assertEqual(self.journal().highest_seen("Bottle"), 3)

    def test_it_never_falls_when_the_newest_bottle_is_finished(self):
        self.refresh_with([1, 2, 3])
        self.refresh_with([1, 2])                     # B-3 emptied and its row deleted
        self.assertEqual(self.journal().highest_seen("Bottle"), 3,
                         "the mark went backwards; B-3 would be handed out again")

    def test_the_next_code_clears_the_deleted_one(self):
        import collection as collection_mod
        self.refresh_with([1, 2, 3])
        self.refresh_with([1, 2])
        coll = self.bottles([1, 2])
        coll.next_code = lambda sheet, floor=0: collection_mod.Collection.next_code(
            coll, sheet, floor=floor)
        coll.rows = {"Bottle": [{"code": "B-1", "_row": 9}, {"code": "B-2", "_row": 10}]}
        self.assertEqual(coll.next_code("Bottle", floor=self.journal().highest_seen("Bottle")),
                         "B-4")

    def test_without_the_mark_the_code_would_be_reused(self):
        """Stated plainly so the fix is not quietly removed as redundant."""
        import collection as collection_mod
        coll = collection_mod.Collection.__new__(collection_mod.Collection)
        coll.rows = {"Bottle": [{"code": "B-1", "_row": 9}, {"code": "B-2", "_row": 10}]}
        self.assertEqual(coll.next_code("Bottle"), "B-3")


class TestBarPours(AppCase):
    """A pour at a bar is scored on the phone before it has a code — X- numbers are handed out
    here, afterwards — so the card names the encounter by its uid and can never be rewritten to
    say anything else. If the two are not put back together the score simply disappears: the
    encounter shows on the table unscored, and the card belongs to a spirit that does not exist."""

    def journal(self):
        return store_mod.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()

    def bar_pour(self, name="Something From The Back Bar", pours=1, **fields):
        """Exactly what the phone uploads: an encounter file, then cards naming it by uid."""
        j = self.journal()
        enc = j.write_encounter(name=name, entered_from="phone", **fields)
        for _ in range(pours):
            j.write_tasting(spirit_id=enc["encounter_uid"], scores=dict(EXAMPLE_CARD),
                            entered_from="phone", venue=fields.get("venue"))
        return enc

    def test_the_score_reaches_the_encounter(self):
        self.bar_pour(distillery="A Distillery", type="Bourbon", venue="A Bar")
        rows = self.c.get("/api/table/collection").get_json()["rows"]
        pours = [r for r in rows if r["source"] == "Encounter"]
        self.assertEqual(len(pours), 1, "the bar pour is missing from the table")
        self.assertEqual(pours[0]["n"], 1, "the card did not attach to the encounter")
        self.assertIsNotNone(pours[0]["career_score"])
        self.assertTrue(str(pours[0]["code"]).startswith("X-"))

    def test_it_is_not_owned(self):
        """The point of the distinction: "have I had this" is not "do I own this"."""
        self.bar_pour()
        row = [r for r in self.c.get("/api/table/collection").get_json()["rows"]
               if r["source"] == "Encounter"][0]
        self.assertFalse(row["owned"])

    def test_a_card_by_uid_is_not_left_stranded(self):
        """Before this was joined up the card grouped under its own uid, which matches no spirit
        at all, so the sitting existed and counted towards nothing."""
        self.bar_pour()
        rows = self.c.get("/api/table/tastings").get_json()["rows"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(str(rows[0]["code"]).startswith("X-"),
                        "the sitting still names the raw uid: %r" % (rows[0]["code"],))

    def test_two_pours_of_the_same_thing_average(self):
        self.bar_pour(name="Twice", pours=2)
        row = [r for r in self.c.get("/api/table/collection").get_json()["rows"]
               if r["source"] == "Encounter"][0]
        self.assertEqual(row["n"], 2)


class TestScoringSheetIdentifiesTheBottle(unittest.TestCase):
    """You cannot judge what you cannot identify. "Double Oaked" is several different whiskies
    without the distillery beside it, and the sheet used to show only that."""

    def source(self, name="app.js"):
        src = (Path(__file__).resolve().parent / "static" / name).read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
        return re.sub(r"^\s*//.*$", " ", src, flags=re.MULTILINE)

    def header(self):
        src = self.source()
        return src[src.index("const hidden = isBlind(p)"):src.index('class: "sheet-head"')]

    def test_it_names_the_distillery_and_the_code(self):
        head = self.header()
        self.assertIn("sp.distillery", head)
        self.assertIn("sp.code", head)

    def test_it_carries_the_rest_of_what_the_workbook_knows(self):
        head = self.header()
        for field in ("sp.release_year", "sp.rarity", "sp.status", "sp.entry_proof", "sp.paid"):
            self.assertIn(field, head, f"{field} is not shown while scoring")

    def test_it_shows_the_workbook_note_about_the_bottle(self):
        """Barrel numbers and provenance, which is exactly what you want in front of you."""
        head = self.header()
        for field in ("sp.notes", "sp.comment", "sp.special_note"):
            self.assertIn(field, head)

    def test_blind_hides_every_one_of_them(self):
        """A release year and a price identify a bottle as surely as its name does. Each new
        element must be built only when the identity is not hidden."""
        head = self.header()
        for built in ("const detailEl", "const bookEl"):
            line = head[head.index(built):]
            self.assertRegex(line[:60], r"=\s*!hidden",
                             f"{built} is not gated on the identity being visible")
        self.assertIn("hidden || !sp.distillery", head,
                      "the distillery would show through blind mode")


class TestASubmittedCardIsNotADeadEnd(unittest.TestCase):
    """Submitting removed the Change button and put nothing in its place, so a standalone card
    ended with a finished sheet and no way onward — the only escape was the top navigation."""

    def source(self):
        src = (Path(__file__).resolve().parent / "static" / "app.js").read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
        return re.sub(r"^\s*//.*$", " ", src, flags=re.MULTILINE)

    def test_the_footer_offers_the_next_card(self):
        self.assertIn('id: "score-another"', self.source())

    def test_the_header_offers_a_way_back(self):
        src = self.source()
        block = src[src.index('state.mode === "session" ? "Drop pour" : "Change"'):]
        self.assertIn('"Back"', block[:400],
                      "a submitted card leaves the header with no control at all")

    def test_both_go_through_the_one_function_that_knows_how_to_leave(self):
        """backToPicker also drops an unsubmitted pour from a flight rather than leaving a ghost
        in the switcher; hand-rolling the navigation here would skip that."""
        src = self.source()
        block = src[src.index('id: "score-another"'):]
        self.assertIn("onclick: backToPicker", block[:200])


class TestReviseAndDelete(AppCase):
    """Journal files are immutable, so a correction is a new revision and a deletion is a
    tombstone. Nothing is ever edited in place and nothing is ever unlinked (SPEC.md 1.3)."""

    def card(self, **over):
        body = {"spirit_id": "B-18", "scores": dict(EXAMPLE_CARD), **over}
        r = self._post(body)
        self.assertIn(r.status_code, (200, 201), r.get_json())
        return r.get_json()["tasting"]

    def journal(self):
        return store_mod.Journal(self.tmp / "journal", rubric_mod.load_rubric()).ensure()

    def test_a_correction_is_a_new_revision(self):
        first = self.card()
        fixed = dict(EXAMPLE_CARD, flavor=EXAMPLE_CARD["flavor"] - 3)
        again = self.card(tasting_id=first["tasting_id"], scores=fixed)
        self.assertEqual(again["tasting_id"], first["tasting_id"])
        self.assertEqual(again["revision"], first["revision"] + 1)
        live = self.journal().tastings()
        self.assertEqual(len(live), 1, "a revision must not read as a second sitting")
        self.assertEqual(live[0]["scores"]["flavor"], fixed["flavor"])

    def test_the_earlier_revision_is_still_on_disk(self):
        first = self.card()
        self.card(tasting_id=first["tasting_id"], scores=dict(EXAMPLE_CARD, body=1))
        files = sorted(p.name for p in (self.tmp / "journal" / "tastings").glob("*.json"))
        self.assertEqual(len(files), 2, "the original was overwritten: %s" % files)

    def test_deleting_writes_a_tombstone_rather_than_unlinking(self):
        made = self.card()
        r = self.c.delete("/api/tasting/%s" % made["tasting_id"])
        self.assertEqual(r.status_code, 200, r.get_json())
        files = list((self.tmp / "journal" / "tastings").glob("*.json"))
        self.assertEqual(len(files), 2, "the card was deleted from disk")
        self.assertEqual(self.journal().tastings(), [], "it still reads as live")

    def test_a_deleted_card_stops_counting(self):
        made = self.card()
        before = self.c.get("/api/table/collection").get_json()["rows"]
        self.assertEqual(next(r for r in before if r["code"] == "B-18")["n"], 1)
        self.c.delete("/api/tasting/%s" % made["tasting_id"])
        after = self.c.get("/api/table/collection").get_json()["rows"]
        row = next(r for r in after if r["code"] == "B-18")
        self.assertEqual(row["n"], 0)
        self.assertIsNone(row["career_score"])

    def test_deleting_something_that_is_not_there(self):
        self.assertEqual(self.c.delete("/api/tasting/T-nope").status_code, 404)

    def test_a_draft_may_be_incomplete(self):
        """Autosave writes the card as it stands, half-scored, every few seconds."""
        partial = {k: v for i, (k, v) in enumerate(EXAMPLE_CARD.items()) if i < 3}
        r = self._post({"spirit_id": "B-18", "scores": partial, "status": "draft"})
        self.assertIn(r.status_code, (200, 201), r.get_json())
        self.assertEqual(r.get_json()["tasting"]["status"], "draft")

    def test_a_draft_does_not_count_towards_a_score(self):
        """Half a card is not an opinion yet."""
        self._post({"spirit_id": "B-18", "scores": {"aroma": 7}, "status": "draft"})
        row = next(r for r in self.c.get("/api/table/collection").get_json()["rows"]
                   if r["code"] == "B-18")
        self.assertEqual(row["n"], 0)

    def test_a_draft_still_cannot_hold_an_impossible_score(self):
        """Incomplete is fine; out of range is wrong whether the card is finished or not."""
        r = self._post({"spirit_id": "B-18", "scores": {"aroma": 99}, "status": "draft"})
        self.assertEqual(r.status_code, 400)

    def test_a_submitted_card_must_still_be_whole(self):
        r = self._post({"spirit_id": "B-18", "scores": {"aroma": 7}})
        self.assertEqual(r.status_code, 400)

    def test_a_draft_can_be_promoted_to_submitted(self):
        """Which is what autosave then finishing a card does."""
        draft = self.card(scores={"aroma": 7}, status="draft")
        done = self.card(tasting_id=draft["tasting_id"], scores=dict(EXAMPLE_CARD),
                         status="submitted")
        self.assertEqual(done["status"], "submitted")
        row = next(r for r in self.c.get("/api/table/collection").get_json()["rows"]
                   if r["code"] == "B-18")
        self.assertEqual(row["n"], 1, "promoting a draft should make it count exactly once")


class TestThePickersIdentifyTheRelease(unittest.TestCase):
    """A picker is where you choose *between* bottles, so it needs what tells them apart more than
    any finished view does. Three here are called George T. Stagg; the proof and the release year
    are the only things that separate them."""

    def source(self, name):
        src = (Path(__file__).resolve().parent / "static" / name).read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
        return re.sub(r"^\s*//.*$", " ", src, flags=re.MULTILINE)

    def body(self, src, name):
        """One function's text. Comments are stripped by then, so the section banners cannot be
        used as boundaries — the next function is."""
        start = src.index("function %s(" % name)
        nxt = src.find("function ", start + len(name) + 12)
        return src[start:nxt if nxt != -1 else len(src)]

    def test_the_score_picker_shows_proof_and_year(self):
        block = self.body(self.source("app.js"), "renderResults")
        self.assertIn("s.proof", block)
        self.assertIn("s.release_year", block)

    def test_the_compare_picker_shows_proof_and_year(self):
        block = self.body(self.source("compare.js"), "results")
        self.assertIn("s.proof", block)
        self.assertIn("s.release_year", block)


class TestAutosaveOnTheDesktop(unittest.TestCase):
    """The localStorage mirror lives in one browser profile. Autosave puts the work in the journal
    itself, as a draft, which is the thing that actually survives."""

    def source(self, name="app.js"):
        src = (Path(__file__).resolve().parent / "static" / name).read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
        return re.sub(r"^\s*//.*$", " ", src, flags=re.MULTILINE)

    def test_autosave_writes_a_draft(self):
        src = self.source()
        self.assertIn('cardBody(p, "draft")', src)

    def test_it_hangs_off_the_same_signal_as_the_local_draft(self):
        src = self.source()
        self.assertIn("scheduleAutosave()", src)

    def test_finishing_revises_the_autosaved_card(self):
        """Otherwise every autosaved card would be followed by a second, complete one beside it."""
        src = self.source()
        block = src[src.index("function cardBody("):src.index("let autosaveTimer")]
        self.assertIn("if (p.tasting_id) body.tasting_id = p.tasting_id;", block)

    def test_autosave_and_submit_describe_the_card_the_same_way(self):
        """One builder, so a draft cannot quietly differ from what finishing it would write."""
        src = self.source()
        self.assertEqual(src.count("function cardBody("), 1)
        self.assertIn('cardBody(p, "submitted")', src)

    def test_it_does_not_write_a_revision_that_says_nothing(self):
        src = self.source()
        self.assertIn("fingerprint === autosavedAs", src)

    def test_a_submitted_card_can_be_corrected_or_removed(self):
        src = self.source()
        self.assertIn('id: "edit-card"', src)
        self.assertIn('id: "delete-card"', src)
        self.assertIn('method: "DELETE"', src)


class TestCompareOffersOnlyWhatCanBeCompared(unittest.TestCase):
    """Comparing an unscored bottle produces an empty column, so offering the whole collection is
    offering hundreds of dead ends."""

    def source(self, name="compare.js"):
        src = (Path(__file__).resolve().parent / "static" / name).read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
        return re.sub(r"^\s*//.*$", " ", src, flags=re.MULTILINE)

    def test_the_picker_is_limited_to_scored_spirits(self):
        src = self.source()
        self.assertIn("C.scored.has(s.code)", src)

    def test_it_shows_everything_if_that_list_cannot_be_fetched(self):
        """Failing closed here would leave an empty picker and no way to compare at all."""
        src = self.source()
        block = src[src.index("async function loadScored"):]
        self.assertIn("C.scored = null", block[:400])
        self.assertIn("!C.scored ||", src)

    def test_the_scored_list_is_refetched_after_a_refresh(self):
        block = self.source()
        self.assertIn("C.data = null; C.scored = null;", block)

    def test_a_column_names_the_code_as_well_as_the_bottle(self):
        """Three bottles in this collection are called George T. Stagg. Two columns under the
        same heading is exactly when you need to tell them apart."""
        self.assertIn("it.code", self.source())


class TestNothingIsSilentlyTruncated(unittest.TestCase):
    """A `.slice(0, n)` in a picker hides bottles the collection really has. On a 375-bottle
    collection the score list simply ended at B-60 and the compare list at 25, with nothing on
    screen to say either had stopped early."""

    def source(self, name):
        src = (Path(__file__).resolve().parent / "static" / name).read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
        return re.sub(r"^\s*//.*$", " ", src, flags=re.MULTILINE)

    def test_the_score_picker_shows_every_match(self):
        self.assertNotIn("list.slice(", self.source("app.js"))

    def test_the_compare_picker_shows_every_match(self):
        self.assertNotIn("list.slice(", self.source("compare.js"))

    def test_the_score_picker_says_how_many_it_is_showing(self):
        """The count is what makes a future cap visible rather than silent."""
        self.assertIn("picker-count", self.source("app.js"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

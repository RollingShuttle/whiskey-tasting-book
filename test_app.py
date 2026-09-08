"""
test_app.py — the local server, in isolation.

    python test_app.py

Runs against Flask's test client. The journal is a throwaway temp directory and the collection is
a tiny fixture snapshot, so this touches no OneDrive folder and never opens the 147 MB master. The
snapshot-only hot path is proven by pointing the master override at a path that does not exist and
confirming the spirit list still loads.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

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
         "status": "Opened", "rarity": "Uncommon"},
        {"_sheet": "Sample", "_row": 9, "code": "S-1", "distillery": "Sample Co",
         "name": "Test Rye", "type": "Rye", "region": "America", "proof": 120.0,
         "abv": 60.0, "sizeoz": 1.0},
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
        )
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


class TestPageAndHealth(AppCase):
    def test_index_is_served(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Whiskey Tasting Book", r.data)

    def test_static_assets_are_served(self):
        self.assertEqual(self.c.get("/static/app.js").status_code, 200)
        self.assertEqual(self.c.get("/static/style.css").status_code, 200)

    def test_health_reports_the_journal_and_catalog(self):
        h = self.c.get("/api/health").get_json()
        self.assertTrue(h["ok"])
        self.assertEqual(h["catalog"]["spirits"], 2)
        self.assertIn("journal", h["journal_root"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

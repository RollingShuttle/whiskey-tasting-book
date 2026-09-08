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

"""
test_store.py — the journal, in isolation. Runs entirely in a temp directory; touches no
OneDrive folder and no workbook.

    python tests/test_store.py
"""

# Run straight from the shell, only tests/ is on the path; discovered from the repo root, only
# the root is. Put all three where imports can find them so both ways of running behave alike.
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT), str(ROOT / "tools")]

import json
import shutil
import tempfile
import unittest

import rubric as rubric_mod
import store
from test_rubric import EXAMPLE_CARD, EXAMPLE_SITTINGS


class JournalCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="journal-test-"))
        self.j = store.Journal(self.tmp, rubric_mod.load_rubric()).ensure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestLayoutAndWriting(JournalCase):
    def test_ensure_creates_every_subdir_and_is_idempotent(self):
        for d in store.SUBDIRS:
            self.assertTrue((self.tmp / d).is_dir(), d)
        self.j.ensure()                                    # twice must not fail
        self.assertTrue((self.tmp / "pending/rejected").is_dir())

    def test_a_tasting_round_trips_with_its_total_and_medal(self):
        rec = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD,
                                   notes={"aroma": "caramel, sweet"}, date="2026-09-08")
        self.assertEqual(rec["total"], 66)
        self.assertEqual(rec["medal"], "Bronze")
        back = self.j.tastings()
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0]["scores"], EXAMPLE_CARD)
        self.assertEqual(back[0]["notes"]["aroma"], "caramel, sweet")
        self.assertEqual(back[0]["rubric_version"], 1)

    def test_invalid_card_is_refused_before_anything_is_written(self):
        with self.assertRaises(ValueError):
            self.j.write_tasting(spirit_id="B-19", scores=dict(EXAMPLE_CARD, flavor=21))
        self.assertEqual(list((self.tmp / "tastings").glob("*.json")), [])

    def test_files_are_json_and_written_atomically(self):
        self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        files = list((self.tmp / "tastings").glob("*.json"))
        self.assertEqual(len(files), 1)
        json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(list((self.tmp / "tastings").glob(".tmp-*")), [])   # no debris


class TestImmutabilityAndRevisions(JournalCase):
    def test_a_correction_is_a_new_file_not_an_edit(self):
        """It is written as the next revision, never over the last one — and then the last one
        goes: a review is one file, its newest."""
        first = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD, date="2026-09-08")
        r1 = self.tmp / "tastings" / f"{first['tasting_id']}-r1.json"

        self.j.write_tasting(spirit_id="B-19", scores=dict(EXAMPLE_CARD, finish=8),
                             date="2026-09-08", tasting_id=first["tasting_id"])

        self.assertFalse(r1.exists(), "the version it replaced is not kept")
        self.assertEqual([p.name for p in (self.tmp / "tastings").glob("*.json")],
                         [f"{first['tasting_id']}-r2.json"])

        live = self.j.tastings()
        self.assertEqual(len(live), 1, "only the highest revision is live")
        self.assertEqual(live[0]["revision"], 2)
        self.assertEqual(live[0]["total"], 69)

    def test_a_deleted_sitting_leaves_a_marker_and_nothing_of_the_card(self):
        rec = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD,
                                   notes={"nose": "toffee", "overall": "again"})
        self.j.delete_tasting(rec["tasting_id"], reason="wrong bottle")
        self.assertEqual(self.j.tastings(), [])
        left = list((self.tmp / "tastings").glob("*.json"))
        self.assertEqual([p.name for p in left], [f"{rec['tasting_id']}-r2.json"])
        marker = json.loads(left[0].read_text(encoding="utf-8"))
        self.assertEqual(set(marker), {"tasting_id", "revision", "deleted", "reason", "created_at"},
                         "no scores, no notes, no bottle: a marker, not a copy")
        self.assertTrue(marker["deleted"])

    def test_the_marker_keeps_a_late_copy_from_bringing_the_sitting_back(self):
        """A phone holds the cards it scored. One that uploads its copy after the deletion must
        not undo it — which is the whole reason the marker stays."""
        rec = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        late = (self.tmp / "tastings" / f"{rec['tasting_id']}-r1.json").read_text(encoding="utf-8")
        self.j.delete_tasting(rec["tasting_id"])
        (self.tmp / "tastings" / f"{rec['tasting_id']}-r1.json").write_text(late, encoding="utf-8")
        self.assertEqual(self.j.tastings(), [], "the highest revision is the deletion")
        self.assertEqual(self.j.prune_superseded(), 1, "and the next sweep takes the late copy")
        self.assertEqual(len(list((self.tmp / "tastings").glob("*.json"))), 1)

    def test_two_devices_writing_at_once_do_not_collide(self):
        """Same spirit, same second, two independent writers. Second-resolution timestamps are
        not enough on their own — this failed until tasting_id gained a random suffix."""
        a = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD, entered_from="phone")
        b = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_SITTINGS[0]["scores"], entered_from="pc")
        self.assertNotEqual(a["tasting_id"], b["tasting_id"])
        self.assertEqual(len(self.j.tastings()), 2)
        self.assertEqual(len(list((self.tmp / "tastings").glob("*.json"))), 2)

    def test_ten_rapid_writes_all_survive(self):
        ids = {self.j.write_tasting(spirit_id="B-18", scores=EXAMPLE_CARD)["tasting_id"]
               for _ in range(10)}
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(self.j.tastings()), 10)

    def test_overwriting_a_revision_is_refused_loudly(self):
        rec = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        with self.assertRaises(FileExistsError):
            self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD,
                                 tasting_id=rec["tasting_id"], revision=1)


class TestCareerAggregation(JournalCase):
    def _load_stagg(self):
        for s in EXAMPLE_SITTINGS:
            self.j.write_tasting(spirit_id="B-18", scores=s["scores"], date=s["date"],
                                 include_in_average=s["include_in_average"])

    def test_career_matches_the_hand_computed_fixture(self):
        self._load_stagg()
        c = self.j.career("B-18")
        self.assertEqual(c["n"], 3)
        self.assertEqual(c["n_excluded"], 1)
        self.assertEqual(c["mean_total"], 89.0)
        self.assertEqual(c["medal"], "Gold")
        self.assertEqual(c["range"], (87, 91))
        self.assertEqual(c["next_band"], ("Diamond", 1))
        self.assertEqual(len(c["sittings"]), 4)

    def test_deleting_a_sitting_changes_the_career_score(self):
        self._load_stagg()
        worst = min((t for t in self.j.tastings() if t["include_in_average"]),
                    key=lambda t: t["total"])
        self.j.delete_tasting(worst["tasting_id"], reason="test")
        self.assertEqual(self.j.career("B-18")["n"], 2)
        self.assertEqual(self.j.career("B-18")["mean_total"], 90.0)

    def test_careers_covers_every_scored_spirit(self):
        self._load_stagg()
        self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        self.assertEqual(set(self.j.careers()), {"B-18", "B-19"})


class TestEncounters(JournalCase):
    def test_bar_pour_is_recorded_without_a_bottle_code(self):
        e = self.j.write_encounter(name="Example Cask 10 Year", distillery="Example Distillery",
                                   type="Bourbon", proof=107.0, venue="Bar pour")
        self.assertIsNone(e["code"], "the X- code is assigned on the PC, not the phone")
        self.assertTrue(e["encounter_uid"].startswith("E-"))
        self.assertEqual(len(self.j.encounters()), 1)

    def test_simultaneous_encounters_get_different_filenames(self):
        a = self.j.write_encounter(name="One")
        b = self.j.write_encounter(name="Two")
        self.assertNotEqual(a["encounter_uid"], b["encounter_uid"])
        self.assertEqual(len(self.j.encounters()), 2)

    def test_codes_are_assigned_oldest_first_and_stay_put(self):
        first = self.j.write_encounter(name="First")
        second = self.j.write_encounter(name="Second")
        codes = self.j.assign_encounter_codes()
        self.assertEqual(codes[first["encounter_uid"]], "X-1")
        self.assertEqual(codes[second["encounter_uid"]], "X-2")

        self.j.write_encounter(name="Third")
        codes = self.j.assign_encounter_codes()            # rerun must not renumber
        self.assertEqual(codes[first["encounter_uid"]], "X-1")
        self.assertEqual(codes[second["encounter_uid"]], "X-2")
        self.assertEqual(len(codes), 3)
        self.assertEqual({e["code"] for e in self.j.encounters()}, {"X-1", "X-2", "X-3"})

    def test_a_bar_pour_can_be_scored_like_anything_else(self):
        e = self.j.write_encounter(name="Example Cask 10 Year")
        self.j.assign_encounter_codes()
        code = self.j.encounters()[0]["code"]
        self.j.write_tasting(spirit_id=code, scores=EXAMPLE_CARD, venue="Bar",
                             pour_price=45.0, pour_size_oz=1.5, entered_from="phone")
        c = self.j.career(code)
        self.assertEqual(c["mean_total"], 66)
        self.assertEqual(c["sittings"][0]["pour_price"], 45.0)


class TestPendingBottles(JournalCase):
    def test_a_request_is_queued_not_written_to_the_master(self):
        p = self.j.write_pending_bottle(sheet="Bottle",
                                        fields={"Distillery": "Example Distillery", "Name": "Reserve 15"})
        self.assertEqual(p["state"], "pending")
        self.assertEqual(len(self.j.pending()), 1)
        self.assertIsNone(p["fields"].get("Bottle Code"),
                          "codes are assigned at approval time on the PC")

    def test_unknown_sheet_is_refused(self):
        with self.assertRaises(ValueError):
            self.j.write_pending_bottle(sheet="Whisky", fields={})

    def test_approval_clears_the_queue(self):
        p = self.j.write_pending_bottle(sheet="Bottle", fields={"Name": "X"})
        r = self.j.resolve_pending(p["pending_uid"], approved=True, assigned_code="B-145")
        self.assertEqual(r["assigned_code"], "B-145")
        self.assertEqual(self.j.pending(), [])

    def test_rejection_keeps_the_record(self):
        p = self.j.write_pending_bottle(sheet="Sample", fields={"Name": "Y"})
        self.j.resolve_pending(p["pending_uid"], approved=False, reason="changed my mind")
        self.assertEqual(self.j.pending(), [])
        kept = list((self.tmp / "pending/rejected").glob("P-*.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(json.loads(kept[0].read_text())["reason"], "changed my mind")


class TestSessions(JournalCase):
    def test_a_flight_is_created_with_its_own_id(self):
        s = self.j.write_session(title="Thursday flight", location="Home", company="two of us")
        self.assertTrue(s["session_id"].startswith("F-"), "F- keeps it clear of Sample S- codes")
        self.assertEqual(s["revision"], 1)
        self.assertEqual(len(self.j.sessions()), 1)
        self.assertEqual(self.j.sessions()[0]["title"], "Thursday flight")

    def test_two_flights_in_the_same_second_do_not_collide(self):
        """Same rule as tastings — second-resolution stamps are not unique on their own."""
        a = self.j.write_session(title="One")
        b = self.j.write_session(title="Two")
        self.assertNotEqual(a["session_id"], b["session_id"])
        self.assertEqual(len(self.j.sessions()), 2)

    def test_amending_a_flight_is_a_new_revision_not_an_edit(self):
        first = self.j.write_session(title="Untitled")
        original = (self.tmp / "sessions" / f"{first['session_id']}-r1.json").read_text()

        self.j.write_session(session_id=first["session_id"], title="Barrel picks", blind=True)

        self.assertEqual((self.tmp / "sessions" / f"{first['session_id']}-r1.json").read_text(),
                         original, "revision 1 must never be modified")
        live = self.j.sessions()
        self.assertEqual(len(live), 1, "only the highest revision is live")
        self.assertEqual(live[0]["revision"], 2)
        self.assertEqual(live[0]["title"], "Barrel picks")
        self.assertTrue(live[0]["blind"])

    def test_overwriting_a_session_revision_is_refused_loudly(self):
        s = self.j.write_session(title="X")
        with self.assertRaises(FileExistsError):
            self.j.write_session(session_id=s["session_id"], revision=1, title="Y")

    def test_pours_come_back_in_flight_order(self):
        sid = self.j.write_session(title="Flight")["session_id"]
        self.j.write_tasting(spirit_id="B-3", scores=EXAMPLE_CARD, session_id=sid, flight_pos=2)
        self.j.write_tasting(spirit_id="B-1", scores=EXAMPLE_CARD, session_id=sid, flight_pos=1)
        self.j.write_tasting(spirit_id="B-9", scores=EXAMPLE_CARD, session_id=sid, flight_pos=3)
        pours = self.j.session(sid)["pours"]
        self.assertEqual([p["spirit_id"] for p in pours], ["B-1", "B-3", "B-9"])
        self.assertEqual([p["flight_pos"] for p in pours], [1, 2, 3])

    def test_next_flight_pos_counts_up(self):
        sid = self.j.write_session()["session_id"]
        self.assertEqual(self.j.next_flight_pos(sid), 1)
        self.j.write_tasting(spirit_id="B-1", scores=EXAMPLE_CARD, session_id=sid, flight_pos=1)
        self.assertEqual(self.j.next_flight_pos(sid), 2)

    def test_a_standalone_pour_belongs_to_no_flight(self):
        sid = self.j.write_session()["session_id"]
        self.j.write_tasting(spirit_id="B-1", scores=EXAMPLE_CARD, session_id=sid, flight_pos=1)
        self.j.write_tasting(spirit_id="B-2", scores=EXAMPLE_CARD)          # no session
        self.assertEqual(len(self.j.session(sid)["pours"]), 1)
        self.assertEqual(len(self.j.tastings()), 2)

    def test_unknown_session_is_none(self):
        self.assertIsNone(self.j.session("F-20260101-000000-dead"))

    def test_a_flight_survives_a_fresh_journal_object(self):
        sid = self.j.write_session(title="Persisted")["session_id"]
        reopened = store.Journal(self.tmp, rubric_mod.load_rubric())
        self.assertEqual(reopened.session(sid)["title"], "Persisted")
        self.assertEqual(reopened.stats()["sessions"], 1)


class TestReReadingIsStable(JournalCase):
    def test_a_fresh_journal_object_sees_the_same_data(self):
        """What the PC reads must be exactly what the phone wrote — no in-memory state."""
        for s in EXAMPLE_SITTINGS:
            self.j.write_tasting(spirit_id="B-18", scores=s["scores"], date=s["date"],
                                 include_in_average=s["include_in_average"])
        reopened = store.Journal(self.tmp, rubric_mod.load_rubric())
        self.assertEqual(reopened.career("B-18")["mean_total"], 89.0)
        self.assertEqual(reopened.stats()["tastings"], 4)


class TestMovingASitting(JournalCase):
    """A card scored against the wrong bottle is put under the right one. It is the next revision
    of the same card and nothing is edited, so the mistake and its correction are both on file."""

    def card(self, **over):
        return self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD, date="2026-09-08",
                                    venue="Home", pour_price=14.0, tags=["neat"], **over)

    def test_a_move_is_the_next_revision_naming_the_other_spirit(self):
        first = self.card()
        r1 = self.tmp / "tastings" / f"{first['tasting_id']}-r1.json"

        moved = self.j.move_tasting(first["tasting_id"], "B-20")

        self.assertFalse(r1.exists(), "the copy under the bottle it left is not kept")
        self.assertEqual((moved["revision"], moved["spirit_id"], moved["moved_from"]),
                         (2, "B-20", "B-19"))
        live = self.j.tastings()
        self.assertEqual([t["spirit_id"] for t in live], ["B-20"], "one sitting, under the new bottle")
        for key in ("scores", "total", "medal", "date", "venue", "pour_price", "tags",
                    "include_in_average", "status"):
            self.assertEqual(live[0][key], first[key], f"{key} must come across untouched")

    def test_the_scores_follow_the_card(self):
        first = self.card()
        self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD, date="2026-09-09")
        self.j.move_tasting(first["tasting_id"], "B-20")
        self.assertEqual(self.j.career("B-19")["n"], 1)
        self.assertEqual(self.j.career("B-20")["n"], 1)

    def test_it_can_be_moved_back(self):
        first = self.card()
        self.j.move_tasting(first["tasting_id"], "B-20")
        back = self.j.move_tasting(first["tasting_id"], "B-19")
        self.assertEqual((back["revision"], back["spirit_id"], back["moved_from"]), (3, "B-19", "B-20"))
        self.assertEqual([p.name for p in (self.tmp / "tastings").glob("*.json")],
                         [f"{first['tasting_id']}-r3.json"], "one file, however often it moves")

    def test_moving_to_where_it_already_is_writes_nothing(self):
        first = self.card()
        with self.assertRaises(ValueError):
            self.j.move_tasting(first["tasting_id"], "B-19")
        self.assertEqual(len(list((self.tmp / "tastings").glob("*.json"))), 1)

    def test_a_deleted_or_unknown_sitting_cannot_be_moved(self):
        first = self.card()
        self.j.delete_tasting(first["tasting_id"])
        with self.assertRaises(KeyError):
            self.j.move_tasting(first["tasting_id"], "B-20")
        with self.assertRaises(KeyError):
            self.j.move_tasting("T-never-was", "B-20")
        self.assertEqual(self.j.tastings(), [], "a tombstone must not be brought back by a move")

class TestOneFilePerReview(JournalCase):
    """A review is its newest revision and nothing else (the owner's rule, 26 Nov 2026). Before
    it, 51 reviews had come to 243 files — most of them the autosaves a card passes through while
    it is filled in. What a new revision replaces is removed, but only once the new one is safe."""

    def files(self):
        return sorted(p.name for p in (self.tmp / "tastings").glob("*.json"))

    def test_a_card_autosaved_ten_times_is_one_file(self):
        first = self.j.write_tasting(spirit_id="B-19", scores={"aroma": 7}, status="draft")
        for n in range(8):
            self.j.write_tasting(spirit_id="B-19", scores={"aroma": 7, "flavor": 10 + n},
                                 status="draft", tasting_id=first["tasting_id"])
        done = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD, tasting_id=first["tasting_id"])
        self.assertEqual(done["revision"], 10, "the numbering carries on from the file that is left")
        self.assertEqual(self.files(), [f"{first['tasting_id']}-r10.json"])
        self.assertEqual([(t["status"], t["total"]) for t in self.j.tastings()], [("submitted", 66)])

    def test_one_reviews_clean_up_does_not_touch_another(self):
        a = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        b = self.j.write_tasting(spirit_id="B-20", scores=EXAMPLE_CARD)
        self.j.write_tasting(spirit_id="B-19", scores=dict(EXAMPLE_CARD, body=1), tasting_id=a["tasting_id"])
        self.assertEqual(self.files(), sorted([f"{a['tasting_id']}-r2.json", f"{b['tasting_id']}-r1.json"]))

    def test_nothing_older_goes_if_the_newest_cannot_be_read(self):
        """A review must never be left with no file. A half-arrived upload is the case: the
        newest revision is there by name and not yet by content."""
        rec = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        (self.tmp / "tastings" / f"{rec['tasting_id']}-r2.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.j.prune_tasting(rec["tasting_id"]), 0)
        self.assertIn(f"{rec['tasting_id']}-r1.json", self.files())

    def test_nothing_older_goes_if_the_newest_is_some_other_card(self):
        rec = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        wrong = dict(rec, tasting_id="T-someone-else", revision=2)
        (self.tmp / "tastings" / f"{rec['tasting_id']}-r2.json").write_text(json.dumps(wrong), encoding="utf-8")
        self.assertEqual(self.j.prune_tasting(rec["tasting_id"]), 0)
        self.assertEqual(len(self.files()), 2)

    def test_the_sweep_takes_what_arrived_from_elsewhere(self):
        """The phone uploads its new file and leaves the old one. So did the journal, before."""
        rec = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        other = self.j.write_tasting(spirit_id="B-20", scores=EXAMPLE_CARD)
        for rev in (2, 3):                                   # as a phone would have left them
            later = dict(rec, revision=rev, scores=dict(EXAMPLE_CARD, body=rev))
            (self.tmp / "tastings" / f"{rec['tasting_id']}-r{rev}.json").write_text(
                json.dumps(later), encoding="utf-8")
        self.assertEqual(self.j.prune_superseded(), 2)
        self.assertEqual(self.files(), sorted([f"{rec['tasting_id']}-r3.json",
                                               f"{other['tasting_id']}-r1.json"]))
        self.assertEqual(self.j.prune_superseded(), 0, "and a second sweep finds nothing to do")
        self.assertEqual(len(self.j.tastings()), 2)

    def test_a_file_is_still_never_written_over(self):
        rec = self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD)
        self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD, tasting_id=rec["tasting_id"])
        with self.assertRaises(FileExistsError):
            self.j.write_tasting(spirit_id="B-19", scores=EXAMPLE_CARD,
                                 tasting_id=rec["tasting_id"], revision=2)

if __name__ == "__main__":
    unittest.main(verbosity=1)

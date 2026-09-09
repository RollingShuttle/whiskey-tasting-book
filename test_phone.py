"""
test_phone.py — the iPhone bundle in docs/, checked from Python.

    python test_phone.py

The screens themselves need a phone to judge, but plenty about this bundle is checkable without
one: that every file the service worker promises to cache exists, that the iOS rules in SPEC.md
§9.2 are actually in the markup and the stylesheet, that the rubric shipped to the phone still
matches the one the PC scores with, and that no client ID has been committed by accident.

These are the failures that would only show up on a phone, in a bar, with no signal.
"""
import io
import json
import re
import struct
import unittest
from pathlib import Path

import rubric as rubric_mod

DOCS = Path(__file__).resolve().parent / "docs"
REQUIRED = ["index.html", "style.css", "app.js", "store.js", "graph.js", "config.js",
            "sw.js", "manifest.webmanifest", "rubric.json", "icon-180.png"]


def text(name):
    return (DOCS / name).read_text(encoding="utf-8")


def code(name):
    """The file with its comments stripped. The rules below are about what the code does;
    the comments deliberately *name* the things being avoided in order to explain why."""
    src = text(name)
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", " ", src, flags=re.MULTILINE)


class TestBundle(unittest.TestCase):
    def test_every_file_is_present(self):
        for name in REQUIRED:
            self.assertTrue((DOCS / name).exists(), f"docs/{name} is missing")

    def test_the_service_worker_only_promises_files_that_exist(self):
        """A precache list with a missing entry makes addAll reject and the app never installs."""
        listed = re.findall(r'"\./([^"]*)"', text("sw.js"))
        for name in listed:
            if not name:
                continue                     # "./" is the start URL, not a file
            self.assertTrue((DOCS / name).exists(), f"sw.js caches {name}, which does not exist")

    def test_the_manifest_is_valid_and_points_at_the_icon(self):
        m = json.loads(text("manifest.webmanifest"))
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["theme_color"], "#12100E")
        src = m["icons"][0]["src"]
        self.assertTrue((DOCS / src).exists())

    def test_the_icon_is_a_real_180_square_png(self):
        data = (DOCS / "icon-180.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (180, 180), "apple-touch-icon must be 180x180")


class TestIosRules(unittest.TestCase):
    """SPEC.md §9.2 — the ones that break in specific ways rather than looking slightly off."""

    def setUp(self):
        self.html = text("index.html")
        self.css = text("style.css")

    def test_home_screen_install_meta_is_present(self):
        for meta in ("apple-mobile-web-app-capable", "mobile-web-app-capable",
                     "apple-touch-icon", "theme-color"):
            self.assertIn(meta, self.html, f"{meta} missing — installs badly")

    def test_the_viewport_covers_the_safe_areas(self):
        self.assertIn("viewport-fit=cover", self.html,
                      "without this the safe-area insets resolve to zero")

    def test_safe_area_insets_are_used_on_every_edge(self):
        for edge in ("safe-area-inset-top", "safe-area-inset-bottom",
                     "safe-area-inset-left", "safe-area-inset-right"):
            self.assertIn(edge, self.css, f"{edge} unused")

    def test_no_hundred_vh(self):
        """100vh is wrong on iOS Safari with a visible URL bar; 100dvh is the fix."""
        self.assertNotIn("100vh", code("style.css"))
        self.assertIn("100dvh", self.css)

    def test_inputs_are_sixteen_pixels(self):
        """Anything smaller and Safari zooms the page on focus and never zooms back."""
        block = re.search(r"textarea, input\[type=\"text\"\][^{]*\{[^}]*\}", self.css)
        self.assertIsNotNone(block, "the shared input rule went missing")
        self.assertIn("font-size: 16px", block.group(0))
        self.assertIn("font-size: 16px", re.search(r"\.search\s*\{[^}]*\}", self.css).group(0))

    def test_the_score_strip_is_a_touch_target(self):
        """44 px tall on a phone, not the 26 px used on the desktop sheet."""
        strip = re.search(r"\.strip\s*\{[^}]*\}", self.css).group(0)
        self.assertIn("height: 44px", strip)
        self.assertIn("touch-action: none", strip, "drag needs this or the page scrolls instead")

    def test_the_tab_bar_hides_while_the_keyboard_is_up(self):
        self.assertIn("body.keyboard .tabs", self.css)
        self.assertIn('classList.add("keyboard")', text("app.js"))

    def test_every_pushed_screen_has_its_own_back_control(self):
        """Standalone has no browser back button and no edge-swipe."""
        self.assertIn('id="back"', self.html)
        self.assertIn("function back()", text("app.js"))

    def test_number_fields_ask_for_the_right_keyboard(self):
        js = text("app.js")
        self.assertIn('inputmode: "decimal"', js)
        self.assertIn('inputmode: "numeric"', js)

    def test_notes_fields_behave_like_prose(self):
        js = text("app.js")
        self.assertIn('autocapitalize: "sentences"', js)
        self.assertIn('autocorrect: "on"', js)


class TestSafety(unittest.TestCase):
    def test_no_client_secret_is_committed(self):
        """A single-page app must not have a secret — adding one would actually break the PKCE
        flow this uses. The client ID is a different thing: it is public by design once the app
        is deployed, so filling it in is expected and must not fail the suite."""
        cfg = text("config.js")
        for bad in ("client_secret", "clientSecret", "CLIENT_SECRET", "password"):
            self.assertNotIn(bad, cfg)

    def test_the_client_id_is_blank_or_a_real_one(self):
        """Blank until you deploy. Once filled in it has to be an actual Application (client) ID
        rather than a leftover placeholder, which would fail at sign-in with a confusing error."""
        m = re.search(r'CLIENT_ID:\s*"([^"]*)"', text("config.js"))
        self.assertIsNotNone(m, "config.js lost its CLIENT_ID line")
        value = m.group(1).strip()
        if value:
            self.assertRegex(
                value.lower(),
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                "that does not look like an Application (client) ID from the Entra portal")

    def test_the_only_scope_requested_is_the_app_folder(self):
        """This scope is what makes the collection workbook invisible to the phone (§9.1)."""
        cfg = code("config.js")
        self.assertIn("Files.ReadWrite.AppFolder", cfg)
        for wider in ("Files.ReadWrite.All", "Files.Read.All", "Sites.", "User.ReadWrite"):
            self.assertNotIn(wider, cfg, f"{wider} would reach beyond the app folder")

    def test_the_phone_never_names_the_collection_workbook(self):
        for name in ("app.js", "store.js", "graph.js", "config.js"):
            self.assertNotIn("Whiskey Collection", code(name),
                             f"{name} refers to the master workbook in code")

    def test_uploads_go_only_to_the_app_folder_root(self):
        self.assertIn("/me/drive/special/approot:", text("graph.js"))


class TestRubricStaysInStep(unittest.TestCase):
    def test_the_shipped_rubric_matches_the_one_the_pc_scores_with(self):
        """The phone computes its own totals and medals, so a drifted copy would score
        differently from the desktop against the same card."""
        shipped = json.loads(text("rubric.json"))
        self.assertEqual(shipped, rubric_mod.load_rubric().as_config(),
                         "regenerate docs/rubric.json from config.yaml")

    def test_the_phone_reads_bands_not_medals(self):
        shipped = json.loads(text("rubric.json"))
        self.assertIn("bands", shipped)
        self.assertIn("rub.bands", text("store.js"))


class TestQueueRules(unittest.TestCase):
    """The filename rules have to match store.py or two devices will collide (SPEC.md §0)."""

    def test_ids_carry_a_random_suffix(self):
        js = text("store.js")
        self.assertIn("rand4()", js)
        self.assertIn("T-${stamp()}-${fields.spirit_id}-${rand4()}", js)
        self.assertIn("P-${stamp()}-${rand4()}", js)

    def test_a_tasting_is_written_as_revision_one(self):
        self.assertIn("tastings/${id}-r1.json", text("store.js"))

    def test_the_stamp_is_utc_like_store_py(self):
        js = text("store.js")
        for part in ("getUTCFullYear", "getUTCMonth", "getUTCHours", "getUTCSeconds"):
            self.assertIn(part, js)

    def test_the_card_is_saved_before_it_is_queued(self):
        """Scoring with no connection must work exactly as it does with one."""
        js = text("store.js")
        body = js[js.index("function submitScorecard"):]
        self.assertLess(body.index("addCard("), body.index("enqueue("))

    def test_storage_access_is_guarded(self):
        """Private browsing and a full quota both throw; a lost draft must not take the app down."""
        js = text("store.js")
        self.assertGreaterEqual(js.count("catch"), 2)


class TestFinishedBottlesOnThePhone(unittest.TestCase):
    """Standing at a bar believing you still own a bottle you finished is the failure this
    prevents. The snapshot carries the status; the phone has to show it."""

    def test_the_list_marks_what_is_gone(self):
        src = code("app.js")
        self.assertIn('s.owned === false', src)
        self.assertIn('class: "tag"', src)

    def test_there_is_a_filter_for_what_you_still_have(self):
        src = code("app.js")
        self.assertIn('["instock", "In stock"]', src)
        self.assertIn('s.owned !== false', src)

    def test_a_missing_owned_flag_is_not_read_as_gone(self):
        """Older snapshots have no owned field, and hiding a real bottle is the worse mistake."""
        src = code("app.js")
        self.assertIn("s.owned !== false", src)
        self.assertNotIn("!s.owned", src)

    def test_a_bar_pour_never_counts_as_stock(self):
        self.assertIn("owned: false", code("store.js"))


class TestTimeZones(unittest.TestCase):
    """The phone is the device that travels. Instants are stored in UTC; calendar days and
    anything shown to a person are local."""

    def test_no_calendar_date_comes_from_utc(self):
        """toISOString().slice(0, 10) is the UTC day. East of Greenwich an evening pour lands on
        tomorrow; west of it a late one lands on yesterday. The PC uses datetime.now(), so the
        two devices would also disagree about the same sitting."""
        for name in ("store.js", "app.js"):
            self.assertNotIn("toISOString().slice(0, 10)", code(name),
                             "%s still takes a calendar date from UTC" % name)

    def test_today_reads_the_local_clock(self):
        src = code("store.js")
        block = src[src.index("function today("):src.index("function localTime(")]
        for local, utc in (("getFullYear", "getUTCFullYear"), ("getMonth", "getUTCMonth"),
                           ("getDate", "getUTCDate")):
            self.assertIn(local, block)
            self.assertNotIn(utc, block)

    def test_the_card_is_dated_locally(self):
        src = code("store.js")
        block = src[src.index("function scorecard("):src.index("function encounter(")]
        self.assertIn("date: fields.date || today()", block)

    def test_nothing_shown_to_a_person_is_a_sliced_utc_string(self):
        """Slicing the stored ISO string prints UTC under a local-looking label, which is what
        made the sync time read five hours out."""
        src = code("app.js")
        self.assertNotIn('String(m.lastSync)', src)
        self.assertNotIn('String(m.offlineSince).slice', src)
        self.assertIn("Store.localTime(m.lastSync)", src)
        self.assertIn("Store.localClock(m.offlineSince)", src)

    def test_filenames_and_created_at_stay_utc(self):
        """Those are instants and sort keys shared with store.py, which stamps them in UTC.
        Making them local would break the ordering the whole journal relies on."""
        src = code("store.js")
        block = src[src.index("function stamp("):src.index("function rand4(")]
        for part in ("getUTCFullYear", "getUTCMonth", "getUTCDate", "getUTCHours"):
            self.assertIn(part, block)
        self.assertIn("new Date().toISOString()", src)      # nowIso, for created_at


class TestBarPours(unittest.TestCase):
    """Scoring something you do not own, standing at a bar. The phone writes the encounter file
    itself, so its shape is a contract with store.py rather than a convention."""

    def js_encounter_fields(self):
        """The keys docs/store.js puts in the encounter record."""
        src = code("store.js")
        start = src.index("function encounter(fields)")
        block = src[start:src.index("path: `encounters/", start)]
        return set(re.findall(r"^\s{8}(\w+):", block, re.MULTILINE))

    def test_the_phone_writes_the_record_store_py_reads(self):
        """Both halves write this file; only the PC reads it. A field named differently on the
        phone would not fail anywhere — it would just quietly never arrive."""
        import tempfile
        import store as store_mod
        with tempfile.TemporaryDirectory() as d:
            journal = store_mod.Journal(Path(d), rubric_mod.load_rubric()).ensure()
            written = journal.write_encounter(name="X", entered_from="phone")
        self.assertEqual(self.js_encounter_fields(), set(written),
                         "the phone and the PC disagree about an encounter's fields")

    def test_the_code_is_left_for_the_pc_to_assign(self):
        """X- numbers are handed out in order of first tasting, which only the PC can know."""
        src = code("store.js")
        block = src[src.index("function encounter(fields)"):src.index("function pendingBottle")]
        self.assertRegex(block, r"code:\s*null")

    def test_it_is_queued_like_everything_else(self):
        src = code("store.js")
        self.assertIn('kind: "encounter"', src)
        self.assertIn("`encounters/${uid}.json`", src)

    def test_the_uid_carries_a_random_suffix(self):
        """Same rule as every other generated filename: the timestamp is second-resolution, so
        two pours in one second would otherwise share a name (SPEC.md 0)."""
        src = code("store.js")
        block = src[src.index("function encounter(fields)"):src.index("function pendingBottle")]
        self.assertRegex(block, r"E-\$\{stamp\(\)\}-\$\{rand4\(\)\}")

    def test_the_card_names_the_encounter_by_uid(self):
        """The scorecard scores a spirit object, not a code, because a bar pour has no code yet.
        asSpirit puts the uid in `code`, and submitCard sends that as spirit_id."""
        self.assertRegex(code("store.js"), r"code:\s*e\.encounter_uid")
        self.assertIn("spirit_id: d.spirit.code", code("app.js"))

    def test_age_and_proof_are_stored_as_numbers(self):
        """A text field hands back a string, and a string sorts as text wherever the PC lists
        it - 9 after 18 - which nobody notices until the table looks wrong months later."""
        block = code("app.js")
        block = block[block.index("function startBarPour"):]
        block = block[:block.index("\n}")]
        self.assertIn("age: num(", block)
        self.assertIn("proof: num(", block)
        self.assertIn("Number.isFinite", block)

    def test_a_pour_stays_visible_on_the_phone(self):
        """The PC does not publish encounters back, so if the list only showed the snapshot a
        pour would vanish the moment it was submitted."""
        src = code("app.js")
        self.assertIn("Store.encounters().map(Store.asSpirit)", src)
        self.assertIn("knownSpirits()", src)

    def test_scoring_it_needs_no_connection(self):
        """The bar is the whole point: the encounter and the card are both stored before either
        is uploaded, and flush() is a separate, retryable step."""
        src = code("store.js")
        block = src[src.index("function submitEncounter"):src.index("function submitPending")]
        self.assertIn("addEncounter(rec)", block)
        self.assertIn("enqueue(", block)


class TestCollectionList(unittest.TestCase):
    """Two faults that only appear once a real collection is behind the screen."""

    def test_the_list_shows_every_spirit(self):
        """`spirits.slice(0, 200)` dropped 175 of 375 bottles off the end with no sign of it."""
        self.assertNotIn("spirits.slice(", code("app.js"))

    def test_the_whole_search_haystack_is_lowercased(self):
        """`a + b.toLowerCase()` lowercases only b. The search terms are lowercased, so every
        name and distillery that had a capital in it stopped matching anything."""
        found = re.findall(r"const hay = (.+?);", code("app.js"), re.DOTALL)
        self.assertTrue(found, "no search haystack found to check")
        for expr in found:
            if "+" not in expr:
                continue
            expr = expr.strip()
            self.assertTrue(expr.startswith("(") and expr.endswith(").toLowerCase()"),
                            "a concatenated haystack must be bracketed before lowercasing: " + expr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

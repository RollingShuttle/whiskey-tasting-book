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
    def test_no_client_id_is_committed(self):
        """It is public by design once deployed, but it is not ours to publish unasked."""
        m = re.search(r'CLIENT_ID:\s*"([^"]*)"', text("config.js"))
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "", "fill this in when you deploy, not in the repo")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

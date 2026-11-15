"""
test_phone.py — the iPhone bundle in docs/, checked from Python.

    python tests/test_phone.py

The screens themselves need a phone to judge, but plenty about this bundle is checkable without
one: that every file the service worker promises to cache exists, that the iOS rules in SPEC.md
§9.2 are actually in the markup and the stylesheet, that the rubric shipped to the phone still
matches the one the PC scores with, and that no client ID has been committed by accident.

These are the failures that would only show up on a phone, in a bar, with no signal.
"""

# Run straight from the shell, only tests/ is on the path; discovered from the repo root, only
# the root is. Put all three where imports can find them so both ways of running behave alike.
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT), str(ROOT / "tools")]

import io
import json
import re
import struct
import unittest

import rubric as rubric_mod

DOCS = ROOT / "docs"
REQUIRED = ["index.html", "style.css", "app.js", "store.js", "graph.js", "sync.js", "config.js",
            "analysis.js", "compare.js", "table.js", "xlsx.js",
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

    def test_the_page_does_not_zoom(self):
        """An app, not a page: a double-tap that magnified the screen was the one gesture that
        gave the web away. WKWebView honours the viewport's two flags; touch-action covers Safari,
        which ignores them, and also removes WebKit's double-tap wait before a click."""
        viewport = re.search(r'name="viewport" content="([^"]*)"', self.html).group(1)
        self.assertIn("maximum-scale=1", viewport)
        self.assertIn("user-scalable=no", viewport)
        root = re.search(r"\nhtml\s*\{[^}]*\}", self.css).group(0)
        self.assertIn("touch-action: pan-x pan-y", root,
                      "the one layer Safari honours; it must deny pinch-zoom, not just double-tap")
        for weaker in ("pinch-zoom", "manipulation", "touch-action: auto"):
            self.assertNotIn(weaker, root)
        strip = re.search(r"\.strip\s*\{[^}]*\}", self.css).group(0)
        self.assertIn("touch-action: none", strip, "the drag still needs none, not the page rule")

    def test_the_screen_title_reads_as_a_title(self):
        """The h1 names the screen. It was caption-sized; it must now be larger than the spirit
        names in the rows beneath it, which are 1rem."""
        h1 = re.search(r"\.bar h1\s*\{[^}]*\}", self.css).group(0)
        size = float(re.search(r"font-size:\s*([\d.]+)rem", h1).group(1))
        lh = float(re.search(r"line-height:\s*([\d.]+)", h1).group(1))
        bar = float(re.search(r"--bar-h:\s*(\d+)px", self.css).group(1))
        self.assertGreaterEqual(size, 1.4)
        self.assertIn("font-weight: 600", h1)
        self.assertLessEqual(size * 16 * lh, bar - 8, "the title no longer fits the bar")
        for keep in ("white-space: nowrap", "text-overflow: ellipsis", "min-width: 0"):
            self.assertIn(keep, h1, "a long name would wrap the bar taller than --bar-h")
        main = re.search(r"\n#main\s*\{[^}]*\}", self.css).group(0)
        self.assertIn("var(--bar-h)", main, "the content offset must follow the bar height")

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


class TestPhoneTable(unittest.TestCase):
    """The desktop table is wide and sortable. A phone is 375 points across, so the sorting stays
    and the column chooser goes."""

    def test_it_is_a_view_of_the_collection_not_a_tab(self):
        html = text("index.html")
        self.assertNotIn("screen-table", html)
        self.assertNotIn('data-tab="table"', html)
        self.assertLess(html.index("table.js"), html.index('src="app.js"'))

    def test_it_carries_proof_and_year(self):
        """What separates one release from the next when three share a name."""
        src = code("table.js")
        self.assertIn('key: "proof"', src)
        self.assertIn('key: "year"', src)

    def test_unscored_rows_sort_to_the_bottom_either_way(self):
        """An unscored bottle is not a low score. Floating them to the top of an ascending sort
        would bury everything that has one, so the null test must come before the direction is
        applied rather than being folded into it."""
        src = code("table.js")
        block = src[src.index("function compare("):src.index("function tieBreak(")]
        missing = block.index("x === null")
        self.assertLess(missing, block.index("desc ?"),
                        "nulls are ordered by the sort direction instead of always sinking")
        self.assertIn("return 1;", block)
        self.assertIn("return -1;", block)

    def test_it_sorts_on_every_field_the_desktop_does(self):
        """The point of the tab. The desktop sorts on all of these; a phone that only sorts on
        what happens to be on screen is a different, smaller thing."""
        src = code("table.js")
        for key in ("score", "name", "code", "type", "proof", "year", "n", "region", "age",
                    "paid", "value_per_oz", "score_per_dollar", "medal", "best", "worst",
                    "rarity", "status", "conc_ratio", "proof_diff"):
            self.assertIn('key: "%s"' % key, src, "cannot sort by %s" % key)

    def test_the_sorted_field_is_always_shown(self):
        """Sorting by something you cannot see is a list silently reordered by a number that is
        nowhere on the screen."""
        src = code("table.js")
        block = src[src.index("function columns("):src.index("function pick(")]
        self.assertIn("core.concat([chosen])", block)

    def test_medals_sort_by_standing_not_alphabetically(self):
        """Bronze before Diamond would be nonsense."""
        src = code("table.js")
        self.assertIn("function medalRank(", src)
        self.assertIn("app.rubric.bands", src)

    def test_the_first_tap_on_medal_leads_with_the_best(self):
        """It is an ordered field even though it reads as a word, so it cannot take its default
        direction from whether the column is right-aligned."""
        src = code("table.js")
        self.assertIn('key: "medal", label: "Medal", high: true', src)
        self.assertIn("field(key).num || field(key).high", src)

    def test_equal_scores_are_broken_the_way_the_desktop_breaks_them(self):
        """A 90 from four nights outranks a 90 from one."""
        src = code("table.js")
        block = src[src.index("function tieBreak("):src.index("function columns(")]
        self.assertIn("a.n !== b.n", block)

    def test_the_two_derived_columns_are_worked_out_here(self):
        """$ / oz and score / $ are the two the spreadsheet could never produce, and the phone is
        not sent them — it has the price and the size, so it computes them."""
        src = code("table.js")
        self.assertIn("paid / oz", src)
        self.assertIn("score / paid", src)

    def test_the_strip_scrolls_rather_than_being_squeezed(self):
        self.assertIn("overflow-x: auto", text("style.css"))
        self.assertIn("ptable-wrap", code("table.js"))


class TestEditingAndDeletingOnThePhone(unittest.TestCase):
    """Journal files are immutable on the phone too: a correction is a new revision file and a
    deletion is a tombstone, both queued like any other upload so they work with no signal."""

    def test_a_correction_writes_the_next_revision(self):
        src = code("store.js")
        block = src[src.index("function reviseCard("):src.index("function deleteCard(")]
        self.assertIn("(previous.revision || 1) + 1", block)
        self.assertIn("-r${revision}.json", block)
        self.assertIn("enqueue(", block)

    def test_a_deletion_is_a_tombstone_not_an_unlink(self):
        src = code("store.js")
        block = src[src.index("function deleteCard("):src.index("function submitScorecard(")]
        self.assertIn("deleted: true", block)
        self.assertIn("enqueue(", block)

    def test_editing_revises_rather_than_adding_a_second_sitting(self):
        """Otherwise correcting a typo would double the spirit's average."""
        src = code("app.js")
        self.assertIn("d.revising", src)
        self.assertIn("Store.reviseCard(d.revising", src)

    def test_a_sitting_opens_rather_than_carrying_its_controls(self):
        """Two buttons squeezed beside a date is a poor tap target, and the card behind them had
        nowhere to be read."""
        src = code("app.js")
        self.assertIn("openSitting(c, s)", src)

    def test_both_are_offered_on_the_sitting_itself(self):
        src = code("app.js")
        block = src[src.index("function openSitting("):src.index("function editSitting(")]
        self.assertIn("editSitting(card)", block)
        self.assertIn("removeSitting(card, spirit)", block)

    def test_the_breakdown_shows_every_category(self):
        """The scores and the notes written against them are the sitting; a total alone is not."""
        block = code("app.js")
        block = block[block.index("function openSitting("):block.index("function editSitting(")]
        self.assertIn("rub.categories", block)
        self.assertIn("card.scores", block)
        self.assertIn("notes[cat.key]", block)
        self.assertIn("overall", block)

    def test_an_uncounted_sitting_says_so(self):
        block = code("app.js")
        block = block[block.index("function openSitting("):block.index("function editSitting(")]
        self.assertIn("include_in_average === false", block)

    def test_deleting_from_the_breakdown_does_not_go_back_to_it(self):
        """The screen behind the chevron would be a sitting that no longer exists."""
        src = code("app.js")
        self.assertIn('currentScreen() === "sitting"', src)

    def test_the_screen_exists(self):
        self.assertIn("screen-sitting", text("index.html"))
        self.assertIn('sitting: document.getElementById("screen-sitting")', code("app.js"))

    def test_deleting_asks_first(self):
        self.assertIn("window.confirm(", code("app.js"))


class TestAnalysisAndCompare(unittest.TestCase):
    """Both screens work from what the phone already holds, because the whole app is built to
    open in a bar basement with no signal."""

    def test_the_screens_and_tabs_exist(self):
        html = text("index.html")
        for screen in ("screen-compare", "screen-analysis"):
            self.assertIn(screen, html)
        for tab in ('data-tab="compare"', 'data-tab="analysis"'):
            self.assertIn(tab, html)

    def test_they_are_loaded_before_the_app_that_calls_them(self):
        html = text("index.html")
        self.assertLess(html.index("analysis.js"), html.index('src="app.js"'))
        self.assertLess(html.index("compare.js"), html.index('src="app.js"'))

    def test_nothing_is_fetched_to_draw_them(self):
        """A chart that needs the network is useless where this app is used."""
        for name in ("analysis.js", "compare.js"):
            src = code(name)
            self.assertNotIn("fetch(", src, f"{name} reaches for the network")
            self.assertNotIn("Graph.", src, f"{name} depends on being signed in")

    def test_no_charting_library_is_pulled_in(self):
        """Hand-written SVG. A library would be a large download for a page whose point is
        opening instantly without one."""
        html = text("index.html")
        for bad in ("cdn.", "chart.js", "d3.", "unpkg", "jsdelivr"):
            self.assertNotIn(bad, html.lower())
        self.assertIn("createElementNS", code("analysis.js"))

    def test_careers_survive_the_app_being_closed(self):
        """They used to live only in memory, so closing the app threw away every score the PC had
        reconciled — and analysis would open blank until the next sync, which needs a connection."""
        store = code("store.js")
        self.assertIn('careers: "wtb.careers"', store)
        self.assertIn("setCareers", store)
        boot = code("app.js")
        self.assertIn("Store.careers()", boot)

    def test_compare_and_the_table_fold_the_figures_the_same_way(self):
        """One helper, so a comparison and a ranking cannot disagree about the same spirit."""
        self.assertIn("categoryMeansFor(code)", code("compare.js"))
        self.assertIn("categoryMeansFor(s.code)", code("table.js"))
        shared = code("app.js")
        self.assertIn("function categoryMeansFor(", shared)
        self.assertIn("pc.categories", shared)


class TestRemovingASittingFromThePhone(unittest.TestCase):
    """Listing only the phone's own cards meant a collection scored at a desk showed none, so
    nothing could be corrected or removed from here at all."""

    def test_the_detail_lists_the_published_sittings_too(self):
        src = code("app.js")
        self.assertIn("function sittingsFor(", src)
        block = src[src.index("function sittingsFor("):src.index("function openDetail(")]
        self.assertIn("Store.cardsFor(code)", block)
        self.assertIn("Store.careers().sittings", block)

    def test_a_local_copy_wins_over_the_published_one(self):
        """The phone's is the newer of the two whenever it has scored since the PC last summed."""
        src = code("app.js")
        block = src[src.index("function sittingsFor("):src.index("function openDetail(")]
        self.assertIn("seen.has(t.tasting_id)", block)

    def test_deleting_one_it_never_held_still_takes_effect_here(self):
        """A card scored on the PC has no local copy to drop, so the tombstone is remembered
        separately — otherwise the published list would show it again on the next reload."""
        store = code("store.js")
        self.assertIn('buried: "wtb.buried"', store)
        block = store[store.index("function deleteCard("):]
        self.assertIn("K.buried", block[:900])
        app_src = code("app.js")
        self.assertIn("Store.buried()", app_src)

    def test_the_score_says_why_it_has_not_moved_yet(self):
        """It is the PC's figure and still counts the deleted sitting until the PC reads the
        tombstone. Silence there looks like the deletion did not work."""
        self.assertIn("function buriedHere(", code("app.js"))
        self.assertIn("still counted", code("app.js"))


class TestAnOlderPcOverwritingTheData(unittest.TestCase):
    """Two computers share the OneDrive folder, so whichever refreshed last wins — and an older
    copy of the app wins by *removing* fields the phone is looking for. That is silent: the phone
    finds nothing where the sittings were and has no way to say why."""

    def test_the_published_file_carries_a_version(self):
        import app as app_mod
        self.assertGreaterEqual(app_mod.PUBLISHED_VERSION, 2)

    def test_the_phone_knows_which_version_it_needs(self):
        src = code("app.js")
        self.assertIn("NEEDS_PUBLISHED_VERSION", src)
        self.assertIn("function staleSource", src.replace("const staleSource", "function staleSource"))

    def test_the_two_numbers_agree(self):
        """If the phone asks for a version the PC never publishes, every screen says the PC is
        out of date for ever."""
        import app as app_mod
        m = re.search(r"NEEDS_PUBLISHED_VERSION = (\d+)", code("app.js"))
        self.assertIsNotNone(m)
        self.assertLessEqual(int(m.group(1)), app_mod.PUBLISHED_VERSION)

    def test_an_empty_sitting_list_explains_itself(self):
        """Rendering nothing is indistinguishable from a screen that failed to draw."""
        src = code("app.js")
        self.assertIn("sittings.length ? null :", src)
        self.assertIn("staleSource()", src)

    def test_the_sync_screen_says_what_it_holds(self):
        """So an empty screen elsewhere can be traced rather than guessed at."""
        src = code("app.js")
        self.assertIn("scored ·", src.replace("` · ", "` · "))
        self.assertIn("sittings`", src)


class TestAFinishedBottleIsLeftOutOnThePhone(unittest.TestCase):
    """The desktop's rule, held here too: an empty bottle is not offered for scoring, the ones
    scored while they lasted sit under a Finished chip, and the rest are not listed at all."""

    def block(self, start):
        src = code("app.js")
        block = src[src.index(start):]
        return block[:block.index("\nfunction ")]

    def test_the_ordinary_lists_leave_finished_bottles_out(self):
        self.assertIn("spirits.filter((s) => !goneSpirit(s))", self.block("function shownSpirits("))

    def test_the_finished_chip_needs_a_sitting(self):
        self.assertIn("goneSpirit(s) && sittingsFor(s.code).length",
                      self.block("function shownSpirits("))
        self.assertIn('["gone", "Finished"]', code("app.js"))

    def test_the_detail_screen_offers_no_card_for_one(self):
        block = self.block("function openDetail(")
        i = block.index('"Score a pour"')
        self.assertIn("goneSpirit(s)", block[max(0, i - 400):i],
                      "the Score button is offered unconditionally")

    def test_opening_a_card_for_one_is_turned_back(self):
        block = self.block("function openScore(")
        self.assertIn("goneSpirit(s)", block)
        self.assertLess(block.index("goneSpirit(s)"), block.index("openScoreFor("))


class TestTheRankingLens(unittest.TestCase):
    """Aesthetics is the bottle and value is the price, so "which is the better whiskey" is a
    different question from "which was the better buy". The desktop table can ask either; the
    phone table now can too."""

    def test_the_lens_is_built_from_the_rubric_not_hard_coded(self):
        """Which categories count as flavour is config. A third non-flavour category one day
        should generate its own chips rather than needing this rewritten."""
        src = code("app.js")
        self.assertIn("(c) => c.flavour", src)
        block = src[src.index("function lensPresets("):src.index("function lensScore(")]
        self.assertNotIn("aesthetics", block, "the presets name a category instead of reading it")
        self.assertNotIn("value", block.replace("flavour", ""), "same for value")

    def test_the_presets_cover_flavour_each_extra_and_everything(self):
        src = code("app.js")
        block = src[src.index("function lensPresets("):src.index("function lensScore(")]
        self.assertIn('id: "flavour"', block)
        self.assertIn("flavour+", block)
        self.assertIn('id: "all"', block)
        self.assertIn("for (const c of other) out.push", block)

    def test_any_set_can_be_chosen(self):
        self.assertIn("lensPicker(", code("table.js"))

    def test_an_empty_lens_is_impossible(self):
        """Unticking the last category would blank every row with no way back."""
        block = code("table.js")
        block = block[block.index("function lensPicker("):]
        self.assertIn("keys.length ? keys : [c.key]", block)

    def test_a_partly_scored_spirit_scores_nothing_under_the_lens(self):
        """Summing only the categories that happen to be filled in would read as a low score
        rather than an incomplete one."""
        block = code("app.js")
        block = block[block.index("function lensScore("):]
        self.assertIn("return null;", block[:400])

    def test_medals_are_withheld_unless_the_lens_is_the_whole_card(self):
        """The bands are defined against 100. A medal beside a flavour-only score would claim
        something the number cannot support."""
        src = code("table.js")
        self.assertIn("lensIsEverything(lens.keys)", src)
        self.assertIn("whole && career ? career.medal : null", src)

    def test_the_table_says_what_the_score_is_out_of(self):
        self.assertIn("lensMax(lens.keys)", code("table.js"))

    def test_a_compare_column_is_identified_beyond_its_name(self):
        """Two different bottles can carry the same name; the column has to say which is which."""
        src = code("compare.js")
        self.assertIn("cmp-sub", src)
        self.assertIn("it.spirit.distillery, it.code", src)

    def test_it_does_not_send_you_to_the_wrong_refresh(self):
        """Per-category figures are computed on the PC. Telling the user to refresh the phone
        sends them round a loop that cannot end, because refreshing here cannot produce them."""
        src = code("compare.js")
        block = src[src.index("Only the totals"):]
        self.assertIn("Refresh there first", block[:300])

    def test_each_axis_is_drawn_against_its_own_maximum(self):
        """Otherwise Flavor out of 20 looks twice as good as Balance out of 10 (SPEC.md 4.2)."""
        self.assertIn("cat.max) * 100", code("compare.js"))

    def test_a_chart_says_how_many_points_it_stands_on(self):
        """Three bottles is not a finding, and a chart that does not say so is making a claim it
        cannot support."""
        self.assertIn("chart-basis", code("analysis.js"))

    def test_the_scatter_uses_career_scores(self):
        self.assertIn("careerFor(s.code)", code("analysis.js"))


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


class TestNativeShell(unittest.TestCase):
    """The same bundle also runs inside the native iOS app (guide/IOS-APP.md), where sign-in and
    a backup of local storage are done in Swift and reached over the bridge in native.js. Every
    rule here protects the GitHub Pages copy as much as the app: the browser must keep taking the
    paths it always took, and the two halves must agree on the messages between them.

    These read the source rather than run it — no JavaScript engine is installed on the machine
    that runs them — so they pin names, order and the agreement between the two halves, not
    behaviour. The phone is where behaviour is checked (ios/README.md, the three tests)."""

    # The protocol between docs/ and the shell. A message the web sends that is not here would be
    # ignored by Swift without a word; one here that the web never sends is dead weight.
    WEB_TO_NATIVE = {"ready", "auth.status", "auth.signIn", "auth.token", "auth.signOut",
                     "backup.save", "backup.load", "file.share"}

    def test_native_js_is_precached_and_loaded_before_what_uses_it(self):
        self.assertIn('"./native.js"', text("sw.js"))
        html = text("index.html")
        self.assertLess(html.index('src="native.js"'), html.index('src="store.js"'))
        self.assertLess(html.index('src="native.js"'), html.index('src="graph.js"'))

    def test_the_bridge_is_a_plain_script(self):
        """The rest of the bundle is plain scripts; one ES module among them would load in a
        different order from the <script> tags and Native would not exist when store.js ran."""
        src = code("native.js")
        self.assertNotIn("export ", src)
        self.assertNotIn("import ", src)
        self.assertIn("const Native = (() => {", src)

    def test_no_service_worker_inside_the_shell(self):
        """The app ships and updates its own bundle; a service worker there would keep serving
        the copy it cached first, however many times the app updated."""
        self.assertIn('if (!Native.available && "serviceWorker" in navigator)', code("app.js"))

    def test_boot_waits_for_the_shell_before_reading_storage(self):
        """A backup can only be restored into an empty store, so the restore has to run before
        the first read — and the bridge has to be up before the restore can ask for it."""
        block = code("app.js")
        block = block[block.index("async function boot("):]
        self.assertLess(block.index("await Native.init()"),
                        block.index("await Store.restoreFromNative()"))
        self.assertLess(block.index("await Store.restoreFromNative()"),
                        block.index("Store.careers()"))

    def test_init_cannot_hang_the_browser_path(self):
        """A shell that installs the handler but never answers must still produce a working app,
        so init() gives up rather than waiting on a hello that never comes — and within a few
        seconds, or the shell's first paint would visibly wait. The timer only ever runs inside
        the shell: the browser returns before it exists."""
        src = code("native.js")
        block = src[src.index("function init("):src.index("function request(")]
        self.assertIn("setTimeout(", block)
        self.assertIn("clearTimeout(timer)", block)
        self.assertIn("Promise.resolve(false)", block)
        m = re.search(r"const HELLO_TIMEOUT = (\d+)", src)
        self.assertIsNotNone(m, "the wait is not a named constant")
        self.assertTrue(1000 <= int(m.group(1)) <= 5000, m.group(1))

    def test_a_request_the_shell_never_answers_does_not_hang_the_page(self):
        """restoreFromNative is awaited before the first paint; a reply that never comes must
        become a rejection, not a blank screen. The sign-in sheet is the one request allowed to
        wait on a person, so it opts out with timeout 0."""
        src = code("native.js")
        block = src[src.index("function request("):src.index("const send")]
        self.assertIn("no reply to", block)
        self.assertRegex(src, r"const REQUEST_TIMEOUT = \d+")
        self.assertIn("timeout: 0", code("graph.js"))

    def test_the_two_replies_named_differently_are_handled_as_such(self):
        src = code("native.js")
        self.assertIn('"auth.signIn": "auth.result"', src)
        self.assertIn('"backup.load": "backup.result"', src)

    def test_graph_adds_the_native_branch_and_keeps_the_browser_one(self):
        """The PWA on GitHub Pages must behave exactly as before: MSAL, redirect flow and all."""
        src = code("graph.js")
        self.assertIn('Native.caps.has("auth")', src)
        for kept in ("loginRedirect", "handleRedirectPromise", "MSAL_SRC", "acquireTokenSilent"):
            self.assertIn(kept, src, f"the browser path lost {kept}")

    def test_the_shell_never_loads_msal(self):
        src = code("graph.js")
        block = src[src.index("async function ensure("):src.index("async function resume(")]
        self.assertLess(block.index("native()"), block.index("loadScript(CONFIG.MSAL_SRC)"))

    def test_the_redirect_scheme_is_the_bundle_id(self):
        """What Entra has on file under Mobile and desktop applications; the bundle id and this
        string have to move together."""
        src = code("graph.js")
        self.assertIn('"msauth.com.mikeyang.whiskey"', src)
        block = src[src.index("async function signIn("):src.index("async function signOut(")]
        self.assertIn("redirectScheme: NATIVE_REDIRECT_SCHEME", block)
        for field in ("clientId: CONFIG.CLIENT_ID", "authority: CONFIG.AUTHORITY",
                      "scopes: CONFIG.SCOPES"):
            self.assertIn(field, block, "the shell is handed what config.js says, never its own copy")

    def test_a_token_refusal_reads_the_same_as_in_the_browser(self):
        """The queue tells a lapsed sign-in from a dropped connection by this message; a different
        one would be treated as the network and nobody would be asked to sign in again."""
        src = code("graph.js")
        block = src[src.index("function token("):src.index("const url =")]
        self.assertEqual(block.count('"sign-in required"'), 2,
                         "both branches of acquireToken() must throw the same error")
        self.assertIn("r.interactive", block)

    def test_the_store_mirrors_itself_to_the_shell(self):
        src = code("store.js")
        self.assertIn('"tasting-book.json"', src)
        self.assertIn('Native.send("backup.save"', src)
        block = src[src.index("function write("):src.index("const BACKUP_NAME")]
        self.assertIn("mirror()", block, "a write that is not mirrored is a write the shell loses")

    def test_a_backup_is_restored_only_into_an_empty_store(self):
        """The live store is always the newer of the two; a backup put over it would roll back
        every card scored since the last save."""
        src = code("store.js")
        block = src[src.index("async function restoreFromNative("):src.index("function present(")]
        ask = block.index('Native.request("backup.load")')
        self.assertLess(block.index("Object.values(K).some(present)"), ask)
        self.assertIn("return false", block[:ask])

    def test_every_message_the_web_sends_is_in_the_protocol(self):
        """Swift switches on the type string. A spelling the shell does not know is not an error
        anywhere — the request simply never comes back."""
        sent = set()
        for name in ("native.js", "graph.js", "store.js", "sync.js", "app.js"):
            sent.update(re.findall(r'Native\.(?:request|send)\(\s*"([^"]+)"', code(name)))
        sent.update(re.findall(r'\btype:\s*"([^"]+)"', code("native.js")))
        self.assertEqual(sent, self.WEB_TO_NATIVE)

    def test_coming_to_the_foreground_syncs(self):
        """The shell's foreground is the browser's online and visible at once: it pulls what the
        PC published if it has been a while, and tries the queue regardless."""
        src = code("app.js")
        self.assertIn('Native.on("foreground"', src)
        line = src[src.index('Native.on("foreground"'):]
        self.assertIn('autoSync("foreground")', line[:line.index("\n")])

    def test_signing_in_inside_the_shell_finishes_what_boot_would_have(self):
        """In the browser, sign-in leaves the page and boot() picks up on return. The shell's page
        never moves, so the pull and the upload have to happen where the sign-in resolves."""
        src = code("app.js")
        block = src[src.index("async function signInFlow("):src.index("async function signOutFlow(")]
        self.assertIn("await Graph.signIn()", block)
        # One call: a manual Sync.start pulls the snapshot and then pushes the queue.
        self.assertIn("await refresh()", block)
        self.assertIn("onclick: signInFlow", src)



class TestSyncWiring(unittest.TestCase):
    """docs/sync.js is the engine; app.js draws it and calls it. The engine's logic runs for
    real in test_phone_js.py; what is pinned here is the wiring the DOM-free tests cannot see."""

    def test_the_engine_is_loaded_between_graph_and_the_screens(self):
        html = text("index.html")
        order = [m for m in re.findall(r'<script src="([^"]+)">', html)]
        self.assertLess(order.index("graph.js"), order.index("sync.js"))
        self.assertLess(order.index("sync.js"), order.index("app.js"))
        self.assertIn('"./sync.js"', text("sw.js"), "the PWA would open without its sync engine")

    def test_the_engine_needs_no_page(self):
        """So it can run in V8. The page's names stay in app.js."""
        src = code("sync.js")
        self.assertNotRegex(src, r"\bdocument\.")
        self.assertNotRegex(src, r"\bapp\.")
        self.assertNotRegex(src, r"\bscreens\b")
        self.assertNotIn("setTimeout", src, "the throttle is on the clock, not a timer")

    def test_a_quick_app_switch_is_throttled_but_not_for_long(self):
        m = re.search(r"MIN_INTERVAL = (\d+) \* 60 \* 1000", code("sync.js"))
        self.assertIsNotNone(m)
        self.assertTrue(1 <= int(m.group(1)) <= 15, "minutes, and a bottle approved on the PC "
                        "should show on the next real return")

    def test_the_downloads_run_together_and_one_failure_keeps_the_rest(self):
        self.assertIn("Promise.allSettled", code("sync.js"))

    def test_the_button_is_answered_before_the_first_byte(self):
        src = code("sync.js")
        block = src[src.index("async function run("):src.index("let uploaded")]
        self.assertIn("emit();", block, "the checklist must be published before any download")
        app_src = code("app.js")
        self.assertIn("disabled: S.running", app_src)
        self.assertIn('"Refreshing…"', app_src)

    def test_every_way_back_syncs(self):
        boot = code("app.js")
        boot = boot[boot.index("async function boot("):]
        for trigger in ('autoSync("online")', 'autoSync("visible")', 'autoSync("foreground")',
                        'autoSync("boot")'):
            self.assertIn(trigger, boot)
        self.assertIn("!document.hidden", boot, "visibilitychange fires for hiding too")
        self.assertLess(boot.index("Sync.onChange(onSync)"), boot.index('setTab("collection")'),
                        "subscribe before the first screen, or the first run's changes go unseen")
        self.assertNotIn("await refresh()", boot, "boot must not wait for the network")

    def test_an_automatic_sync_never_leaves_the_page(self):
        """In the browser build MSAL used to answer a lapsed token with acquireTokenRedirect — a
        full-page navigation. Harmless when only the button asked; fatal once boot and coming
        back to the page ask too, because a half-scored card lives only in memory."""
        src = code("graph.js")
        self.assertNotIn("acquireTokenRedirect", src)
        self.assertIn("loginRedirect", src, "the Sign in button is the one navigation left")

    def test_last_sync_means_everything_came_down(self):
        """A pull with a failed step must not move lastSync, or the next foreground would skip
        the retry — and the flush of old never set it at all after a failure."""
        src = code("sync.js")
        self.assertRegex(src, r"if \(pull && !pulls\.some\(.*failed.*\)\) meta\.lastSync")
        self.assertNotIn("lastSync:", code("app.js").replace("m.lastSync", ""))

    def test_a_refused_card_does_not_block_the_queue(self):
        src = code("sync.js")
        self.assertIn("Store.noteFailure(item.path, msg)", src)
        self.assertIn("function noteFailure(", code("store.js"))
        self.assertIn("i.error", code("app.js"), "the Sync screen shows which card and why")

    def test_one_token_serves_a_burst(self):
        src = code("graph.js")
        self.assertIn("tokenInflight", src)
        self.assertIn("listFolder", src)
        self.assertIn("$select=name,eTag,size,lastModifiedDateTime", src)

    def test_a_finished_sync_redraws_what_is_on_screen_not_the_tab(self):
        src = code("app.js")
        block = src[src.index("function onSync("):src.index("async function signInFlow(")]
        self.assertIn("currentScreen()", block)
        self.assertNotIn("app.tab", block)
        for screen in ("collection", "detail", "compare", "analysis", "sync"):
            self.assertIn(f'case "{screen}"', block)
        self.assertNotIn('case "score"', block, "a card being written is never redrawn under a finger")
        self.assertIn('classList.contains("keyboard")', block)
        self.assertIn("window.scrollY", block)



class TestPhoneOnlyWiring(unittest.TestCase):
    """The store's half of phone-only mode runs for real in test_phone_js.py. This pins what the
    page does with it, and that the OneDrive road is still there to be chosen."""

    def setUp(self):
        self.src = code("app.js")

    def test_both_arrangements_are_offered(self):
        block = self.src[self.src.index("function modeCard("):self.src.index("function modeChooser(")]
        self.assertIn('opt("local", "This phone only"', block)
        self.assertIn('opt("onedrive", "OneDrive"', block)
        sync = self.src[self.src.index("function renderSync("):self.src.index("const refresh =")]
        self.assertEqual(sync.count("modeCard()"), 2, "shown in both modes, so either can be left")
        self.assertIn("onclick: signInFlow", sync, "the OneDrive screen still signs in")
        self.assertIn("onclick: refresh", sync)

    def test_a_phone_that_has_not_chosen_is_asked_on_its_first_screen(self):
        block = self.src[self.src.index("function emptyCollectionNote("):self.src.index("function localCard(")]
        self.assertIn("return modeChooser()", block)
        self.assertIn("emptyCollectionNote()", self.src[self.src.index("function renderCollection("):])

    def test_leaving_onedrive_says_what_it_costs(self):
        block = self.src[self.src.index("function chooseMode("):self.src.index("function applyMode(")]
        self.assertEqual(block.count("window.confirm"), 2, "each direction is confirmed")
        self.assertIn("not sent to the PC later", block)

    def test_phone_only_adds_a_bottle_straight_in_and_onedrive_still_queues_it(self):
        block = self.src[self.src.index("function renderAdd("):self.src.index("function startBarPour(")]
        self.assertIn("Store.addLocalSpirit(bottleFields(fields))", block)
        self.assertIn('Store.submitPending("Bottle", filled)', block)
        self.assertLess(block.index("if (local) {"), block.index("Store.submitPending("),
                        "the phone-only branch must return before the approval queue")

    def test_a_phone_only_book_loads_no_sign_in_library(self):
        boot = self.src[self.src.index("async function boot("):]
        self.assertLess(boot.index("if (Store.isLocal()) return;"), boot.index("Graph.resume()"))

    def test_a_backup_can_be_taken_and_put_back(self):
        self.assertIn("Store.exportAll()", self.src)
        self.assertIn("Store.importAll(", self.src)
        block = self.src[self.src.index("function importBook("):self.src.index("function backupCard(")]
        self.assertLess(block.index("window.confirm"), block.index("Store.importAll("),
                        "an import overwrites the phone; it asks first")
        self.assertIn('id="tab-sync-label"', text("index.html"))

    def test_a_phone_only_card_is_not_waiting_for_anything(self):
        self.assertIn("!c._sent && !c._local", self.src)


class TestAddForm(unittest.TestCase):
    """Type is picked from the workbook's list, and an age can be a word. Both were plain text
    boxes: one let "Burbon" through, and the other had a digits-only keypad that could not type
    NAS at all."""

    def setUp(self):
        self.js = code("app.js")
        self.css = text("style.css")

    def test_type_is_chosen_from_the_list(self):
        self.assertIn("Store.types()", self.js)
        self.assertIn('el("select"', self.js)
        self.assertNotIn('input("Type"', self.js, "a free-text Type is how a misspelling gets in")
        self.assertIn("typeField(),", self.js)

    def test_there_is_room_for_a_type_the_list_does_not_hold(self):
        self.assertIn('el("option", { value: OTHER_TYPE }, "Other…")', self.js)
        self.assertIn("other.hidden = !typed", self.js)
        self.assertIn("Store.canonicalType(fields.Type)", self.js)

    def test_age_offers_the_two_words_a_label_may_carry(self):
        self.assertIn('[["NAS", "no age statement"], ["N/A", "not applicable"]]', self.js)
        self.assertNotIn('input("Age"', self.js)
        self.assertIn("ageField(),", self.js)
        self.assertIn('inputmode: "numeric"', self.js, "years are still typed on the number pad")

    def test_the_drop_down_does_not_zoom_the_page(self):
        """A select under 16 px zooms Safari on focus exactly as an input does."""
        block = re.search(r"textarea, input\[type=\"text\"\][^{]*\{[^}]*\}", self.css)
        self.assertIn("select", block.group(0).split("{")[0])
        self.assertIn("font-size: 16px", block.group(0))

    def test_the_age_words_are_big_enough_to_tap(self):
        rule = re.search(r"\.age-row \.chip\s*\{[^}]*\}", self.css)
        self.assertIsNotNone(rule)
        self.assertIn("min-height: 44px", rule.group(0))


class TestExcelExportWiring(unittest.TestCase):
    """A phone-only book can be taken out as an Excel workbook shaped like the PC's. What the
    file holds is checked by running the writer (test_phone_js.py); this is the wiring."""

    def setUp(self):
        self.js = code("app.js")
        self.xlsx = code("xlsx.js")

    def test_it_is_offered_to_a_phone_only_book_and_to_no_other(self):
        """With a PC behind the phone the PC's workbook is the collection. A second file of the
        same shape, without its photographs, is only something to mistake for it."""
        self.assertEqual(self.js.count("workbookCard()"), 2, "defined once, used once")
        self.assertIn("fill(screens.sync, localCard(q), workbookCard(), backupCard(), modeCard());", self.js)
        self.assertIn('"Export to Excel"', self.js)

    def test_the_file_is_not_given_the_master_s_name(self):
        self.assertIn("`Tasting Book Collection ${Store.today()}.xlsx`", self.js)

    def test_the_workbook_is_built_inside_the_tap(self):
        """A share sheet opens only inside the tap that asked for it. Anything awaited first —
        which is what a CompressionStream would be — spends the tap, so the whole writer is
        synchronous, compression included."""
        self.assertIn("function exportWorkbook() {", self.js)
        self.assertNotIn("async function exportWorkbook", self.js)
        self.assertIn("Xlsx.collectionBook(Store.spirits(), { types: Store.types() })", self.js)
        for word in ("await ", "async ", "Promise", "CompressionStream"):
            self.assertNotIn(word, self.xlsx)

    def test_the_writer_needs_no_page_and_no_library(self):
        for word in ("TextEncoder", "import ", "require("):
            self.assertNotIn(word, self.xlsx)
        # Whole words: "officedocument." is in every content type an .xlsx declares.
        self.assertIsNone(re.search(r"(?<![A-Za-z])(document|window|navigator)\.", self.xlsx))
        html = text("index.html")
        self.assertLess(html.index('src="xlsx.js"'), html.index('src="app.js"'))
        self.assertIn('"./xlsx.js"', text("sw.js"))

    def test_the_backup_still_goes_out_and_comes_back(self):
        """The workbook has no scores in it and cannot be imported. The backup is still the copy
        that restores a phone."""
        self.assertIn('"application/json", Store.exportAll()', self.js)
        self.assertIn("Store.importAll(", self.js)


class TestScreensAreLaidOutForHowTheyAreUsed(unittest.TestCase):
    """Three things the screens made harder than they needed to be: a scorecard nearly three
    screens long, half the collection's filters off the edge of the phone, and a Sync screen that
    opened on a setting chosen once."""

    def setUp(self):
        self.js = code("app.js")
        self.css = text("style.css")
        self.score = self.js[self.js.index("function renderScore()"):self.js.index("function fitNotes()")]

    def test_a_category_note_is_folded_until_it_is_wanted(self):
        """Ten note boxes open at once were a third of the card's height."""
        self.assertIn('class: "note-add", hidden: written ? "" : null', self.score)
        self.assertIn('spellcheck: "true", hidden: written ? null : ""', self.score)
        self.assertIn("addNote.hidden = true; notes.hidden = false; notes.focus();", self.score)
        self.assertLess(self.score.index("addNote,"), self.score.index("val),"), "in the head row, not a row of its own")

    def test_a_note_opened_and_left_empty_folds_back(self):
        blur = self.score[self.score.index('notes.addEventListener("blur"'):]
        blur = blur[:blur.index("});") + 3]
        self.assertIn("if (notes.value.trim()) return;", blur)
        self.assertIn("notes.hidden = true;", blur)
        self.assertIn("addNote.hidden = false;", blur)

    def test_the_small_button_is_still_a_whole_finger_wide(self):
        rule = re.search(r"\.note-add::before\s*\{[^}]*\}", self.css)
        self.assertIsNotNone(rule, "the visible pill is ~20 px; the hit area is what makes it tappable")
        self.assertIn("inset: -12px -10px", rule.group(0))

    def test_a_sitting_being_corrected_shows_what_it_already_says(self):
        """The boxes used to open empty on an edit: the notes and the venue were kept and sent
        back unchanged, but could be neither read nor corrected."""
        self.assertIn("}, written);", self.score)
        self.assertIn('}, d.overall || "");', self.score)
        self.assertIn("value: d.venue || null", self.score)
        for opener in ('show("score", "Scorecard", { push: true });', 'show("score", "Edit sitting", { push: true });'):
            after = self.js[self.js.index(opener) + len(opener):][:40]
            self.assertIn("fitNotes();", after, "measured only once the screen is showing")

    def test_two_labels_never_share_two_narrow_cells(self):
        """A 20-point strip has cells about 14 points wide. A score of 16 beside the tick at 15
        printed as "1516"."""
        self.assertIn("const crowded = !showAll && v !== null && Math.abs(n - v) === 1;", self.score)
        self.assertIn("(n % 5 === 0 && !crowded)", self.score)

    def test_venue_comes_after_the_scores(self):
        """Most pours are at home and leave it blank; it was the first thing on the card."""
        self.assertLess(self.score.index("cats,"), self.score.index('el("span", {}, "Venue"), venue)'))
        self.assertLess(self.score.index('el("span", {}, "Overall notes")'),
                        self.score.index('el("span", {}, "Venue"), venue)'))
        self.assertEqual(self.score.count('"Venue"'), 1)

    def test_the_filters_asked_most_come_first_and_not_scored_is_one_of_them(self):
        self.assertIn('[["all", "All"], ["instock", "In stock"], ["unscored", "Not scored"], ["scored", "Scored"],',
                      self.js)
        self.assertIn('app.filter === "unscored") spirits = spirits.filter((s) => s.owned !== false && !careerFor(s.code))',
                      self.js)
        self.assertIn('"Everything you have has been scored."', self.js)

    def test_the_sync_screen_opens_on_what_it_is_for(self):
        """"Where your book lives" is chosen once. It used to sit above the status and the
        Refresh button on every visit."""
        sync = self.js[self.js.index("function renderSync()"):self.js.index("const refresh = ")]
        self.assertNotRegex(sync, r"fill\(screens\.sync,\s*modeCard\(\)")
        self.assertRegex(sync, r"backupCard\(\),\s*modeCard\(\)\);")
        self.assertLess(sync.index("waiting to upload"), sync.rindex("modeCard()"))

    def test_a_comparison_can_start_from_the_bottle(self):
        self.assertIn('CompareView.begin(code); setTab("compare");', self.js)
        cmp_js = code("compare.js")
        self.assertIn("return { render, begin, reset:", cmp_js)
        self.assertIn("picking = codes.length < 2;", cmp_js)
        self.assertIn('(up to " + MAX + ")', cmp_js, "the text said four where the limit is six")
        self.assertNotIn("two to four", cmp_js)


class TestOneCollectionTwoViews(unittest.TestCase):
    """Collection and Table were two tabs for the same bottles, each with its own search box and
    its own filters that did not know about the other's. They are one tab now: a list and a
    table over one search, one set of filters, and five tabs instead of six."""

    def setUp(self):
        self.js = code("app.js")
        self.table = code("table.js")
        self.html = text("index.html")
        self.css = text("style.css")

    def fn(self, start):
        block = self.js[self.js.index(start):]
        return block[:block.index("\nfunction ")]

    def test_there_are_five_tabs(self):
        tabs = re.findall('data-tab="([a-z]+)"', self.html)
        self.assertEqual(tabs, ["collection", "add", "compare", "analysis", "sync"])
        self.assertNotIn("Six tabs", self.css, "the labels were shrunk to fit six")

    def test_one_search_and_one_set_of_filters_serve_both(self):
        """The table is handed what the collection is showing. It has no search box, no filter
        and no idea what was typed."""
        paint = self.fn("function paintRows()")
        self.assertIn("TableView.render(body, shownSpirits(true))", paint)
        self.assertIn("listSorted(shownSpirits())", paint)
        for gone in ('class: "search"', "let query", "let filter", "knownSpirits()"):
            self.assertNotIn(gone, self.table)
        self.assertIn("function render(host, spirits)", self.table)

    def test_the_switch_is_remembered(self):
        bar = self.fn("function paintViewBar(")
        self.assertIn('way("list", "List"), way("table", "Table")', bar)
        self.assertIn("Store.setMeta({ view })", bar)
        self.assertIn('if (Store.meta().view === "table") app.view = "table";', self.js)

    def test_typing_repaints_the_rows_and_leaves_the_box_alone(self):
        """The bar and the body are repainted on a keystroke; the search box above them is not,
        or it would lose the cursor on every letter."""
        self.assertIn("oninput: (e) => { app.query = e.target.value; paintRows(); }", self.js)
        paint = self.fn("function paintRows()")
        self.assertIn('document.getElementById("cview")', paint)
        self.assertNotIn('class: "search"', paint)
        self.assertNotIn("renderCollection()", paint)

    def test_a_ranking_keeps_the_finished_bottles_the_list_leaves_out(self):
        """The list is where a pour is chosen; the table is a record of what was drunk. A
        finished bottle that earned a score keeps its greyed row in the table, as it always did."""
        shown = self.fn("function shownSpirits(")
        self.assertIn("? spirits.filter((s) => !goneSpirit(s) || careerFor(s.code))", shown)
        self.assertIn(": spirits.filter((s) => !goneSpirit(s));", shown)

    def test_two_pickers_stand_where_two_rows_of_chips_stood(self):
        """Nineteen sort chips and seven rank-by chips sat between the filters and the first row
        of figures. Each is one chip now, opening the phone's own picker."""
        controls = self.table[self.table.index("function controls("):self.table.index("function lensPicker(")]
        self.assertEqual(controls.count("pickerChip("), 2)
        self.assertIn("FIELDS.map((f) => [f.key, f.label])", controls, "every field, not only the columns")
        self.assertIn('[["custom", "Custom…"]]', controls)
        for old in ("sortbar", "lensbar", "sortlabel"):
            self.assertNotIn(old, self.table)
            self.assertNotIn(old, self.css)

    def test_a_header_tap_still_sorts_and_turns_the_sort_round(self):
        self.assertIn("onclick: () => pick(f.key)", self.table)
        pick = self.table[self.table.index("function pick("):self.table.index("function controls(")]
        self.assertIn("if (sort === key) desc = !desc;", pick)
        self.assertIn("paintRows();", pick, "the bar above the table names the sort, so it repaints too")

    def test_the_picker_is_a_real_select_the_finger_can_reach(self):
        chip = self.fn("function pickerChip(")
        self.assertIn('el("select", { "aria-label": name }', chip)
        self.assertIn('class: "chip pick"', chip)
        rule = self.css[self.css.index(".chip.pick select {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("opacity: 0", rule)
        self.assertIn("inset: 0", rule)
        self.assertIn("font-size: 16px", rule, "smaller, and Safari zooms the page when it opens")

    def test_the_list_can_be_put_in_order_too(self):
        self.assertIn('["", "Shelf order"], ["score", "Score"], ["name", "Name"]', self.js)
        ordered = self.fn("function listSorted(")
        self.assertIn("if (!of) return spirits;", ordered, "the shelf's order is the order it arrives in")
        self.assertIn("(a === null) - (b === null)", ordered, "no value is not a low value: it goes last")

    def test_a_finished_sync_repaints_whichever_view_is_up(self):
        sync = self.js[self.js.index("function onSync("):self.js.index("async function signInFlow(")]
        self.assertIn("renderCollection() : paintRows()", sync)
        self.assertNotIn("TableView", sync)


if __name__ == "__main__":
    unittest.main(verbosity=2)

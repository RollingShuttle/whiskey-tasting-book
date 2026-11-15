"""
test_phone_js.py — the phone's scripts, actually executed.

    python tests/test_phone_js.py

Every other phone test reads the source. This one runs it in a real JavaScript engine (V8 through
mini-racer), with the handful of browser objects the scripts touch at load replaced by stubs, so a
call to a function that no longer exists fails here and not on the phone. That exact failure
shipped once: a helper was removed while its caller stayed, the suite was green because nothing in
it executed JavaScript, and the app silently stored nothing.

mini-racer is a development dependency only (pip install mini-racer). Without it these tests skip.
"""

# Run straight from the shell, only tests/ is on the path; discovered from the repo root, only
# the root is. Put all three where imports can find them so both ways of running behave alike.
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT), str(ROOT / "tools")]

import json
import tempfile
import unittest

try:
    from py_mini_racer import MiniRacer
except ImportError:                                   # pragma: no cover
    MiniRacer = None

DOCS = ROOT / "docs"

# The least browser that lets the shared scripts load: a window, a storage, a document that
# accepts listeners, timers that never fire, a fetch that refuses. Storage can be told to fail.
STUBS = """
var window = globalThis; var self = globalThis;
var __store = {};
var __reads = 0;
var localStorage = {
  getItem: function (k) { __reads++; return Object.prototype.hasOwnProperty.call(__store, k) ? __store[k] : null; },
  setItem: function (k, v) {
    if (globalThis.__failWrites) { var e = new Error("pretend the disk is full"); e.name = "QuotaExceededError"; throw e; }
    __store[k] = String(v);
  },
  removeItem: function (k) { delete __store[k]; },
  clear: function () { __store = {}; }
};
function addEventListener() {}
var document = {
  hidden: false,
  addEventListener: function () {},
  getElementById: function () { return null; },
  createElement: function () { return { setAttribute: function () {}, appendChild: function () {}, style: {} }; },
  head: { append: function () {} }
};
var navigator = { onLine: true };
// Deterministic, not random: a filename needs four hex characters, not entropy, in a test.
var crypto = { getRandomValues: function (a) { for (var i = 0; i < a.length; i++) a[i] = (i * 37 + 11) % 256; return a; } };
var __timers = 0;
function setTimeout() { return ++__timers; }
function clearTimeout() {}
var fetch = function () { return Promise.reject(new Error("no network in tests")); };
var console = { log: function () {}, warn: function () {}, error: function () {} };
"""

SHARED = ("config.js", "native.js", "store.js", "graph.js", "sync.js", "xlsx.js")


def engine():
    ctx = MiniRacer()
    ctx.eval(STUBS)
    for name in SHARED:
        ctx.eval((DOCS / name).read_text(encoding="utf-8"))
    return ctx


@unittest.skipIf(MiniRacer is None, "pip install mini-racer to run the phone scripts for real")
class TestTheScriptsRun(unittest.TestCase):
    def test_every_script_compiles(self):
        """A syntax error in any file, including the ones that need a page to run."""
        ctx = MiniRacer()
        for path in sorted(DOCS.glob("*.js")):
            ctx.eval("new Function(" + json.dumps(path.read_text(encoding="utf-8")) + ")")

    def test_the_shared_scripts_load_without_a_shell(self):
        engine()

    def test_a_write_is_a_write(self):
        """The bug that shipped: write() called a helper that had been deleted."""
        ctx = engine()
        self.assertTrue(ctx.eval("Store.setMeta({probe: 1})"))
        self.assertEqual(ctx.eval("Store.meta().probe"), 1)
        self.assertEqual(ctx.eval("Store.setSnapshot({spirits: [{code: 'B-1'}]}) && Store.spirits().length"), 1)

    def test_a_refused_write_says_why(self):
        ctx = engine()
        ctx.eval("globalThis.__failWrites = true")
        self.assertFalse(ctx.eval("Store.setMeta({probe: 2})"))
        self.assertIn("QuotaExceededError", ctx.eval("Store.lastWriteError()"))

    def test_a_key_is_parsed_once_not_once_per_row(self):
        """The collection list asks for the snapshot several times per row. Each ask used to be a
        full parse of two hundred spirits; now the first one is and the rest are free."""
        ctx = engine()
        ctx.eval("Store.setSnapshot({spirits: Array.from({length: 200}, (_, i) => ({code: 'B-' + i}))})")
        ctx.eval("__reads = 0")
        ctx.eval("for (let i = 0; i < 1000; i++) Store.spirits()")
        self.assertEqual(ctx.eval("__reads"), 1)
        self.assertEqual(ctx.eval("Store.spirits().length"), 200)

    def test_a_write_is_seen_by_the_next_read(self):
        ctx = engine()
        ctx.eval("Store.setMeta({a: 1})")
        self.assertEqual(ctx.eval("Store.meta().a"), 1)
        ctx.eval("Store.setMeta({a: 2})")
        self.assertEqual(ctx.eval("Store.meta().a"), 2)
        # The queue goes through the same memo: enqueue, drop, count all agree.
        ctx.eval("Store.enqueue({path: 'x/1.json', kind: 'tasting', body: {}})")
        self.assertEqual(ctx.eval("Store.queueCount()"), 1)
        ctx.eval("Store.drop('x/1.json')")
        self.assertEqual(ctx.eval("Store.queueCount()"), 0)

    def test_a_refused_write_leaves_the_old_value_readable(self):
        """Storage still holds what it held; the memo must not pretend the write happened."""
        ctx = engine()
        ctx.eval("Store.setMeta({a: 1}); Store.meta()")
        ctx.eval("globalThis.__failWrites = true")
        self.assertFalse(ctx.eval("Store.setMeta({a: 2})"))
        self.assertEqual(ctx.eval("Store.meta().a"), 1)

    def test_a_restore_from_the_shell_is_seen(self):
        """restoreFromNative writes storage directly, behind the memo. A store already read as
        empty must come back full, or a reinstalled app would show nothing until a sync."""
        ctx = engine()
        self.assertEqual(ctx.eval("Store.spirits().length"), 0)        # memoised as absent
        ctx.eval("""
          Native.caps.add('backup');
          Native.request = async () => ({ type: 'backup.result',
            text: JSON.stringify({ 'wtb.snapshot': JSON.stringify({ spirits: [{ code: 'B-1' }] }) }) });
        """)
        self.assertTrue(ctx.eval("Store.restoreFromNative()").get(timeout=2000))
        self.assertEqual(ctx.eval("Store.spirits().length"), 1)

    def test_without_a_shell_the_bridge_is_inert(self):
        ctx = engine()
        self.assertFalse(ctx.eval("Native.available"))
        self.assertEqual(ctx.eval("Native.caps.size"), 0)
        self.assertEqual(ctx.eval("typeof window.nativeEvent"), "function")
        self.assertTrue(ctx.eval("Graph.configured()"))



# Synthetic, like every fixture in this repo: the repo is public.
SNAP = {"spirits": [{"code": "B-1", "name": "One", "status": "Opened", "owned": True},
                    {"code": "B-2", "name": "Two", "status": "Unopened", "owned": True}]}
CAREERS = {"version": 2,
           "careers": {"B-1": {"score": 88.0, "medal": "Gold", "n": 2, "categories": {}}},
           "calibration": [],
           "sittings": {"B-1": [{"tasting_id": "T-x", "total": 88}, {"tasting_id": "T-y", "total": 88}]},
           "generated_at": "2026-11-08T12:00:00+00:00"}
LISTING = [{"name": "collection.json", "eTag": "c1", "size": 2048, "lastModifiedDateTime": "2026-11-08T12:00:00Z"},
           {"name": "careers.json", "eTag": "k1", "size": 512, "lastModifiedDateTime": "2026-11-08T12:00:00Z"},
           {"name": "rubric.json", "eTag": "r1", "size": 1024, "lastModifiedDateTime": "2026-11-08T12:00:00Z"}]


def signed_in(ctx, fixtures, listing=None):
    """Graph answers from fixtures; counters say how many downloads and uploads it was asked for."""
    ctx.eval("Graph.isSignedIn = function () { return true; }")
    ctx.eval("var __gets = 0, __puts = 0, __lists = 0; var __FIX = " + json.dumps(fixtures) + ";"
             "var __LISTING = " + json.dumps(listing) + ";"
             "Graph.getJSON = function (p) { __gets++; return Promise.resolve(p in __FIX ? __FIX[p] : null); };"
             "Graph.listFolder = function () { __lists++; return Promise.resolve(__LISTING); };"
             "Graph.putJSON = function () { __puts++; return Promise.resolve({}); };")


def settle(promise):
    return promise.get(timeout=2000)


@unittest.skipIf(MiniRacer is None, "pip install mini-racer to run the phone scripts for real")
class TestSync(unittest.TestCase):
    """docs/sync.js for real: fetches stubbed, storage real (the stub), clock real."""

    def setUp(self):
        self.ctx = engine()
        self.fixtures = {"snapshot/collection.json": SNAP, "snapshot/careers.json": CAREERS,
                         "snapshot/rubric.json": json.loads((DOCS / "rubric.json").read_text(encoding="utf-8"))}
        signed_in(self.ctx, self.fixtures)

    def test_the_tap_is_answered_before_any_byte_arrives(self):
        self.ctx.eval("Graph.getJSON = function () { return new Promise(function () {}); }")
        self.ctx.eval("Sync.start({reason: 'manual'})")
        self.assertTrue(self.ctx.eval("Sync.state.running"))
        self.assertEqual(self.ctx.eval("Sync.state.steps.length"), 4)
        self.assertTrue(self.ctx.eval("Sync.state.steps.every(s => s.state === 'pending' || s.state === 'running')"))

    def test_each_download_reports_what_landed(self):
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        details = self.ctx.eval("Sync.state.steps.map(s => s.state + ':' + s.detail)")
        self.assertEqual(list(details), ["done:2 spirits", "done:1 scored · 2 sittings",
                                         "done:10 categories", "done:nothing waiting"])
        self.assertEqual(self.ctx.eval("Store.spirits().length"), 2)
        self.assertIsNotNone(self.ctx.eval("Store.meta().lastSync"))
        self.assertIsNone(self.ctx.eval("Store.meta().lastError"))
        self.assertFalse(self.ctx.eval("Sync.state.running"))

    def test_the_summary_says_what_changed(self):
        self.ctx.eval("Store.setSnapshot({spirits: [{code: 'B-1', status: 'Opened'}, {code: 'B-9'}]})")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        s = self.ctx.eval("Sync.state.summary")
        self.assertEqual((list(s["added"]), list(s["removed"]), list(s["newScores"]), s["uploaded"]),
                         (["B-2"], ["B-9"], ["B-1"], 0))
        self.assertEqual(list(self.ctx.eval("Store.meta().lastSummary.added")), ["B-2"])

    def test_one_failed_download_does_not_lose_the_others(self):
        self.ctx.eval("Graph.getJSON = function (p) { return p.endsWith('careers.json')"
                      " ? Promise.reject(new Error('503 reading ' + p)) : Promise.resolve(__FIX[p]); }")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertEqual(self.ctx.eval("Store.spirits().length"), 2)
        self.assertEqual(self.ctx.eval("Sync.state.steps[1].state"), "failed")
        self.assertIn("503", self.ctx.eval("Store.meta().lastError"))
        self.assertIsNone(self.ctx.eval("Store.meta().lastSync || null"), "not everything came down")

    def test_a_quick_app_switch_does_not_refetch(self):
        self.ctx.eval("Store.setMeta({lastSync: new Date().toISOString()})")
        r = settle(self.ctx.eval("Sync.start({reason: 'foreground'})"))
        self.assertEqual(r["skipped"], "nothing to do")
        self.assertEqual(self.ctx.eval("__gets"), 0)
        self.ctx.eval("Store.setMeta({lastSync: new Date(Date.now() - Sync.MIN_INTERVAL - 1).toISOString()})")
        settle(self.ctx.eval("Sync.start({reason: 'foreground'})"))
        self.assertEqual(self.ctx.eval("__gets"), 3)

    def test_the_button_and_a_cold_start_always_pull(self):
        for reason in ("manual", "boot"):
            self.ctx.eval("Store.setMeta({lastSync: new Date().toISOString()}); __gets = 0")
            settle(self.ctx.eval(f"Sync.start({{reason: '{reason}'}})"))
            self.assertEqual(self.ctx.eval("__gets"), 3, reason)

    def test_an_unchanged_file_is_not_downloaded_again(self):
        signed_in(self.ctx, self.fixtures, LISTING)
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertEqual(self.ctx.eval("__gets"), 3)
        self.ctx.eval("__gets = 0")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertEqual(self.ctx.eval("__gets"), 0, "every eTag matched")
        self.assertTrue(self.ctx.eval("Sync.state.steps[0].detail.startsWith('unchanged')"))
        # The PC publishes new scores: only that file comes down.
        self.ctx.eval("__LISTING[1].eTag = 'k2'")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertEqual(self.ctx.eval("__gets"), 1)

    def test_an_etag_is_trusted_only_when_the_store_has_the_file(self):
        """Storage was wiped (or refused the write) but meta kept the eTag: the file is owed."""
        signed_in(self.ctx, self.fixtures, LISTING)
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.ctx.eval("__store['wtb.snapshot'] = undefined; delete __store['wtb.snapshot']; __gets = 0")
        self.ctx.eval("Store.setMeta({probe: 1})")          # any write; the memo of the snapshot is stale too
        self.ctx.eval("localStorage.removeItem('wtb.snapshot')")
        fresh = MiniRacer()                                   # a new page, same storage and meta
        fresh.eval(STUBS)
        fresh.eval("__store = " + self.ctx.eval("JSON.stringify(__store)"))
        for name in SHARED:
            fresh.eval((DOCS / name).read_text(encoding="utf-8"))
        signed_in(fresh, self.fixtures, LISTING)
        settle(fresh.eval("Sync.start({reason: 'manual'})"))
        self.assertEqual(fresh.eval("__gets"), 1, "the collection, whose eTag matched but which was gone")
        self.assertEqual(fresh.eval("Store.spirits().length"), 2)

    def test_a_failed_listing_just_downloads_everything(self):
        self.ctx.eval("Graph.listFolder = function () { return Promise.reject(new Error('503 listing')); }")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertEqual(self.ctx.eval("__gets"), 3)
        self.assertIsNotNone(self.ctx.eval("Store.meta().lastSync"))

    def test_two_starts_share_one_run(self):
        self.assertTrue(self.ctx.eval(
            "var a = Sync.start({reason: 'manual'}); var b = Sync.start({reason: 'foreground'}); a === b"))
        settle(self.ctx.eval("a"))
        self.assertEqual(self.ctx.eval("__gets"), 3)

    def test_a_card_submitted_mid_run_still_goes_up(self):
        """The run's upload step has already passed the point where the card was queued; one more
        pass follows the run rather than leaving the card for the next trigger — and the first run
        does not call the late card refused."""
        self.ctx.eval("Store.setMeta({lastSync: new Date().toISOString()});"
                      "for (const n of ['a', 'b']) Store.enqueue({path: 'tastings/T-' + n + '.json', kind: 'tasting', body: {}, created_at: 'x'});"
                      "var second = null, outcomes = [];"
                      "Sync.onChange(s => { if (!s.running && s.finishedAt) outcomes.push(s.steps[0].state + ':' + s.steps[0].detail); });"
                      "Graph.putJSON = function (p) { __puts++; if (p.endsWith('T-b.json')) {"
                      "  Store.enqueue({path: 'tastings/T-late.json', kind: 'tasting', body: {}, created_at: 'x'});"
                      "  second = Sync.start({reason: 'upload'}); } return Promise.resolve({}); };"
                      "var first = Sync.start({reason: 'upload'});")
        settle(self.ctx.eval("first"))
        self.assertFalse(self.ctx.eval("first === second"))
        settle(self.ctx.eval("second"))
        self.assertEqual(self.ctx.eval("__puts"), 3)
        self.assertEqual(self.ctx.eval("Store.queueCount()"), 0)
        self.assertEqual(list(self.ctx.eval("outcomes")), ["done:2 uploaded", "done:1 uploaded"])

    def test_a_broken_careers_file_still_ends_the_run(self):
        """A null entry in careers.json used to throw inside the summary, after every step was
        done — leaving running true for ever and the Refresh button dead."""
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.ctx.eval("__FIX['snapshot/careers.json'] = {version: 2, careers: {'B-1': null},"
                      " calibration: [], sittings: {}, generated_at: 'y'}")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertFalse(self.ctx.eval("Sync.state.running"))
        self.assertEqual(self.ctx.eval("Sync.state.steps.map(s => s.state).join(',')"), "done,done,done,done")

    def test_a_file_not_yet_published_is_not_a_failure(self):
        """A PC on the old version publishes no careers.json: the other two come down, the step
        says so, and lastSync still moves — there was nothing more to fetch."""
        signed_in(self.ctx, self.fixtures, [LISTING[0], LISTING[2]])
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertEqual(self.ctx.eval("__gets"), 2)
        self.assertEqual(self.ctx.eval("Sync.state.steps[1].detail"), "not published yet")
        self.assertIsNotNone(self.ctx.eval("Store.meta().lastSync"))

    def test_a_bottle_finished_on_the_pc_counts_as_changed(self):
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.ctx.eval("__FIX['snapshot/collection.json'].spirits[0].status = 'Finished';"
                      "__FIX['snapshot/collection.json'].spirits[0].owned = false")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        s = self.ctx.eval("Sync.state.summary")
        self.assertEqual((list(s["changed"]), list(s["added"]), list(s["newScores"])), (["B-1"], [], []))

    def test_an_uploaded_card_is_marked_sent(self):
        self.ctx.eval("Store.setMeta({lastSync: new Date().toISOString()});"
                      "Store.submitScorecard({spirit_id: 'B-1', scores: {}})")
        self.assertFalse(self.ctx.eval("Store.cards()[0]._sent"))
        settle(self.ctx.eval("Sync.start({reason: 'upload'})"))
        self.assertTrue(self.ctx.eval("Store.cards()[0]._sent"))
        self.assertEqual(self.ctx.eval("Store.queueCount()"), 0)

    def test_a_listener_that_throws_does_not_stop_the_run(self):
        self.ctx.eval("Sync.onChange(() => { throw new Error('bad listener'); })")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertFalse(self.ctx.eval("Sync.state.running"))
        self.assertEqual(self.ctx.eval("Store.spirits().length"), 2)

    def test_offline_or_signed_out_is_a_skip_not_an_error(self):
        self.ctx.eval("navigator.onLine = false")
        self.assertEqual(settle(self.ctx.eval("Sync.start({reason: 'boot'})"))["skipped"], "offline")
        self.ctx.eval("navigator.onLine = true; Graph.isSignedIn = function () { return false; }")
        self.assertEqual(settle(self.ctx.eval("Sync.start({reason: 'boot'})"))["skipped"], "signed out")
        self.assertEqual(self.ctx.eval("Sync.state.steps.length"), 0)

    def test_a_refused_write_is_a_failed_step_not_a_fresh_sync(self):
        self.ctx.eval("globalThis.__failWrites = true")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        self.assertEqual(self.ctx.eval("Sync.state.steps[0].state"), "failed")
        self.assertIn("storage refused", self.ctx.eval("Sync.state.steps[0].detail"))
        self.assertIn("QuotaExceededError", self.ctx.eval("Sync.state.error"))

    def test_uploads_report_progress_and_one_refusal_does_not_block_the_rest(self):
        self.ctx.eval("Store.setMeta({lastSync: new Date().toISOString()});"
                      "for (const n of ['a', 'b', 'c']) Store.enqueue({path: 'tastings/T-' + n + '.json', kind: 'tasting', body: {}, created_at: 'x'});"
                      "var seen = []; Sync.onChange(s => { const u = s.steps.find(x => x.name === 'upload'); if (u) seen.push(u.detail); });"
                      "Graph.putJSON = function (p) { __puts++; return p.endsWith('T-b.json')"
                      " ? Promise.reject(new Error('400 writing ' + p + ': malformed')) : Promise.resolve({}); }")
        settle(self.ctx.eval("Sync.start({reason: 'upload'})"))
        seen = list(self.ctx.eval("seen"))
        self.assertIn("uploading 1 of 3 · tasting T-a.json", seen)
        self.assertIn("uploading 3 of 3 · tasting T-c.json", seen)
        self.assertEqual(list(self.ctx.eval("Store.queue().map(i => i.path)")), ["tastings/T-b.json"])
        self.assertIn("400", self.ctx.eval("Store.queue()[0].error"))
        self.assertEqual(self.ctx.eval("Store.queue()[0].attempts"), 1)
        self.assertEqual(self.ctx.eval("Sync.state.steps[0].state"), "failed")
        self.assertIn("2 sent · 1 refused", self.ctx.eval("Sync.state.steps[0].detail"))

    def test_an_error_every_card_would_meet_stops_the_queue_at_once(self):
        for msg in ("sign-in required", "could not get a token", "401 writing tastings/x.json",
                    "429 writing tastings/x.json", "503 writing tastings/x.json",
                    "Load failed — could not reach OneDrive for tastings/x.json"):
            ctx = engine()
            signed_in(ctx, self.fixtures)
            ctx.eval("Store.setMeta({lastSync: new Date().toISOString()});"
                     "for (const n of ['a', 'b']) Store.enqueue({path: 'tastings/T-' + n + '.json', kind: 'tasting', body: {}, created_at: 'x'});"
                     "Graph.putJSON = function () { __puts++; return Promise.reject(new Error(" + json.dumps(msg) + ")); }")
            settle(ctx.eval("Sync.start({reason: 'upload'})"))
            self.assertEqual(ctx.eval("__puts"), 1, f"{msg}: no point walking the rest into the same wall")
            self.assertEqual(ctx.eval("Store.queueCount()"), 2, msg)
            self.assertEqual(ctx.eval("Sync.state.steps[0].state"), "failed", msg)

    def test_progress_runs_from_nothing_to_everything(self):
        self.ctx.eval("var ps = []; Sync.onChange(() => ps.push(Sync.progress()));")
        settle(self.ctx.eval("Sync.start({reason: 'manual'})"))
        ps = list(self.ctx.eval("ps"))
        self.assertEqual(ps[0], 0)
        self.assertEqual(ps[-1], 1)
        self.assertEqual(ps, sorted(ps), "progress never goes backwards")



@unittest.skipIf(MiniRacer is None, "pip install mini-racer to run the phone scripts for real")
class TestPhoneOnly(unittest.TestCase):
    """The book can live on the phone alone — for someone with no PC and no OneDrive — and the
    OneDrive arrangement must go on working exactly as it did for the phone that already uses it."""

    def setUp(self):
        self.ctx = engine()

    def test_a_phone_that_has_synced_is_on_onedrive_without_being_asked(self):
        self.assertIsNone(self.ctx.eval("Store.mode()"), "a new phone has not chosen")
        self.ctx.eval("Store.setMeta({lastSync: new Date().toISOString()})")
        self.assertEqual(self.ctx.eval("Store.mode()"), "onedrive")
        self.assertFalse(self.ctx.eval("Store.isLocal()"))
        fresh = engine()
        fresh.eval("Store.setSnapshot({spirits: [{code: 'B-1'}]})")
        self.assertEqual(fresh.eval("Store.mode()"), "onedrive", "a collection came from somewhere")

    def test_the_choice_is_remembered(self):
        self.ctx.eval("Store.setMode('local')")
        self.assertEqual(self.ctx.eval("Store.mode()"), "local")
        self.ctx.eval("Store.setMode('onedrive')")
        self.assertEqual(self.ctx.eval("Store.mode()"), "onedrive")

    def test_phone_only_queues_nothing_and_starts_no_sync(self):
        signed_in(self.ctx, {})
        self.ctx.eval("Store.setMode('local')")
        self.ctx.eval("Store.submitScorecard({spirit_id: 'L-1', scores: {}});"
                      "Store.submitEncounter({name: 'A Pour'});")
        self.assertEqual(self.ctx.eval("Store.queueCount()"), 0)
        self.assertEqual(self.ctx.eval("Store.cards().length"), 1, "the card itself is kept")
        self.assertTrue(self.ctx.eval("Store.cards()[0]._local"))
        self.assertEqual(self.ctx.eval("Store.encounters().length"), 1)
        for reason in ("manual", "boot", "upload", "foreground"):
            r = settle(self.ctx.eval(f"Sync.start({{reason: '{reason}'}})"))
            self.assertEqual(r["skipped"], "local", reason)
        self.assertEqual(self.ctx.eval("__gets + __puts + __lists"), 0)

    def test_onedrive_still_queues_exactly_as_before(self):
        self.ctx.eval("Store.setMode('onedrive'); Store.submitScorecard({spirit_id: 'B-1', scores: {}})")
        self.assertEqual(self.ctx.eval("Store.queueCount()"), 1)
        self.assertFalse(self.ctx.eval("'_local' in Store.cards()[0]"))

    def test_a_bottle_added_here_gets_a_code_the_pc_never_issues(self):
        self.ctx.eval("Store.setMode('local')")
        a = self.ctx.eval("Store.addLocalSpirit({distillery: 'A Distillery', name: 'First', proof: '101', age: '12', paid: '80'})")
        self.assertEqual((a["code"], a["display_name"], a["proof"], a["age"], a["paid"]),
                         ("L-1", "A Distillery First", 101, 12, 80))
        self.assertTrue(a["owned"])
        self.assertEqual((a["status"], a["_sheet"]), ("Opened", "Bottle"))
        self.assertEqual(self.ctx.eval("Store.addLocalSpirit({name: 'Second', age: 'NAS'}).code"), "L-2")
        self.assertEqual(self.ctx.eval("Store.spirits()[1].age_label"), "NAS")
        self.assertIsNone(self.ctx.eval("Store.spirits()[1].age"))

    def test_a_removed_bottles_code_is_never_reissued(self):
        """The same rule the PC keeps, for the same reason: reviews name a bottle by its code."""
        self.ctx.eval("Store.addLocalSpirit({name: 'One'}); Store.addLocalSpirit({name: 'Two'})")
        self.assertTrue(self.ctx.eval("Store.removeLocalSpirit('L-2')"))
        self.assertEqual(self.ctx.eval("Store.addLocalSpirit({name: 'Three'}).code"), "L-3")
        self.assertEqual(list(self.ctx.eval("Store.spirits().map(s => s.code)")), ["L-1", "L-3"])

    def test_finishing_a_bottle_takes_it_out_of_stock_and_editing_keeps_the_rest(self):
        self.ctx.eval("Store.addLocalSpirit({distillery: 'D', name: 'One', proof: '90'})")
        done = self.ctx.eval("Store.updateLocalSpirit('L-1', {status: 'Finished'})")
        self.assertEqual((done["status"], done["owned"], done["proof"], done["name"]),
                         ("Finished", False, 90, "One"))
        back = self.ctx.eval("Store.updateLocalSpirit('L-1', {status: 'Opened', name: 'Renamed'})")
        self.assertEqual((back["owned"], back["display_name"]), (True, "D Renamed"))

    def test_only_a_bottle_added_here_can_be_changed_here(self):
        self.ctx.eval("Store.setSnapshot({spirits: [{code: 'B-1', name: 'From the PC'}]})")
        self.assertIsNone(self.ctx.eval("Store.updateLocalSpirit('B-1', {name: 'Mine now'})"))
        self.assertFalse(self.ctx.eval("Store.removeLocalSpirit('B-1')"))
        self.assertEqual(self.ctx.eval("Store.spirits()[0].name"), "From the PC")

    def test_a_sync_never_sweeps_away_a_bottle_added_here(self):
        self.ctx.eval("Store.addLocalSpirit({name: 'Mine'})")
        self.ctx.eval("Store.setSnapshot({spirits: [{code: 'B-1'}, {code: 'B-2'}]})")
        self.assertEqual(list(self.ctx.eval("Store.spirits().map(s => s.code)")), ["B-1", "B-2", "L-1"])
        self.ctx.eval("Store.setSnapshot({spirits: [{code: 'B-1'}]})")
        self.assertEqual(list(self.ctx.eval("Store.spirits().map(s => s.code)")), ["B-1", "L-1"],
                         "and it is not duplicated by the next one")

    def test_a_backup_goes_out_and_comes_back(self):
        self.ctx.eval("Store.setMode('local'); Store.addLocalSpirit({name: 'Kept'});"
                      "Store.submitScorecard({spirit_id: 'L-1', scores: {}});"
                      "var backup = Store.exportAll();")
        other = engine()
        other.eval("Store.addLocalSpirit({name: 'Overwritten'}); Store.spirits()")     # memoised
        self.assertIsNone(other.eval("Store.importAll(" + json.dumps(self.ctx.eval("backup")) + ")"))
        self.assertEqual(other.eval("Store.spirits()[0].name"), "Kept", "read through the memo")
        self.assertEqual(other.eval("Store.cards().length"), 1)
        self.assertEqual(other.eval("Store.mode()"), "local")

    def test_a_wrong_file_changes_nothing(self):
        self.ctx.eval("Store.addLocalSpirit({name: 'Still here'})")
        for bad in ("not json at all", "[1, 2]", '{"something": "else"}',
                    '{"wtb.snapshot": "{broken"}'):
            self.assertIsInstance(self.ctx.eval("Store.importAll(" + json.dumps(bad) + ")"), str, bad)
            self.assertEqual(self.ctx.eval("Store.spirits()[0].name"), "Still here", bad)


@unittest.skipIf(MiniRacer is None, "pip install mini-racer to run the phone scripts for real")
class TestTypesAndAges(unittest.TestCase):
    """Type is chosen from the workbook's own list, with room for what the list does not hold;
    and an age may be a word — NAS, N/A — without becoming a number that sorts."""

    def setUp(self):
        self.ctx = engine()

    def types(self):
        return json.loads(self.ctx.eval("JSON.stringify(Store.types())"))

    def js(self, expr):
        return json.loads(self.ctx.eval(f"JSON.stringify({expr})"))

    def test_a_phone_with_no_pc_has_the_list_built_in(self):
        got = self.types()
        self.assertEqual(got[:2], ["Bourbon", "Rye"])
        self.assertEqual(len(got), 20)
        for t in ("Single Malt", "Gin", "Vodka", "Moonshine"):
            self.assertIn(t, got)

    def test_the_published_list_is_the_one_offered(self):
        """What the PC read from the workbook's drop-down replaces the built-in list, and a type
        already on a bottle is offered even if the drop-down has since lost it."""
        self.ctx.eval("Store.setSnapshot({types: ['Alpha', 'Beta'], spirits: ["
                      "{code: 'B-1', type: 'Beta'}, {code: 'B-2', type: 'gamma'}, {code: 'B-3'}]})")
        self.assertEqual(self.types(), ["Alpha", "Beta", "gamma"])

    def test_a_type_entered_once_is_offered_from_then_on(self):
        self.ctx.eval("Store.setMode('local');"
                      "Store.addLocalSpirit({name: 'A Red', type: 'Red Wine'});"
                      "Store.submitEncounter({name: 'A Glass', type: 'Champagne'});")
        got = self.types()
        self.assertEqual(got[20:], ["Champagne", "Red Wine"], "after the list, in order")
        self.assertEqual(len(got), len(set(t.lower() for t in got)))

    def test_a_second_spelling_joins_the_first(self):
        self.ctx.eval("Store.setMode('local'); Store.addLocalSpirit({name: 'A Red', type: 'Red Wine'});")
        self.assertEqual(self.ctx.eval("Store.canonicalType('  red wine ')"), "Red Wine")
        self.assertEqual(self.ctx.eval("Store.canonicalType('BOURBON')"), "Bourbon")
        self.assertEqual(self.ctx.eval("Store.canonicalType('Sake')"), "Sake", "new is allowed")
        self.assertIsNone(self.ctx.eval("Store.canonicalType('   ')"))

    def test_a_bar_pour_keeps_a_worded_age_apart_from_the_years(self):
        """`s.age ? age + " yr" : s.age_label` is how every list prints it: a pour whose age was
        the string "NAS" printed "NAS yr" and sorted among the numbers."""
        for given, want in (("'NAS'", [None, "NAS"]), ("'N/A'", [None, "N/A"]), ("12", [12, None]),
                            ("'12'", [12, None]), ("null", [None, None]), ("''", [None, None])):
            s = self.js(f"Store.submitEncounter({{name: 'A Pour', age: {given}}}).spirit")
            self.assertEqual([s["age"], s["age_label"]], want, given)
        rec = self.js("Store.submitEncounter({name: 'A Pour', age: 'NAS'}).rec")
        self.assertEqual(rec["age"], "NAS", "the file keeps one field, as store.py writes it")
        self.assertNotIn("age_label", rec)

    def test_a_bottle_added_here_may_have_no_age_to_state(self):
        self.ctx.eval("Store.setMode('local')")
        gin = self.js("Store.addLocalSpirit({name: 'A Gin', type: 'Gin', age: 'N/A'})")
        self.assertEqual([gin["age"], gin["age_label"]], [None, "N/A"])
        old = self.js("Store.addLocalSpirit({name: 'An Old One', age: '18'})")
        self.assertEqual([old["age"], old["age_label"]], [18, None])
        back = self.js(f"Store.updateLocalSpirit('{gin['code']}', {{age: 'NAS'}})")
        self.assertEqual([back["age"], back["age_label"]], [None, "NAS"])


BOTTLE_COLUMNS = ["Bottle Code", "Distillery", "Name", "Type", "Region", "Notes", "Release Year", "Age",
                  "Rarity", "Proof", "ABV", "Entry Proof", "Barrel Proof", "Proof Diff", "Conc. Ratio",
                  "Size (ml)", "Status", "Paid"]
MINIATURE_COLUMNS = ["Bottle Code", "Distillery", "Name", "Notes", "Type", "Region", "Age", "Proof", "ABV",
                     "Size (ml)", "Status"]
SAMPLE_COLUMNS = ["Bottle Code", "Distillery", "Name", "Special note", "Comment", "Type", "Region",
                  "Release Year", "Age", "Rarity", "Proof", "ABV", "Entry Proof", "Barrel Proof",
                  "Proof Diff", "Conc. Ratio", "Size(oz)", "Bottle Type", "Quantity", "Status", "Picture"]
AWKWARD = 'Twelve & <Co> "Cask" café ☕ 🥃'
PHONE_BOTTLES = [
    {"code": "L-1", "name": "House Red", "distillery": None, "type": "Red Wine", "region": "France",
     "age": None, "age_label": "N/A", "proof": 27, "size_ml": 750, "status": "Opened", "paid": 18.5,
     "_sheet": "Bottle", "_local": True},
    {"code": "L-2", "name": AWKWARD, "distillery": "A Distillery", "type": "Single Malt",
     "region": "Scotland", "age": 12, "age_label": None, "proof": 92, "size_ml": 700,
     "status": "Unopened", "paid": 60, "_sheet": "Bottle", "_local": True},
    {"code": "L-3", "name": "Gone", "distillery": "A Distillery", "type": "Rye", "age": None,
     "age_label": "NAS", "proof": 100, "status": "Finished", "paid": 40, "_sheet": "Bottle", "_local": True},
]
TYPES = ["Bourbon", "Rye", "Single Malt", "Gin", "Red Wine"]


@unittest.skipIf(MiniRacer is None, "pip install mini-racer to run the phone scripts for real")
class TestExcelExport(unittest.TestCase):
    """The phone writes its collection out as an .xlsx with no library: its own zip, its own
    deflate, its own XML. Every byte of that is checked here by something that did not write it —
    zlib, zipfile, openpyxl — and the shape of it by the PC's own loader, which is the only
    honest meaning of "the same structure as the collection workbook"."""

    @classmethod
    def setUpClass(cls):
        cls.ctx = engine()
        cls.ctx.eval(
            "var hex = function (b) { var s = ''; for (var i = 0; i < b.length; i++)"
            " s += (b[i] < 16 ? '0' : '') + b[i].toString(16); return s; };"
            "var unhex = function (h) { var b = new Uint8Array(h.length / 2);"
            " for (var i = 0; i < b.length; i++) b[i] = parseInt(h.substr(i * 2, 2), 16); return b; };")
        cls.tmp = tempfile.TemporaryDirectory(prefix="xlsx-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.n = 0

    def book(self, spirits=PHONE_BOTTLES, types=TYPES):
        """The workbook the phone would write, as a file on disk."""
        data = bytes.fromhex(self.ctx.eval(
            f"hex(Xlsx.collectionBook({json.dumps(spirits)}, {{types: {json.dumps(types)}}}))"))
        type(self).n += 1
        path = Path(self.tmp.name) / f"book-{self.n}" / "Tasting Book Collection.xlsx"
        path.parent.mkdir()
        path.write_bytes(data)
        return path

    def load(self, path):
        import collection
        return collection.Collection(path).load()

    # -- the bytes --------------------------------------------------------
    def test_the_compression_is_what_zlib_reads(self):
        import random
        import zlib
        rnd = random.Random(7)
        rows = b"".join(b'<row r="%d"><c><f>IF(K%d="","",K%d/2)</f></c></row>' % (r, r, r)
                        for r in range(9, 900))
        cases = [b"", b"a", b"abc", b"abcabcabc" * 400, b"x" * 100000, rows,
                 bytes(rnd.randrange(256) for _ in range(70000)),       # nothing to find
                 bytes(rnd.randrange(4) for _ in range(120000)),        # matches everywhere
                 bytes(rnd.randrange(256) for _ in range(40000)) * 3]   # a match 40,000 back: too far
        size = {}
        for c in cases:
            out = bytes.fromhex(self.ctx.eval(f"hex(Xlsx.deflate(unhex('{c.hex()}')))"))
            self.assertEqual(zlib.decompress(out, -15), c, f"{len(c)} bytes did not survive")
            size[c] = len(out)
        self.assertLess(size[rows], len(rows) / 5, "rows of one formula are what a sheet is made of")

    def test_the_text_is_utf8_whatever_is_in_it(self):
        for s in ("", "plain", AWKWARD, "\ud83e\udd43 pair", "lone \ud83e surrogate"):
            got = bytes.fromhex(self.ctx.eval(f"hex(Xlsx.utf8({json.dumps(s)}))"))
            want = s.encode("utf-16", "surrogatepass").decode("utf-16", "replace").encode("utf-8")
            self.assertEqual(got, want, s)

    def test_the_file_is_a_sound_zip(self):
        import zipfile
        with zipfile.ZipFile(self.book()) as z:
            self.assertIsNone(z.testzip(), "a CRC or a length is wrong")
            names = z.namelist()
            self.assertEqual(names[0], "[Content_Types].xml")
            for n in (1, 2, 3):
                self.assertIn(f"xl/worksheets/sheet{n}.xml", names)
                self.assertIn(f"xl/tables/table{n}.xml", names)
            self.assertTrue(all(i.compress_type in (0, 8) for i in z.infolist()))
            self.assertLess(sum(i.compress_size for i in z.infolist()),
                            sum(i.file_size for i in z.infolist()) / 4, "it is meant to be sent")

    # -- the shape --------------------------------------------------------
    def test_the_pc_reads_it_as_it_reads_the_master(self):
        coll = self.load(self.book())
        shape = {s: (t.name, t.ref, t.columns, sorted(t.calc_columns)) for s, t in coll.tables.items()}
        self.assertEqual(shape, {
            "Bottle": ("Collection", "B8:S308", BOTTLE_COLUMNS, ["Conc. Ratio", "Proof Diff"]),
            "Miniature": ("Collection4", "B8:L308", MINIATURE_COLUMNS, ["ABV"]),
            "Sample": ("SampleList", "B8:V603", SAMPLE_COLUMNS, ["ABV", "Conc. Ratio", "Proof Diff"]),
        })
        self.assertEqual(list(coll.tables), ["Bottle", "Miniature", "Sample"], "sheet order")
        formulas = coll.formula_columns("Bottle")
        self.assertEqual({k: (v["declared"], v["uses_a1_refs"]) for k, v in formulas.items()},
                         {"ABV": (False, True), "Proof Diff": (True, False), "Conc. Ratio": (True, False)},
                         "ABV is a filled-down formula on this sheet, the other two the table's own")
        self.assertEqual(coll.first_empty_row("Bottle"), 12, "the rows below are there to be filled")
        self.assertEqual(coll.first_empty_row("Sample"), 9)
        self.assertEqual(coll.options("Type"), TYPES, "the drop-down is the phone's own list")
        self.assertFalse([i for i in coll.issues if "malformed" in i.message],
                         "a code the workbook's own rules reject")

    def test_the_bottles_come_back_as_they_went_in(self):
        rows = {r["code"]: r for r in self.load(self.book()).rows["Bottle"]}
        self.assertEqual(sorted(rows), ["B-1", "B-2", "B-3"], "L-n on the phone is B-n in the workbook")
        red, malt, gone = rows["B-1"], rows["B-2"], rows["B-3"]
        self.assertEqual((red["name"], red["distillery"], red["type"], red["region"]),
                         ("House Red", None, "Red Wine", "France"))
        self.assertEqual((red["age"], red.get("age_label"), red["proof"], red["abv"]), (None, "N/A", 27, 13.5))
        self.assertEqual((red["size_ml"], red["status"], red["paid"], red["owned"]), (750, "Opened", 18.5, True))
        self.assertEqual(malt["name"], AWKWARD, "ampersands, brackets, quotes, accents and emoji")
        self.assertEqual((malt["age"], malt.get("age_label"), malt["abv"]), (12, None, 46))
        self.assertEqual((gone.get("age_label"), gone["status"], gone["owned"]), ("NAS", "Finished", False))
        self.assertEqual([r["_row"] for r in (red, malt, gone)], [9, 10, 11])

    def test_what_a_reader_that_cannot_calculate_sees(self):
        """Quick Look on a phone and collection.py both read the value stored beside a formula.
        A formula written without one shows as a blank until Excel itself opens the file."""
        import openpyxl
        path = self.book()
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb["Bottle"]
        self.assertEqual([ws["B8"].value, ws["S8"].value], ["Bottle Code", "Paid"])
        self.assertEqual([ws["A9"].value, ws["A10"].value], [None, 2], "numbered by the distillery column")
        self.assertEqual([ws["L9"].value, ws["L10"].value, ws["O10"].value], [13.5, 46, "N/A"])
        self.assertEqual([ws["B2"].value, ws["T2"].value, ws["T5"].value, ws["V5"].value, ws["X5"].value],
                         ["Bottle List", "Total Bottles", 2, 1, 78.5], "the finished one is not counted")
        self.assertEqual(ws.freeze_panes, "A9")
        self.assertIn("B2:S7", {str(m) for m in ws.merged_cells.ranges})
        self.assertEqual(wb["Miniature"]["B2"].value, "Miniature List")
        wb.close()
        wb = openpyxl.load_workbook(path)                      # and the formulas themselves
        ws = wb["Bottle"]
        self.assertEqual(ws["L10"].value, '=IF(K10="","",K10/2)')
        self.assertEqual(ws["L200"].value, '=IF(K200="","",K200/2)', "allocated rows carry them too")
        self.assertTrue(ws["O10"].value.startswith('=IF(Collection[[#This Row],[Name]]="","",IF('))
        lists = {str(d.sqref): d.formula1 for d in ws.data_validations.dataValidation}
        self.assertEqual(lists["E9:E308"], '"Bourbon,Rye,Single Malt,Gin,Red Wine"')
        self.assertEqual(lists["R9:R308"], '"Opened,Unopened,Finished,Removed"')
        self.assertEqual(lists["N9:N308"], '"Yes,No"')
        self.assertIn("France", lists["F9:F308"])
        wb.close()

    def test_the_pc_can_add_a_bottle_to_it(self):
        """master_write.py fills a row that already exists inside the table and then verifies the
        file. If it can do that here, the phone's workbook is one the PC app can live with."""
        import master_write
        path = self.book()
        coll = self.load(path)
        result = master_write.append_row(
            path, "Bottle", {"Distillery": "Another Distillery", "Name": "From The Desk",
                             "Type": "Bourbon", "Proof": 110, "Status": "Unopened"},
            backup_dir=Path(self.tmp.name) / f"backups-{self.n}", coll=coll)
        self.assertEqual((result["code"], result["row"]), ("B-4", 12))
        rows = {r["code"]: r for r in self.load(path).rows["Bottle"]}
        self.assertEqual(sorted(rows), ["B-1", "B-2", "B-3", "B-4"])
        self.assertEqual((rows["B-4"]["name"], rows["B-4"]["proof"]), ("From The Desk", 110))

    # -- the edges --------------------------------------------------------
    def test_a_phone_bottle_never_takes_a_published_code(self):
        """A phone that once synced holds bottles the PC numbered. One added here goes past
        the highest of them, so L-1 can never come out as a B- number already in use."""
        spirits = [{"code": "B-7", "name": "Published", "distillery": "A Distillery", "_sheet": "Bottle"},
                   {"code": "M-2", "name": "A Mini", "distillery": "A Distillery", "proof": 80,
                    "_sheet": "Miniature"},
                   {"code": "L-1", "name": "Mine", "distillery": "A Distillery", "_local": True},
                   {"code": "E-20261115-1", "name": "Odd", "distillery": "A Distillery", "_sheet": "Encounter"}]
        coll = self.load(self.book(spirits))
        self.assertEqual({r["name"]: r["code"] for r in coll.rows["Bottle"]},
                         {"Published": "B-7", "Mine": "B-8", "Odd": "E-20261115-1"})
        mini = coll.rows["Miniature"][0]
        self.assertEqual((mini["code"], mini["abv"]), ("M-2", 40), "on the sheet its _sheet names")

    def test_a_long_list_of_types_stays_inside_excel_s_limit(self):
        """Excel takes 255 characters of drop-down and calls the file damaged at 256."""
        types = [f"Type Number {n:02d}" for n in range(40)] + ["Has, a comma", 'Has "quotes"']
        got = self.ctx.eval(f"Xlsx.listFormula({json.dumps(types)})")
        self.assertLessEqual(len(got), 255)
        self.assertTrue(got.startswith('"Type Number 00,Type Number 01,'))
        self.assertNotIn("comma", got)
        self.assertNotIn("quotes", got)
        self.assertIsNone(self.ctx.eval("Xlsx.listFormula([])"))
        self.assertEqual(self.load(self.book(types=types)).options("Type")[:2],
                         ["Type Number 00", "Type Number 01"])

    def test_a_collection_bigger_than_the_table_grows_it(self):
        spirits = [{"code": f"L-{n}", "name": f"Bottle {n}", "distillery": "A Distillery", "proof": 90 + n % 20,
                    "_local": True} for n in range(1, 321)]
        coll = self.load(self.book(spirits))
        self.assertEqual(coll.tables["Bottle"].ref, "B8:S353", "320 bottles and room for 25 more")
        self.assertEqual(len(coll.rows["Bottle"]), 320)
        self.assertEqual(coll.rows["Bottle"][-1]["code"], "B-320")
        self.assertEqual(coll.first_empty_row("Bottle"), 329)

    def test_an_empty_book_is_still_a_workbook(self):
        coll = self.load(self.book([]))
        self.assertEqual({s: len(r) for s, r in coll.rows.items()}, {"Bottle": 0, "Miniature": 0, "Sample": 0})
        self.assertEqual(coll.first_empty_row("Bottle"), 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)

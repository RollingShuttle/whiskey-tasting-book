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

SHARED = ("config.js", "native.js", "store.js", "graph.js", "sync.js")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

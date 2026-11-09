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
var localStorage = {
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(__store, k) ? __store[k] : null; },
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
var __timers = 0;
function setTimeout() { return ++__timers; }
function clearTimeout() {}
var fetch = function () { return Promise.reject(new Error("no network in tests")); };
var console = { log: function () {}, warn: function () {}, error: function () {} };
"""

SHARED = ("config.js", "native.js", "store.js", "graph.js")


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

    def test_without_a_shell_the_bridge_is_inert(self):
        ctx = engine()
        self.assertFalse(ctx.eval("Native.available"))
        self.assertEqual(ctx.eval("Native.caps.size"), 0)
        self.assertEqual(ctx.eval("typeof window.nativeEvent"), "function")
        self.assertTrue(ctx.eval("Graph.configured()"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

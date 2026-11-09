/* Whiskey Tasting Book — the bridge to the native iOS shell (guide/IOS-APP.md).

   The same docs/ bundle is served two ways: from GitHub Pages as a web app, and inside a
   WKWebView by the native app. In the shell, Microsoft sign-in and a backup of local storage are
   done in Swift — a web view has no safe place to keep a refresh token, and iOS may evict its
   storage — so the page posts requests to Swift and Swift answers by calling window.nativeEvent.

   Nothing here runs in the browser: without window.webkit.messageHandlers.native every call is a
   no-op, `available` is false and `caps` stays empty, so graph.js and store.js take their old
   paths unchanged. The handler is looked up on every call rather than once at load so that a
   fake one can be injected afterwards by a test. */
"use strict";

const Native = (() => {
  const caps = new Set();           // what the shell said it can do, from its hello
  const pending = new Map();        // request id -> how to settle its promise
  const listeners = new Map();      // event type -> handlers, for unsolicited events
  let seq = 0;
  let onHello = null;               // settles init() when the shell answers "ready"

  /* The protocol names two replies differently from the rest, which are the request type plus
     ".result"; they are listed rather than inferred so a third one day is a one-line change. */
  const REPLY = { "auth.signIn": "auth.result", "backup.load": "backup.result" };
  const replyType = (type) => REPLY[type] || `${type}.result`;

  function handler() {
    try {
      const w = window.webkit;
      return w && w.messageHandlers && w.messageHandlers.native ? w.messageHandlers.native : null;
    } catch { return null; }
  }

  function post(msg) {
    const h = handler();
    if (!h) return false;
    try { h.postMessage(msg); return true; } catch { return false; }
  }

  /** Native -> web. Swift calls window.nativeEvent({type, ...}). A reply carries the id of the
      request it answers; everything else is an event for on(). */
  function dispatch(detail) {
    if (!detail || typeof detail !== "object") return;
    if (detail.type === "hello") {
      // A hello after init() gave up would flip the page to the native paths mid-session, after
      // the backup restore and the service-worker decision were made the other way. Say so; stay.
      if (decided && !onHello) {
        console.warn("native: hello arrived after init() gave up; staying on the web paths");
        return;
      }
      caps.clear();
      for (const c of (Array.isArray(detail.caps) ? detail.caps : [])) caps.add(c);
      if (onHello) { onHello(); onHello = null; }
      return;
    }
    if (detail.id && pending.has(detail.id)) {
      const p = pending.get(detail.id);
      pending.delete(detail.id);
      clearTimeout(p.timer);
      // Matched on id, so a reply still lands when its type is wrong — but say so, because a
      // request the shell answers under another name is a bug on one side or the other.
      if (detail.type !== p.expect) console.warn(`native: expected ${p.expect}, got ${detail.type}`);
      p.resolve(detail);
      return;
    }
    for (const fn of listeners.get(detail.type) || []) fn(detail);
  }

  /* How long init() waits for the shell's hello. Only ever waited inside the shell — the browser
     returns before the timer exists — so it costs the web app nothing, and generous is right: a
     cold WKWebView answers in milliseconds, and a hello that arrives after init() gave up is
     refused (see dispatch), so a wait that is too short would strand the app on the web paths. */
  const HELLO_TIMEOUT = 3000;
  let decided = false;              // init() has settled, one way or the other

  /** Say "ready" and wait for the shell's hello. Resolves at once when there is no shell, and
      after HELLO_TIMEOUT when the handler exists but nothing answers, so a broken shell falls
      back to the web paths instead of a blank screen. */
  function init() {
    if (!handler()) { decided = true; return Promise.resolve(false); }
    return new Promise((resolve) => {
      const timer = setTimeout(() => { onHello = null; decided = true; resolve(false); }, HELLO_TIMEOUT);
      onHello = () => { clearTimeout(timer); decided = true; resolve(true); };
      if (!post({ type: "ready" })) { clearTimeout(timer); onHello = null; decided = true; resolve(false); }
    });
  }

  /* A request the shell never answers must not leave the page waiting for ever — one of them is
     awaited before the first paint. Zero means no limit, for a request that legitimately waits on
     a person, such as the sign-in sheet. */
  const REQUEST_TIMEOUT = 15000;

  /** Ask the shell something and get its reply. Rejects at once when there is no shell, so a
      caller that forgot to check caps fails loudly rather than waiting for ever. */
  function request(type, fields = {}, { timeout = REQUEST_TIMEOUT } = {}) {
    return new Promise((resolve, reject) => {
      // The counter alone would repeat after a reload; the clock keeps a late reply meant for
      // the previous page from settling a request made by this one.
      const id = `${Date.now().toString(36)}-${++seq}`;
      const timer = timeout ? setTimeout(() => {
        if (pending.delete(id)) reject(new Error(`native: no reply to ${type}`));
      }, timeout) : null;
      pending.set(id, { resolve, expect: replyType(type), timer });
      if (!post({ type, id, ...fields })) {
        clearTimeout(timer);
        pending.delete(id);
        reject(new Error("native bridge not available"));
      }
    });
  }

  /** Fire and forget. Returns whether it was posted. */
  const send = (type, fields = {}) => post({ type, ...fields });

  /** Unsolicited events from the shell, such as "foreground". */
  function on(type, fn) {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(fn);
  }

  // Installed when the script loads, not in init(): the shell may post its first "foreground"
  // event while the page is still parsing, and a missing receiver would drop it without a word.
  window.nativeEvent = dispatch;

  return {
    get available() { return !!handler(); },
    caps,
    init, request, send, on,
  };
})();

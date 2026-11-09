/* Whiskey Tasting Book — the sync engine (SPEC.md §9.1).

   One entry point, Sync.start(), for every reason a sync can begin: the Refresh button, a cold
   start, the shell's foreground event, the browser's visible and online events, a card just
   submitted. It pulls the three files the PC publishes, pushes the upload queue, and keeps a
   running account of itself in `state` — which step is doing what, what landed, what changed —
   that the Sync screen draws as a checklist and the header's thin bar follows. The account is
   published before the first byte moves, so a tap is answered on the same frame. It lives in
   memory on purpose: a run that died with the app must not come back as "running" for ever.
   Only the outcome goes to meta.

   No `document` and no `app` in here: this file runs in V8 under tests/test_phone_js.py with
   Graph and Store stubbed, which is the only place its logic can be exercised without a phone. */
"use strict";

const Sync = (() => {
  /* A switch to Messages and back should not cost a download over bar wifi; a bottle approved on
     the PC should show up on the next real return. Two minutes: when nothing has changed a pull
     is one small listing (see download), so the throttle guards the token fetch and the round
     trip, not the bytes. The button and a cold start always pull. */
  const MIN_INTERVAL = 2 * 60 * 1000;
  const ALWAYS_PULL = new Set(["manual", "boot"]);

  /* `have` says whether the store already holds this file: a listing that calls it unchanged is
     only to be believed when there is something here for it to be unchanged from. */
  const FILES = [
    { key: "collection", path: "snapshot/collection.json", label: "Collection",
      keep: (d) => Store.setSnapshot(d),
      have: () => Store.spirits().length > 0 },
    { key: "careers", path: "snapshot/careers.json", label: "Scores",
      keep: (d) => Store.setCareers(d.careers ? d : { careers: d }),
      have: () => { const k = Store.careers(); return !!k.generatedAt || Object.keys(k.careers || {}).length > 0; } },
    { key: "rubric", path: "snapshot/rubric.json", label: "Rubric",
      keep: (d) => Store.setRubric(d),
      have: () => !!Store.rubric() },
  ];

  const state = {
    running: false,
    reason: null,          // why this run started: manual, boot, foreground, visible, online, upload
    startedAt: null,
    finishedAt: null,
    steps: [],             // {name, label, state: pending|running|done|failed, detail}
    summary: null,         // what changed, once the run is over (see summarise)
    error: null,           // every failed step, in one line
  };

  const listeners = new Set();
  function emit() {
    for (const fn of listeners) {
      try { fn(state); } catch (e) { console.warn("sync: a listener failed", e); }
    }
  }
  function step(name, label) {
    const s = { name, label, state: "pending", detail: "" };
    state.steps.push(s);
    return s;
  }
  function mark(s, st, detail) {
    s.state = st;
    if (detail !== undefined) s.detail = detail;
    emit();
  }
  const settled = (s) => s.state === "done" || s.state === "failed";
  const progress = () => (state.steps.length
    ? state.steps.filter(settled).length / state.steps.length : 0);

  /** One line about what a file held, for the checklist. */
  function describe(key, data) {
    if (key === "collection") return `${(data.spirits || []).length} spirits`;
    if (key === "careers") {
      const c = data.careers || data || {};
      const sits = Object.values(data.sittings || {}).reduce((a, v) => a + v.length, 0);
      return `${Object.keys(c).length} scored · ${sits} sittings`;
    }
    if (key === "rubric") return `${(data.categories || []).length} categories`;
    return "";
  }
  const kb = (n) => `${Math.max(1, Math.round(n / 1024))} KB`;

  /* One listing instead of three blind downloads. The folder's eTags say which files have
     changed since the last pull; the ones that have not are marked done without a download,
     and the rest come down together. A listing that fails means download everything, as it
     always was — so the optimisation can only save requests, never lose a file. An eTag is
     remembered only once the store has taken the file: a write the phone refused is a file
     still owed, not a file it has. */
  async function download(pulls) {
    let listing = null;
    try { listing = await Graph.listFolder("snapshot"); } catch { /* download everything */ }
    const byName = new Map((listing || []).map((f) => [f.name, f]));
    const tags = Store.meta().etags || {};
    const jobs = [];
    for (const { f, s } of pulls) {
      const remote = listing ? byName.get(f.path.split("/").pop()) : undefined;
      if (listing && !remote) { mark(s, "done", "not published yet"); continue; }
      if (remote && remote.eTag && tags[f.key] === remote.eTag && f.have()) {
        mark(s, "done", remote.lastModifiedDateTime
          ? `unchanged · published ${Store.localTime(remote.lastModifiedDateTime)}` : "unchanged");
        continue;
      }
      mark(s, "running", remote && remote.size ? `downloading ${kb(remote.size)}…` : "downloading…");
      jobs.push({ f, s, eTag: remote ? remote.eTag || null : null, promise: Graph.getJSON(f.path) });
    }
    const results = await Promise.allSettled(jobs.map((j) => j.promise));
    const etags = { ...tags };
    results.forEach((r, i) => {
      const { f, s, eTag } = jobs[i];
      if (r.status === "rejected") {
        mark(s, "failed", String((r.reason && r.reason.message) || r.reason));
        return;
      }
      const data = r.value;
      if (data === null || data === undefined) { mark(s, "done", "not published yet"); return; }
      if (!f.keep(data)) {
        mark(s, "failed", `storage refused: ${Store.lastWriteError() || "unknown"}`);
        return;
      }
      if (eTag) etags[f.key] = eTag;
      mark(s, "done", describe(f.key, data));
    });
    Store.setMeta({ etags });
  }

  /* Errors that would meet every item alike: there is no point walking the rest of the queue
     into the same wall. Anything else is one file's own problem, and the files behind it still
     get their turn — one refused card used to block every card after it, for ever. */
  const WHOLE_QUEUE_FAILS = /sign-in required|could not get a token|could not reach OneDrive|^(401|403|429|5\d\d) /;

  /** The queue, one item at a time, newest last. Uploads are idempotent (SPEC.md §1.3), so a
      failure simply leaves the item where it was, now carrying the reason. Returns how many
      went up. */
  async function upload(s) {
    const q = Store.queue();
    if (!q.length) { mark(s, "done", "nothing waiting"); return 0; }
    let sent = 0;
    let refused = 0;                      // counted here, not from the queue: a card queued mid-run is waiting, not refused
    for (let i = 0; i < q.length; i++) {
      const item = q[i];
      const name = item.path.split("/").pop().slice(0, 24);
      mark(s, "running", `uploading ${i + 1} of ${q.length} · ${item.kind} ${name}`);
      try {
        await Graph.putJSON(item.path, item.body);
        Store.drop(item.path);
        if (item.kind === "tasting") Store.markSent(item.path);
        sent++;
      } catch (e) {
        const msg = String((e && e.message) || e);
        Store.noteFailure(item.path, msg);
        refused++;
        if (WHOLE_QUEUE_FAILS.test(msg)) {
          mark(s, "failed", `${sent} of ${q.length} sent; ${name}: ${msg}`);
          return sent;
        }
      }
    }
    mark(s, refused ? "failed" : "done",
         refused ? `${sent} sent · ${refused} refused, see below` : `${sent} uploaded`);
    return sent;
  }

  /* The fields of a spirit whose change is worth reporting. Price, proof and status are what
     the PC edits; a change to anything else is noise here. */
  const WATCH = ["status", "owned", "name", "distillery", "paid", "proof", "age", "release_year"];

  /** What a run changed, as codes: the Sync screen turns them into names, because it has the
      spirit list and this file does not. */
  function summarise(before, after, uploaded) {
    const byCode = (list) => new Map((list || []).filter(Boolean).map((s) => [s.code, s]));
    const was = byCode(before.spirits);
    const now = byCode(after.spirits);
    const bc = before.careers.careers || {};
    const ac = after.careers.careers || {};
    const differs = (a, b) => WATCH.some((k) => a[k] !== b[k]);
    // A null entry in careers.json is a PC-side bug; it must not become a run that never ends.
    const n = (c) => (c ? c.n : undefined);
    const score = (c) => (c ? c.score : undefined);
    return {
      added: [...now.keys()].filter((c) => !was.has(c)),
      removed: [...was.keys()].filter((c) => !now.has(c)),
      changed: [...now.keys()].filter((c) => was.has(c) && differs(was.get(c), now.get(c))),
      newScores: Object.keys(ac).filter((c) => !bc[c] || n(bc[c]) !== n(ac[c]) || score(bc[c]) !== score(ac[c])),
      uploaded,
    };
  }

  async function run(reason, pull) {
    Object.assign(state, {
      running: true, reason, startedAt: new Date().toISOString(), finishedAt: null,
      steps: [], summary: null, error: null,
    });
    const before = { spirits: Store.spirits(), careers: Store.careers() };
    const pulls = pull ? FILES.map((f) => ({ f, s: step(f.key, f.label) })) : [];
    const up = step("upload", "Upload");
    emit();                                        // the tap is answered here, before any byte moves
    let uploaded = 0;
    try {
      if (pull) await download(pulls);
      uploaded = await upload(up);
    } catch (e) {
      // Nothing above is meant to throw; if something does, the run must still end.
      const open = state.steps.find((s) => !settled(s)) || up;
      mark(open, "failed", String((e && e.message) || e));
    }
    const failed = state.steps.filter((s) => s.state === "failed");
    try {
      state.summary = summarise(before, { spirits: Store.spirits(), careers: Store.careers() }, uploaded);
    } catch (e) {
      // The summary is a courtesy. A run ends whatever it holds, or the button stays dead.
      state.summary = null;
      failed.push({ label: "Summary", detail: String((e && e.message) || e) });
    }
    state.finishedAt = new Date().toISOString();
    state.error = failed.length ? failed.map((s) => `${s.label}: ${s.detail}`).join("; ") : null;
    state.running = false;

    // lastSync means "the published files are all here as of this moment", so a pull with a
    // failed step does not move it — and the next foreground tries again rather than waiting.
    const meta = {};
    if (state.summary) meta.lastSummary = { at: state.finishedAt, ...state.summary };
    if (pull && !pulls.some(({ s }) => s.state === "failed")) meta.lastSync = state.finishedAt;
    if (uploaded) meta.lastUpload = state.finishedAt;
    meta.lastError = state.error;
    meta.lastErrorAt = failed.length ? state.finishedAt : null;
    Store.setMeta(meta);
    emit();
    return state;
  }

  let inflight = null;      // the run in progress, so every trigger during it joins rather than doubles
  let followUp = null;      // one more run after it, for a card submitted while it was uploading

  /** Long enough since the last complete pull for an automatic one to be worth the round trip. */
  function due(now = Date.now()) {
    const last = Date.parse(Store.meta().lastSync || "");
    return !Number.isFinite(last) || now - last >= MIN_INTERVAL;
  }

  /** Every trigger comes through here. Resolves to the state when a run happened, and to
      {skipped: why} when there was nothing to do — offline, signed out, or too soon with an
      empty queue. Never rejects. */
  function start({ reason = "manual" } = {}) {
    if (inflight) {
      // A card submitted mid-run may have missed the upload step; one more pass catches it.
      if (reason === "upload") {
        if (!followUp) followUp = inflight.then(() => { followUp = null; return start({ reason }); });
        return followUp;
      }
      return inflight;
    }
    if (!navigator.onLine) return Promise.resolve({ skipped: "offline" });
    if (!Graph.isSignedIn()) return Promise.resolve({ skipped: "signed out" });
    const pull = ALWAYS_PULL.has(reason) || due();
    if (!pull && !Store.queueCount()) return Promise.resolve({ skipped: "nothing to do" });
    inflight = run(reason, pull).finally(() => { inflight = null; });
    return inflight;
  }

  /** Called on every change of state; returns the function that unsubscribes. */
  function onChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  return { state, progress, start, due, onChange, MIN_INTERVAL };
})();

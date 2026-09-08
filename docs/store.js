/* Whiskey Tasting Book — phone-side storage and the upload queue (SPEC.md §9.1, §9.2).

   Scoring a pour with no connection has to work exactly as it does with one. So every scorecard is
   written to local storage first and queued; uploading is a separate, retryable step. Nothing is
   ever held only in memory, because the app can be killed between the bar and the taxi.

   Filenames follow the same rules as store.py, and for the same reason: the timestamp is only
   second-resolution, so the random suffix is load-bearing. Two cards for one spirit in the same
   second would otherwise share a name and the second would destroy the first (SPEC.md §0). */
"use strict";

const Store = (() => {
  const K = {
    snapshot: "wtb.snapshot",
    rubric: "wtb.rubric",
    queue: "wtb.queue",
    cards: "wtb.cards",
    meta: "wtb.meta",
  };

  // Every read and write is guarded: private browsing and a full quota both throw, and losing a
  // draft must never take the app down with it.
  function read(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
  }

  const pad = (n, w = 2) => String(n).padStart(w, "0");

  /** UTC, matching store.py's _stamp() so both devices name files the same way. */
  function stamp(d = new Date()) {
    return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}`
      + `-${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`;
  }
  /** Four hex characters, the same width as secrets.token_hex(2). */
  function rand4() {
    const b = new Uint8Array(2);
    (self.crypto || window.crypto).getRandomValues(b);
    return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  }
  const nowIso = () => new Date().toISOString();

  // -- cached collection ----------------------------------------------------
  const snapshot = () => read(K.snapshot, null);
  const setSnapshot = (data) => write(K.snapshot, data);
  const spirits = () => (snapshot()?.spirits) || [];

  const rubric = () => read(K.rubric, null);
  const setRubric = (data) => write(K.rubric, data);

  // -- the queue ------------------------------------------------------------
  const queue = () => read(K.queue, []);
  function enqueue(item) {
    const q = queue();
    q.push(item);
    write(K.queue, q);
    return item;
  }
  function drop(path) {
    write(K.queue, queue().filter((i) => i.path !== path));
  }
  const queueCount = () => queue().length;

  // -- records, shaped exactly as store.py writes them ----------------------
  function scorecard(fields, rub) {
    const id = `T-${stamp()}-${fields.spirit_id}-${rand4()}`;
    const scores = { ...fields.scores };
    const total = rub ? rub.categories.reduce((a, c) => a + (scores[c.key] || 0), 0) : null;
    const rec = {
      tasting_id: id,
      revision: 1,
      deleted: false,
      spirit_id: fields.spirit_id,
      session_id: fields.session_id ?? null,
      date: fields.date || new Date().toISOString().slice(0, 10),
      flight_pos: fields.flight_pos ?? null,
      scores,
      barrel_id: fields.barrel_id ?? null,
      notes: fields.notes || {},
      tags: fields.tags || [],
      blind: !!fields.blind,
      venue: fields.venue ?? null,
      pour_price: fields.pour_price ?? null,
      pour_size_oz: fields.pour_size_oz ?? null,
      include_in_average: true,
      status: "submitted",
      entered_from: "phone",
      rubric: rub ? rub.name : null,
      rubric_version: rub ? rub.version : null,
      total,
      medal: rub && total !== null ? medalFor(total, rub) : null,
      created_at: nowIso(),
    };
    return { rec, path: `tastings/${id}-r1.json` };
  }

  function pendingBottle(sheet, fields) {
    const uid = `P-${stamp()}-${rand4()}`;
    return {
      rec: {
        pending_uid: uid, sheet, fields,
        entered_from: "phone", created_at: nowIso(), state: "pending",
      },
      path: `pending/${uid}.json`,
    };
  }

  /** Bands come high-to-low from the rubric; half-up is irrelevant here because a single card's
      total is always a whole number. */
  function medalFor(total, rub) {
    for (const b of rub.bands) if (total >= b.min) return b.name;
    return rub.bands[rub.bands.length - 1].name;
  }

  // -- submitted cards, kept locally whether or not they have gone up -------
  const cards = () => read(K.cards, []);
  function addCard(rec, path) {
    const list = cards();
    list.unshift({ ...rec, _path: path, _sent: false });
    write(K.cards, list.slice(0, 500));
  }
  function markSent(path) {
    write(K.cards, cards().map((c) => (c._path === path ? { ...c, _sent: true } : c)));
  }
  /** Every locally-known sitting for one spirit, newest first. */
  const cardsFor = (code) => cards().filter((c) => c.spirit_id === code);

  // -- connection notes -----------------------------------------------------
  const meta = () => read(K.meta, {});
  const setMeta = (patch) => write(K.meta, { ...meta(), ...patch });

  /** Save the card, queue the upload, and tell the caller it is safe — in that order. */
  function submitScorecard(fields) {
    const { rec, path } = scorecard(fields, rubric());
    addCard(rec, path);
    enqueue({ path, kind: "tasting", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  function submitPending(sheet, fields) {
    const { rec, path } = pendingBottle(sheet, fields);
    enqueue({ path, kind: "pending", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  return {
    snapshot, setSnapshot, spirits, rubric, setRubric,
    queue, enqueue, drop, queueCount,
    cards, cardsFor, addCard, markSent,
    meta, setMeta,
    submitScorecard, submitPending,
    stamp, rand4, medalFor,
  };
})();

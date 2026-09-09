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
    encounters: "wtb.encounters",
    careers: "wtb.careers",
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

  /** The local calendar date, not the UTC one.

      A pour belongs to the night it was drunk. toISOString() would file a card by UTC, so east of
      Greenwich an early-evening pour lands on tomorrow and west of it a late one lands on
      yesterday — and the PC, which uses datetime.now(), would disagree with the phone about the
      same sitting. Instants (created_at, filenames) stay UTC; calendar days are local. */
  function today(d = new Date()) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  /** An ISO instant shown in the phone's own time zone. Everything is stored in UTC and nothing
      is displayed in it. */
  function localTime(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return `${today(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  /** Just the clock part, for "offline since". */
  const localClock = (iso) => localTime(iso).slice(11);

  // -- cached collection ----------------------------------------------------
  const snapshot = () => read(K.snapshot, null);
  const setSnapshot = (data) => write(K.snapshot, data);
  const spirits = () => (snapshot()?.spirits) || [];

  const rubric = () => read(K.rubric, null);
  const setRubric = (data) => write(K.rubric, data);

  /* Careers used to live only in memory, so closing the app threw away every score the PC had
     reconciled and the collection went blank until the next sync — which needs a connection, the
     one thing this app is built not to need. */
  const careers = () => read(K.careers, { careers: {}, calibration: [] });
  const setCareers = (data) => write(K.careers, {
    careers: (data && data.careers) || {},
    calibration: (data && data.calibration) || [],
  });

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
      date: fields.date || today(),
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

  /** A spirit tasted but never owned — a pour at a bar. Shaped exactly as store.py writes one.

      `code` stays null on purpose: the human-facing X- number is handed out on the PC, in order
      of first tasting, and this phone cannot know what it will be. The uid is the identity the
      scorecard refers to, and the PC translates one to the other when it reads. */
  function encounter(fields) {
    const uid = `E-${stamp()}-${rand4()}`;
    return {
      rec: {
        encounter_uid: uid,
        code: null,
        name: fields.name,
        distillery: fields.distillery ?? null,
        type: fields.type ?? null,
        region: fields.region ?? null,
        age: fields.age ?? null,
        proof: fields.proof ?? null,
        venue: fields.venue ?? null,
        notes: fields.notes ?? null,
        linked_bottle_code: null,
        entered_from: "phone",
        first_tasted: nowIso(),
        created_at: nowIso(),
      },
      path: `encounters/${uid}.json`,
    };
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

  // -- bar pours this phone knows about -------------------------------------
  // Kept locally because the PC does not publish encounters back: without this a pour would
  // vanish from the phone the moment it was submitted, which is no way to run a flight at a bar.
  const encounters = () => read(K.encounters, []);
  function addEncounter(rec) {
    write(K.encounters, [rec, ...encounters().filter((e) => e.encounter_uid !== rec.encounter_uid)]);
  }
  /** In the shape the collection list and the scorecard expect of a spirit. */
  const asSpirit = (e) => ({
    code: e.encounter_uid,
    display_name: [e.distillery, e.name].filter(Boolean).join(" ") || e.name,
    name: e.name,
    distillery: e.distillery,
    type: e.type,
    region: e.region,
    age: e.age,
    proof: e.proof,
    venue: e.venue,
    _sheet: "Encounter",
    _encounter: true,
    owned: false,             // a bar pour was never yours; it must not count as stock
  });

  // -- connection notes -----------------------------------------------------
  const meta = () => read(K.meta, {});
  const setMeta = (patch) => write(K.meta, { ...meta(), ...patch });

  /** A correction: the same card, one revision later.

      Journal files are immutable, so this writes a new file rather than touching the old one, and
      the PC resolves the highest revision when it reads. The phone can only do this for cards it
      holds itself — it keeps its own cards, not the journal — which is exactly the set it shows. */
  function reviseCard(previous, fields) {
    const rub = rubric();
    const revision = (previous.revision || 1) + 1;
    const scores = { ...fields.scores };
    const total = rub ? rub.categories.reduce((a, c) => a + (scores[c.key] || 0), 0) : null;
    const rec = {
      ...previous,
      revision,
      deleted: false,
      scores,
      notes: fields.notes || {},
      date: fields.date || previous.date,
      venue: fields.venue ?? previous.venue ?? null,
      total,
      medal: rub && total !== null ? medalFor(total, rub) : null,
      created_at: nowIso(),
    };
    delete rec._path;
    delete rec._sent;
    const path = `tastings/${rec.tasting_id}-r${revision}.json`;
    write(K.cards, [{ ...rec, _path: path, _sent: false },
                    ...cards().filter((c) => c.tasting_id !== rec.tasting_id)]);
    enqueue({ path, kind: "tasting", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  /** A tombstone is just another revision. Nothing is unlinked, here or on the PC. */
  function deleteCard(previous, reason) {
    const revision = (previous.revision || 1) + 1;
    const rec = {
      tasting_id: previous.tasting_id,
      revision,
      deleted: true,
      reason: reason || null,
      created_at: nowIso(),
    };
    const path = `tastings/${rec.tasting_id}-r${revision}.json`;
    write(K.cards, cards().filter((c) => c.tasting_id !== rec.tasting_id));
    enqueue({ path, kind: "tasting", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  /** Save the card, queue the upload, and tell the caller it is safe — in that order. */
  function submitScorecard(fields) {
    const { rec, path } = scorecard(fields, rubric());
    addCard(rec, path);
    enqueue({ path, kind: "tasting", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  /** Start a bar pour: remember it here, queue the encounter, and hand back the spirit-shaped
      object the scorecard scores against. The card follows separately when it is submitted. */
  function submitEncounter(fields) {
    const { rec, path } = encounter(fields);
    addEncounter(rec);
    enqueue({ path, kind: "encounter", body: rec, created_at: nowIso() });
    return { rec, spirit: asSpirit(rec) };
  }

  function submitPending(sheet, fields) {
    const { rec, path } = pendingBottle(sheet, fields);
    enqueue({ path, kind: "pending", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  return {
    snapshot, setSnapshot, spirits, rubric, setRubric, careers, setCareers,
    queue, enqueue, drop, queueCount,
    cards, cardsFor, addCard, markSent, reviseCard, deleteCard,
    meta, setMeta,
    submitScorecard, submitPending, submitEncounter,
    encounters, addEncounter, asSpirit,
    stamp, rand4, medalFor, today, localTime, localClock,
  };
})();

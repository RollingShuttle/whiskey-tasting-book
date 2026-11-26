/* Whiskey Tasting Book — phone-side storage and the upload queue (SPEC.md §9.1, §9.2).

   Scoring a pour with no connection has to work exactly as it does with one. So every scorecard is
   written to local storage first and queued; uploading is a separate, retryable step. Nothing is
   ever held only in memory, because the app can be killed between the bar and the taxi.

   Filenames follow the same rules as store.py, and for the same reason: the timestamp is only
   second-resolution, so the random suffix is load-bearing. Two cards for one spirit in the same
   second would otherwise share a name and the second would destroy the first (SPEC.md §0).

   Inside the native iOS app (guide/IOS-APP.md) the whole store is also mirrored to a file the
   shell keeps: a web view's storage can be evicted and does not survive a reinstall, and a phone
   full of unsent cards is exactly the thing this app exists to protect. */
"use strict";

const Store = (() => {
  const K = {
    snapshot: "wtb.snapshot",
    rubric: "wtb.rubric",
    queue: "wtb.queue",
    cards: "wtb.cards",
    encounters: "wtb.encounters",
    careers: "wtb.careers",
    buried: "wtb.buried",
    meta: "wtb.meta",
  };

  // Every read and write is guarded: private browsing and a full quota both throw, and losing a
  // draft must never take the app down with it.
  //
  // Reads are memoised. The collection is two hundred spirits and the list, the detail screen
  // and the table each ask for it several times per row — every one of those used to be a full
  // JSON.parse of the whole snapshot, and the phone felt it. A key is parsed once and kept until
  // something writes it; a write invalidates rather than storing the caller's object, so the
  // cache can never hold anything that is not exactly what storage holds. Callers get the cached
  // value itself, not a copy — nothing in the app mutates what it reads, and a copy per call
  // would bring back a fraction of the cost this removes.
  const cache = new Map();
  function read(key, fallback) {
    if (cache.has(key)) return cache.get(key) ?? fallback;
    try {
      const raw = localStorage.getItem(key);
      const value = raw ? JSON.parse(raw) : null;
      cache.set(key, value);                      // null too: "known to be absent" saves a lookup
      return value ?? fallback;
    } catch { return fallback; }                  // a failed read is not remembered
  }
  // Why a failed write is remembered: iOS can refuse storage (quota, eviction, a broken origin),
  // and a store that swallows that silently looks exactly like a sync that never ran.
  let lastWriteError = null;
  function write(key, value) {
    cache.delete(key);                            // whatever happens next, the memo is stale
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      lastWriteError = `${e && e.name ? `${e.name}: ` : ""}${(e && e.message) || e}`;
      return false;
    }
    mirror();
    return true;
  }
  // -- the shell's copy -----------------------------------------------------
  /* Debounced: a flight scored in a hurry writes several keys in a row, and one save after the
     burst is enough. The object is keyed by the storage key itself so restoring needs no
     translation. With no shell — a browser opened to work on the screens — none of it runs. */
  const BACKUP_NAME = "tasting-book.json";
  const MIRROR_DELAY = 1000;
  let mirrorTimer = null;
  const nativeBackup = () => typeof Native !== "undefined" && Native.caps.has("backup");

  function mirrorNow() {
    clearTimeout(mirrorTimer);
    mirrorTimer = null;
    // The raw strings, not parsed values: one serialisation instead of three, and the file holds
    // exactly what the store held.
    const data = {};
    for (const key of Object.values(K)) {
      try { data[key] = localStorage.getItem(key); } catch { data[key] = null; }
    }
    Native.send("backup.save", { name: BACKUP_NAME, json: JSON.stringify(data) });
  }
  function mirror() {
    if (!nativeBackup()) return;
    clearTimeout(mirrorTimer);
    mirrorTimer = setTimeout(mirrorNow, MIRROR_DELAY);
  }
  // Backgrounding the app suspends its timers, so a save still waiting goes now rather than
  // whenever the app next wakes — which may be after iOS has already emptied the web view.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && mirrorTimer && nativeBackup()) mirrorNow();
  });
  // The shell reloads this page itself after iOS kills the web process; pagehide is the last word.
  window.addEventListener("pagehide", () => {
    if (mirrorTimer && nativeBackup()) mirrorNow();
  });

  /** The shell holds a backup and this web view holds nothing of ours — a first launch, a
      reinstall, an eviction — so put it back. Never over a live store, even a partial one: the
      live one is the newer. Resolves false everywhere else, no shell included, so boot() can
      always await it. */
  async function restoreFromNative() {
    if (!nativeBackup()) return false;
    if (Object.values(K).some(present)) return false;
    let reply;
    try { reply = await Native.request("backup.load"); } catch { return false; }
    if (!reply || reply.none || typeof reply.text !== "string") return false;
    let data;
    try { data = JSON.parse(reply.text); } catch { return false; }
    if (!data || typeof data !== "object") return false;
    let restored = false;
    for (const key of Object.values(K)) {
      if (typeof data[key] !== "string") continue;             // absent when it was backed up
      try { localStorage.setItem(key, data[key]); restored = true; } catch { /* quota; carry on */ }
    }
    cache.clear();                                             // written behind the memo's back
    return restored;
  }
  function present(key) {
    try { return localStorage.getItem(key) !== null; } catch { return false; }
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
  const spirits = () => (snapshot()?.spirits) || [];
  /* What the PC publishes replaces what the PC published — never a bottle that was added on
     this phone. Those carry `_local` and an L- code the PC does not issue, so they ride along
     behind whatever comes down and nothing a person typed in ever disappears on a sync. */
  const setSnapshot = (data) => {
    const mine = spirits().filter((s) => s._local);
    return write(K.snapshot, mine.length
      ? { ...data, spirits: ((data && data.spirits) || []).concat(mine) } : data);
  };

  // -- where the book lives -------------------------------------------------
  /* "onedrive": the PC publishes the collection and this phone uploads its cards — the original
     arrangement. "local": this phone is the whole book; bottles are added here, nothing is
     queued and nothing is fetched, so it needs no account and no computer. A phone from before
     the choice existed has no mode on file: one that has ever synced is plainly on OneDrive, and
     one that has not is asked (null). */
  const mode = () => {
    const m = meta();
    if (m.mode === "local" || m.mode === "onedrive") return m.mode;
    return m.lastSync || snapshot() ? "onedrive" : null;
  };
  const isLocal = () => meta().mode === "local";
  const setMode = (m) => setMeta({ mode: m });

  // -- the Type list, and an age that is not a number ------------------------
  /* What the Add form offers for Type. The PC publishes the list from the workbook's own Type
     drop-down (snapshot.types); DEFAULT_TYPES is that list as it stood when this was written, for
     a phone with no PC behind it and for a snapshot published before the PC sent one. Whatever
     this phone already holds under another type — a bottle or a bar pour entered under "Other" —
     is offered too, so a type typed once never has to be typed, or misspelt, again. */
  const DEFAULT_TYPES = ["Bourbon", "Rye", "Wheated", "BourRye", "Wheat", "Blended", "Finished",
    "Single Malt", "Single Grain", "Tequila", "Mezcal", "Rum", "Gin", "Vodka", "Cognac",
    "Armagnac", "Liqueur", "Baijiu", "Bitter", "Moonshine"];
  function types() {
    const snap = snapshot();
    const listed = snap && Array.isArray(snap.types) && snap.types.length ? snap.types : DEFAULT_TYPES;
    const seen = new Set();
    const fresh = (t) => {
      const s = String(t ?? "").trim();
      if (!s || seen.has(s.toLowerCase())) return null;
      seen.add(s.toLowerCase());
      return s;
    };
    const out = listed.map(fresh).filter(Boolean);
    const own = [...spirits(), ...encounters()].map((s) => fresh(s && s.type)).filter(Boolean);
    return out.concat(own.sort((a, b) => a.localeCompare(b)));
  }
  /** The spelling already in use for a type: "red wine" typed a second time joins "Red Wine"
      instead of starting a second group beside it. Null for nothing typed. */
  function canonicalType(t) {
    const s = String(t ?? "").trim();
    if (!s) return null;
    return types().find((x) => x.toLowerCase() === s.toLowerCase()) || s;
  }

  // -- the collection's filters ----------------------------------------------
  /* What the Collection screen can be narrowed by. Each filter is a set of choices: within one,
     any of the choices will do (Bourbon or Rye); across them, every one must hold (…and Opened,
     and not scored). `scored` alone is a single choice — a spirit cannot be both. The rule lives
     here, away from the page, so that the tests run it rather than read it. */
  const noFilters = () => ({ scored: "", status: [], kind: [], type: [], region: [], rarity: [] });
  const sameWord = (a, b) => String(a ?? "").trim().toLowerCase() === String(b ?? "").trim().toLowerCase();
  const countFilters = (f) => (f.scored ? 1 : 0)
    + f.status.length + f.kind.length + f.type.length + f.region.length + f.rarity.length;

  /** Does a spirit pass the filters? `facts` are the three things only the page can say:
      whether the bottle is gone (Finished or Removed), whether it has any sitting, and whether
      it has a score.

      A gone bottle is offered only when Finished is asked for — nothing can be poured from it —
      and then only if it was scored while it lasted; one never scored has nothing to show
      anywhere. A bar pour has no status, so asking for one leaves bar pours out. */
  function passes(s, f, { gone, sittings, scored }) {
    if (gone) {
      if (!f.status.some((v) => sameWord(v, "Finished")) || !sittings) return false;
    } else if (f.status.length && !f.status.some((v) => sameWord(v, s.status))) {
      return false;
    }
    if (f.kind.length && !f.kind.includes(s._sheet)) return false;
    for (const key of ["type", "region", "rarity"]) {
      if (f[key].length && !f[key].some((v) => sameWord(v, s[key]))) return false;
    }
    if (f.scored && (f.scored === "yes") !== !!scored) return false;
    return true;
  }

  /** The choices each filter offers, from what the phone holds — a type nobody has is not a
      choice. Types run in the workbook's own order (types()); rarity in its order of scarcity;
      regions alphabetically; kinds as the sheets come. */
  const RARITY_ORDER = ["common", "uncommon", "rare", "unicorn", "impossible", "dusty"];
  function filterChoices(list) {
    const present = (key) => {
      const seen = new Map();
      for (const s of list) {
        const v = String((s && s[key]) ?? "").trim();
        if (v && !seen.has(v.toLowerCase())) seen.set(v.toLowerCase(), v);
      }
      return seen;
    };
    const type = present("type"), region = present("region"), rarity = present("rarity");
    const kinds = new Set(list.map((s) => s && s._sheet));
    const rank = (v) => { const i = RARITY_ORDER.indexOf(v.toLowerCase()); return i === -1 ? RARITY_ORDER.length : i; };
    return {
      type: types().filter((t) => type.has(t.toLowerCase())),
      region: [...region.values()].sort((a, b) => a.localeCompare(b)),
      rarity: [...rarity.values()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)),
      kind: ["Bottle", "Miniature", "Sample", "Encounter"].filter((k) => kinds.has(k)),
    };
  }

  /** An age as the form or an encounter file hands it over — "12", 12, "NAS", "N/A", nothing —
      split the way the PC's snapshot splits it: years as a number, anything else as the label
      shown in their place. One field cannot be both: a list that sorts on it would sort "NAS" in
      among the years, and the row would read "NAS yr". */
  function ageParts(v) {
    const t = String(v ?? "").trim();
    if (!t) return { age: null, age_label: null };
    return Number.isFinite(Number(t)) ? { age: Number(t), age_label: null } : { age: null, age_label: t };
  }

  // -- bottles added on this phone ------------------------------------------
  const GONE = new Set(["finished", "removed"]);       // collection.py's vocabulary, for one field
  const LOCAL_CODE = /^L-(\d+)$/;

  /** A bottle in the shape the PC's snapshot gives one, built from what the Add form collects.
      Numbers go in as numbers — a string sorts as text, "9" after "18". */
  function localSpirit(code, f) {
    const text = (v) => { const t = String(v ?? "").trim(); return t || null; };
    const num = (v) => { const t = text(v); return t !== null && Number.isFinite(Number(t)) ? Number(t) : null; };
    const name = text(f.name), distillery = text(f.distillery);
    const status = text(f.status) || "Opened";
    return {
      code,
      name: name || distillery,
      distillery,
      display_name: [distillery, name].filter(Boolean).join(" ") || name || distillery || code,
      type: text(f.type), region: text(f.region),
      ...ageParts(f.age),
      proof: num(f.proof), release_year: num(f.release_year),
      paid: num(f.paid), size_ml: num(f.size_ml),
      status, owned: !GONE.has(status.toLowerCase()),
      _sheet: "Bottle", _local: true,
    };
  }
  const asFields = (s) => ({
    name: s.name, distillery: s.distillery, type: s.type, region: s.region,
    age: s.age ?? s.age_label, proof: s.proof, release_year: s.release_year,
    paid: s.paid, size_ml: s.size_ml, status: s.status,
  });

  /** Add a bottle to the collection on this phone. Its code is L-n, one past the highest ever
      handed out here — kept in meta as well as read off the list, because a code that was reissued
      after its bottle was removed would re-point every review written against the old one. */
  function addLocalSpirit(fields) {
    const snap = snapshot() || { spirits: [] };
    const used = (snap.spirits || []).map((s) => LOCAL_CODE.exec(s.code || ""))
      .filter(Boolean).map((x) => Number(x[1]));
    const n = Math.max(meta().localHigh || 0, ...used) + 1;
    const spirit = localSpirit(`L-${n}`, fields);
    if (!write(K.snapshot, { ...snap, spirits: [...(snap.spirits || []), spirit] })) return null;
    setMeta({ localHigh: n });
    return spirit;
  }
  /** Change a bottle added here — its details or its status. Null for any other bottle: what the
      PC published is the PC's to change. */
  function updateLocalSpirit(code, patch) {
    const snap = snapshot();
    const cur = snap && (snap.spirits || []).find((s) => s.code === code && s._local);
    if (!cur) return null;
    const next = localSpirit(code, { ...asFields(cur), ...patch });
    const list = snap.spirits.map((s) => (s === cur ? next : s));
    return write(K.snapshot, { ...snap, spirits: list }) ? next : null;
  }
  function removeLocalSpirit(code) {
    const snap = snapshot();
    if (!snap || !(snap.spirits || []).some((s) => s.code === code && s._local)) return false;
    return write(K.snapshot, { ...snap, spirits: snap.spirits.filter((s) => s.code !== code) });
  }

  // -- a copy to keep -------------------------------------------------------
  /** Everything this phone holds, as one JSON text. The storage keys are the keys, exactly as in
      the shell's own backup file, so importAll takes either. */
  function exportAll() {
    const data = { _format: "whiskey-tasting-book", _version: 1, _exported: nowIso() };
    for (const key of Object.values(K)) {
      try { data[key] = localStorage.getItem(key); } catch { data[key] = null; }
    }
    return JSON.stringify(data);
  }
  /** Replace what this phone holds with a backup. Checked in full before the first write, so a
      wrong or damaged file changes nothing. Returns what was wrong, or null when it is done. */
  function importAll(text) {
    let data;
    try { data = JSON.parse(text); } catch { return "That file is not a Tasting Book backup."; }
    if (!data || typeof data !== "object" || Array.isArray(data)) return "That file is not a Tasting Book backup.";
    const held = Object.values(K).filter((k) => typeof data[k] === "string");
    if (!held.length) return "That file holds no Tasting Book data.";
    for (const k of held) {
      try { JSON.parse(data[k]); } catch { return `The backup is damaged (${k}).`; }
    }
    try {
      for (const key of Object.values(K)) {
        if (typeof data[key] === "string") localStorage.setItem(key, data[key]);
        else localStorage.removeItem(key);
      }
    } catch (e) {
      cache.clear();
      return `This phone refused the backup: ${(e && e.message) || e}`;
    }
    cache.clear();                                             // written behind the memo's back
    mirror();
    return null;
  }

  const rubric = () => read(K.rubric, null);
  const setRubric = (data) => write(K.rubric, data);

  /* Careers used to live only in memory, so closing the app threw away every score the PC had
     reconciled and the collection went blank until the next sync — which needs a connection, the
     one thing this app is built not to need. */
  const careers = () => read(K.careers,
    { version: 0, careers: {}, calibration: [], sittings: {}, generatedAt: "" });
  const setCareers = (data) => write(K.careers, {
    version: (data && data.version) || 1,
    careers: (data && data.careers) || {},
    calibration: (data && data.calibration) || [],
    sittings: (data && data.sittings) || {},
    generatedAt: (data && data.generated_at) || "",
  });

  // -- the queue ------------------------------------------------------------
  const queue = () => read(K.queue, []);
  function enqueue(item) {
    // Phone-only: there is nowhere to send it, and a queue that can never drain would only grow.
    if (isLocal()) return item;
    const q = queue();
    q.push(item);
    write(K.queue, q);
    return item;
  }
  function drop(path) {
    write(K.queue, queue().filter((i) => i.path !== path));
  }
  /** Why an upload was refused, kept on the item itself: the Sync screen can then say which
      card and what OneDrive said, and the next pass can see how often it has tried. */
  function noteFailure(path, error) {
    write(K.queue, queue().map((i) => (i.path === path
      ? { ...i, error: String(error).slice(0, 200), attempts: (i.attempts || 0) + 1, lastTried: nowIso() }
      : i)));
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
  /** The mean of whole-number totals, to `places` decimals, half-up — worked on whole numbers
      so it is rubric.half_up to the digit. Multiplying a float by ten and rounding is not: 84.05
      becomes 840.4999… and drops to 84.0 where the PC shows 84.1. */
  function meanHalfUp(sum, n, places = 1) {
    const k = 10 ** places;
    return Math.floor((2 * k * sum + n) / (2 * n)) / k;
  }

  function medalFor(total, rub) {
    for (const b of rub.bands) if (total >= b.min) return b.name;
    return rub.bands[rub.bands.length - 1].name;
  }

  // -- submitted cards, kept locally whether or not they have gone up -------
  const cards = () => read(K.cards, []);
  function addCard(rec, path) {
    const list = cards();
    list.unshift({ ...rec, _path: path, _sent: false, ...(isLocal() ? { _local: true } : {}) });
    // With a PC behind it the journal is the record and this list is a convenience; alone, this
    // list is the record, and trimming it would be throwing sittings away.
    write(K.cards, list.slice(0, isLocal() ? 5000 : 500));
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
    ...ageParts(e.age),       // the file holds one field; the lists want years and label apart
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
    write(K.cards, [{ ...rec, _path: path, _sent: false, ...(isLocal() ? { _local: true } : {}) },
                    ...cards().filter((c) => c.tasting_id !== rec.tasting_id)]);
    enqueue({ path, kind: "tasting", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  /** Put a sitting under another bottle: the card was scored against the wrong one.

      The same card, one revision later, naming the other spirit and changing nothing else — the
      scores, notes and date are the ones that were given. `moved_from` records where it was, so
      the journal shows the correction. Nothing happens for a card already under that spirit. */
  function moveCard(previous, spiritId) {
    if (!spiritId || previous.spirit_id === spiritId) return null;
    const revision = (previous.revision || 1) + 1;
    const rec = {
      ...previous,
      revision,
      deleted: false,
      spirit_id: spiritId,
      moved_from: previous.spirit_id,
      created_at: nowIso(),
    };
    delete rec._path;
    delete rec._sent;
    delete rec._local;
    const path = `tastings/${rec.tasting_id}-r${revision}.json`;
    write(K.cards, [{ ...rec, _path: path, _sent: false, ...(isLocal() ? { _local: true } : {}) },
                    ...cards().filter((c) => c.tasting_id !== rec.tasting_id)]);
    enqueue({ path, kind: "tasting", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  // -- one name, several bottles ----------------------------------------------
  // The shelf holds the same whiskey more than once: a second bottle bought, another year's
  // release, three samples of one thing. Same distillery and same name is the same bottle for
  // showing; the sheet is part of the key, so a bottle and a sample of it are never put together.
  // A bar pour is never grouped — it is a pour, and has no sheet.
  const GROUPED_SHEETS = ["Bottle", "Miniature", "Sample"];
  const folded = (v) => String(v ?? "").trim().replace(/\s+/g, " ").toLowerCase();
  function groupKey(s) {
    if (!s || !GROUPED_SHEETS.includes(s._sheet)) return null;
    const name = folded(s.name);
    return name ? `${s._sheet}|${folded(s.distillery)}|${name}` : null;
  }
  /** The list as it came, with same-named bottles gathered where the first of them stood.
      Every entry is `{ key, members }`; one member means an ordinary row. */
  function groupSpirits(list) {
    const at = new Map();
    const out = [];
    for (const s of list) {
      const key = groupKey(s);
      let g = key === null ? null : at.get(key);
      if (!g) {
        g = { key, members: [] };
        if (key !== null) at.set(key, g);
        out.push(g);
      }
      g.members.push(s);
    }
    return out;
  }
  /** What tells one bottle of a name from the next: its special note, its year, its status.
      (The Bottle and Miniature sheets call the note "Notes"; the Sample sheet, "Special note".) */
  function memberLabel(s) {
    const year = Number(s.release_year);
    const parts = [s.special_note || s.notes,
                   Number.isFinite(year) && year > 0 ? String(Math.round(year)) : null,
                   s.status];
    return parts.map((v) => String(v ?? "").trim()).filter(Boolean).join(" · ") || s.code;
  }

  /** The PC's figures for one spirit, less the sittings this phone has since taken off it: the
      ones deleted here, and the ones moved to another bottle. The PC goes on counting those until
      it has read the tombstone or the move, and a bottle that still showed the score of a card
      just moved off it looked as if the move had not taken.

      Worked out again from the published sittings that remain, not subtracted from the rounded
      mean, so it is the figure the PC itself will arrive at. The published career comes back
      untouched when nothing has left, and null when everything has. */
  function publishedCareer(code, rub) {
    const all = careers();
    const pc = (all.careers || {})[code] || null;
    const listed = (all.sittings || {})[code] || [];
    if (!pc || !listed.length) return pc;
    const dead = new Set(buried());
    const held = new Map(cards().map((c) => [c.tasting_id, c.spirit_id]));
    const left = (t) => dead.has(t.tasting_id)
      || (held.has(t.tasting_id) && held.get(t.tasting_id) !== code);
    const has = (v) => v !== null && v !== undefined;
    const rest = listed.filter((t) => !left(t) && t.include_in_average !== false && has(t.total));
    if (!listed.some(left)) {
      // Nothing has left: the PC's own figure, with the exact sum behind it when the list of
      // sittings accounts for every one it counted.
      return rest.length === pc.n ? { ...pc, sum: rest.reduce((a, t) => a + t.total, 0) } : pc;
    }
    if (!rest.length) return null;
    const mean = (vals) => vals.reduce((a, b) => a + b, 0) / vals.length;
    const totals = rest.map((t) => t.total);
    const sum = totals.reduce((a, b) => a + b, 0);
    const score = meanHalfUp(sum, rest.length);
    const categories = {};
    for (const cat of ((rub && rub.categories) || [])) {
      const vals = rest.map((t) => (t.scores || {})[cat.key]).filter(has);
      if (vals.length) categories[cat.key] = mean(vals);
    }
    return {
      score,
      // From the score as it is shown, rounded half-up — rubric.medal's rule: a bottle that
      // reads 89.5 is Diamond, whatever the digits behind it.
      medal: rub ? medalFor(Math.round(score), rub) : pc.medal,
      n: rest.length,
      sum,                    // exact, so a card folded in on top is not added to a rounded mean
      best: Math.max(...totals),
      worst: Math.min(...totals),
      categories,
    };
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
    // A card scored on the PC has no local copy to drop, so the tombstone is also remembered
    // here — otherwise the published list would keep showing it until the PC next refreshed.
    write(K.buried, [rec.tasting_id, ...buried().filter((id) => id !== rec.tasting_id)].slice(0, 500));
    enqueue({ path, kind: "tasting", body: rec, created_at: nowIso() });
    return { rec, path };
  }

  /** Sittings this phone has deleted but the PC has not yet reconciled away. */
  const buried = () => read(K.buried, []);

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
    mode, isLocal, setMode, addLocalSpirit, updateLocalSpirit, removeLocalSpirit,
    types, canonicalType,
    noFilters, countFilters, passes, filterChoices,
    exportAll, importAll,
    queue, enqueue, drop, noteFailure, queueCount,
    cards, cardsFor, addCard, markSent, reviseCard, moveCard, deleteCard, buried,
    groupKey, groupSpirits, memberLabel, publishedCareer,
    meta, setMeta, restoreFromNative, lastWriteError: () => lastWriteError,
    submitScorecard, submitPending, submitEncounter,
    encounters, addEncounter, asSpirit,
    stamp, rand4, medalFor, meanHalfUp, today, localTime, localClock,
  };
})();

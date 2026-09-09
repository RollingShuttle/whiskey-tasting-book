/* Whiskey Tasting Book — the judging sheet and the flight/session view.
   Builds the sheet from /api/config (rubric is config-driven), scores one spirit at a time, and
   submits to the immutable journal. A standalone pour and a flight are the same thing internally:
   a list of pours with one active. SPEC.md §3, §4.1, §5, §10. */
"use strict";

const ML_PER_OZ = 29.5735;

// Category accent by Type — 3px left edge on the card (SPEC.md §5).
const TYPE_ACCENT = {
  Bourbon: "#C8952F", Rye: "#B4703A", "Single Malt": "#D9B26A", Blended: "#8E7A52",
  BourRye: "#BE8434", Rum: "#8A5A34", Tequila: "#7E8A5A", Gin: "#6E8A86",
  Cognac: "#A06A3C", Armagnac: "#A06A3C",
};
const accentFor = (t) => TYPE_ACCENT[t] || "var(--muted)";

const state = {
  config: null,
  medalColors: {},
  spirits: [],
  filter: "all",
  view: "score",        // "score" | "table" | "compare" | "analysis"
  quick: null,          // Quick Entry rows waiting on the workbook
  pending: null,        // new-bottle requests waiting for approval
  mode: "single",       // "single" — one standalone pour | "session" — a flight of pours
  session: null,        // the flight record from the server
  pours: [],            // [{spirit, scores, notes, overall, context, submitted, tasting}]
  active: 0,
};

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid?.nodeType ? kid : document.createTextNode(kid));
  }
  return n;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) throw Object.assign(new Error((body && body.error) || r.statusText),
                                 { status: r.status, body });
  return body;
}
const postJSON = (path, payload) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});

// ---------------------------------------------------------------- pours
function newPour(spirit, flightPos = null) {
  return {
    spirit,
    flight_pos: flightPos,
    scores: Object.fromEntries(state.config.rubric.categories.map((c) => [c.key, null])),
    notes: {},
    overall: "",
    context: { date: today(), venue: "", pour_price: "", pour_size_oz: "" },
    blind: false,                      // standalone-pour blind; in a flight the session's wins
    submitted: false,
    tasting: null,
    tasting_id: null,       // set once autosaved, so finishing writes a revision not a new card
  };
}
const activePour = () => state.pours[state.active] || null;
const isBlind = (p) => (state.mode === "session" ? !!state.session?.blind : !!p.blind);

// ---------------------------------------------------------------- launcher lifecycle
/* Started from the desktop icon there is no console to close, so this window *is* the app. Telling
   the launcher when it goes lets it stop the server rather than leave one running invisibly and
   holding the port. Minimising changes nothing: the heartbeat keeps going, throttled but far
   inside the launcher's patience.

   A reload fires pagehide too, so the launcher waits a few seconds before acting and any heartbeat
   cancels it. Run under `python app.py` these simply 404 and are ignored. */
function watchWindow() {
  // Quitting from the tray leaves this window on screen with nothing behind it, so the page has
  // to notice. Two missed beats is ten seconds: past a hiccup, short enough to still be useful.
  let missed = 0;
  const beat = () => fetch("/api/heartbeat", { method: "POST" })
    .then(() => { missed = 0; })
    .catch(() => { if (++missed >= 2) showStopped(); });
  beat();
  setInterval(beat, 5000);
  window.addEventListener("pagehide", () => {
    try { navigator.sendBeacon("/api/goodbye"); } catch { /* closing anyway */ }
  });
}

function showStopped() {
  if (document.getElementById("stopped")) return;
  // The launcher finds the live window by this exact title, so a dead one must stop answering
  // to it — otherwise reopening the app would raise this corpse instead of a working window.
  document.title = "Whiskey Tasting Book (closed)";
  document.body.append(el("div", { id: "stopped", class: "stopped" },
    el("div", { class: "stopped-card" },
      el("div", { class: "stopped-title" }, "The app has been closed"),
      el("div", { class: "stopped-note" },
        "You can close this window. Open it again from the Desktop or the Start menu."))));
}

// ---------------------------------------------------------------- boot
async function boot() {
  document.getElementById("btn-refresh").addEventListener("click", refresh);
  document.getElementById("btn-flight").addEventListener("click", () => {
    showView("score"); startFlightForm();
  });
  document.getElementById("nav-score").addEventListener("click", () => showView("score"));
  document.getElementById("nav-table").addEventListener("click", () => showView("table"));
  document.getElementById("nav-compare").addEventListener("click", () => showView("compare"));
  document.getElementById("nav-analysis")
    .addEventListener("click", () => showView("analysis"));
  try {
    state.config = await api("/api/config");
    state.medalColors = state.config.medal_colors || {};
  } catch (e) {
    return showStatus("err", `Could not load config: ${e.message}`);
  }
  await loadSpirits();
  await loadHealth();
  await loadQuick();
  await loadPending();
  buildPicker();
  if (!(await restoreDraft())) showPicker();
  watchWindow();
}

async function loadSpirits() {
  try {
    const data = await api("/api/spirits");
    state.spirits = data.spirits || [];
    renderResults();
  } catch (e) {
    const pill = document.getElementById("syncpill");
    pill.textContent = "no collection";
    pill.className = "pill pill-warn";
    showStatus("err", e.body?.error || e.message, e.status === 503);
  }
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    const pill = document.getElementById("syncpill");
    const t = h.stats?.tastings ?? 0;
    pill.textContent = `${state.spirits.length} spirits · ${t} tasting${t === 1 ? "" : "s"}`;
    pill.className = "pill pill-ok";
    pill.title = `Journal: ${h.journal_root}`;
  } catch { /* pill stays as-is */ }
}

async function refresh() {
  const btn = document.getElementById("btn-refresh");
  btn.disabled = true; btn.textContent = "Refreshing…";
  try {
    const r = await api("/api/refresh", { method: "POST" });
    TableView.invalidate(); CompareView.invalidate(); AnalysisView.invalidate();
    await loadSpirits(); await loadHealth(); await loadQuick(); await loadPending();
    showStatus("ok", `Collection reread from the master (read-only): ${r.count} spirits.`);
  } catch (e) {
    showStatus("err", `Refresh failed: ${e.body?.errors ? e.body.errors.join("; ") : e.message}`);
  } finally {
    btn.disabled = false; btn.textContent = "Refresh";
  }
}

// ---------------------------------------------------------------- ranking lens
/* Which categories a ranking is taken over. Aesthetics is the bottle and value is the price, so
   "which is the better whiskey" is a different question from "which was the better buy" — and on
   the full card several spirits often tie while their flavour scores separate cleanly.

   Which categories count as flavour is config, not code: `flavour: false` in config.yaml. Add a
   third non-flavour category one day and the chips below generate themselves. */
const sameKeys = (a, b) => a.length === b.length && a.every((k) => b.includes(k));

function categoryMax() {
  return Object.fromEntries(state.config.rubric.categories.map((c) => [c.key, c.max]));
}
function flavourKeys() {
  return state.config.rubric.categories.filter((c) => c.flavour).map((c) => c.key);
}
function lensMax(keys) {
  const by = categoryMax();
  return keys.reduce((a, k) => a + (by[k] || 0), 0);
}
function lensIsEverything(keys) {
  return lensMax(keys) === state.config.rubric.max_total;
}

/** The six lenses, built from config: flavour, flavour plus each non-flavour, everything, and
    each non-flavour on its own. */
function lensPresets() {
  const cats = state.config.rubric.categories;
  const flavour = cats.filter((c) => c.flavour);
  const other = cats.filter((c) => !c.flavour);
  const keysOf = (list) => list.map((c) => c.key);
  const total = (list) => list.reduce((a, c) => a + c.max, 0);

  const out = [{ id: "flavour", label: `Flavour ${total(flavour)}`, keys: keysOf(flavour) }];
  for (const c of other) {
    out.push({ id: `flavour+${c.key}`, label: `+ ${c.label.toLowerCase()} ${total(flavour) + c.max}`,
               keys: [...keysOf(flavour), c.key] });
  }
  out.push({ id: "all", label: `Everything ${total(cats)}`, keys: keysOf(cats) });
  for (const c of other) out.push({ id: c.key, label: `${c.label} ${c.max}`, keys: [c.key] });
  return out;
}

/** Sum a subset of per-category figures. `source` is exact career means or a sitting's integers;
    null when the spirit has nothing scored, so unscored rows still sink to the bottom. */
function lensScore(source, keys) {
  if (!source || !keys.length) return null;
  let sum = 0;
  for (const k of keys) {
    const v = source[k];
    if (v === null || v === undefined) return null;
    sum += v;
  }
  return Math.round(sum * 10) / 10;
}

// ---------------------------------------------------------------- pending bottles
/* The approval gate (SPEC.md §8.5). A bottle added away from the computer queues here; nothing
   reaches the 147 MB master until it is approved on the PC, one row at a time, with the parsed
   values editable first — a typo made one-handed in a liquor store should not land silently in a
   144-row inventory. The Bottle Code is assigned by the server at approval time. */
const PENDING_FIELDS = ["Distillery", "Name", "Type", "Region", "Age", "Proof", "Size (ml)",
                        "Paid"];
const PENDING_NUMERIC = new Set(["Age", "Proof", "Size (ml)", "Paid", "Release Year"]);

async function loadPending() {
  try { state.pending = await api("/api/pending"); } catch { state.pending = null; }
  renderPending();
}

function renderPending() {
  const card = document.getElementById("pending");
  const p = state.pending;
  if (!p || !p.pending || !p.pending.length || state.view !== "score") {
    card.hidden = true; return;
  }
  card.hidden = false;
  const n = p.pending.length;
  const locked = !!p.master?.locked;

  card.replaceChildren(
    el("div", { class: "quick-head" },
      el("div", { class: "quick-title-wrap" },
        el("div", { class: "quick-title" },
          `${n} new bottle${n === 1 ? "" : "s"} waiting for approval`),
        el("div", { class: "quick-sub" },
          "Added away from the computer. Check the values, then approve — that writes the row "
          + "into the collection workbook and assigns its Bottle Code. Nothing is written until "
          + "you say so.")),
      locked ? el("span", { class: "pill pill-warn" }, "Workbook open in Excel") : null),
    ...p.pending.map((rec) => pendingRow(rec, p, locked)));
}

function pendingRow(rec, info, locked) {
  const fields = { ...(rec.fields || {}) };
  const inputs = {};
  const grid = el("div", { class: "pend-grid" },
    ...PENDING_FIELDS.map((name) => {
      const input = el("input", { type: "text", value: fields[name] ?? "",
                                  inputmode: PENDING_NUMERIC.has(name) ? "decimal" : null });
      inputs[name] = input;
      return el("label", { class: "field" }, el("span", {}, name), input);
    }));

  const next = (info.next_code || {})[rec.sheet];
  const row = (info.first_empty_row || {})[rec.sheet];

  return el("div", { class: "pend-row" },
    el("div", { class: "pend-head" },
      el("span", { class: "pend-sheet" }, rec.sheet),
      el("span", { class: "pend-meta" },
        next ? `will become ${next}${row ? ` in row ${row}` : ""}` : "code assigned on approval"),
      el("span", { class: "pend-meta" }, `from ${rec.entered_from || "?"}`)),
    grid,
    el("div", { class: "pend-actions" },
      el("button", { class: "submit", type: "button", disabled: locked,
        onclick: (e) => approvePending(rec.pending_uid, inputs, e.currentTarget) },
        locked ? "Workbook open" : "Approve & write"),
      el("button", { class: "ghost", type: "button",
        onclick: () => rejectPending(rec.pending_uid) }, "Reject")));
}

function collectPending(inputs) {
  const out = {};
  for (const [name, input] of Object.entries(inputs)) {
    const raw = input.value.trim();
    if (!raw) continue;
    out[name] = (PENDING_NUMERIC.has(name) && !Number.isNaN(Number(raw))) ? Number(raw) : raw;
  }
  return out;
}

async function approvePending(uid, inputs, btn) {
  btn.disabled = true; btn.textContent = "Writing…";
  try {
    const r = await postJSON(`/api/pending/${encodeURIComponent(uid)}/approve`,
                             { fields: collectPending(inputs) });
    TableView.invalidate(); CompareView.invalidate(); AnalysisView.invalidate();
    await loadPending(); await loadSpirits(); await loadHealth();
    showStatus("ok", `Written to the collection as ${r.code}, row ${r.row}. `
      + "A backup of the workbook was taken first.");
  } catch (e) {
    showStatus("err", `Not written: ${e.body?.error || e.message}`, true);
    btn.disabled = false; btn.textContent = "Approve & write";
    await loadPending();
  }
}

async function rejectPending(uid) {
  try {
    await postJSON(`/api/pending/${encodeURIComponent(uid)}/reject`,
                   { reason: "rejected on the PC" });
    await loadPending();
    showStatus("ok", "Rejected. The request is kept in pending/rejected/, not deleted.");
  } catch (e) {
    showStatus("err", `Could not reject: ${e.body?.error || e.message}`);
  }
}

// ---------------------------------------------------------------- quick entry
/* The phone lane (SPEC.md §1.2). Rows typed into the Quick Entry sheet of the tastings workbook
   are filed as unscored drafts against their bottle; anything that cannot be matched stays on the
   sheet with a reason, because guessing would put a score on the wrong bottle. */
async function loadQuick() {
  try { state.quick = await api("/api/quickentry"); } catch { state.quick = null; }
  renderQuick();
}

function renderQuick() {
  const card = document.getElementById("quick");
  const q = state.quick;
  if (!q || !q.rows || !q.rows.length || state.view !== "score") { card.hidden = true; return; }
  card.hidden = false;
  const n = q.rows.length;
  card.replaceChildren(
    el("div", { class: "quick-head" },
      el("div", { class: "quick-title-wrap" },
        el("div", { class: "quick-title" },
          `${n} Quick Entry row${n === 1 ? "" : "s"} waiting`),
        el("div", { class: "quick-sub" },
          "Typed on the phone into the tastings workbook. Draining files each one as an "
          + "unscored draft against its bottle, ready to score properly.")),
      el("button", { class: "submit", id: "quick-drain", type: "button", onclick: drainQuick,
                     disabled: !!q.locked },
        q.locked ? "Workbook open in Excel" : "Drain")),
    el("ul", { class: "quick-list" }, ...q.rows.map((r) =>
      el("li", { class: r.problem ? "flagged" : "" },
        el("span", { class: "quick-name" }, r.display_name || "(no name typed)"),
        el("span", { class: "quick-bits" },
          [r.date, r.nose, r.palate, r.finish].filter(Boolean).join(" · ")),
        r.problem ? el("span", { class: "quick-problem" }, r.problem) : null))));
}

async function drainQuick() {
  const btn = document.getElementById("quick-drain");
  btn.disabled = true; btn.textContent = "Draining…";
  try {
    const r = await api("/api/quickentry/drain", { method: "POST" });
    TableView.invalidate(); CompareView.invalidate(); AnalysisView.invalidate();
    await loadQuick();
    await loadHealth();
    const c = r.counts;
    showStatus(c.unmatched ? "err" : "ok",
      `Drained ${c.drained} row${c.drained === 1 ? "" : "s"} as unscored draft`
      + `${c.drained === 1 ? "" : "s"}.`
      + (c.unmatched ? ` ${c.unmatched} left on the sheet — see the reason on each.` : ""),
      !!c.unmatched);
  } catch (e) {
    showStatus("err", `Drain failed: ${e.body?.error || e.message}`);
    btn.disabled = false; btn.textContent = "Drain";
  }
}

// ---------------------------------------------------------------- draft cache
/* Unsubmitted pours live only in the browser, so a reload used to lose a half-scored flight.
   Everything in progress is mirrored into localStorage and restored on the next load. Submitted
   pours and the flight itself are already safe on the server; this only covers the drafts. */
const DRAFT_KEY = "whiskey.draft.v1";
let draftTimer = null;

function draftPayload() {
  return {
    v: 1,
    saved_at: new Date().toISOString(),
    mode: state.mode,
    session: state.session,
    active: state.active,
    pours: state.pours.map((p) => ({
      spirit: p.spirit, flight_pos: p.flight_pos, scores: p.scores, notes: p.notes,
      overall: p.overall, context: p.context, blind: p.blind,
      submitted: p.submitted, tasting: p.tasting,
    })),
  };
}

function hasUnsavedWork() {
  return state.pours.some((p) => !p.submitted && (
    Object.values(p.scores).some((v) => v !== null)
    || Object.values(p.notes).some((v) => v && String(v).trim())
    || (p.overall && p.overall.trim())));
}

function saveDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    try {
      const worth = state.pours.length && (hasUnsavedWork() || state.mode === "session");
      if (worth) localStorage.setItem(DRAFT_KEY, JSON.stringify(draftPayload()));
      else localStorage.removeItem(DRAFT_KEY);
    } catch { /* private mode or quota — a lost draft must not break the app */ }
  }, 350);
  scheduleAutosave();
}

function clearDraft() {
  clearTimeout(draftTimer);
  try { localStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
}

function readDraft() {
  try {
    const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    return (d && d.v === 1 && Array.isArray(d.pours) && d.pours.length) ? d : null;
  } catch { return null; }
}

async function restoreDraft() {
  const d = readDraft();
  if (!d) return false;
  // A flight the server has never heard of is not worth restoring.
  if (d.mode === "session" && d.session?.session_id) {
    try {
      await api(`/api/session/${encodeURIComponent(d.session.session_id)}`);
    } catch {
      clearDraft();
      return false;
    }
  }
  state.mode = d.mode === "session" ? "session" : "single";
  state.session = d.session || null;
  state.pours = d.pours.map((p) => ({ ...newPour(p.spirit, p.flight_pos), ...p }));
  state.active = Math.max(0, Math.min(d.active || 0, state.pours.length - 1));
  if (state.mode === "session") renderSession();
  document.getElementById("picker").hidden = true;
  renderSheet();
  const when = new Date(d.saved_at);
  const unsent = state.pours.filter((x) => !x.submitted).length;
  showStatus("ok", el("span", {},
    `Restored ${unsent} unsubmitted pour${unsent === 1 ? "" : "s"} from `,
    Number.isNaN(when.getTime()) ? "an earlier session" : when.toLocaleTimeString(), ". ",
    el("button", { class: "link", type: "button", onclick: discardDraft }, "Discard")), true);
  return true;
}

function discardDraft() {
  clearDraft();
  state.mode = "single"; state.session = null; state.pours = []; state.active = 0;
  document.getElementById("session").hidden = true;
  hideStatus();
  showPicker();
}

// ---------------------------------------------------------------- views
function showView(v) {
  state.view = v;
  for (const [id, name] of [["nav-score", "score"], ["nav-table", "table"],
                            ["nav-compare", "compare"], ["nav-analysis", "analysis"]]) {
    document.getElementById(id).classList.toggle("active", v === name);
  }
  document.getElementById("table-view").hidden = true;
  document.getElementById("compare-view").hidden = true;
  document.getElementById("analysis-view").hidden = true;
  renderQuick();
  renderPending();

  if (v !== "score") {
    document.getElementById("session").hidden = true;
    document.getElementById("picker").hidden = true;
    document.getElementById("sheet").hidden = true;
    document.getElementById("quick").hidden = true;
    document.getElementById("pending").hidden = true;
    if (v === "table") TableView.open();
    else if (v === "compare") CompareView.open();
    else AnalysisView.open();
    return;
  }
  if (state.mode === "session" && state.session) renderSession();
  if (activePour()) {
    document.getElementById("picker").hidden = true;
    renderSheet();
  } else {
    showPicker();
  }
}

// ---------------------------------------------------------------- picker
function buildPicker() {
  document.getElementById("picker-input").addEventListener("input", renderResults);
  document.querySelectorAll(".picker-filters .chip").forEach((c) =>
    c.addEventListener("click", () => {
      state.filter = c.dataset.source;
      document.querySelectorAll(".picker-filters .chip")
        .forEach((x) => x.classList.toggle("active", x === c));
      renderResults();
    }));
}

function showPicker() {
  const picker = document.getElementById("picker");
  picker.hidden = false;
  document.getElementById("sheet").hidden = true;
  document.getElementById("picker-label").textContent =
    state.mode === "session"
      ? `Add pour ${state.pours.length + 1} to the flight`
      : "Score a spirit";
  const input = document.getElementById("picker-input");
  input.value = "";
  renderResults();
  input.focus();
}

function renderResults() {
  const ul = document.getElementById("picker-results");
  if (!ul) return;
  const q = (document.getElementById("picker-input")?.value || "").trim().toLowerCase();
  const terms = q.split(/\s+/).filter(Boolean);
  let list = state.spirits;
  if (state.filter !== "all") list = list.filter((s) => s._sheet === state.filter);
  if (terms.length) {
    list = list.filter((s) => {
      const hay = `${s.code} ${s.display_name} ${s.type || ""} ${s.region || ""}`.toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
  }
  // Every match is rendered. This used to stop at 60, which silently hid two thirds of a
  // 375-bottle collection: the list simply ended at B-60 with nothing to say it had. The count
  // below is the guard against that returning unnoticed.
  const count = document.getElementById("picker-count");
  if (count) {
    count.textContent = !state.spirits.length ? ""
      : list.length === state.spirits.length ? `${list.length} spirits`
      : `${list.length} of ${state.spirits.length}`;
  }
  ul.replaceChildren();
  if (!list.length) {
    ul.append(el("li", { class: "pr-empty" },
      state.spirits.length ? "No match." : "No spirits loaded."));
    return;
  }
  for (const s of list) {
    const meta = [s.type, s.age ? `${s.age}y` : s.age_label,
                  s.proof ? `${s.proof}pf` : null,
                  s.release_year ? String(Math.round(s.release_year)) : null]
      .filter(Boolean).join(" · ");
    ul.append(el("li", { role: "option", style: `border-left-color:${accentFor(s.type)}`,
                         onclick: () => addPour(s.code) },
      el("span", { class: "pr-name" }, s.name || s.display_name),
      el("span", { class: "pr-dist" }, s.distillery || ""),
      el("span", { class: "pr-meta" }, meta),
      el("span", { class: "pr-code" }, s.code)));
  }
}

// ---------------------------------------------------------------- flights
function startFlightForm() {
  const card = document.getElementById("session");
  card.hidden = false;
  document.getElementById("picker").hidden = true;
  document.getElementById("sheet").hidden = true;
  hideStatus();

  const f = {};
  const field = (key, label, attrs = {}) =>
    el("label", { class: "field" }, el("span", {}, label),
      el("input", Object.assign({ type: "text", oninput: (e) => { f[key] = e.target.value; } },
                                attrs)));

  card.replaceChildren(
    el("div", { class: "session-newtitle" }, "New flight"),
    el("div", { class: "context" },
      field("title", "Title", { placeholder: "e.g. Thursday barrel picks" }),
      field("date", "Date", { type: "date", value: today(),
                              oninput: (e) => { f.date = e.target.value; } }),
      field("location", "Location", { placeholder: "blank = home", autocapitalize: "words" }),
      field("company", "Company", { placeholder: "who was there" })),
    el("label", { class: "blindtoggle session-blind" },
      el("input", { type: "checkbox", onchange: (e) => { f.blind = e.target.checked; } }),
      "Blind flight — hide each spirit's identity until its card is submitted"),
    el("div", { class: "session-formactions" },
      el("button", { class: "submit", type: "button",
                     onclick: () => createFlight(f) }, "Start flight"),
      el("button", { class: "ghost", type: "button", onclick: cancelFlightForm }, "Cancel")));
}

function cancelFlightForm() {
  document.getElementById("session").hidden = true;
  state.mode = "single"; state.session = null; state.pours = []; state.active = 0;
  showPicker();
}

async function createFlight(f) {
  try {
    const r = await postJSON("/api/session", {
      title: f.title || null, date: f.date || today(),
      location: f.location || null, company: f.company || null, blind: !!f.blind,
    });
    state.mode = "session";
    state.session = r.session;
    state.pours = [];
    state.active = 0;
    saveDraft();
    renderSession();
    showPicker();
  } catch (e) {
    showStatus("err", `Could not start the flight: ${e.body?.error || e.message}`);
  }
}

async function setFlightBlind(on) {
  try {
    const r = await postJSON("/api/session", { session_id: state.session.session_id, blind: on,
      title: state.session.title, date: state.session.date,
      location: state.session.location, company: state.session.company });
    state.session = r.session;             // a new revision, never an edit
    saveDraft();
    renderSession();
    if (!document.getElementById("sheet").hidden) renderSheet();
  } catch (e) {
    showStatus("err", `Could not change blind mode: ${e.body?.error || e.message}`);
  }
}

function endFlight() {
  clearDraft();
  state.mode = "single"; state.session = null; state.pours = []; state.active = 0;
  document.getElementById("session").hidden = true;
  hideStatus();
  showPicker();
}

/** The pour switcher across the top — SPEC.md §10. */
function renderSession() {
  const card = document.getElementById("session");
  if (state.mode !== "session" || !state.session) { card.hidden = true; return; }
  card.hidden = false;
  const s = state.session;
  const meta = [s.date, s.location, s.company].filter(Boolean).join(" · ");

  const switcher = el("div", { class: "switcher" });
  state.pours.forEach((p, i) => {
    const hidden = isBlind(p) && !p.submitted;
    const label = hidden ? `Pour ${i + 1}` : (p.spirit.name || p.spirit.display_name);
    const chip = el("button", {
      class: `pour-chip${i === state.active ? " active" : ""}${p.submitted ? " done" : ""}`,
      type: "button", onclick: () => { state.active = i; renderSession(); renderSheet(); },
    }, el("span", { class: "pour-num" }, String(i + 1)), el("span", {}, label));
    if (p.submitted && p.tasting) {
      chip.append(el("span", { class: "pour-medal",
        style: `--m:${state.medalColors[p.tasting.medal] || "var(--muted)"}` }, p.tasting.total));
    }
    switcher.append(chip);
  });
  switcher.append(el("button", { class: "add-pour", type: "button", onclick: showPicker },
                     "+ Add pour"));

  const submitted = state.pours.filter((p) => p.submitted).length;
  card.replaceChildren(
    el("div", { class: "session-head" },
      el("div", { class: "session-title-wrap" },
        el("div", { class: "session-title" }, s.title || "Untitled flight"),
        el("div", { class: "session-meta" },
          meta || "—",
          el("span", { class: "dot" }, "·"),
          `${state.pours.length} pour${state.pours.length === 1 ? "" : "s"}, ${submitted} submitted`)),
      el("div", { class: "session-actions" },
        el("label", { class: "blindtoggle" },
          el("input", { type: "checkbox", checked: !!s.blind,
                        onchange: (e) => setFlightBlind(e.target.checked) }), "Blind"),
        state.pours.filter((p) => p.submitted).length >= 2
          ? el("button", { class: "ghost", type: "button",
                           onclick: () => CompareView.fromSession(s.session_id) }, "Compare pours")
          : null,
        el("button", { class: "ghost", type: "button", onclick: endFlight }, "End flight"))),
    switcher);
}

// ---------------------------------------------------------------- sheet
function addPour(code) {
  const sp = state.spirits.find((s) => s.code === code);
  if (!sp) return;
  hideStatus();
  if (state.mode === "session") {
    state.pours.push(newPour(sp, state.pours.length + 1));
    state.active = state.pours.length - 1;
    renderSession();
  } else {
    state.pours = [newPour(sp)];
    state.active = 0;
  }
  document.getElementById("picker").hidden = true;
  renderSheet();
  saveDraft();
}

function backToPicker() {
  if (state.mode === "session") {
    // leaving an unsubmitted pour drops it rather than leaving a ghost in the switcher
    const p = activePour();
    if (p && !p.submitted) {
      state.pours.splice(state.active, 1);
      state.pours.forEach((q, i) => { q.flight_pos = i + 1; });
      state.active = Math.max(0, state.pours.length - 1);
      renderSession();
    }
  }
  showPicker();
}

function renderSheet() {
  const p = activePour();
  if (!p) { showPicker(); return; }
  const sp = p.spirit;
  const sheet = document.getElementById("sheet");
  sheet.hidden = false;
  sheet.className = `card sheet${p.submitted ? " readonly" : ""}`;
  sheet.style.borderLeftColor = accentFor(sp.type);
  sheet.replaceChildren();

  const hidden = isBlind(p) && !p.submitted;
  // Everything the workbook knows, because "Double Oaked" is four different whiskies without the
  // distillery beside it, and you cannot judge what you cannot identify.
  const money = (v) => (v === null || v === undefined || v === "" ? null
    : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  const size = sp.size_ml ? `${+sp.size_ml} ml` : (sp.sizeoz ? `${+sp.sizeoz} oz` : null);
  const metaBits = [sp.code, sp.type, sp.region, sp.age ? `${sp.age} yr` : sp.age_label,
                    sp.proof ? `${sp.proof} proof` : null, sp.abv ? `${sp.abv}% ABV` : null]
                   .filter(Boolean);
  const detailBits = [
    sp.release_year ? `Released ${Math.round(sp.release_year)}` : null,
    sp.rarity, sp.status, sp.bottle_type,
    sp.entry_proof ? `entry ${sp.entry_proof} proof` : null,
    size, money(sp.paid),
    sp.quantity && +sp.quantity > 1 ? `${+sp.quantity} in stock` : null,
  ].filter(Boolean);
  // The workbook's own note about the bottle — barrel numbers, provenance, what it was finished
  // in. Not the tasting notes; those are written below, on this card.
  const fromBook = [sp.notes, sp.comment, sp.special_note]
    .map((t) => (t || "").trim()).filter(Boolean).join("\n");

  const nameEl = el("div", { class: `sheet-name${hidden ? " blind" : ""}` },
    hidden ? `Pour ${state.active + 1} — identity hidden` : (sp.name || sp.display_name),
    hidden || !sp.distillery ? null : el("span", { class: "sheet-dist" }, sp.distillery));
  const metaEl = el("div", { class: "sheet-meta" });
  if (hidden) {
    metaEl.append("Revealed when this card is submitted");
  } else {
    metaBits.forEach((b, i) => {
      if (i) metaEl.append(el("span", { class: "dot" }, "·"));
      metaEl.append(document.createTextNode(b));
    });
  }
  // Blind hides all of it: a release year and a price identify a bottle as surely as its name.
  const detailEl = !hidden && detailBits.length
    ? el("div", { class: "sheet-detail" }, detailBits.join("  ·  ")) : null;
  const bookEl = !hidden && fromBook
    ? el("div", { class: "sheet-book" },
        el("span", { class: "sheet-book-label" }, "From the collection"),
        el("div", { class: "sheet-book-text" }, fromBook))
    : null;

  const actions = el("div", { class: "sheet-actions" });
  if (state.mode === "session") {
    actions.append(el("span", { class: "pill pill-muted" }, `Pour ${state.active + 1}`));
  } else if (!p.submitted) {
    actions.append(el("label", { class: "blindtoggle" },
      el("input", { type: "checkbox", checked: p.blind,
                    onchange: (e) => { p.blind = e.target.checked; renderSheet(); } }), "Blind"));
  }
  if (!p.submitted) {
    actions.append(el("button", { class: "ghost", type: "button", onclick: backToPicker },
                       state.mode === "session" ? "Drop pour" : "Change"));
  } else if (state.mode !== "session") {
    actions.append(el("button", { class: "ghost", type: "button", onclick: backToPicker },
                       "Back"));
  }
  sheet.append(el("div", { class: "sheet-head" },
    el("div", { class: "sheet-title" }, nameEl, metaEl, detailEl), actions));
  if (bookEl) sheet.append(bookEl);

  // context row
  const ctxInput = (key, labelText, extra = {}) =>
    el("label", { class: `field field-${extra.cls || key}` },
      el("span", {}, labelText),
      el("input", Object.assign({
        type: "text", value: p.context[key], disabled: p.submitted,
        oninput: (e) => { p.context[key] = e.target.value; onScoreChange(); },
      }, extra.attrs || {})));

  sheet.append(el("div", { class: "context" },
    ctxInput("date", "Date", { attrs: { type: "date" } }),
    ctxInput("venue", "Venue", { attrs: { placeholder: "blank = home", autocapitalize: "words" } }),
    ctxInput("pour_price", "Pour $", { cls: "price", attrs: { inputmode: "decimal", placeholder: "—" } }),
    ctxInput("pour_size_oz", "Pour oz", { cls: "size", attrs: { inputmode: "decimal", placeholder: "—" } })));

  const cats = el("div", { class: "cats" });
  for (const c of state.config.rubric.categories) cats.append(buildRow(c, p));
  sheet.append(cats);

  const overall = el("textarea", {
    rows: "2", placeholder: "Overall impression, separate from the ten rows above",
    autocapitalize: "sentences", autocorrect: "on", spellcheck: "true", disabled: p.submitted,
    oninput: (e) => { p.overall = e.target.value; autoGrow(e.target); saveDraft(); },
  });
  overall.value = p.overall;
  sheet.append(el("div", { class: "overall" }, el("label", {}, "Overall notes"), overall));

  sheet.append(buildFooter(p));
  onScoreChange();
}

function buildRow(cat, p) {
  const row = document.getElementById("tpl-row").content.cloneNode(true).querySelector(".cat");
  row.dataset.key = cat.key;
  row.querySelector(".cat-name").textContent = cat.label;
  row.querySelector(".cat-max").textContent = `/ ${cat.max}`;
  row.querySelector(".hint").addEventListener("click",
    (e) => toggleHint(e.currentTarget, cat.question));

  wireStrip(row.querySelector(".strip"), row.querySelector(".cat-score"), cat, p);

  const notes = row.querySelector(".cat-notes");
  notes.value = p.notes[cat.key] || "";
  notes.disabled = p.submitted;
  notes.addEventListener("input", (e) => {
    p.notes[cat.key] = e.target.value; autoGrow(e.target); saveDraft();
  });

  if (cat.key === "value") row.append(el("div", { class: "value-hint", id: "value-hint" }, ""));
  return row;
}

// ---------------------------------------------------------------- the score control
function wireStrip(strip, scoreInput, cat, p) {
  strip.style.setProperty("--n", cat.max);
  strip.setAttribute("aria-valuemax", cat.max);
  strip.setAttribute("aria-label", cat.label);
  const showAll = cat.max <= 10;
  const segs = [];
  for (let i = 1; i <= cat.max; i++) {
    const seg = el("div", { class: "seg", "data-index": i, "data-label": i });
    segs.push(seg); strip.append(seg);
  }

  const paintSegs = () => {
    const v = p.scores[cat.key];
    segs.forEach((seg, idx) => {
      const n = idx + 1;
      seg.classList.toggle("filled", v !== null && n <= v);
      seg.classList.toggle("sel", v !== null && n === v);
      seg.classList.toggle("tick", showAll || n % 5 === 0 || n === v);
    });
    strip.setAttribute("aria-valuenow", v ?? 0);
    strip.setAttribute("aria-valuetext", v === null ? "not scored" : `${v} of ${cat.max}`);
  };
  const paint = () => {
    const v = p.scores[cat.key];
    paintSegs();
    scoreInput.value = v === null ? "" : String(v);
    scoreInput.classList.toggle("unset", v === null);
  };

  if (p.submitted) {
    scoreInput.disabled = true;
    strip.removeAttribute("tabindex");
    paint();
    return;
  }

  const set = (v) => {
    p.scores[cat.key] = Math.max(0, Math.min(cat.max, v));
    paint(); onScoreChange();
  };
  const valueFromX = (clientX) => {
    const r = strip.getBoundingClientRect();
    const frac = (clientX - r.left) / r.width;
    if (frac <= 0.02) return 0;
    return Math.max(0, Math.min(cat.max, Math.ceil(frac * cat.max)));
  };

  let dragging = false;
  strip.addEventListener("pointerdown", (e) => {
    dragging = true; strip.setPointerCapture(e.pointerId);
    set(valueFromX(e.clientX)); strip.focus();
  });
  strip.addEventListener("pointermove", (e) => { if (dragging) set(valueFromX(e.clientX)); });
  strip.addEventListener("pointerup", () => { dragging = false; });
  strip.addEventListener("pointercancel", () => { dragging = false; });
  strip.addEventListener("keydown", (e) => {
    const v = p.scores[cat.key] ?? 0;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { set(v + 1); e.preventDefault(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") { set(v - 1); e.preventDefault(); }
    else if (e.key === "Home") { set(0); e.preventDefault(); }
    else if (e.key === "End") { set(cat.max); e.preventDefault(); }
  });

  scoreInput.addEventListener("input", (e) => {
    const raw = e.target.value.trim();
    if (raw === "") { p.scores[cat.key] = null; scoreInput.classList.add("unset"); }
    else {
      const n = Number(raw);
      if (!Number.isInteger(n) || n < 0 || n > cat.max) return;   // ignore, keep the caret
      p.scores[cat.key] = n;
      scoreInput.classList.remove("unset");
    }
    paintSegs();                       // repaint the bar without clobbering the caret
    onScoreChange();
  });
  scoreInput.addEventListener("blur", paint);

  paint();
}

// ---------------------------------------------------------------- footer / live totals
function buildFooter(p) {
  const total = el("span", { class: "total-num", id: "total-num" }, "0");
  const totalBlock = el("div", { class: "total-block" },
    total, el("span", { class: "total-of" }, `/ ${state.config.rubric.max_total}`));

  const thresh = el("div", { class: "thresh", id: "thresh" },
    el("div", { class: "thresh-fill", id: "thresh-fill" }));
  for (const b of state.config.rubric.bands) {
    if (b.min <= 0 || b.min >= state.config.rubric.max_total) continue;
    thresh.append(el("div", { class: "thresh-mark",
      style: `left:${(b.min / state.config.rubric.max_total) * 100}%` }, el("span", {}, b.min)));
  }

  const foot = el("div", { class: "foot" },
    totalBlock,
    el("div", { class: "medal-dist" }, el("div", { class: "medal-line", id: "medal-line" }), thresh));

  if (p.submitted) {
    foot.append(el("span", { class: "pill pill-ok" }, "Submitted"));
    // A correction is a new revision and a deletion is a tombstone, so both are safe to offer
    // right here: nothing is edited in place and nothing is unlinked.
    foot.append(el("button", { class: "ghost", type: "button", id: "edit-card",
                               onclick: editCard }, "Edit"));
    foot.append(el("button", { class: "ghost danger", type: "button", id: "delete-card",
                               onclick: deleteCard }, "Delete"));
    // Submitting used to be the end of the road: the Change button is gone by then, so a
    // standalone card left you looking at a finished sheet with no way onward. A flight has its
    // pour switcher and "+ Add pour" right above, so it needs nothing here.
    if (state.mode !== "session") {
      foot.append(el("button", { class: "ghost", type: "button", id: "score-another",
                                 onclick: backToPicker }, "Score another"));
    }
  } else {
    foot.append(el("span", { class: "autosaved", id: "autosaved" }, ""));
    foot.append(el("button", { class: "submit", id: "submit", type: "button",
                               onclick: submitCard }, "Submit"));
  }
  return foot;
}

function medalFor(total) {
  for (const b of state.config.rubric.bands) if (total >= b.min) return b.name;  // bands high→low
  return state.config.rubric.bands[state.config.rubric.bands.length - 1].name;
}
function nextBand(total) {
  const higher = state.config.rubric.bands.filter((b) => b.min > total);
  if (!higher.length) return null;
  const t = higher.reduce((a, b) => (b.min < a.min ? b : a));
  return [t.name, t.min - total];
}

function onScoreChange() {
  const p = activePour();
  if (!p) return;
  const cats = state.config.rubric.categories;
  const scored = cats.filter((c) => p.scores[c.key] !== null);
  const complete = scored.length === cats.length;
  const total = cats.reduce((s, c) => s + (p.scores[c.key] || 0), 0);

  const totalNum = document.getElementById("total-num");
  if (totalNum) {
    totalNum.textContent = total;
    totalNum.classList.toggle("incomplete", !complete);
  }
  const fill = document.getElementById("thresh-fill");
  if (fill) fill.style.width = `${(total / state.config.rubric.max_total) * 100}%`;

  const line = document.getElementById("medal-line");
  if (line) {
    line.replaceChildren();
    if (complete) {
      const m = medalFor(total);
      line.append(el("span", { class: "medal",
        style: `--m:${state.medalColors[m] || "var(--muted)"}` }, m));
      const nb = nextBand(total);
      line.append(el("span", { class: "dist" },
        nb ? `${nb[1]} point${nb[1] === 1 ? "" : "s"} to ${nb[0]}` : "Top band"));
    } else {
      const left = cats.length - scored.length;
      line.append(el("span", { class: "dist" },
        `${left} categor${left === 1 ? "y" : "ies"} left to score`));
    }
  }
  const submit = document.getElementById("submit");
  if (submit) submit.disabled = !complete;

  updateValueHint(p);
  saveDraft();
}

function updateValueHint(p) {
  const hint = document.getElementById("value-hint");
  if (!hint) return;
  const price = parseFloat(p.context.pour_price);
  const size = parseFloat(p.context.pour_size_oz);
  if (price > 0 && size > 0) {
    hint.textContent = `This pour: $${(price / size).toFixed(2)}/oz`;
  } else if (p.spirit.value_per_oz && !isBlind(p)) {
    hint.textContent = `Owned: $${p.spirit.value_per_oz.toFixed(2)}/oz `
      + `(paid $${(p.spirit.paid ?? 0).toFixed(2)} · ${p.spirit.size_oz} oz)`;
  } else {
    hint.textContent = "";
  }
}

// ---------------------------------------------------------------- hint popover
let openHint = null;
function toggleHint(btn, text) {
  if (openHint) {
    const same = openHint._for === btn;
    openHint.remove();
    document.querySelectorAll(".hint.open").forEach((h) => h.classList.remove("open"));
    openHint = null;
    if (same) return;
  }
  const pop = el("div", { class: "hintpop" }, text);
  pop._for = btn;
  document.body.append(pop);
  const r = btn.getBoundingClientRect();
  pop.style.top = `${window.scrollY + r.bottom + 6}px`;
  pop.style.left = `${Math.min(window.scrollX + r.left,
    window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 12)}px`;
  btn.classList.add("open");
  openHint = pop;
  setTimeout(() => document.addEventListener("pointerdown", close, { once: true }), 0);
  function close(e) {
    if (e.target === btn || pop.contains(e.target)) {
      document.addEventListener("pointerdown", close, { once: true }); return;
    }
    pop.remove(); btn.classList.remove("open"); openHint = null;
  }
}

// ---------------------------------------------------------------- submit
/** The card as the server wants it. Autosave and Submit send the same thing under a different
    status, so a draft cannot quietly differ from what finishing it would have written. */
function cardBody(p, status) {
  const scores = status === "draft"
    ? Object.fromEntries(Object.entries(p.scores).filter(([, v]) => v !== null))
    : p.scores;
  const body = {
    spirit_id: p.spirit.code,
    scores,
    notes: { ...p.notes },
    overall_notes: p.overall,
    date: p.context.date || today(),
    venue: p.context.venue,
    pour_price: p.context.pour_price,
    pour_size_oz: p.context.pour_size_oz,
    blind: isBlind(p),
    include_in_average: true,
    status,
    entered_from: "desktop",
  };
  if (p.tasting_id) body.tasting_id = p.tasting_id;     // a correction, not a second sitting
  if (state.mode === "session" && state.session) {
    body.session_id = state.session.session_id;
    body.flight_pos = state.active + 1;
  }
  return body;
}

/* Autosave. The localStorage mirror lives in this browser only, so it goes with the profile; this
   puts the work in the journal itself as a draft, which is the thing that actually survives.
   A draft never counts towards a score, so half a card cannot move an average, and it carries its
   tasting_id so finishing it later writes a revision of the same card rather than a second one. */
let autosaveTimer = null;
let autosavedAs = "";

function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(autosaveNow, 6000);
}

async function autosaveNow() {
  const p = activePour();
  if (!p || p.submitted) return;
  const body = cardBody(p, "draft");
  if (!Object.keys(body.scores).length && !p.overall.trim()) return;   // nothing yet worth a file
  const fingerprint = JSON.stringify([body.scores, body.notes, body.overall_notes, body.date,
                                      body.venue, body.pour_price, body.pour_size_oz]);
  if (fingerprint === autosavedAs) return;             // unchanged; a revision would say nothing
  try {
    const r = await postJSON("/api/tasting", body);
    p.tasting_id = r.tasting.tasting_id;
    autosavedAs = fingerprint;
    showSaved();
  } catch { /* offline or refused — the localStorage draft still has it */ }
}

function showSaved() {
  const tag = document.getElementById("autosaved");
  if (tag) tag.textContent = `Draft saved ${new Date().toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit" })}`;
}

/** Put an existing sitting back on the sheet, ready to correct. It arrives already submitted and
    carrying its tasting_id, so pressing Edit and submitting writes revision n+1 of this card. */
function loadTastingIntoSheet(t) {
  const spirit = state.spirits.find((s) => s.code === t.spirit_id)
    || { code: t.spirit_id, display_name: t.spirit_id, name: t.spirit_id };
  const notes = { ...(t.notes || {}) };
  const overall = notes.overall || "";
  delete notes.overall;

  const p = newPour(spirit);
  p.scores = Object.fromEntries(state.config.rubric.categories
    .map((c) => [c.key, t.scores?.[c.key] ?? null]));
  p.notes = notes;
  p.overall = overall;
  p.context = {
    date: t.date || today(),
    venue: t.venue || "",
    pour_price: t.pour_price ?? "",
    pour_size_oz: t.pour_size_oz ?? "",
  };
  p.blind = false;                   // it has been submitted; there is nothing left to hide
  p.submitted = true;
  p.tasting = t;
  p.tasting_id = t.tasting_id;

  state.mode = "single";
  state.session = null;
  state.pours = [p];
  state.active = 0;
  autosavedAs = "";
  document.getElementById("picker").hidden = true;
  renderSheet();
  showStatus("ok", "Opened for correction. Edit, then Submit — that saves a new revision and "
                 + "leaves the earlier one in the journal.");
}

/** Reopen a submitted card. It keeps its tasting_id, so finishing it again writes revision n+1
    of the same sitting rather than a second one beside it. */
function editCard() {
  const p = activePour();
  if (!p) return;
  p.submitted = false;
  autosavedAs = "";
  renderSheet();
  showStatus("ok", "Editing this card. Submitting again saves a new revision — "
                 + "the earlier one stays in the journal.");
}

async function deleteCard() {
  const p = activePour();
  const id = p && (p.tasting_id || (p.tasting && p.tasting.tasting_id));
  if (!id) return;
  const name = p.spirit.name || p.spirit.display_name;
  if (!window.confirm(`Delete this sitting of ${name}?\n\n`
      + "It stops counting and stops being listed. The record stays in the journal as a "
      + "tombstone, so nothing is actually destroyed.")) return;
  try {
    await api(`/api/tasting/${encodeURIComponent(id)}`, { method: "DELETE" });
    TableView.invalidate(); CompareView.invalidate(); AnalysisView.invalidate();
    await loadHealth();
    state.pours.splice(state.active, 1);
    state.active = Math.max(0, state.pours.length - 1);
    autosavedAs = "";
    if (state.mode === "session") { renderSession(); }
    showStatus("ok", `Deleted the sitting of ${name}.`);
    if (state.pours.length) renderSheet(); else backToPicker();
  } catch (e) {
    showStatus("err", `Could not delete: ${e.body?.error || e.message}`);
  }
}

async function submitCard() {
  const p = activePour();
  if (!p) return;
  const submit = document.getElementById("submit");
  submit.disabled = true; submit.textContent = "Submitting…";

  const body = cardBody(p, "submitted");

  try {
    const r = await postJSON("/api/tasting", body);
    p.submitted = true;
    p.tasting = r.tasting;
    p.tasting_id = r.tasting.tasting_id;
    autosavedAs = "";
    clearTimeout(autosaveTimer);
    TableView.invalidate();            // the table must not show a stale career score
    CompareView.invalidate();
    AnalysisView.invalidate();
    saveDraft();
    await loadHealth();

    const name = p.spirit.name || p.spirit.display_name;
    if (state.mode === "session") {
      renderSession();
      renderSheet();                       // re-renders read-only, and reveals a blind identity
      showStatus("ok", el("span", {},
        `Pour ${state.active + 1} saved — ${name} scored `,
        el("strong", {}, `${r.tasting.total}`), ", ", el("strong", {}, r.tasting.medal), ". ",
        el("button", { class: "link", type: "button", onclick: showPicker }, "Add next pour")));
    } else {
      renderSheet();
      showStatus("ok", el("span", {},
        `Saved ${r.tasting.tasting_id} — ${name} scored `,
        el("strong", {}, `${r.tasting.total}`), ", ", el("strong", {}, r.tasting.medal), ". ",
        el("button", { class: "link", type: "button", onclick: () => {
          state.pours = []; state.active = 0; clearDraft(); showPicker();
        } }, "Score another")));
    }
  } catch (e) {
    showStatus("err", `Could not save: ${e.body?.error || e.message}`);
    submit.disabled = false; submit.textContent = "Submit";
  }
}

// ---------------------------------------------------------------- misc ui
function autoGrow(ta) { ta.style.height = "auto"; ta.style.height = `${ta.scrollHeight}px`; }

function showStatus(kind, content, keepOpen = false) {
  const s = document.getElementById("status");
  s.className = `statusline ${kind}`;
  s.replaceChildren(content?.nodeType ? content : document.createTextNode(content));
  s.hidden = false;
  if (kind === "ok" && !keepOpen) {
    setTimeout(() => { if (s.className.includes("ok")) s.hidden = true; }, 12000);
  }
}
function hideStatus() { const s = document.getElementById("status"); if (s) s.hidden = true; }

boot();

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
  view: "score",        // "score" | "table"
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
  };
}
const activePour = () => state.pours[state.active] || null;
const isBlind = (p) => (state.mode === "session" ? !!state.session?.blind : !!p.blind);

// ---------------------------------------------------------------- boot
async function boot() {
  document.getElementById("btn-refresh").addEventListener("click", refresh);
  document.getElementById("btn-flight").addEventListener("click", () => {
    showView("score"); startFlightForm();
  });
  document.getElementById("nav-score").addEventListener("click", () => showView("score"));
  document.getElementById("nav-table").addEventListener("click", () => showView("table"));
  try {
    state.config = await api("/api/config");
    state.medalColors = state.config.medal_colors || {};
  } catch (e) {
    return showStatus("err", `Could not load config: ${e.message}`);
  }
  await loadSpirits();
  await loadHealth();
  buildPicker();
  showPicker();
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
    TableView.invalidate();
    await loadSpirits(); await loadHealth();
    showStatus("ok", `Collection reread from the master (read-only): ${r.count} spirits.`);
  } catch (e) {
    showStatus("err", `Refresh failed: ${e.body?.errors ? e.body.errors.join("; ") : e.message}`);
  } finally {
    btn.disabled = false; btn.textContent = "Refresh";
  }
}

// ---------------------------------------------------------------- views
function showView(v) {
  state.view = v;
  document.getElementById("nav-score").classList.toggle("active", v === "score");
  document.getElementById("nav-table").classList.toggle("active", v === "table");
  const tableEl = document.getElementById("table-view");

  if (v === "table") {
    document.getElementById("session").hidden = true;
    document.getElementById("picker").hidden = true;
    document.getElementById("sheet").hidden = true;
    TableView.open();
    return;
  }
  tableEl.hidden = true;
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
  list = list.slice(0, 60);
  ul.replaceChildren();
  if (!list.length) {
    ul.append(el("li", { class: "pr-empty" },
      state.spirits.length ? "No match." : "No spirits loaded."));
    return;
  }
  for (const s of list) {
    const meta = [s.type, s.age ? `${s.age}y` : s.age_label, s.proof ? `${s.proof}pf` : null]
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
    renderSession();
    if (!document.getElementById("sheet").hidden) renderSheet();
  } catch (e) {
    showStatus("err", `Could not change blind mode: ${e.body?.error || e.message}`);
  }
}

function endFlight() {
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
  const metaBits = [sp.type, sp.region, sp.age ? `${sp.age} yr` : sp.age_label,
                    sp.proof ? `${sp.proof} proof` : null, sp.abv ? `${sp.abv}% ABV` : null]
                   .filter(Boolean);

  const nameEl = el("div", { class: `sheet-name${hidden ? " blind" : ""}` },
    hidden ? `Pour ${state.active + 1} — identity hidden` : (sp.name || sp.display_name));
  const metaEl = el("div", { class: "sheet-meta" });
  if (hidden) {
    metaEl.append("Revealed when this card is submitted");
  } else {
    metaBits.forEach((b, i) => {
      if (i) metaEl.append(el("span", { class: "dot" }, "·"));
      metaEl.append(document.createTextNode(b));
    });
  }

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
  }
  sheet.append(el("div", { class: "sheet-head" },
    el("div", { class: "sheet-title" }, nameEl, metaEl), actions));

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
    oninput: (e) => { p.overall = e.target.value; autoGrow(e.target); },
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
  notes.addEventListener("input", (e) => { p.notes[cat.key] = e.target.value; autoGrow(e.target); });

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
  } else {
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
async function submitCard() {
  const p = activePour();
  if (!p) return;
  const submit = document.getElementById("submit");
  submit.disabled = true; submit.textContent = "Submitting…";

  const notes = { ...p.notes };
  const body = {
    spirit_id: p.spirit.code,
    scores: p.scores,
    notes,
    overall_notes: p.overall,
    date: p.context.date || today(),
    venue: p.context.venue,
    pour_price: p.context.pour_price,
    pour_size_oz: p.context.pour_size_oz,
    blind: isBlind(p),
    include_in_average: true,
    status: "submitted",
    entered_from: "desktop",
  };
  if (state.mode === "session") {
    body.session_id = state.session.session_id;
    body.flight_pos = state.active + 1;
  }

  try {
    const r = await postJSON("/api/tasting", body);
    p.submitted = true;
    p.tasting = r.tasting;
    TableView.invalidate();            // the table must not show a stale career score
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
          state.pours = []; state.active = 0; showPicker();
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

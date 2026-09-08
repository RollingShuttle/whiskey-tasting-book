/* Whiskey Tasting Book — the judging sheet.
   Builds the sheet from /api/config (rubric is config-driven), scores one spirit at a time,
   and submits to the immutable journal via POST /api/tasting. SPEC.md §3, §5, §10. */
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
  selected: null,      // spirit record
  scores: {},          // key -> int | null
  notes: {},           // key -> string
  overall: "",
  blind: false,
  context: { date: today(), venue: "", pour_price: "", pour_size_oz: "" },
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
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) n.append(kid?.nodeType ? kid : document.createTextNode(kid ?? ""));
  return n;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) throw Object.assign(new Error((body && body.error) || r.statusText), { status: r.status, body });
  return body;
}

// ---------------------------------------------------------------- boot
async function boot() {
  document.getElementById("btn-refresh").addEventListener("click", refresh);
  try {
    state.config = await api("/api/config");
    state.medalColors = state.config.medal_colors || {};
  } catch (e) {
    return showStatus("err", `Could not load config: ${e.message}`);
  }
  await loadSpirits();
  await loadHealth();
  buildPicker();
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
    await loadSpirits(); await loadHealth();
    showStatus("ok", `Collection reread from the master (read-only): ${r.count} spirits.`);
  } catch (e) {
    showStatus("err", `Refresh failed: ${e.body?.errors ? e.body.errors.join("; ") : e.message}`);
  } finally {
    btn.disabled = false; btn.textContent = "Refresh";
  }
}

// ---------------------------------------------------------------- picker
function buildPicker() {
  const picker = document.getElementById("picker");
  picker.hidden = false;
  const input = document.getElementById("picker-input");
  input.addEventListener("input", renderResults);
  document.querySelectorAll(".picker-filters .chip").forEach((c) =>
    c.addEventListener("click", () => {
      state.filter = c.dataset.source;
      document.querySelectorAll(".picker-filters .chip").forEach((x) => x.classList.toggle("active", x === c));
      renderResults();
    }));
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
    ul.append(el("li", { class: "pr-empty" }, state.spirits.length ? "No match." : "No spirits loaded."));
    return;
  }
  for (const s of list) {
    const meta = [s.type, s.age ? `${s.age}y` : s.age_label, s.proof ? `${s.proof}pf` : null]
      .filter(Boolean).join(" · ");
    const li = el("li", { role: "option", style: `border-left-color:${accentFor(s.type)}`,
                          onclick: () => selectSpirit(s.code) },
      el("span", { class: "pr-name" }, s.name || s.display_name),
      el("span", { class: "pr-dist" }, s.distillery || ""),
      el("span", { class: "pr-meta" }, meta),
      el("span", { class: "pr-code" }, s.code));
    ul.append(li);
  }
}

// ---------------------------------------------------------------- sheet
function selectSpirit(code) {
  const sp = state.spirits.find((s) => s.code === code);
  if (!sp) return;
  state.selected = sp;
  state.scores = Object.fromEntries(state.config.rubric.categories.map((c) => [c.key, null]));
  state.notes = {};
  state.overall = "";
  state.blind = false;
  state.context = { date: today(), venue: "", pour_price: "", pour_size_oz: "" };
  hideStatus();
  document.getElementById("picker").hidden = true;
  renderSheet();
}

function backToPicker() {
  state.selected = null;
  document.getElementById("sheet").hidden = true;
  document.getElementById("picker").hidden = false;
  document.getElementById("picker-input").focus();
}

function renderSheet() {
  const sp = state.selected;
  const sheet = document.getElementById("sheet");
  sheet.hidden = false;
  sheet.style.borderLeftColor = accentFor(sp.type);
  sheet.replaceChildren();

  const metaBits = [sp.type, sp.region, sp.age ? `${sp.age} yr` : sp.age_label,
                    sp.proof ? `${sp.proof} proof` : null, sp.abv ? `${sp.abv}% ABV` : null]
                   .filter(Boolean);

  const nameEl = el("div", { class: "sheet-name" }, sp.name || sp.display_name);
  const metaEl = el("div", { class: "sheet-meta" });
  metaBits.forEach((b, i) => {
    if (i) metaEl.append(el("span", { class: "dot" }, "·"));
    metaEl.append(document.createTextNode(b));
  });

  const applyBlind = () => {
    nameEl.classList.toggle("blind", state.blind);
    nameEl.textContent = state.blind ? "Blind — identity hidden" : (sp.name || sp.display_name);
    metaEl.style.visibility = state.blind ? "hidden" : "visible";
  };

  const blindToggle = el("label", { class: "blindtoggle" },
    el("input", { type: "checkbox", onchange: (e) => { state.blind = e.target.checked; applyBlind(); } }),
    "Blind");

  sheet.append(el("div", { class: "sheet-head" },
    el("div", { class: "sheet-title" }, nameEl, metaEl),
    el("div", { class: "sheet-actions" },
      blindToggle,
      el("button", { class: "ghost", type: "button", onclick: backToPicker }, "Change"))));

  // context row
  const ctxInput = (key, labelText, extra = {}) =>
    el("label", { class: `field field-${extra.cls || key}` },
      el("span", {}, labelText),
      el("input", Object.assign({ type: "text", value: state.context[key],
        oninput: (e) => { state.context[key] = e.target.value; onScoreChange(); } }, extra.attrs || {})));

  sheet.append(el("div", { class: "context" },
    ctxInput("date", "Date", { attrs: { type: "date" } }),
    ctxInput("venue", "Venue", { attrs: { placeholder: "blank = home", autocapitalize: "words" } }),
    ctxInput("pour_price", "Pour $", { cls: "price", attrs: { inputmode: "decimal", placeholder: "—" } }),
    ctxInput("pour_size_oz", "Pour oz", { cls: "size", attrs: { inputmode: "decimal", placeholder: "—" } })));

  // category rows
  const cats = el("div", { class: "cats" });
  for (const c of state.config.rubric.categories) cats.append(buildRow(c));
  sheet.append(cats);

  // overall notes
  const overall = el("textarea", { rows: "2", placeholder: "Overall impression, separate from the ten rows above",
    autocapitalize: "sentences", autocorrect: "on", spellcheck: "true",
    oninput: (e) => { state.overall = e.target.value; autoGrow(e.target); } });
  sheet.append(el("div", { class: "overall" }, el("label", {}, "Overall notes"), overall));

  // footer
  sheet.append(buildFooter());
  onScoreChange();
}

function buildRow(cat) {
  const tpl = document.getElementById("tpl-row").content.cloneNode(true);
  const row = tpl.querySelector(".cat");
  row.dataset.key = cat.key;
  row.querySelector(".cat-name").textContent = cat.label;
  row.querySelector(".cat-max").textContent = `/ ${cat.max}`;

  const hint = row.querySelector(".hint");
  hint.addEventListener("click", (e) => toggleHint(e.currentTarget, cat.question));

  const strip = row.querySelector(".strip");
  const scoreInput = row.querySelector(".cat-score");
  wireStrip(strip, scoreInput, cat);

  const notes = row.querySelector(".cat-notes");
  notes.addEventListener("input", (e) => { state.notes[cat.key] = e.target.value; autoGrow(e.target); });

  if (cat.key === "value") {
    row.append(el("div", { class: "value-hint", id: "value-hint" }, ""));
  }
  return row;
}

// ---------------------------------------------------------------- the score control
function wireStrip(strip, scoreInput, cat) {
  strip.style.setProperty("--n", cat.max);
  strip.setAttribute("aria-valuemax", cat.max);
  strip.setAttribute("aria-label", cat.label);
  const segs = [];
  for (let i = 1; i <= cat.max; i++) {
    const seg = el("div", { class: "seg", "data-index": i, "data-label": i });
    segs.push(seg); strip.append(seg);
  }

  const paint = () => {
    const v = state.scores[cat.key];
    const showAll = cat.max <= 10;
    segs.forEach((seg, idx) => {
      const n = idx + 1;
      seg.classList.toggle("filled", v !== null && n <= v);
      seg.classList.toggle("sel", v !== null && n === v);
      const tick = showAll || n % 5 === 0 || n === v;
      seg.classList.toggle("tick", tick);
    });
    scoreInput.value = v === null ? "" : String(v);
    scoreInput.classList.toggle("unset", v === null);
    strip.setAttribute("aria-valuenow", v ?? 0);
    strip.setAttribute("aria-valuetext", v === null ? "not scored" : `${v} of ${cat.max}`);
  };

  const set = (v) => {
    v = Math.max(0, Math.min(cat.max, v));
    state.scores[cat.key] = v;
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
    const v = state.scores[cat.key] ?? 0;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { set(v + 1); e.preventDefault(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") { set(v - 1); e.preventDefault(); }
    else if (e.key === "Home") { set(0); e.preventDefault(); }
    else if (e.key === "End") { set(cat.max); e.preventDefault(); }
  });

  scoreInput.addEventListener("input", (e) => {
    const raw = e.target.value.trim();
    if (raw === "") { state.scores[cat.key] = null; scoreInput.classList.add("unset"); onScoreChange(); paintOthers(); return; }
    const n = Number(raw);
    if (Number.isInteger(n) && n >= 0 && n <= cat.max) { state.scores[cat.key] = n; paintOthers(); onScoreChange(); }
  });
  scoreInput.addEventListener("blur", paint);
  // repaint filled bar without clobbering the caret in the number field
  function paintOthers() {
    const v = state.scores[cat.key];
    const showAll = cat.max <= 10;
    segs.forEach((seg, idx) => {
      const n = idx + 1;
      seg.classList.toggle("filled", v !== null && n <= v);
      seg.classList.toggle("sel", v !== null && n === v);
      seg.classList.toggle("tick", showAll || n % 5 === 0 || n === v);
    });
  }

  paint();
}

// ---------------------------------------------------------------- footer / live totals
function buildFooter() {
  const total = el("span", { class: "total-num", id: "total-num" }, "0");
  const totalBlock = el("div", { class: "total-block" },
    total, el("span", { class: "total-of" }, `/ ${state.config.rubric.max_total}`));

  const thresh = el("div", { class: "thresh", id: "thresh" }, el("div", { class: "thresh-fill", id: "thresh-fill" }));
  for (const b of state.config.rubric.bands) {
    if (b.min <= 0 || b.min >= state.config.rubric.max_total) continue;
    const pct = (b.min / state.config.rubric.max_total) * 100;
    thresh.append(el("div", { class: "thresh-mark", style: `left:${pct}%` }, el("span", {}, b.min)));
  }

  const medalLine = el("div", { class: "medal-line", id: "medal-line" });
  const submit = el("button", { class: "submit", id: "submit", type: "button", onclick: submitCard }, "Submit");

  return el("div", { class: "foot" },
    totalBlock,
    el("div", { class: "medal-dist" }, medalLine, thresh),
    submit);
}

function medalFor(total) {
  for (const b of state.config.rubric.bands) if (total >= b.min) return b.name;  // bands are high→low
  return state.config.rubric.bands[state.config.rubric.bands.length - 1].name;
}
function nextBand(total) {
  const higher = state.config.rubric.bands.filter((b) => b.min > total);
  if (!higher.length) return null;
  const t = higher.reduce((a, b) => (b.min < a.min ? b : a));
  return [t.name, t.min - total];
}

function onScoreChange() {
  const cats = state.config.rubric.categories;
  const scored = cats.filter((c) => state.scores[c.key] !== null);
  const complete = scored.length === cats.length;
  const total = cats.reduce((s, c) => s + (state.scores[c.key] || 0), 0);

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
      line.append(el("span", { class: "medal", style: `--m:${state.medalColors[m] || "var(--muted)"}` }, m));
      const nb = nextBand(total);
      line.append(el("span", { class: "dist" }, nb ? `${nb[1]} point${nb[1] === 1 ? "" : "s"} to ${nb[0]}` : "Top band"));
    } else {
      line.append(el("span", { class: "dist" }, `${cats.length - scored.length} categor${cats.length - scored.length === 1 ? "y" : "ies"} left to score`));
    }
  }
  const submit = document.getElementById("submit");
  if (submit) submit.disabled = !complete;

  updateValueHint();
}

function updateValueHint() {
  const hint = document.getElementById("value-hint");
  if (!hint) return;
  const sp = state.selected;
  const price = parseFloat(state.context.pour_price);
  const size = parseFloat(state.context.pour_size_oz);
  let text = "";
  if (price > 0 && size > 0) {
    text = `This pour: $${(price / size).toFixed(2)}/oz`;
  } else if (sp.value_per_oz) {
    text = `Owned: $${sp.value_per_oz.toFixed(2)}/oz (paid $${(sp.paid ?? 0).toFixed(2)} · ${sp.size_oz} oz)`;
  }
  hint.textContent = text;
}

// ---------------------------------------------------------------- hint popover
let openHint = null;
function toggleHint(btn, text) {
  if (openHint) { openHint.remove(); const wasSame = openHint._for === btn; document.querySelectorAll(".hint.open").forEach((h) => h.classList.remove("open")); openHint = null; if (wasSame) return; }
  const pop = el("div", { class: "hintpop" }, text);
  pop._for = btn;
  document.body.append(pop);
  const r = btn.getBoundingClientRect();
  pop.style.top = `${window.scrollY + r.bottom + 6}px`;
  pop.style.left = `${Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 12)}px`;
  btn.classList.add("open");
  openHint = pop;
  setTimeout(() => document.addEventListener("pointerdown", closeHintOnce, { once: true }), 0);
  function closeHintOnce(e) {
    if (e.target === btn || pop.contains(e.target)) { document.addEventListener("pointerdown", closeHintOnce, { once: true }); return; }
    pop.remove(); btn.classList.remove("open"); openHint = null;
  }
}

// ---------------------------------------------------------------- submit
async function submitCard() {
  const submit = document.getElementById("submit");
  submit.disabled = true; submit.textContent = "Submitting…";
  const notes = { ...state.notes };
  const body = {
    spirit_id: state.selected.code,
    scores: state.scores,
    notes,
    overall_notes: state.overall,
    date: state.context.date || today(),
    venue: state.context.venue,
    pour_price: state.context.pour_price,
    pour_size_oz: state.context.pour_size_oz,
    blind: state.blind,
    include_in_average: true,
    status: "submitted",
    entered_from: "desktop",
  };
  try {
    const r = await api("/api/tasting", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const t = r.tasting;
    const name = state.selected.name || state.selected.display_name;
    showStatus("ok",
      el("span", {}, `Saved ${t.tasting_id} — ${name} scored `,
        el("strong", {}, `${t.total}`), `, `,
        el("strong", {}, t.medal), `. `,
        el("button", { class: "link", type: "button", onclick: backToPicker }, "Score another")));
    await loadHealth();
    document.getElementById("sheet").hidden = true;
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) {
    showStatus("err", `Could not save: ${e.body?.error || e.message}`);
  } finally {
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
  if (kind === "ok" && !keepOpen) setTimeout(() => { if (s.className.includes("ok")) s.hidden = true; }, 9000);
}
function hideStatus() { const s = document.getElementById("status"); if (s) s.hidden = true; }

boot();

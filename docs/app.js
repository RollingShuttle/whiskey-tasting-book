/* Whiskey Tasting Book — the iPhone client (SPEC.md §9, §9.2, §10).

   Five screens: collection, bottle detail, scorecard, add bottle, sync. Installed to the home
   screen there is no browser back button and no edge-swipe, so every pushed screen carries its
   own chevron and the tab bar hides while the keyboard is up.

   The phone never touches the collection workbook. It reads the snapshot the PC writes, and it
   writes scorecards and new-bottle requests into the app folder — queued locally first, so a bar
   basement with no signal changes nothing about scoring. */
"use strict";

const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid?.nodeType ? kid : document.createTextNode(kid));
  }
  return n;
};

/* replaceChildren turns a null into the text "null" — el() skips them, this must too. */
const fill = (host, ...kids) => host.replaceChildren(...kids.flat().filter(Boolean));

const TYPE_ACCENT = {
  Bourbon: "#C8952F", Rye: "#B4703A", "Single Malt": "#D9B26A", Blended: "#8E7A52",
  BourRye: "#BE8434", Rum: "#8A5A34", Tequila: "#7E8A5A", Gin: "#6E8A86",
  Cognac: "#A06A3C", Armagnac: "#A06A3C",
};
const accentFor = (t) => TYPE_ACCENT[t] || "#9A9086";
const MEDAL_COLORS = { Diamond: "#AFC7DE", Gold: "#C8952F", Silver: "#B4ADA2",
                       Bronze: "#B4703A", "No Medal": "#9A9086" };

const app = {
  rubric: null,
  careers: {},            // code -> {score, medal, n, categories} as last reconciled by the PC
  careersAt: "",          // when the PC worked those out, so its cards are not counted twice
  calibration: [],        // one mean per month, published by the PC (it holds the whole journal)
  stack: [],              // pushed screens, for the back chevron
  tab: "collection",
  filter: "all",
  query: "",
  draft: null,
  addMode: "pour",       // the Add tab opens on the bar pour, which is the one done standing up
};

const screens = {
  collection: document.getElementById("screen-collection"),
  detail: document.getElementById("screen-detail"),
  score: document.getElementById("screen-score"),
  add: document.getElementById("screen-add"),
  table: document.getElementById("screen-table"),
  compare: document.getElementById("screen-compare"),
  analysis: document.getElementById("screen-analysis"),
  sync: document.getElementById("screen-sync"),
};

// ---------------------------------------------------------------- navigation
function show(name, title, { push = false } = {}) {
  if (push) app.stack.push({ name: currentScreen(), title: document.getElementById("title").textContent });
  for (const [key, node] of Object.entries(screens)) node.hidden = key !== name;
  document.getElementById("title").textContent = title;
  document.getElementById("back").hidden = app.stack.length === 0;
  document.getElementById("tabs").hidden = false;
  window.scrollTo(0, 0);
}
const currentScreen = () =>
  Object.keys(screens).find((k) => !screens[k].hidden) || "collection";

function back() {
  const prev = app.stack.pop();
  if (!prev) return;
  show(prev.name, prev.title);
  if (prev.name === "collection") renderCollection();
}

const markTab = (tab) => document.querySelectorAll(".tab")
  .forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));

function setTab(tab) {
  app.tab = tab;
  app.stack = [];
  markTab(tab);
  if (tab === "collection") { renderCollection(); show("collection", "Collection"); }
  if (tab === "add") { renderAdd(); show("add", "Add"); }
  if (tab === "table") { TableView.render(screens.table); show("table", "Table"); }
  if (tab === "compare") { CompareView.render(screens.compare); show("compare", "Compare"); }
  if (tab === "analysis") { AnalysisView.render(screens.analysis); show("analysis", "Analysis"); }
  if (tab === "sync") { renderSync(); show("sync", "Sync"); }
}

/** Everything this phone can show: the collection the PC published, plus the bar pours scored
    here. The PC does not publish encounters back, so without the second half a pour would leave
    the screen the moment it was submitted. */
const knownSpirits = () => Store.spirits().concat(Store.encounters().map(Store.asSpirit));
const findSpirit = (code) => knownSpirits().find((x) => x.code === code);

// ---------------------------------------------------------------- helpers
/* The PC's figures plus this phone's own cards. The cutoff is the whole difficulty: the PC counts
   a card as soon as it has been uploaded and refreshed, and the phone keeps its copy for ever, so
   without knowing when the PC last did its sums the same sitting was added on top of itself — the
   score stayed right, being an average of a number with itself, but n climbed. careers.json now
   says when it was generated, so only cards written after that are still the phone's to add. */
const countedLocally = (code) => Store.cardsFor(code).filter((c) => {
  if (!c.include_in_average) return false;
  if (!app.careersAt) return true;                    // nothing reconciled yet; all of them count
  return String(c.created_at || "") > app.careersAt;
});

function careerFor(code) {
  const local = countedLocally(code).filter((c) => c.total !== null);
  const fromPc = app.careers[code];
  if (!local.length) return fromPc || null;
  const totals = local.map((c) => c.total);
  const pcN = fromPc?.n || 0;
  const pcSum = (fromPc?.score || 0) * pcN;
  const n = pcN + totals.length;
  const score = Math.round(((pcSum + totals.reduce((a, b) => a + b, 0)) / n) * 10) / 10;
  return { score, n, medal: app.rubric ? Store.medalFor(Math.round(score), app.rubric) : null,
           best: fromPc?.best, worst: fromPc?.worst };
}

/** Per-category means, folded the same way and for the same reason. The lens and the comparison
    both need the parts rather than the total. */
function categoryMeansFor(code) {
  const pc = app.careers[code] || {};
  const published = pc.categories && Object.keys(pc.categories).length ? pc.categories : null;
  const pcN = published ? (pc.n || 0) : 0;
  const mine = countedLocally(code).filter((c) => c.scores);
  if (!published && !mine.length) return null;

  const out = {};
  for (const cat of (app.rubric?.categories || [])) {
    const vals = mine.map((c) => c.scores[cat.key])
      .filter((v) => v !== null && v !== undefined);
    const base = published ? published[cat.key] : undefined;
    const haveBase = base !== null && base !== undefined;
    if (!vals.length && !haveBase) continue;
    const sum = (haveBase ? base * pcN : 0) + vals.reduce((a, b) => a + b, 0);
    const n = (haveBase ? pcN : 0) + vals.length;
    if (n) out[cat.key] = sum / n;
  }
  return Object.keys(out).length ? out : null;
}

/* ---------------------------------------------------------------- ranking lens
   Which categories a ranking is taken over. Aesthetics is the bottle and value is the price, so
   "which is the better whiskey" is a different question from "which was the better buy" — and on
   the full card several spirits often tie where their flavour scores separate cleanly.

   Which categories count as flavour is config, not code (`flavour: false` in the rubric), so a
   third non-flavour category one day would generate its own chips. */
const sameKeys = (a, b) => a.length === b.length && a.every((k) => b.includes(k));
const flavourKeys = () => (app.rubric?.categories || []).filter((c) => c.flavour).map((c) => c.key);

function lensMax(keys) {
  const by = Object.fromEntries((app.rubric?.categories || []).map((c) => [c.key, c.max]));
  return keys.reduce((a, k) => a + (by[k] || 0), 0);
}
const lensIsEverything = (keys) => lensMax(keys) === (app.rubric?.max_total ?? -1);

/** Flavour, flavour plus each non-flavour, everything, and each non-flavour on its own. */
function lensPresets() {
  const cats = app.rubric?.categories || [];
  const flavour = cats.filter((c) => c.flavour);
  const other = cats.filter((c) => !c.flavour);
  const keysOf = (l) => l.map((c) => c.key);
  const total = (l) => l.reduce((a, c) => a + c.max, 0);

  const out = [{ id: "flavour", label: `Flavour ${total(flavour)}`, keys: keysOf(flavour) }];
  for (const c of other) {
    out.push({ id: `flavour+${c.key}`,
               label: `+ ${(c.label || c.key).toLowerCase()} ${total(flavour) + c.max}`,
               keys: [...keysOf(flavour), c.key] });
  }
  out.push({ id: "all", label: `Everything ${total(cats)}`, keys: keysOf(cats) });
  for (const c of other) out.push({ id: c.key, label: `${c.label || c.key} ${c.max}`, keys: [c.key] });
  return out;
}

/** Sum a subset of per-category figures. Null when anything in the lens is unscored, so those
    rows still sink rather than posing as a low score. */
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

function scoreCell(career) {
  if (!career) return el("div", { class: "row-score" }, el("span", { class: "muted" }, "—"));
  return el("div", { class: "row-score" },
    el("b", {}, career.score.toFixed(1)),
    career.medal ? el("div", {}, el("span", {
      class: "medal", style: `--m:${MEDAL_COLORS[career.medal] || "#9A9086"}` }, career.medal)) : null);
}

function notice(kind, text) { return el("div", { class: `notice ${kind}` }, text); }

// ---------------------------------------------------------------- collection
function renderCollection() {
  const host = screens.collection;
  const spirits = Store.spirits();
  const search = el("input", {
    class: "search", type: "text", value: app.query, enterkeyhint: "search",
    placeholder: "Search name, distillery or code", "aria-label": "Search spirits",
    autocapitalize: "none", autocorrect: "off", spellcheck: "false",
    oninput: (e) => { app.query = e.target.value; paintRows(); },
  });

  const chips = el("div", { class: "chips" },
    ...[["all", "All"], ["instock", "In stock"], ["Bottle", "Bottles"],
        ["Miniature", "Minis"], ["Sample", "Samples"], ["Encounter", "Bar"],
        ["scored", "Scored"]].map(([id, label]) =>
      el("button", { type: "button", class: `chip${app.filter === id ? " active" : ""}`,
        onclick: () => { app.filter = id; renderCollection(); } }, label)));

  const list = el("ul", { class: "rows", id: "rows" });
  fill(host,
    spirits.length ? null : notice("warn",
      "No collection yet. Open Sync and refresh once while you have a connection."),
    search, chips, list);
  paintRows();
}

function paintRows() {
  const list = document.getElementById("rows");
  if (!list) return;
  const terms = app.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let spirits = knownSpirits();
  if (app.filter === "scored") spirits = spirits.filter((s) => careerFor(s.code));
  else if (app.filter === "instock") spirits = spirits.filter((s) => s.owned !== false);
  else if (app.filter !== "all") spirits = spirits.filter((s) => s._sheet === app.filter);
  if (terms.length) {
    spirits = spirits.filter((s) => {
      // The parentheses matter: `a + b.toLowerCase()` lowercases only b, so every name and
      // distillery kept its capitals while the search terms did not, and nothing matched.
      const hay = (`${s.code} ${s.display_name || ""} ${s.distillery || ""} `
        + `${s.name || ""} ${s.type || ""}`).toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
  }

  list.replaceChildren();
  if (!spirits.length) {
    list.append(el("li", { class: "empty" }, "Nothing matches."));
    return;
  }
  for (const s of spirits) {
    const meta = [s.type, s.age ? `${s.age} yr` : s.age_label,
                  s.proof ? `${s.proof} pf` : null,
                  s.release_year ? String(Math.round(s.release_year)) : null]
      .filter(Boolean).join(" · ");
    const gone = s.owned === false && s.status;
    list.append(el("li", {
      class: `row${s.owned === false ? " gone" : ""}`,
      style: `border-left-color:${accentFor(s.type)}`,
      onclick: () => openDetail(s.code),
    },
      el("div", { class: "row-main" },
        el("div", { class: "row-name" },
          s.name || s.display_name || s.code,
          gone ? el("span", { class: "tag" }, s.status) : null),
        el("div", { class: "row-sub" }, [s.distillery, meta].filter(Boolean).join(" · "))),
      scoreCell(careerFor(s.code))));
  }
}

// ---------------------------------------------------------------- detail
/** Every sitting of this spirit the phone can act on: the ones it scored itself and the ones the
    PC published. Listing only its own meant a collection scored at a desk showed none at all, and
    nothing could be corrected or removed from here. Local copies win — they are the newer of the
    two whenever the phone has scored since the PC last did its sums. */
function sittingsFor(code) {
  const mine = Store.cardsFor(code);
  const seen = new Set(mine.map((c) => c.tasting_id));
  const gone = new Set(Store.buried());
  const published = ((Store.careers().sittings || {})[code] || [])
    .filter((t) => !gone.has(t.tasting_id));
  return mine.concat(published.filter((t) => !seen.has(t.tasting_id)))
    .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
}

/** How many of this spirit's sittings have been deleted here but not yet reconciled away. */
function buriedHere(code) {
  const gone = new Set(Store.buried());
  if (!gone.size) return 0;
  return ((Store.careers().sittings || {})[code] || []).filter((t) => gone.has(t.tasting_id)).length;
}

function openDetail(code) {
  const s = findSpirit(code);
  if (!s) return;
  const career = careerFor(code);
  const sittings = sittingsFor(code);
  const meta = [s.type, s.region, s.age ? `${s.age} yr` : s.age_label,
                s.proof ? `${s.proof} proof` : null,
                s.release_year ? `released ${Math.round(s.release_year)}` : null]
    .filter(Boolean).join(" · ");

  fill(screens.detail,
    el("div", { class: "card", style: `border-left:3px solid ${accentFor(s.type)}` },
      el("div", { class: "spirit-name" }, s.name || s.display_name),
      el("div", { class: "spirit-meta" }, [s.distillery, meta].filter(Boolean).join(" · ")),
      el("div", { class: "spirit-meta" },
        s._encounter ? ["Bar pour", s.venue].filter(Boolean).join(" · ") : s.code),
      career
        ? el("div", { style: "margin-top:12px;display:flex;align-items:center;gap:10px" },
            el("span", { class: "total-num" }, career.score.toFixed(1)),
            el("span", { class: "total-of" }, `/ 100 · n=${career.n}`),
            career.medal ? el("span", { class: "medal",
              style: `--m:${MEDAL_COLORS[career.medal] || "#9A9086"}` }, career.medal) : null)
        : el("div", { class: "muted", style: "margin-top:10px" }, "Not scored yet."),
      // The score above is the PC's, and it still counts anything deleted here until the PC has
      // read the tombstone. Saying so beats looking like the deletion did not take.
      buriedHere(code)
        ? el("div", { class: "muted", style: "margin-top:8px" },
            `${buriedHere(code)} deleted sitting${buriedHere(code) === 1 ? "" : "s"} still counted `
            + "above — the score updates once the PC has read it.")
        : null),

    el("button", { class: "btn wide", type: "button", onclick: () => openScore(code) },
      "Score a pour"),

    sittings.length
      ? el("div", { class: "card", style: "margin-top:12px" },
          el("div", { class: "muted", style: "margin-bottom:8px" },
            `${sittings.length} sitting${sittings.length === 1 ? "" : "s"}`),
          ...sittings.map((c) => el("div", { class: "qrow sitting" },
            el("span", {}, c.date),
            el("span", { class: "when" },
              `${c.total}${c._path && !c._sent ? " · waiting" : ""}`),
            el("button", { class: "linkish", type: "button",
                           onclick: () => editSitting(c) }, "Edit"),
            el("button", { class: "linkish danger", type: "button",
                           onclick: () => removeSitting(c, s) }, "Delete"))))
      : null);

  show("detail", s.name || s.code, { push: true });
}

/** Reopen a sitting to correct it. Submitting writes revision n+1 of the same card, never a
    second sitting beside it, so an average cannot be doubled by fixing a typo. */
function editSitting(card) {
  const s = findSpirit(card.spirit_id);
  if (!s || !app.rubric) return;
  app.draft = {
    spirit: s,
    revising: card,
    scores: Object.fromEntries(app.rubric.categories.map((c) => [c.key, card.scores?.[c.key] ?? null])),
    notes: { ...(card.notes || {}) },
    overall: (card.notes && card.notes.overall) || "",
    date: card.date || Store.today(),
    venue: card.venue || "",
  };
  delete app.draft.notes.overall;
  renderScore();
  show("score", "Edit sitting", { push: true });
}

function removeSitting(card, spirit) {
  // Works for a card scored on the PC as well: the tombstone is a file named after the tasting
  // and its next revision, and the phone has both from the published list.
  const name = spirit.name || spirit.display_name || card.spirit_id;
  if (!window.confirm(`Delete this sitting of ${name}?\n\nIt stops counting. The record stays `
      + "in the journal as a tombstone, so nothing is actually destroyed.")) return;
  Store.deleteCard(card);
  updateBadge();
  flush();
  openDetail(card.spirit_id);
}

// ---------------------------------------------------------------- scorecard
function openScore(code) {
  const s = findSpirit(code);
  if (s) openScoreFor(s, s.venue || "");
}

/** Takes the spirit itself rather than a code, because a bar pour has no code yet — the X- number
    is handed out on the PC afterwards, and until then the encounter's uid is its only name. */
function openScoreFor(s, venue = "") {
  if (!s || !app.rubric) return;
  app.draft = {
    spirit: s,
    scores: Object.fromEntries(app.rubric.categories.map((c) => [c.key, null])),
    notes: {},
    overall: "",
    date: Store.today(),
    venue,
  };
  renderScore();
  show("score", "Scorecard", { push: true });
}

function renderScore() {
  const d = app.draft;
  const host = screens.score;
  const cats = el("div", { class: "card" });

  for (const c of app.rubric.categories) {
    const val = el("span", { class: "cat-val unset" }, "–");
    const strip = el("div", {
      class: "strip", role: "slider", tabindex: "0",
      style: `grid-template-columns:repeat(${c.max},1fr)`,
      "aria-label": c.label, "aria-valuemin": "0", "aria-valuemax": String(c.max),
    });
    const segs = [];
    for (let i = 1; i <= c.max; i++) {
      const seg = el("div", { class: "seg", "data-label": String(i) });
      segs.push(seg); strip.append(seg);
    }
    const paint = () => {
      const v = d.scores[c.key];
      const showAll = c.max <= 10;
      segs.forEach((seg, i) => {
        const n = i + 1;
        seg.classList.toggle("filled", v !== null && n <= v);
        seg.classList.toggle("sel", v !== null && n === v);
        seg.classList.toggle("tick", showAll || n % 5 === 0 || n === v);
      });
      val.textContent = v === null ? "–" : String(v);
      val.classList.toggle("unset", v === null);
      strip.setAttribute("aria-valuenow", String(v ?? 0));
      updateTotal();
    };
    // Ten segments across a 390 pt screen is ~33 pt each, under the 44 pt guideline, so a tap is
    // backed by a drag: press anywhere and slide to the value (SPEC.md §9.2).
    const from = (clientX) => {
      const r = strip.getBoundingClientRect();
      const frac = (clientX - r.left) / r.width;
      if (frac <= 0.02) return 0;
      return Math.max(0, Math.min(c.max, Math.ceil(frac * c.max)));
    };
    let dragging = false;
    strip.addEventListener("pointerdown", (e) => {
      dragging = true; strip.setPointerCapture(e.pointerId);
      d.scores[c.key] = from(e.clientX); paint();
    });
    strip.addEventListener("pointermove", (e) => {
      if (dragging) { d.scores[c.key] = from(e.clientX); paint(); }
    });
    strip.addEventListener("pointerup", () => { dragging = false; });
    strip.addEventListener("pointercancel", () => { dragging = false; });

    const notes = el("textarea", {
      rows: "1", placeholder: "notes", autocapitalize: "sentences", autocorrect: "on",
      spellcheck: "true",
      oninput: (e) => { d.notes[c.key] = e.target.value; grow(e.target); },
    });

    cats.append(el("div", { class: "cat" },
      el("div", { class: "cat-head" },
        el("span", { class: "cat-name" }, c.label),
        el("span", { class: "cat-max" }, `/ ${c.max}`),
        val),
      strip, notes));
    paint();
  }

  const overall = el("textarea", {
    rows: "2", placeholder: "Overall impression",
    autocapitalize: "sentences", autocorrect: "on", spellcheck: "true",
    oninput: (e) => { d.overall = e.target.value; grow(e.target); },
  });

  const submit = el("button", { class: "btn", id: "submit", type: "button", disabled: true,
                                onclick: submitCard }, "Submit");

  fill(host,
    el("div", { class: "card", style: `border-left:3px solid ${accentFor(d.spirit.type)}` },
      el("div", { class: "spirit-name" }, d.spirit.name || d.spirit.display_name),
      el("div", { class: "spirit-meta" }, [d.spirit.distillery, d.spirit.type]
        .filter(Boolean).join(" · ")),
      el("label", { class: "field" }, el("span", {}, "Venue"),
        el("input", { type: "text", value: "", placeholder: "blank = home",
                      autocapitalize: "words",
                      oninput: (e) => { d.venue = e.target.value; } }))),
    cats,
    el("div", { class: "card" }, el("label", { class: "field" },
      el("span", {}, "Overall notes"), overall)),
    el("div", { class: "total-bar" },
      el("span", { class: "total-num incomplete", id: "total" }, "0"),
      el("span", { class: "total-of", id: "total-of" },
        `/ ${app.rubric.max_total}`),
      el("span", { style: "margin-left:auto" }, submit)));
  updateTotal();
}

function updateTotal() {
  const d = app.draft;
  if (!d) return;
  const node = document.getElementById("total");
  const submit = document.getElementById("submit");
  if (!node) return;
  const cats = app.rubric.categories;
  const scored = cats.filter((c) => d.scores[c.key] !== null).length;
  const total = cats.reduce((a, c) => a + (d.scores[c.key] || 0), 0);
  node.textContent = String(total);
  node.classList.toggle("incomplete", scored !== cats.length);
  const of = document.getElementById("total-of");
  if (of) {
    of.textContent = scored === cats.length
      ? `/ ${app.rubric.max_total} · ${Store.medalFor(total, app.rubric)}`
      : `/ ${app.rubric.max_total} · ${cats.length - scored} left`;
  }
  if (submit) submit.disabled = scored !== cats.length;
}

function submitCard() {
  const d = app.draft;
  const notes = { ...d.notes };
  if (d.overall.trim()) notes.overall = d.overall.trim();
  const { rec } = d.revising
    ? Store.reviseCard(d.revising, { scores: d.scores, notes, date: d.date,
                                     venue: d.venue.trim() || null })
    : Store.submitScorecard({
      spirit_id: d.spirit.code, scores: d.scores, notes,
      date: d.date, venue: d.venue.trim() || null,
    });
  app.draft = null;
  updateBadge();
  flush();
  app.stack.pop();                                   // straight back to the bottle
  app.stack = [{ name: "collection", title: "Collection" }];
  app.tab = "collection";
  markTab("collection");
  openDetail(rec.spirit_id);
}

const grow = (ta) => { ta.style.height = "auto"; ta.style.height = `${ta.scrollHeight}px`; };

// ---------------------------------------------------------------- add bottle
function renderAdd() {
  const pour = app.addMode === "pour";
  const fields = {};
  const input = (name, attrs = {}) => {
    const i = el("input", Object.assign({ type: "text" }, attrs));
    i.addEventListener("input", (e) => { fields[name] = e.target.value; });
    return el("label", { class: "field" }, el("span", {}, name), i);
  };
  const named = () => (fields.Name || "").trim() || (fields.Distillery || "").trim();

  const modes = el("div", { class: "chips" },
    ...[["pour", "Bar pour"], ["bottle", "Bottle I bought"]].map(([id, label]) =>
      el("button", { type: "button", class: `chip${app.addMode === id ? " active" : ""}`,
        onclick: () => { app.addMode = id; renderAdd(); } }, label)));

  fill(screens.add,
    modes,
    notice(pour ? "ok" : "ok", pour
      ? "Something you are drinking but do not own. Score it now — it is saved on this phone "
        + "first, so no signal is needed — and it lands on the PC as a bar pour, never in your "
        + "collection."
      : "Queued for approval on the PC. Nothing reaches the collection workbook from this phone "
        + "— a person checks each row first."),
    el("div", { class: "card" },
      input("Distillery", { autocapitalize: "words" }),
      input("Name", { autocapitalize: "words" }),
      input("Type", { autocapitalize: "words" }),
      input("Region", { autocapitalize: "words" }),
      input("Age", { inputmode: "numeric" }),
      input("Proof", { inputmode: "decimal" }),
      pour ? input("Venue", { autocapitalize: "words" }) : input("Size (ml)", { inputmode: "decimal" }),
      pour ? null : input("Paid", { inputmode: "decimal" }),
      el("div", { class: "actions" },
        el("button", { class: "btn wide", type: "button", onclick: () => {
          if (!named()) return;
          if (pour) { startBarPour(fields); return; }
          const filled = Object.fromEntries(
            Object.entries(fields).filter(([, v]) => v && String(v).trim()));
          Store.submitPending("Bottle", filled);
          updateBadge();
          flush();
          setTab("sync");
        } }, pour ? "Score this pour" : "Queue for approval"))));
}

/** A pour at a bar: the encounter is recorded, then its scorecard opens straight away. Both are
    queued separately, and the card names the encounter by uid — the PC hands out the X- number
    later and joins the two when it reads. */
function startBarPour(f) {
  const trimmed = (k) => ((f[k] || "").trim() || null);
  // Age and proof go in as numbers. A text field hands back a string, and a string sorts as text
  // wherever the PC lists it — "9" after "18" — which is the sort of thing nobody notices until
  // the table looks wrong months later.
  const num = (k) => {
    const v = trimmed(k);
    return v !== null && v !== "" && Number.isFinite(Number(v)) ? Number(v) : v;
  };
  const { spirit } = Store.submitEncounter({
    name: trimmed("Name") || trimmed("Distillery"),
    distillery: trimmed("Distillery"),
    type: trimmed("Type"),
    region: trimmed("Region"),
    age: num("Age"),
    proof: num("Proof"),
    venue: trimmed("Venue"),
  });
  updateBadge();
  flush();
  openScoreFor(spirit, spirit.venue || "");
}

// ---------------------------------------------------------------- sync
function renderSync() {
  const online = navigator.onLine;
  const q = Store.queue();
  const m = Store.meta();
  const signedIn = Graph.isSignedIn();

  const rows = q.length
    ? q.map((i) => el("div", { class: "qrow" },
        el("span", { class: "kind" }, i.kind),
        el("span", {}, i.path.split("/").pop().slice(0, 24)),
        el("span", { class: "when" }, (i.created_at || "").slice(11, 16))))
    : [el("div", { class: "muted" }, "Nothing waiting.")];

  fill(screens.sync,
    !Graph.configured()
      ? notice("warn", "Sign-in is not configured yet. Add the Application (client) ID from "
          + "SETUP.md Part 3 to config.js and redeploy. Scoring works without it — cards queue "
          + "on this phone until there is somewhere to send them.")
      : null,

    el("div", { class: "card" },
      el("div", { class: "state" },
        el("span", { class: `dot ${online ? "on" : "off"}` }),
        el("span", {}, online ? "Online" : `Offline${m.offlineSince
          ? ` since ${Store.localClock(m.offlineSince)}` : ""}`)),
      el("div", { class: "state" },
        el("span", { class: `dot ${signedIn ? "on" : ""}` }),
        el("span", {}, signedIn ? `Signed in as ${Graph.who()}` : "Not signed in")),
      el("div", { class: "muted" },
        m.lastSync ? `Last sync ${Store.localTime(m.lastSync)}`
                   : "Never synced on this phone"),
      el("div", { class: "actions" },
        Graph.configured() && !signedIn
          ? el("button", { class: "btn", type: "button", onclick: () => Graph.signIn() }, "Sign in")
          : null,
        Graph.configured() && signedIn
          ? el("button", { class: "btn", type: "button", onclick: refresh }, "Refresh")
          : null,
        signedIn
          ? el("button", { class: "btn ghost", type: "button", onclick: () => Graph.signOut() },
              "Sign out")
          : null)),

    el("div", { class: "card" },
      el("div", { class: "muted", style: "margin-bottom:8px" },
        `${q.length} waiting to upload`),
      ...rows,
      q.length ? el("div", { class: "actions" },
        el("button", { class: "btn wide", type: "button", disabled: !online || !signedIn,
                       onclick: flush }, "Upload now")) : null));
}

// ---------------------------------------------------------------- sync engine
async function refresh() {
  try {
    const snap = await Graph.getJSON("snapshot/collection.json");
    if (snap) Store.setSnapshot(snap);
    const careers = await Graph.getJSON("snapshot/careers.json");
    if (careers) {
      Store.setCareers(careers.careers ? careers : { careers });
      app.careers = Store.careers().careers;
      app.calibration = Store.careers().calibration;
      app.careersAt = Store.careers().generatedAt;
    }
    const rub = await Graph.getJSON("snapshot/rubric.json");
    if (rub) { Store.setRubric(rub); app.rubric = rub; }
    Store.setMeta({ lastSync: new Date().toISOString() });
  } catch (e) {
    Store.setMeta({ lastError: String(e.message || e) });
  }
  renderSync();
  if (app.tab === "collection") renderCollection();
  if (app.tab === "table") TableView.render(screens.table);
  if (app.tab === "analysis") AnalysisView.render(screens.analysis);
  if (app.tab === "compare") CompareView.render(screens.compare);
}

/** Walk the queue one item at a time. Uploads are idempotent, so a failure just leaves the item
    in place for the next attempt. */
async function flush() {
  if (!navigator.onLine || !Graph.isSignedIn()) return;
  for (const item of Store.queue()) {
    try {
      await Graph.putJSON(item.path, item.body);
      Store.drop(item.path);
      if (item.kind === "tasting") Store.markSent(item.path);
    } catch {
      break;                                          // stop on the first failure; try again later
    }
  }
  Store.setMeta({ lastSync: new Date().toISOString() });
  updateBadge();
  if (currentScreen() === "sync") renderSync();
}

function updateBadge() {
  const n = Store.queueCount();
  const badge = document.getElementById("tab-badge");
  badge.textContent = String(n);
  badge.hidden = n === 0;
}

function updateNet() {
  const net = document.getElementById("net");
  const online = navigator.onLine;
  net.textContent = online ? "" : "offline";
  net.classList.toggle("off", !online);
  if (!online && !Store.meta().offlineSince) {
    Store.setMeta({ offlineSince: new Date().toISOString() });
  }
  if (online) Store.setMeta({ offlineSince: null });
}

// ---------------------------------------------------------------- boot
async function boot() {
  document.getElementById("back").addEventListener("click", back);
  document.querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => setTab(b.dataset.tab)));

  // When the keyboard opens it covers the lower 40% of the screen; hiding the tab bar keeps
  // Submit reachable (SPEC.md §9.2).
  document.addEventListener("focusin", (e) => {
    if (e.target.matches("input, textarea")) {
      document.body.classList.add("keyboard");
      setTimeout(() => e.target.scrollIntoView({ block: "center", behavior: "smooth" }), 150);
    }
  });
  document.addEventListener("focusout", () => document.body.classList.remove("keyboard"));

  window.addEventListener("online", () => { updateNet(); flush(); });
  window.addEventListener("offline", updateNet);

  const kept = Store.careers();
  app.careers = kept.careers;
  app.calibration = kept.calibration;
  app.careersAt = kept.generatedAt;
  app.rubric = Store.rubric();
  if (!app.rubric) {
    try {
      app.rubric = await (await fetch("rubric.json")).json();
      Store.setRubric(app.rubric);
    } catch { /* offline first run — the sync screen explains */ }
  }

  updateNet();
  updateBadge();
  setTab("collection");

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => { /* http:// dev, or unsupported */ });
  }

  if (await Graph.resume()) { await refresh(); flush(); }
}

boot();

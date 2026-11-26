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

/** A chip that opens the phone's own picker. What is seen is the chip and its `label`; what is
    tapped is a select laid invisibly over it, kept at 16 px so Safari does not zoom to it.
    `options` are [value, text] pairs. One of these does the work of a row of chips. */
function pickerChip(label, options, value, onPick, name) {
  const select = el("select", { "aria-label": name },
    ...options.map(([v, text]) => el("option", { value: v }, text)));
  select.value = value;
  select.addEventListener("change", () => onPick(select.value));
  return el("label", { class: "chip pick" }, el("span", {}, label), select);
}

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
  filters: Store.noFilters(),   // what the collection is narrowed by; see Store.passes
  filterOpen: false,      // the panel of choices under the search box
  query: "",
  view: "list",           // the collection as a list or as a table; remembered in meta
  listSort: "",           // the list's order: "" is the shelf's, as the collection arrives
  openGroups: new Set(),  // the same-named bottles whose row is opened out (Store.groupKey)
  draft: null,
  editCode: null,         // the phone-added bottle the Add form is editing, if any
  detailCode: null,       // the spirit the detail screen shows, so a sync can redraw it in place
  addMode: "pour",       // the Add tab opens on the bar pour, which is the one done standing up
};

const screens = {
  collection: document.getElementById("screen-collection"),
  detail: document.getElementById("screen-detail"),
  sitting: document.getElementById("screen-sitting"),
  move: document.getElementById("screen-move"),
  score: document.getElementById("screen-score"),
  add: document.getElementById("screen-add"),
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
  if (tab === "add") { app.editCode = null; renderAdd(); show("add", "Add"); }
  if (tab === "compare") { CompareView.render(screens.compare); show("compare", "Compare"); }
  if (tab === "analysis") {
    // With no PC to publish the monthly means, they are worked out from this phone's own cards.
    if (Store.isLocal() && !(Store.careers().calibration || []).length) app.calibration = localCalibration();
    AnalysisView.render(screens.analysis);
    show("analysis", "Analysis");
  }
  if (tab === "sync") { renderSync(); show("sync", Store.isLocal() ? "Data" : "Sync"); }
}

/** Everything this phone can show: the collection the PC published, plus the bar pours scored
    here. The PC does not publish encounters back, so without the second half a pour would leave
    the screen the moment it was submitted. */
const knownSpirits = () => Store.spirits().concat(Store.encounters().map(Store.asSpirit));
const findSpirit = (code) => knownSpirits().find((x) => x.code === code);
/** Marked Finished or Removed in the workbook: a row still, a bottle no longer. Read from the
    status and not from `owned` alone — a bar pour is not owned either, and is scored all the time. */
const goneSpirit = (s) => s.owned === false && !!s.status;

// ---------------------------------------------------------------- helpers
/* The PC's figures plus this phone's own cards. The cutoff is the whole difficulty: the PC counts
   a card as soon as it has been uploaded and refreshed, and the phone keeps its copy for ever, so
   without knowing when the PC last did its sums the same sitting was added on top of itself — the
   score stayed right, being an average of a number with itself, but n climbed. careers.json now
   says when it was generated, so only cards written after that are still the phone's to add. */
const countedLocally = (code) => {
  const listed = (Store.careers().sittings || {})[code];
  const published = new Set((listed || []).map((t) => t.tasting_id));
  return Store.cardsFor(code).filter((c) => {
    if (!c.include_in_average) return false;
    if (!app.careersAt) return true;                  // nothing reconciled yet; all of them count
    // A card moved here from another bottle is this bottle's to add until the PC lists it here,
    // whenever it was written: the clock cannot say whether the PC had read the move yet.
    if (c.moved_from && listed) return !published.has(c.tasting_id);
    return String(c.created_at || "") > app.careersAt;
  });
};

/** The PC's figure for a spirit, without the sittings deleted here or moved to another bottle —
    so the bottle a card has just left stops showing its score there and then. */
const publishedFor = (code) => Store.publishedCareer(code, app.rubric);

function careerFor(code) {
  const local = countedLocally(code).filter((c) => c.total !== null);
  const fromPc = publishedFor(code);
  if (!local.length) return fromPc || null;
  const mine = local.reduce((a, c) => a + c.total, 0);
  const pcN = fromPc?.n || 0;
  const n = pcN + local.length;
  // The PC's exact sum where its list of sittings gives one; its rounded mean times n where an
  // older PC published no list. Either way the score is rounded half-up, as the PC rounds it.
  const exact = !fromPc || Number.isFinite(fromPc.sum);
  const score = exact ? Store.meanHalfUp((fromPc?.sum || 0) + mine, n)
                      : halfUp1((fromPc.score * pcN + mine) / n);
  return { score, n, medal: app.rubric ? Store.medalFor(Math.round(score), app.rubric) : null,
           best: fromPc?.best, worst: fromPc?.worst };
}

/** To one decimal, half-up, for a figure that is not a sum of whole numbers. The noise the
    arithmetic leaves in the last place is rounded away three places down first. */
const halfUp1 = (v) => Math.round(Math.round(v * 1000) / 100) / 10;

/** Per-category means, folded the same way and for the same reason. The lens and the comparison
    both need the parts rather than the total. */
function categoryMeansFor(code) {
  const pc = publishedFor(code) || {};
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

/* ---------------------------------------------------------------- part scores
   A score taken over some of the categories rather than all of them. Aesthetics is the bottle
   and value is the price, so "which is the better whiskey" is a different question from "which
   was the better buy" — and on the full card several spirits often tie where their flavour
   totals separate cleanly. The Compare tab's table shows both.

   Which categories count as flavour is config, not code (`flavour: false` in the rubric). */
const flavourKeys = () => (app.rubric?.categories || []).filter((c) => c.flavour).map((c) => c.key);

/** What a set of categories is out of. */
function lensMax(keys) {
  const by = Object.fromEntries((app.rubric?.categories || []).map((c) => [c.key, c.max]));
  return keys.reduce((a, k) => a + (by[k] || 0), 0);
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
  return halfUp1(sum);
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

  // The bar and the body are filled by paintRows, which is also what a keystroke, a sort and a
  // finished sync call: the search box and the chips above them are left alone, so typing keeps
  // its place.
  fill(host,
    spirits.length ? null : emptyCollectionNote(),
    search,
    el("div", { class: "chips", id: "fchips" }),
    el("div", { class: "fpanel", id: "fpanel", hidden: "" }),
    el("div", { class: "viewbar", id: "viewbar" }),
    el("div", { id: "cview" }));
  paintFilters();
  paintRows();
}

/* ---------------------------------------------------------------- the collection's filters
   The filters combine. There used to be one row of chips and one of them lit at a time, so
   "bourbon or rye, opened, not scored yet" — the question a pour is chosen by — could not be
   asked at all. Now each filter is a group of choices: any of the choices in a group will do,
   and every group that has one must hold (Store.passes is the rule).

   The row shows what is in force, each as a chip that takes itself off; the panel under it,
   opened from the first chip, holds every choice. With nothing in force the row offers the
   three that are asked most, so those are still one tap. */
const KIND_LABELS = { Bottle: "Bottles", Miniature: "Minis", Sample: "Samples", Encounter: "Bar pours" };

/** The groups, built from what the phone holds: a type nobody has is not a choice. */
function filterGroups() {
  const c = Store.filterChoices(knownSpirits());
  return [
    { key: "scored", label: "Scored", one: true, options: [["yes", "Scored"], ["no", "Not scored"]] },
    { key: "status", label: "Status",
      options: [["Opened", "Opened"], ["Unopened", "Unopened"], ["Finished", "Finished"]] },
    { key: "type", label: "Type", options: c.type.map((t) => [t, t]) },
    { key: "kind", label: "Kind", options: c.kind.map((k) => [k, KIND_LABELS[k]]) },
    { key: "region", label: "Region", options: c.region.map((r) => [r, r]) },
    { key: "rarity", label: "Rarity", options: c.rarity.map((r) => [r, r]) },
  ].filter((g) => g.options.length);
}

/** What is in force, read from the filters themselves rather than from the groups — so a choice
    that a sync has since taken off the list can still be seen and taken off. */
function activeFilters() {
  const f = app.filters, out = [];
  if (f.scored) out.push({ key: "scored", value: f.scored, label: f.scored === "yes" ? "Scored" : "Not scored" });
  for (const key of ["status", "type", "kind", "region", "rarity"]) {
    for (const value of f[key]) out.push({ key, value, label: key === "kind" ? KIND_LABELS[value] || value : value });
  }
  return out;
}

function toggleFilter(key, value) {
  const f = app.filters;
  if (key === "scored") f.scored = f.scored === value ? "" : value;
  else f[key] = f[key].includes(value) ? f[key].filter((v) => v !== value) : [...f[key], value];
  paintFilters();
  paintRows();
}

/** The three asked most, each as the filters it stands for. "In stock" is the kinds that are
    owned: a bar pour was never on the shelf, and a finished bottle is already left out. */
function filterShortcuts() {
  const owned = Store.filterChoices(knownSpirits()).kind.filter((k) => k !== "Encounter");
  return [
    ...(owned.length ? [["In stock", { kind: owned }]] : []),
    ["Not scored", { scored: "no" }],
    ["Scored", { scored: "yes" }],
  ];
}

/** Draw the row and the panel. The rows below are paintRows's; the search box above is nobody's
    to redraw. */
function paintFilters() {
  const row = document.getElementById("fchips");
  const panel = document.getElementById("fpanel");
  if (!row || !panel) return;
  const active = activeFilters();
  const set = (patch) => { app.filters = { ...Store.noFilters(), ...patch }; paintFilters(); paintRows(); };

  fill(row,
    el("button", {
      type: "button", class: `chip${app.filterOpen ? " active" : active.length ? " on" : ""}`,
      "aria-expanded": String(app.filterOpen),
      onclick: () => { app.filterOpen = !app.filterOpen; paintFilters(); },
    }, active.length ? `Filters · ${active.length}` : "Filters", app.filterOpen ? " ▴" : " ▾"),
    active.length
      ? [...active.map((a) => el("button", {
          type: "button", class: "chip on", "aria-label": `Remove the filter ${a.label}`,
          onclick: () => toggleFilter(a.key, a.value),
        }, a.label, el("span", { class: "x", "aria-hidden": "true" }, "×"))),
         el("button", { type: "button", class: "chip", onclick: () => set({}) }, "Clear")]
      : filterShortcuts().map(([label, patch]) =>
          el("button", { type: "button", class: "chip", onclick: () => set(patch) }, label)));

  panel.hidden = !app.filterOpen;
  if (!app.filterOpen) { panel.replaceChildren(); return; }
  const isOn = (g, value) => (g.one ? app.filters[g.key] === value : app.filters[g.key].includes(value));
  fill(panel,
    ...filterGroups().map((g) => el("div", { class: "fgroup" },
      el("div", { class: "flabel" }, g.label),
      el("div", { class: "fopts" }, ...g.options.map(([value, label]) => el("button", {
        type: "button", class: `chip${isOn(g, value) ? " active" : ""}`,
        "aria-pressed": String(isOn(g, value)), onclick: () => toggleFilter(g.key, value),
      }, label))))),
    el("div", { class: "fnote" },
      "Within a group, any of your choices will do. Across groups, all of them must hold."),
    el("div", { class: "actions" },
      // The list is under the panel and mostly off the screen while it is open, so the button
      // that closes it says how many it will show.
      el("button", { class: "btn", type: "button",
                     onclick: () => { app.filterOpen = false; paintFilters(); } },
        `Show ${shownSpirits().length}`),
      active.length
        ? el("button", { class: "btn ghost", type: "button", onclick: () => set({}) }, "Clear all")
        : null));
}

/** The collection the screen is showing: what the chips and the search box leave. One answer
    for both views, so the list and the table can never disagree about what was asked for.

    A Finished or Removed bottle is not offered: nothing can be poured from it, so nothing can be
    scored. The ones scored while they lasted are listed when Finished is asked for, because the
    sittings outlive the bottle; the ones never scored have nothing to show and are not listed.
    (Every score ever given, finished bottles included, is the Compare tab's table.) */
function shownSpirits() {
  const terms = app.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let spirits = knownSpirits().filter((s) => {
    const gone = goneSpirit(s);
    return Store.passes(s, app.filters, {
      gone,
      sittings: gone ? sittingsFor(s.code).length > 0 : false,     // only asked of a gone bottle
      scored: !!careerFor(s.code),
    });
  });
  if (terms.length) {
    spirits = spirits.filter((s) => {
      // The parentheses matter: `a + b.toLowerCase()` lowercases only b, so every name and
      // distillery kept its capitals while the search terms did not, and nothing matched.
      const hay = (`${s.code} ${s.display_name || ""} ${s.distillery || ""} `
        + `${s.name || ""} ${s.type || ""} ${s.region || ""}`).toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
  }
  return spirits;
}

/* The list's own orders. It opens in the order the collection arrives in — the shelf, as the
   workbook has it — and the others are the things a row shows. One direction each, the one the
   question is asked in: the best score, the oldest, the strongest, the newest. The table, which
   shows every field, sorts on every field and both ways. */
const LIST_SORTS = [["", "Shelf order"], ["book", "Book order"], ["score", "Score"], ["name", "Name"],
                    ["type", "Type"], ["age", "Age"], ["proof", "Proof"], ["year", "Year"]];
function listSorted(spirits) {
  // The workbook's own sort, run across every sheet at once (Store.bookOrder). Shelf order is
  // each sheet as it stands, one after another; this is all of them in one sequence.
  if (app.listSort === "book") return [...spirits].sort(Store.bookOrder);
  const numeric = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v))
    ? null : Number(v));
  const of = {
    score: (s) => { const c = careerFor(s.code); return c ? c.score : null; },
    name: (s) => (s.name || s.display_name || s.code || "").toLowerCase() || null,
    type: (s) => (s.type || "").toLowerCase() || null,
    age: (s) => numeric(s.age),
    proof: (s) => numeric(s.proof),
    year: (s) => numeric(s.release_year),
  }[app.listSort];
  if (!of) return spirits;
  // Something with no value is not a low value: it goes last, whichever way the rest run.
  return spirits.map((s) => [of(s), s]).sort(([a], [b]) => {
    if (a === null || b === null) return (a === null) - (b === null);
    return typeof a === "number" ? b - a : a.localeCompare(b, undefined, { numeric: true });
  }).map(([, s]) => s);
}

/** The bar between the filters and the rows: which view, and that view's own choices. */
function paintViewBar(table) {
  const bar = document.getElementById("viewbar");
  if (!bar) return;
  const way = (view, label) => el("button", {
    type: "button", class: app.view === view ? "active" : "", "aria-pressed": String(app.view === view),
    onclick: () => {
      if (app.view === view) return;
      app.view = view;
      Store.setMeta({ view });                       // the view someone prefers is the one they get back
      paintRows();
    },
  }, label);
  fill(bar,
    el("div", { class: "viewswitch", role: "group", "aria-label": "Show the collection as" },
      way("list", "List"), way("table", "Table")),
    el("span", { class: "grow" }),
    table ? TableView.controls()
      : pickerChip(`${LIST_SORTS.find(([key]) => key === app.listSort)[1]} ▾`, LIST_SORTS,
          app.listSort, (key) => { app.listSort = key; paintRows(); }, "Sort the list by"));
}

/** Why the list is empty, where one filter alone explains it; otherwise that nothing matches. */
function emptyBecause(searching) {
  const f = app.filters;
  if (!searching && Store.countFilters(f) === 1) {
    if (f.status[0] === "Finished") return "No finished bottle was scored while it lasted.";
    if (f.scored === "no") return "Everything you have has been scored.";
  }
  return Store.countFilters(f) ? "Nothing matches these filters." : "Nothing matches.";
}

/** Fill the bar and the body for the view in use. The name is from when there was only a list. */
function paintRows() {
  const body = document.getElementById("cview");
  if (!body) return;
  const table = app.view === "table";
  paintViewBar(table);
  if (table) { TableView.render(body, shownSpirits()); return; }

  const terms = app.query.trim();
  const spirits = listSorted(shownSpirits());
  const list = el("ul", { class: "rows", id: "rows" });
  fill(body, list);
  if (!spirits.length) {
    list.append(el("li", { class: "empty" },
      emptyBecause(terms.length > 0)));
    return;
  }
  // The same whiskey held more than once is one row that opens out into its bottles — same
  // distillery and name, and never a bottle with a sample (Store.groupKey). The row stands where
  // the first of them stood, so every order above still reads true.
  for (const g of Store.groupSpirits(spirits)) {
    if (g.members.length === 1) { list.append(spiritRow(g.members[0])); continue; }
    const open = app.openGroups.has(g.key);
    list.append(groupRow(g, open));
    if (open) for (const s of g.members) list.append(spiritRow(s, true));
  }
}

/** One spirit in the list. As a member of an opened group it is told apart by its special note,
    year and status — the name is on the row above it, and is the one thing they all share. */
function spiritRow(s, member = false) {
  const gone = goneSpirit(s);
  const facts = [s.age ? `${s.age} yr` : s.age_label, s.proof ? `${s.proof} pf` : null];
  const meta = [s.type, ...facts, s.release_year ? String(Math.round(s.release_year)) : null]
    .filter(Boolean).join(" · ");
  return el("li", {
    class: `row${member ? " member" : ""}${s.owned === false ? " gone" : ""}`,
    style: `border-left-color:${accentFor(s.type)}`,
    onclick: () => openDetail(s.code),
  },
    el("div", { class: "row-main" },
      el("div", { class: "row-name" },
        member ? Store.memberLabel(s) : (s.name || s.display_name || s.code),
        gone && !member ? el("span", { class: "tag" }, s.status) : null),
      el("div", { class: "row-sub" }, member
        ? [s.code, ...facts].filter(Boolean).join(" · ")
        : [s.distillery, meta].filter(Boolean).join(" · "))),
    scoreCell(careerFor(s.code)));
}

const GROUP_NOUN = { Bottle: "bottles", Miniature: "miniatures", Sample: "samples" };

/** The row for a name held more than once: how many there are, and what they scored between
    them — one figure if only one has been scored, the span from lowest to highest otherwise.
    Not a mean: three years' releases under one name are three whiskies, not one. */
function groupRow(g, open) {
  const first = g.members[0];
  const sameType = g.members.every((m) => (m.type || "") === (first.type || ""));
  const scored = g.members.map((m) => careerFor(m.code)).filter(Boolean);
  const low = Math.min(...scored.map((c) => c.score));
  const high = Math.max(...scored.map((c) => c.score));
  return el("li", {
    class: `row group${open ? " open" : ""}`,
    style: `border-left-color:${accentFor(first.type)}`,
    role: "button", "aria-expanded": String(open),
    onclick: () => {
      if (open) app.openGroups.delete(g.key); else app.openGroups.add(g.key);
      paintRows();
    },
  },
    el("div", { class: "row-main" },
      el("div", { class: "row-name" }, first.name,
        el("span", { class: "tag" }, `${g.members.length} ${GROUP_NOUN[first._sheet] || "bottles"}`)),
      el("div", { class: "row-sub" },
        [first.distillery, sameType ? first.type : null].filter(Boolean).join(" · "))),
    scored.length > 1 && low !== high
      ? el("div", { class: "row-score" }, el("b", {}, `${low.toFixed(1)}–${high.toFixed(1)}`),
          el("div", { class: "muted" }, `${scored.length} scored`))
      : scoreCell(scored[0] || null),
    el("span", { class: "chev" }, "›"));
}

// ---------------------------------------------------------------- detail
/** Every sitting of this spirit the phone can act on: the ones it scored itself and the ones the
    PC published. Listing only its own meant a collection scored at a desk showed none at all, and
    nothing could be corrected or removed from here. Local copies win — they are the newer of the
    two whenever the phone has scored since the PC last did its sums. */
function sittingsFor(code) {
  const mine = Store.cardsFor(code);
  // Every card this phone holds, not only this spirit's: a sitting moved to another bottle is
  // held here under the new one while the PC's list still has it under the old.
  const seen = new Set(Store.cards().map((c) => c.tasting_id));
  const gone = new Set(Store.buried());
  const published = ((Store.careers().sittings || {})[code] || [])
    .filter((t) => !gone.has(t.tasting_id));
  return mine.concat(published.filter((t) => !seen.has(t.tasting_id)))
    .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
}

/** True when the figures on this phone were published by an app older than the one that wrote
    this page. Two computers share the folder, so the one that refreshed last wins — and an old
    one wins by removing things the phone is looking for. */
const NEEDS_PUBLISHED_VERSION = 2;
const staleSource = () => (Store.careers().version || 0) < NEEDS_PUBLISHED_VERSION;

/** `push` is false when arriving from a screen that is being left behind — a submitted card, or
    a sitting that has just been deleted. Pushing there puts a screen on the back stack that the
    chevron would return to and that no longer means anything. */
function openDetail(code, { push = true } = {}) {
  const s = findSpirit(code);
  if (!s) return;
  app.detailCode = code;
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
      // What the workbook says about this bottle in particular — the thing that tells it from
      // the one beside it on the shelf. (The Sample sheet keeps a comment as well.)
      ...[["Special note", Store.specialNote(s)], ["Comment", String(s.comment ?? "").trim()]]
        .filter(([, text]) => text)
        .map(([label, text]) => el("div", { class: "spirit-note" },
          el("span", { class: "note-label" }, label), text)),
      career
        ? el("div", { style: "margin-top:12px;display:flex;align-items:center;gap:10px" },
            el("span", { class: "total-num" }, career.score.toFixed(1)),
            el("span", { class: "total-of" }, `/ 100 · n=${career.n}`),
            career.medal ? el("span", { class: "medal",
              style: `--m:${MEDAL_COLORS[career.medal] || "#9A9086"}` }, career.medal) : null)
        : el("div", { class: "muted", style: "margin-top:10px" }, "Not scored yet.")),

    // An empty bottle takes no new scores; what it has is listed below, and can still be corrected.
    goneSpirit(s)
      ? el("div", { class: "card muted", style: "margin-top:12px" },
          `${s.status} — no more pours to score from this one. Its sittings stay on the books.`)
      : el("button", { class: "btn wide", type: "button", onclick: () => openScore(code) },
          "Score a pour"),

    // The way into a comparison used to be the Compare tab and nowhere else — but the thought
    // "how does this stand against the others" arrives here, looking at the bottle.
    career
      ? el("button", { class: "btn wide ghost", type: "button", style: "margin-top:10px",
                       onclick: () => { CompareView.begin(code); setTab("compare"); } },
          "Compare with others")
      : null,

    s._local ? localBottleCard(s, sittings.length) : null,

    // An empty list used to render nothing at all, which is indistinguishable from a screen that
    // failed to draw. It has one cause worth naming and one worth ruling out.
    sittings.length ? null : el("div", { class: "card", style: "margin-top:12px" },
      careerFor(code)
        ? notice("warn", staleSource()
            ? "This bottle has been scored, but the sittings did not come through. The PC that "
              + "last refreshed is running an older version of the app — update it there, press "
              + "Refresh, then Sync here."
            : "This bottle has been scored, but the sittings are not on this phone yet. "
              + "Open Sync and refresh.")
        : el("div", { class: "muted" }, goneSpirit(s)
            ? "Nothing was scored while it lasted."
            : "No sittings yet. Score a pour and it appears here.")),

    sittings.length
      ? el("div", { class: "card", style: "margin-top:12px" },
          el("div", { class: "muted", style: "margin-bottom:8px" },
            `${sittings.length} sitting${sittings.length === 1 ? "" : "s"}`),
          // The row opens the sitting rather than carrying its controls. Two buttons squeezed
          // beside a date is a poor tap target, and the card behind them — ten scores and the
          // notes written with them — had nowhere to be read at all.
          ...sittings.map((c) => el("button", {
            class: "qrow sitting", type: "button",
            onclick: () => openSitting(c, s),
          },
            el("span", {}, c.date),
            el("span", { class: "when" },
              // A phone-only card is not waiting for anything: there is nowhere for it to go.
              `${c.total}${c._path && !c._sent && !c._local ? " · waiting" : ""}`),
            c.medal
              ? el("span", { class: "medal",
                  style: `--m:${MEDAL_COLORS[c.medal] || "#9A9086"}` }, c.medal)
              : null,
            el("span", { class: "chev" }, "›"))))
      : null);

  show("detail", s.name || s.code, { push });
}

/** One sitting, in full: what each category was given, the note written against it, and the two
    things you might want to do about it afterwards. */
function openSitting(card, spirit) {
  const rub = app.rubric;
  const notes = { ...(card.notes || {}) };
  const overall = notes.overall || "";
  delete notes.overall;

  const bars = el("div", { class: "card" },
    ...(rub ? rub.categories : []).map((cat) => {
      const v = card.scores ? card.scores[cat.key] : null;
      const has = v !== null && v !== undefined;
      return el("div", { class: "brk" },
        el("div", { class: "brk-head" },
          el("span", {}, cat.label || cat.key),
          el("span", { class: "brk-val" }, has ? `${v} / ${cat.max}` : `— / ${cat.max}`)),
        el("div", { class: "brk-track" },
          el("div", { class: "brk-fill",
                      style: `width:${has ? (v / cat.max) * 100 : 0}%;`
                             + `background:${accentFor(spirit.type)}` })),
        notes[cat.key] ? el("div", { class: "brk-note" }, notes[cat.key]) : null);
    }));

  const context = [card.date, card.venue, card.entered_from ? `entered on the ${card.entered_from}` : null]
    .filter(Boolean).join(" · ");

  fill(screens.sitting,
    el("div", { class: "card", style: `border-left:3px solid ${accentFor(spirit.type)}` },
      el("div", { class: "spirit-name" }, spirit.name || spirit.display_name || card.spirit_id),
      el("div", { class: "spirit-meta" }, context),
      el("div", { style: "margin-top:12px;display:flex;align-items:center;gap:10px" },
        el("span", { class: "total-num" }, card.total === null || card.total === undefined
          ? "—" : String(card.total)),
        el("span", { class: "total-of" }, `/ ${rub ? rub.max_total : 100}`),
        card.medal
          ? el("span", { class: "medal", style: `--m:${MEDAL_COLORS[card.medal] || "#9A9086"}` },
              card.medal)
          : null),
      card.include_in_average === false
        ? el("div", { class: "muted", style: "margin-top:8px" }, "Not counted towards the score.")
        : null),
    bars,
    overall
      ? el("div", { class: "card" },
          el("div", { class: "muted", style: "margin-bottom:6px" }, "Overall"),
          el("div", { class: "brk-overall" }, overall))
      : null,
    el("div", { class: "actions", style: "margin-top:14px;gap:10px" },
      el("button", { class: "btn wide", type: "button",
                     onclick: () => editSitting(card) }, "Edit this sitting"),
      el("button", { class: "btn wide danger", type: "button",
                     onclick: () => removeSitting(card, spirit) }, "Delete")),
    // Scored against the wrong bottle: the card is right, the name on it is not.
    el("button", { class: "btn wide ghost", type: "button", style: "margin-top:10px",
                   onclick: () => openMove(card, spirit) }, "Move to another bottle"));

  show("sitting", card.date || "Sitting", { push: true });
}

/** Choose the bottle a sitting should have been scored against. Everything that can hold a
    sitting is offered — finished bottles and bar pours too, since this corrects the record rather
    than pouring anything — with the other bottles of the same name first, because that is the
    usual mistake: the right whiskey, the wrong one of three. */
function openMove(card, spirit) {
  const name = (s) => s.name || s.display_name || s.code;
  const sameAs = Store.groupKey(spirit);
  const all = knownSpirits().filter((s) => s.code !== card.spirit_id);
  const list = el("ul", { class: "rows" });

  const choose = (s) => {
    if (!window.confirm(`Move this sitting to ${name(s)} (${s._encounter ? "bar pour" : s.code})?\n\n`
        + `${card.date || "Undated"} · ${card.total ?? "—"} points, now under `
        + `${spirit._encounter ? name(spirit) : spirit.code}. `
        + "The scores and notes go across as they are.")) return;
    Store.moveCard(card, s.code);
    updateBadge();
    flush();
    // The screens behind this one are about the bottle it has just left. Drop them and land on
    // the one it went to, so the chevron does not lead back to a sitting that is not there.
    while (app.stack.length && ["sitting", "detail"].includes(app.stack[app.stack.length - 1].name)) {
      app.stack.pop();
    }
    openDetail(s.code, { push: false });
  };

  const row = (s) => el("li", {
    class: `row${s.owned === false ? " gone" : ""}`,
    style: `border-left-color:${accentFor(s.type)}`,
    onclick: () => choose(s),
  },
    el("div", { class: "row-main" },
      el("div", { class: "row-name" }, name(s)),
      el("div", { class: "row-sub" }, s._encounter
        ? ["Bar pour", s.distillery, s.venue].filter(Boolean).join(" · ")
        : [s.distillery, Store.memberLabel(s)].filter(Boolean).join(" · "))),
    el("div", { class: "row-score" }, el("span", { class: "muted" }, s._encounter ? "" : s.code)));

  let query = "";
  const paint = () => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const shown = all.filter((s) => {
      if (!terms.length) return true;
      const hay = (`${s.code} ${s.display_name || ""} ${s.distillery || ""} ${s.name || ""} `
        + `${s.type || ""} ${s.special_note || s.notes || ""} ${s.status || ""}`).toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
    const near = sameAs === null ? [] : shown.filter((s) => Store.groupKey(s) === sameAs);
    const rest = shown.filter((s) => !near.includes(s));
    fill(list,
      near.length ? el("li", { class: "rows-head" }, "Same name") : null,
      ...near.map(row),
      near.length && rest.length ? el("li", { class: "rows-head" }, "Everything else") : null,
      ...rest.map(row),
      shown.length ? null : el("li", { class: "empty" }, "Nothing matches."));
  };

  fill(screens.move,
    el("div", { class: "card", style: `border-left:3px solid ${accentFor(spirit.type)}` },
      el("div", { class: "muted" }, "Moving the sitting of"),
      el("div", { class: "spirit-name" }, name(spirit)),
      el("div", { class: "spirit-meta" },
        [card.date, card.total === null || card.total === undefined ? null : `${card.total} points`,
         spirit._encounter ? "Bar pour" : spirit.code].filter(Boolean).join(" · ")),
      el("div", { class: "muted", style: "margin-top:8px" },
        "Choose the bottle it should have been scored against.")),
    el("input", { class: "search", type: "text", enterkeyhint: "search",
                  placeholder: "Search for the right bottle", "aria-label": "Search for the right bottle",
                  autocapitalize: "none", autocorrect: "off", spellcheck: "false",
                  oninput: (e) => { query = e.target.value; paint(); } }),
    list);
  paint();
  show("move", "Move sitting", { push: true });
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
  fitNotes();
}

function removeSitting(card, spirit) {
  // Works for a card scored on the PC as well: the marker is a file named after the tasting
  // and its next revision, and the phone has both from the published list. The PC removes the
  // card's own files when it reads the marker; nothing of the card is kept.
  const name = spirit.name || spirit.display_name || card.spirit_id;
  if (!window.confirm(`Delete this sitting of ${name}?\n\nIt is removed for good: its scores and `
      + "notes are not kept, and this cannot be undone.")) return;
  Store.deleteCard(card);
  updateBadge();
  flush();
  // The sitting pushed the detail screen on its way in; drop that entry and land back on the
  // detail without pushing again, so the chevron does not lead to a sitting that is gone.
  if (currentScreen() === "sitting") app.stack.pop();
  openDetail(card.spirit_id, { push: false });
}

// ---------------------------------------------------------------- scorecard
function openScore(code) {
  const s = findSpirit(code);
  if (!s) return;
  if (goneSpirit(s)) {             // the detail screen says why; there is nothing to score
    openDetail(code, { push: currentScreen() !== "detail" });
    return;
  }
  openScoreFor(s, s.venue || "");
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
  fitNotes();
}

function renderScore() {
  const d = app.draft;
  const host = screens.score;
  const cats = el("div", { class: "card" });

  for (const c of app.rubric.categories) {
    const val = el("span", { class: "cat-val unset" }, "–");
    // Cells 0..max. Zero is the bottom of every category and needs somewhere to land; it used to
    // be reachable only within 2 % of the left edge, with nothing drawn there — on a phone, about
    // seven points, and the strip is the only input there is. Its cell is hollow and lights only
    // when 0 is the score, so a strip with a 6 on it does not look as though it starts at brass.
    const strip = el("div", {
      class: "strip", role: "slider", tabindex: "0",
      style: `grid-template-columns:repeat(${c.max + 1},1fr)`,
      "aria-label": c.label, "aria-valuemin": "0", "aria-valuemax": String(c.max),
    });
    const segs = [];
    for (let i = 0; i <= c.max; i++) {
      const seg = el("div", { class: i === 0 ? "seg zero" : "seg", "data-label": String(i) });
      segs.push(seg); strip.append(seg);
    }
    const paint = () => {
      const v = d.scores[c.key];
      const showAll = c.max <= 10;
      segs.forEach((seg, n) => {
        seg.classList.toggle("filled", v !== null && (n === 0 ? v === 0 : n <= v));
        seg.classList.toggle("sel", v !== null && n === v);
        // On a 20-point strip the cells are too narrow for two labels side by side: a score of
        // 16 printed "1516". The fifth next to the score gives way to it.
        const crowded = !showAll && v !== null && Math.abs(n - v) === 1;
        seg.classList.toggle("tick", n === v || showAll || n === 0 || (n % 5 === 0 && !crowded));
      });
      val.textContent = v === null ? "–" : String(v);
      val.classList.toggle("unset", v === null);
      strip.setAttribute("aria-valuenow", String(v ?? 0));
      updateTotal();
    };
    // Eleven cells across a 390 pt screen is ~30 pt each, under the 44 pt guideline, so a tap is
    // backed by a drag: press anywhere and slide to the value (SPEC.md §9.2).
    const from = (clientX) => {
      const r = strip.getBoundingClientRect();
      const frac = (clientX - r.left) / r.width;
      return Math.max(0, Math.min(c.max, Math.floor(frac * (c.max + 1))));   // cell under the finger
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

    /* The note is folded away until it is wanted. Ten boxes open at once made the card nearly
       three screens long, and a pour gets a note against two or three categories, not ten. One
       that already says something — a sitting being corrected — opens with the card, with its
       words in it; one opened and left empty folds itself back when the keyboard goes. */
    const written = d.notes[c.key] || "";
    const notes = el("textarea", {
      rows: "1", placeholder: `${c.label} notes`, autocapitalize: "sentences", autocorrect: "on",
      spellcheck: "true", hidden: written ? null : "",
      oninput: (e) => { d.notes[c.key] = e.target.value; grow(e.target); },
    }, written);
    const addNote = el("button", {
      type: "button", class: "note-add", hidden: written ? "" : null,
      "aria-label": `Add a note on ${c.label}`,
      onclick: () => { addNote.hidden = true; notes.hidden = false; notes.focus(); },
    }, "+ note");
    notes.addEventListener("blur", () => {
      if (notes.value.trim()) return;
      notes.hidden = true;
      addNote.hidden = false;
    });

    cats.append(el("div", { class: "cat" },
      el("div", { class: "cat-head" },
        el("span", { class: "cat-name" }, c.label),
        el("span", { class: "cat-max" }, `/ ${c.max}`),
        addNote,
        val),
      strip, notes));
    paint();
  }

  const overall = el("textarea", {
    rows: "2", placeholder: "Overall impression",
    autocapitalize: "sentences", autocorrect: "on", spellcheck: "true",
    oninput: (e) => { d.overall = e.target.value; grow(e.target); },
  }, d.overall || "");
  // Below the scores, not above them: most pours are at home and leave it blank, and it used to
  // be the first thing on the card. It shows what it holds — a bar pour arrives with its venue.
  const venue = el("input", {
    type: "text", value: d.venue || null, placeholder: "blank = home", autocapitalize: "words",
    oninput: (e) => { d.venue = e.target.value; },
  });

  const submit = el("button", { class: "btn", id: "submit", type: "button", disabled: true,
                                onclick: submitCard }, "Submit");

  fill(host,
    el("div", { class: "card", style: `border-left:3px solid ${accentFor(d.spirit.type)}` },
      el("div", { class: "spirit-name" }, d.spirit.name || d.spirit.display_name),
      el("div", { class: "spirit-meta" }, [d.spirit.distillery, d.spirit.type]
        .filter(Boolean).join(" · "))),
    cats,
    el("div", { class: "card" },
      el("label", { class: "field" }, el("span", {}, "Overall notes"), overall),
      el("label", { class: "field" }, el("span", {}, "Venue"), venue)),
    el("div", { class: "total-bar" },
      el("span", { class: "total-num incomplete", id: "total" }, "0"),
      el("span", { class: "total-of", id: "total-of" },
        `/ ${app.rubric.max_total}`),
      el("span", { style: "margin-left:auto" }, submit)));
  updateTotal();
}

/** A note that came with the card needs its box as tall as its words. Called once the screen is
    showing: while it is hidden a box has no height to measure, and would be set to none. */
function fitNotes() {
  screens.score.querySelectorAll("textarea").forEach((ta) => { if (ta.value && !ta.hidden) grow(ta); });
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
  app.stack = [{ name: "collection", title: "Collection" }];
  app.tab = "collection";
  markTab("collection");
  openDetail(rec.spirit_id, { push: false });        // back goes to the collection, not the card
}

const grow = (ta) => { ta.style.height = "auto"; ta.style.height = `${ta.scrollHeight}px`; };

// ---------------------------------------------------------------- add bottle
/** What the Add form collects, under the names Store.addLocalSpirit takes. */
const bottleFields = (f) => ({
  distillery: f.Distillery, name: f.Name, type: f.Type, region: f.Region,
  age: f.Age, proof: f.Proof, size_ml: f["Size (ml)"], paid: f.Paid,
});

const OTHER_TYPE = "__other__";                  // the select's last option; never a type itself
const AGE_WORDS = [["NAS", "no age statement"], ["N/A", "not applicable"]];

function renderAdd() {
  const local = Store.isLocal();
  // Editing reuses the form: only a bottle added on this phone can be edited from it.
  const editing = app.editCode
    ? Store.spirits().find((x) => x.code === app.editCode && x._local) || null : null;
  const pour = !editing && app.addMode === "pour";
  const fields = {};
  const seed = editing ? {
    Distillery: editing.distillery, Name: editing.name, Type: editing.type,
    Region: editing.region, Age: editing.age ?? editing.age_label, Proof: editing.proof,
    "Size (ml)": editing.size_ml, Paid: editing.paid,
  } : {};
  const input = (name, attrs = {}) => {
    const start = seed[name] === null || seed[name] === undefined ? "" : String(seed[name]);
    if (editing) fields[name] = start;
    const i = el("input", Object.assign({ type: "text", value: start || null }, attrs));
    i.addEventListener("input", (e) => { fields[name] = e.target.value; });
    return el("label", { class: "field" }, el("span", {}, name), i);
  };
  const named = () => (fields.Name || "").trim() || (fields.Distillery || "").trim();
  const seeded = (name) => (seed[name] === null || seed[name] === undefined ? "" : String(seed[name]).trim());

  /* Type is chosen, not typed. The list is the workbook's own Type drop-down (Store.types), so a
     bottle cannot reach the PC as "Burbon". "Other…" opens a box for what the list does not hold
     — something strange met at a bar, a friend's gin or champagne — and a type entered there is
     on the list from then on. */
  const typeField = () => {
    const start = seeded("Type");
    if (editing) fields.Type = start;
    const known = Store.types();
    const other = el("input", { type: "text", autocapitalize: "words", hidden: true,
                                placeholder: "Name the type", "aria-label": "Another type" });
    other.addEventListener("input", (e) => { fields.Type = e.target.value; });
    const sel = el("select", { "aria-label": "Type" },
      el("option", { value: "" }, "Choose…"),
      ...known.map((t) => el("option", { value: t }, t)),
      el("option", { value: OTHER_TYPE }, "Other…"));
    sel.value = known.find((t) => t.toLowerCase() === start.toLowerCase()) || "";
    sel.addEventListener("change", () => {
      const typed = sel.value === OTHER_TYPE;
      other.hidden = !typed;
      fields.Type = typed ? other.value : sel.value;
      if (typed) other.focus();
    });
    return el("div", { class: "field" }, el("span", {}, "Type"), sel, other);
  };

  /* Age is a number of years, or one of the two things said instead of one: NAS, no age
     statement, and N/A, for a gin or a wine where the question does not arise. The keypad is
     digits only, so neither could be typed; they are buttons, and typing a number lets go of
     them. */
  const ageField = () => {
    const start = seeded("Age");
    const word = AGE_WORDS.find(([w]) => w.toLowerCase() === start.toLowerCase());
    if (editing) fields.Age = word ? word[0] : start;
    const box = el("input", { type: "text", inputmode: "numeric", placeholder: "Years",
                              "aria-label": "Age in years", value: word ? null : start || null });
    const paint = () => chips.forEach((c, i) => {
      const on = fields.Age === AGE_WORDS[i][0];
      c.classList.toggle("active", on);
      c.setAttribute("aria-pressed", String(on));
    });
    const chips = AGE_WORDS.map(([w, meaning]) => el("button", {
      type: "button", class: "chip", title: meaning, "aria-label": `${w}: ${meaning}`,
      onclick: () => {
        fields.Age = fields.Age === w ? "" : w;          // a second tap takes it back
        box.value = "";
        paint();
      } }, w));
    box.addEventListener("input", (e) => { fields.Age = e.target.value; paint(); });
    paint();
    return el("div", { class: "field" }, el("span", {}, "Age"),
      el("div", { class: "age-row" }, box, ...chips));
  };

  const modes = editing ? null : el("div", { class: "chips" },
    ...[["pour", "Bar pour"], ["bottle", "Bottle I bought"]].map(([id, label]) =>
      el("button", { type: "button", class: `chip${app.addMode === id ? " active" : ""}`,
        onclick: () => { app.addMode = id; renderAdd(); } }, label)));

  fill(screens.add,
    modes,
    editing ? null : notice("ok", pour
      ? (local
        ? "Something you are drinking but do not own. Score it now — it is saved on this phone "
          + "and listed under Bar, never in your collection."
        : "Something you are drinking but do not own. Score it now — it is saved on this phone "
          + "first, so no signal is needed — and it lands on the PC as a bar pour, never in your "
          + "collection.")
      : (local
        ? "Saved on this phone. It joins your collection straight away."
        : "Queued for approval on the PC. Nothing reaches the collection workbook from this phone "
          + "— a person checks each row first.")),
    el("div", { class: "card" },
      input("Distillery", { autocapitalize: "words" }),
      input("Name", { autocapitalize: "words" }),
      typeField(),
      input("Region", { autocapitalize: "words" }),
      ageField(),
      input("Proof", { inputmode: "decimal" }),
      pour ? input("Venue", { autocapitalize: "words" }) : input("Size (ml)", { inputmode: "decimal" }),
      pour ? null : input("Paid", { inputmode: "decimal" }),
      el("div", { class: "actions" },
        el("button", { class: "btn wide", type: "button", onclick: () => {
          if (!named()) return;
          // "red wine" typed a second time joins "Red Wine" instead of starting a group beside
          // it. Not while editing: there the bottle's own spelling is on the list, and a
          // correction to it would be folded straight back into the mistake.
          if (!editing && fields.Type !== undefined) fields.Type = Store.canonicalType(fields.Type) || "";
          if (editing) {
            const code = editing.code;
            if (!Store.updateLocalSpirit(code, bottleFields(fields))) {
              window.alert(`Could not save: ${Store.lastWriteError() || "this phone refused the change"}`);
              return;
            }
            app.editCode = null;
            back();
            openDetail(code, { push: false });
            return;
          }
          if (pour) { startBarPour(fields); return; }
          if (local) {
            // Phone-only: there is no PC to approve it, so the bottle goes straight in.
            const sp = Store.addLocalSpirit(bottleFields(fields));
            if (!sp) {
              window.alert(`Could not save: ${Store.lastWriteError() || "this phone refused the bottle"}`);
              return;
            }
            setTab("collection");
            openDetail(sp.code);
            return;
          }
          const filled = Object.fromEntries(
            Object.entries(fields).filter(([, v]) => v && String(v).trim()));
          Store.submitPending("Bottle", filled);
          updateBadge();
          flush();
          setTab("sync");
        } }, editing ? "Save changes" : pour ? "Score this pour"
              : local ? "Add to my collection" : "Queue for approval"))));
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
// ---------------------------------------------------------------- where the book lives
/* Two arrangements, one app. OneDrive: the PC publishes the collection and this phone sends its
   cards back. Phone-only: no account and no computer — bottles are added here and everything
   stays here. The choice is on the Sync tab, and a phone that has made none yet is asked on
   its first screen. */
function chooseMode(mode) {
  const was = Store.mode();
  if (mode === was && Store.meta().mode) return;
  if (mode === "local" && was === "onedrive" && !window.confirm(
    "Keep the book on this phone only?\n\nNothing is fetched or uploaded in this mode, and what "
    + "you score in it is not sent to the PC later. You can switch back at any time.")) return;
  if (mode === "onedrive" && was === "local" && !window.confirm(
    "Sync with OneDrive?\n\nBottles you added on this phone stay. Sittings scored in phone-only "
    + "mode stay on this phone and are not uploaded.")) return;
  Store.setMode(mode);
  applyMode();
  if (mode === "onedrive") {
    setTab("sync");
    Graph.resume().then((ok) => { if (ok) refresh(); else if (currentScreen() === "sync") renderSync(); });
  } else {
    setTab(app.tab);
  }
}

/** What the rest of the page shows differently by mode: the last tab's name and its badge. */
function applyMode() {
  document.getElementById("tab-sync-label").textContent = Store.isLocal() ? "Data" : "Sync";
  updateBadge();
}

function modeCard() {
  const mode = Store.mode();
  const opt = (id, label, sub) => el("button", {
    type: "button", class: `mode-opt${mode === id ? " active" : ""}`,
    "aria-pressed": String(mode === id), onclick: () => chooseMode(id),
  }, el("b", {}, label), el("span", {}, sub));
  return el("div", { class: "card" },
    el("div", { class: "muted", style: "margin-bottom:8px" }, "Where your book lives"),
    el("div", { class: "mode-opts" },
      opt("local", "This phone only", "No account, no computer"),
      opt("onedrive", "OneDrive", "Syncs with the PC app")));
}

/** The first screen of a phone that has not chosen yet. */
function modeChooser() {
  return el("div", { class: "card" },
    el("div", { class: "chart-title" }, "Where should your tasting book live?"),
    el("div", { class: "muted", style: "margin-bottom:12px" }, "You can change this later, on the last tab."),
    el("button", { class: "btn wide", type: "button", onclick: () => chooseMode("local") },
      "On this phone only"),
    el("div", { class: "muted", style: "margin:6px 0 14px" },
      "No account and no computer. Add your bottles here and score them; everything stays on this phone."),
    el("button", { class: "btn wide ghost", type: "button", onclick: () => chooseMode("onedrive") },
      "Sync with OneDrive"),
    el("div", { class: "muted", style: "margin-top:6px" },
      "For use with the PC app: it publishes your collection, and this phone sends its scorecards back."));
}

/** What an empty collection says depends on where the book lives. */
function emptyCollectionNote() {
  const mode = Store.mode();
  if (mode === "local") {
    return notice("ok", "No bottles yet. Tap Add to put your first one in, or to score a pour at a bar.");
  }
  if (mode === "onedrive") {
    return notice("warn", "No collection yet. Open Sync and refresh once while you have a connection.");
  }
  return modeChooser();
}

function localCard(q) {
  const own = Store.spirits().filter((s) => s._local).length;
  return el("div", { class: "card" },
    el("div", { class: "state" },
      el("span", { class: "dot on" }), el("span", {}, "Kept on this phone")),
    el("div", { class: "muted" },
      [[Store.spirits().length, "bottle"], [Store.encounters().length, "bar pour"],
       [Store.cards().length, "sitting"]].map(([n, word]) => `${n} ${word}${n === 1 ? "" : "s"}`).join(" · ")
      + (own !== Store.spirits().length ? ` (${own} added here)` : "")),
    el("div", { class: "muted", style: "margin-top:8px" },
      "Nothing is uploaded or fetched. Everything is stored on this phone and nowhere else, so "
      + "export a backup now and then."),
    q.length
      ? el("div", { class: "muted", style: "margin-top:8px" },
          `${q.length} item${q.length === 1 ? "" : "s"} from before ${q.length === 1 ? "is" : "are"} `
          + "still waiting, and will upload if you switch back to OneDrive.")
      : null);
}

/** A copy out, and a copy back in: the whole of what this phone holds, as one file. */
function exportBook() {
  return handOver(`tasting-book-${Store.today()}.json`, "application/json", Store.exportAll());
}

/** The collection as an Excel workbook laid out like the PC's own (xlsx.js). Offered to a
    phone-only book and to no other: with a PC behind the phone, the PC's workbook *is* the
    collection, and a second file of the same shape without its photographs is only something to
    mistake for it — which is also why the file is not given the master's name. */
function exportWorkbook() {
  const bytes = Xlsx.collectionBook(Store.spirits(), { types: Store.types() });
  return handOver(`Tasting Book Collection ${Store.today()}.xlsx`, Xlsx.MIME, bytes);
}

/** Give the person a file: the app writes it into Files and raises the share sheet over it
    (Save to Files, AirDrop, Mail). `data` is text or bytes. Only the app can do this — a page
    in a desktop browser, which is how these screens are looked at while they are worked on, is
    told so rather than left with a button that does nothing. */
async function handOver(name, mime, data) {
  if (!Native.caps.has("share")) {
    window.alert("Files are handed over by the iPhone app. This page is not running inside it.");
    return;
  }
  const bytes = typeof data === "string" ? Xlsx.utf8(data) : data;
  let packed = "";                                         // btoa wants a string of bytes
  for (let i = 0; i < bytes.length; i += 0x8000) {
    packed += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  try {
    const r = await Native.request("file.share", { name, mime, base64: btoa(packed) });
    if (!r || !r.ok) window.alert(`Could not make the file: ${(r && r.error) || "the app refused it"}`);
  } catch (e) {
    window.alert(`Could not make the file: ${(e && e.message) || e}`);
  }
}

function workbookCard() {
  return el("div", { class: "card" },
    el("div", { class: "muted", style: "margin-bottom:8px" }, "Excel workbook"),
    el("div", { class: "muted" },
      "Your collection as a spreadsheet, laid out like the collection workbook the PC app reads: "
      + "the same sheets, columns, formulas and drop-downs. Scores are not in it — the backup "
      + "below has those."),
    el("div", { class: "actions" },
      el("button", { class: "btn", type: "button", onclick: exportWorkbook }, "Export to Excel")));
}

function importBook(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    if (!window.confirm("Replace everything on this phone with this backup?\n\n"
      + "Bottles, sittings and settings here are overwritten by the file's.")) return;
    const problem = Store.importAll(String(reader.result || ""));
    if (problem) { window.alert(problem); return; }
    location.reload();
  };
  reader.onerror = () => window.alert("Could not read that file.");
  reader.readAsText(file);
}

function backupCard() {
  const picker = el("input", { type: "file", accept: "application/json,.json", hidden: "",
    "aria-label": "Choose a backup file" });
  picker.addEventListener("change", () => importBook(picker.files && picker.files[0]));
  return el("div", { class: "card" },
    el("div", { class: "muted", style: "margin-bottom:8px" }, "Backup"),
    el("div", { class: "muted" }, "A file with everything this phone holds. Import puts one back."),
    el("div", { class: "actions" },
      el("button", { class: "btn", type: "button", onclick: exportBook }, "Export"),
      el("button", { class: "btn ghost", type: "button", onclick: () => picker.click() }, "Import")),
    picker);
}

/** The monthly mean of this phone's own counted cards — what the PC publishes as the
    calibration series, worked out here when there is no PC to publish it. */
function localCalibration() {
  const by = new Map();
  for (const c of Store.cards()) {
    if (!c.include_in_average || c.total === null || c.total === undefined || !c.date) continue;
    const month = String(c.date).slice(0, 7);
    if (!by.has(month)) by.set(month, []);
    by.get(month).push(c.total);
  }
  return [...by.keys()].sort().map((month) => {
    const v = by.get(month);
    return { month, mean: Store.meanHalfUp(v.reduce((x, y) => x + y, 0), v.length), n: v.length };
  });
}

/** A bottle added on this phone is this phone's to keep: its status, its details, and — while
    nothing has been scored against it — its place in the list. A Finished one follows the same
    rule as everywhere else: no new scores, and its sittings stay under the Finished chip. */
function localBottleCard(s, sittings) {
  const set = (status) => {
    Store.updateLocalSpirit(s.code, { status });
    openDetail(s.code, { push: false });
  };
  return el("div", { class: "card", style: "margin-top:12px" },
    el("div", { class: "muted", style: "margin-bottom:8px" }, "This bottle"),
    el("div", { class: "chips" }, ...["Unopened", "Opened", "Finished"].map((st) =>
      el("button", { type: "button", class: `chip${s.status === st ? " active" : ""}`,
        onclick: () => set(st) }, st))),
    el("div", { class: "actions", style: "margin-top:4px" },
      el("button", { class: "btn small ghost", type: "button", onclick: () => {
        app.editCode = s.code;
        renderAdd();
        show("add", "Edit bottle", { push: true });
      } }, "Edit details"),
      sittings ? null : el("button", { class: "btn small danger", type: "button", onclick: () => {
        if (!window.confirm(`Remove ${s.name || s.code} from your collection?`)) return;
        Store.removeLocalSpirit(s.code);
        back();
        if (currentScreen() !== "collection") setTab(app.tab);
      } }, "Remove")));
}

function renderSync() {
  const online = navigator.onLine;
  const q = Store.queue();
  const m = Store.meta();
  const signedIn = Graph.isSignedIn();
  const S = Sync.state;

  // Phone-only: no account, no queue, nothing to fetch. The screen is the choice itself, what
  // the phone holds, and the way to get a copy out of it.
  if (Store.isLocal()) {
    fill(screens.sync, localCard(q), workbookCard(), backupCard(), modeCard());
    return;
  }

  const rows = q.length
    ? q.map((i) => el("div", { class: `qrow${i.error ? " failed" : ""}` },
        el("span", { class: "kind" }, i.kind),
        el("span", { class: "qname" },
          i.path.split("/").pop().slice(0, 24),
          // Why it was refused, on the card itself: which one, and what OneDrive said.
          i.error ? el("span", { class: "qerr" }, i.error) : null),
        el("span", { class: "when" }, (i.created_at || "").slice(11, 16))))
    : [el("div", { class: "muted" }, "Nothing waiting.")];

  // The checklist of the run in progress or just finished: one row per step, what it is doing
  // or what it found. Drawn from memory, so a run that died with the app leaves no ghost.
  const steps = S.steps.length
    ? el("ul", { class: "steps" }, ...S.steps.map((s) =>
        el("li", { class: `step ${s.state}` },
          el("span", { class: "step-mark", "aria-hidden": "true" }),
          el("span", { class: "step-label" }, s.label),
          el("span", { class: "step-detail" }, s.detail))))
    : null;

  // What the last run changed. This run's if there was one; otherwise what meta remembers.
  const sum = S.summary ? { ...S.summary, at: S.finishedAt } : m.lastSummary;
  const summary = (() => {
    if (!sum || S.running) return null;
    const n = (k) => (sum[k] || []).length;
    const bits = [];
    if (n("added")) bits.push(`${n("added")} new`);
    if (n("removed")) bits.push(`${n("removed")} gone`);
    if (n("changed")) bits.push(`${n("changed")} changed`);
    if (n("newScores")) bits.push(`${n("newScores")} score${n("newScores") === 1 ? "" : "s"} changed`);
    if (sum.uploaded) bits.push(`${sum.uploaded} uploaded`);
    const names = (sum.added || []).slice(0, 3).map((c) => findSpirit(c)?.name || c);
    return el("div", { class: "muted" },
      `${sum.at ? `At ${Store.localClock(sum.at)}: ` : ""}${bits.length ? bits.join(", ") : "nothing changed"}`
      + (names.length ? ` — new: ${names.join(", ")}${n("added") > 3 ? "…" : ""}` : ""));
  })();

  fill(screens.sync,
    !Graph.configured()
      ? notice("warn", "Sign-in is not configured yet. Add the Application (client) ID from "
          + "guide/SETUP.md Part 3 to config.js and redeploy. Scoring works without it — cards queue "
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
      // What actually came down, so an empty screen elsewhere can be traced from here rather
      // than guessed at.
      el("div", { class: "muted" }, (() => {
        const k = Store.careers();
        const scored = Object.keys(k.careers || {}).length;
        const sittings = Object.values(k.sittings || {}).reduce((a, v) => a + v.length, 0);
        return `${Store.spirits().length} spirits · ${scored} scored · ${sittings} sittings`;
      })()),
      steps,
      summary,
      // The last thing that went wrong, in its own words. Silence here once meant an hour of
      // guessing between a sign-in problem, a network problem and a missing file.
      !S.running && (S.error || m.lastError)
        ? notice("warn", `Last attempt failed${(S.error ? S.finishedAt : m.lastErrorAt)
            ? ` at ${Store.localClock(S.error ? S.finishedAt : m.lastErrorAt)}` : ""}: ${S.error || m.lastError}`)
        : null,
      // Only once something has been synced: before that, "nothing published yet" is simply true.
      staleSource() && m.lastSync
        ? notice("warn", "The PC that last refreshed is running an older version of the app, so "
            + "some of what this phone needs was not published. Update it there and press Refresh.")
        : null,
      el("div", { class: "actions" },
        Graph.configured() && !signedIn
          ? el("button", { class: "btn", type: "button", onclick: signInFlow }, "Sign in")
          : null,
        Graph.configured() && signedIn
          ? el("button", { class: "btn", type: "button", disabled: S.running, onclick: refresh },
               S.running ? "Refreshing…" : "Refresh")
          : null,
        signedIn
          ? el("button", { class: "btn ghost", type: "button", disabled: S.running, onclick: signOutFlow },
               "Sign out")
          : null)),

    el("div", { class: "card" },
      el("div", { class: "muted", style: "margin-bottom:8px" },
        `${q.length} waiting to upload`),
      ...rows,
      q.length ? el("div", { class: "actions" },
        el("button", { class: "btn wide", type: "button",
                       disabled: !online || !signedIn || S.running,
                       onclick: flush }, "Upload now")) : null),
    backupCard(),
    // Last: it is chosen once. What the screen is opened for — is it in step, what is waiting —
    // comes first.
    modeCard());
}

// ---------------------------------------------------------------- sync wiring
/* The engine is docs/sync.js; this is what the page does with it. Every trigger goes through
   Sync.start, which decides whether anything needs doing — these three names only say why it
   was asked. The button and a cold start always pull; the rest pull when it has been a while
   and push the queue regardless. */
const refresh = () => Sync.start({ reason: "manual" });
const flush = () => Sync.start({ reason: "upload" });
const autoSync = (reason) => Sync.start({ reason });

/** Something in a file the PC publishes actually came down this run. */
const landed = (s) => s.steps.some((st) =>
  st.name !== "upload" && st.state === "done" && !/^(unchanged|not published)/.test(st.detail));

/* Called on every change of the sync state. While it runs, only the header's bar and the Sync
   screen move. When it ends with new data, whatever is on screen is redrawn in place — by what
   is visible, not by the tab, because a pushed detail screen reads the same figures — except a
   card being written or read, and never under a finger typing in a search box. */
function onSync(s) {
  paintStatus();
  updateBadge();
  const fresh = !s.running && landed(s);                        // something the PC published came down
  const sent = !s.running && !!(s.summary && s.summary.uploaded); // something of ours went up
  if (!fresh && !sent) {
    if (currentScreen() === "sync") renderSync();
    return;
  }
  if (fresh) {
    const kept = Store.careers();
    app.careers = kept.careers;
    app.calibration = kept.calibration;
    app.careersAt = kept.generatedAt;
    app.rubric = Store.rubric() || app.rubric;
  }
  if (document.body.classList.contains("keyboard")) return;
  const y = window.scrollY;
  switch (currentScreen()) {
    // Rows only, so the search box keeps its text — unless the screen was the "no collection
    // yet" notice, which has no rows to repaint.
    // A sync can change which types and regions there are to choose from, so the filters too.
    case "collection":
      if (!fresh) break;
      if (screens.collection.querySelector(".notice")) renderCollection();
      else { paintFilters(); paintRows(); }
      break;
    // Redrawn for an upload too: a sitting that has just gone up stops saying "waiting".
    case "detail": if (app.detailCode) openDetail(app.detailCode, { push: false }); break;
    case "compare": if (fresh) CompareView.render(screens.compare); break;
    case "analysis": if (fresh) AnalysisView.render(screens.analysis); break;
    case "sync": renderSync(); break;
    default: break;                      // score, sitting, add: a card in hand is left alone
  }
  window.scrollTo(0, y);
}

/** The app's sign-in sheet comes up over the page and goes away again; the page never moves.
    So what follows a sign-in — pull the snapshot, try the queue — is done here, and a failure
    is said out loud rather than left in a log nobody opens. */
async function signInFlow() {
  try {
    await Graph.signIn();
  } catch (e) {
    Store.setMeta({ lastError: String(e.message || e) });
    // Closing the sheet is not a failure, and is not announced as one.
    if (!e.cancelled) window.alert(`Sign-in failed: ${e.message || e}`);
  }
  if (Graph.isSignedIn()) {
    if (!Store.meta().mode) { Store.setMode("onedrive"); applyMode(); }   // signing in is the choice
    await refresh();                                      // pulls, then pushes the queue
  }
  else if (currentScreen() === "sync") renderSync();
}

async function signOutFlow() {
  try { await Graph.signOut(); } catch (e) { Store.setMeta({ lastError: String(e.message || e) }); }
  if (currentScreen() === "sync") renderSync();
}

function updateBadge() {
  const n = Store.queueCount();
  const badge = document.getElementById("tab-badge");
  badge.textContent = String(n);
  badge.hidden = n === 0 || Store.isLocal();            // nothing is waiting when nothing is sent
}

/** The header's one word of status and the thin bar beneath it. Offline outranks syncing, and
    neither shows when there is nothing to say. */
function paintStatus() {
  const net = document.getElementById("net");
  const online = navigator.onLine;
  const running = Sync.state.running;
  net.textContent = !online ? "offline" : running ? "syncing" : "";
  net.classList.toggle("off", !online);
  const bar = document.getElementById("syncbar");
  bar.hidden = !running;
  bar.style.transform = `scaleX(${running ? Math.max(0.08, Sync.progress()) : 0})`;
}

function updateNet() {
  const online = navigator.onLine;
  if (!online && !Store.meta().offlineSince) {
    Store.setMeta({ offlineSince: new Date().toISOString() });
  }
  if (online && Store.meta().offlineSince) Store.setMeta({ offlineSince: null });
  paintStatus();
}

// ---------------------------------------------------------------- boot
/* Where a page with no shell is still allowed to run: a browser on the machine it is being
   worked on, served from this folder or opened as a file. */
const DEV_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", ""]);

/** What the old web address shows. The phone client was once a web app there, installable to
    the Home Screen; it is the iPhone app now, and a page that went on half-working — scoring
    into a browser's storage with no way to send it anywhere — would be worse than one that says
    so. Whatever an old install left in this browser can still be taken away as a file, in the
    shape the app's Import reads. */
function retired() {
  document.getElementById("tabs").remove();
  document.getElementById("title").textContent = "Tasting Book";
  for (const [key, node] of Object.entries(screens)) node.hidden = key !== "collection";
  const held = Store.cards().length + Store.encounters().length + Store.queueCount()
    + Store.spirits().filter((s) => s._local).length;
  const save = () => {
    const url = URL.createObjectURL(new Blob([Store.exportAll()], { type: "application/json" }));
    const a = el("a", { href: url, download: `tasting-book-${Store.today()}.json` });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  };
  fill(screens.collection, el("div", { class: "card" },
    el("div", { class: "chart-title" }, "This is an iPhone app now"),
    el("div", { class: "muted" },
      "Whiskey Tasting Book no longer runs in a browser. Open the Tasting Book app on your "
      + "iPhone instead."),
    held ? el("div", { class: "muted", style: "margin-top:10px" },
      "This browser still holds scorecards or bottles from when it did. Download them as a "
      + "backup file; the app can read it under Backup, Import.") : null,
    held ? el("div", { class: "actions" },
      el("button", { class: "btn", type: "button", onclick: save }, "Download backup")) : null));
}

async function boot() {
  // This is the iPhone app's page. Anywhere else it is the retired web address — except on the
  // machine it is worked on, where a desktop browser is how the screens are looked at.
  if (!Native.available && !DEV_HOSTS.has(location.hostname)) { retired(); return; }

  // The bridge has to be up before anything else: graph.js and store.js act on what the shell
  // says it can do, and a backup can only be put back into a store that is still empty — so
  // both come before the first read. With no shell each resolves at once.
  await Native.init();
  await Store.restoreFromNative();

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

  // Every way the app comes back to life pulls what the PC published and pushes the queue:
  // the web view's online and visible events, and the shell's foreground, which is both of
  // those at once. Sync.start decides whether anything needs doing — a quick switch away and back
  // does not fetch again — and onSync redraws the screen when something new has landed.
  window.addEventListener("online", () => { updateNet(); autoSync("online"); });
  window.addEventListener("offline", updateNet);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) autoSync("visible"); });
  Native.on("foreground", () => { updateNet(); autoSync("foreground"); });
  Sync.onChange(onSync);

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
  applyMode();
  if (Store.meta().view === "table") app.view = "table";
  setTab("collection");

  // Not awaited: the screen is already up with what the phone holds, and the header's bar
  // says the rest is on its way.
  // A phone-only book never signs in: there is no token to ask for.
  if (Store.isLocal()) return;
  if (await Graph.resume()) {
    if (!Store.meta().mode) Store.setMode("onedrive");  // signed in before the choice existed
    autoSync("boot");
  }
}

boot();

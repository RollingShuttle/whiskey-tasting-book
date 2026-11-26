/* Whiskey Tasting Book — the compare view (SPEC.md §4.2, §3.6, §10).

   Two views of the same question — how do these stand against each other.

   **All scores** is the view as it opens: every spirit that has been scored, one row each, with
   the whole breakdown across — the total, the medal, the flavor total, every category, and how
   many sittings stand behind them. Any column sorts it. The header row and the name column stay
   put while the figures scroll under them. It is the phone's Compare table (docs/compare.js),
   drawn from what /api/table/collection already returns.

   **Side by side** is 2-4 things in columns, score rows aligned across them, every bar drawn
   against its own maximum so Flavor/20 reads at the same visual scale as Balance/10, per-axis
   deltas, and one shared notes pane underneath. It defaults to career scores; pinning a single
   flight is an explicit toggle, so a head-to-head from one night still works.

   Loaded after app.js and reuses its helpers (el, api, state, accentFor). */
"use strict";

const CompareView = (() => {
  const C = {
    mode: "career",        // "career" | "flight"
    codes: [],             // up to 4 spirit codes
    sessionId: null,
    sessions: [],
    data: null,
    search: "",
    picking: false,
    editing: false,        // the flight details form is open
    scored: null,          // codes that actually have a career score; null until fetched

    view: "scores",        // "scores": every score, as a table · "side": the chosen few
    rows: null,            // the table's rows; null until fetched
    sort: "total",
    desc: true,
    find: "",
  };
  // The desktop grid gives each card its own column and scrolls, so the only reason to stop is
  // legibility. A flight is not bound by this at all: it has the pours it has.
  const MAX = 12;
  const host = () => document.getElementById("compare-view");

  async function open(opts = {}) {
    if (opts.session) { C.mode = "flight"; C.sessionId = opts.session; C.view = "side"; }
    host().hidden = false;
    if (C.view === "scores") {
      if (!C.rows) await loadScores();
      render();
      return;
    }
    try { C.sessions = (await api("/api/sessions")).sessions || []; } catch { C.sessions = []; }
    await loadScored();
    await refresh();
  }

  /** Amend a flight. POST /api/session with its id writes a new revision rather than editing in
      place, so the evening as first recorded is still in the journal. */
  function flightForm() {
    const s = C.sessions.find((x) => x.session_id === C.sessionId);
    if (!s) return null;
    const draft = { title: s.title || "", date: s.date || "", location: s.location || "",
                    company: s.company || "" };
    const field = (key, label, type = "text") =>
      el("label", { class: "field" }, el("span", {}, label),
        el("input", { type, value: draft[key],
                      oninput: (e) => { draft[key] = e.target.value; } }));

    return el("div", { class: "cmp-editflight" },
      field("title", "Title"), field("date", "Date", "date"),
      field("location", "Location"), field("company", "Company"),
      el("button", { class: "btn", type: "button", onclick: async () => {
        try {
          await postJSON("/api/session", { session_id: C.sessionId, ...draft });
          C.sessions = (await api("/api/sessions")).sessions || [];
          C.editing = false;
          render();
          showStatus("ok", "Flight updated. The earlier version stays in the journal.");
        } catch (e) {
          showStatus("err", `Could not save the flight: ${e.body?.error || e.message}`);
        }
      } }, "Save"));
  }

  async function deleteFlight() {
    const s = C.sessions.find((x) => x.session_id === C.sessionId);
    if (!s) return;
    const name = `${s.title || "Untitled flight"} — ${s.date}`;
    if (!window.confirm(`Delete the flight "${name}"?\n\n`
        + `Its ${s.pours} pour${s.pours === 1 ? "" : "s"} are kept: they are sittings that `
        + "happened, and only the evening that grouped them is being taken off the books.")) return;
    try {
      await api(`/api/session/${encodeURIComponent(C.sessionId)}`, { method: "DELETE" });
      C.sessions = (await api("/api/sessions")).sessions || [];
      C.sessionId = null;
      C.editing = false;
      C.data = null;
      TableView.invalidate(); AnalysisView.invalidate();
      render();
      showStatus("ok", `Deleted the flight "${name}". Its pours are still in your reviews.`);
    } catch (e) {
      showStatus("err", `Could not delete the flight: ${e.body?.error || e.message}`);
    }
  }

  function query() {
    if (C.mode === "flight") {
      return C.sessionId ? `session=${encodeURIComponent(C.sessionId)}` : null;
    }
    return C.codes.length ? `codes=${C.codes.map(encodeURIComponent).join(",")}` : null;
  }

  async function refresh() {
    const q = query();
    if (!q) { C.data = null; render(); return; }
    try {
      C.data = await api(`/api/compare?${q}`);
    } catch (e) {
      C.data = null;
      showStatus("err", `Compare failed: ${e.body?.error || e.message}`);
    }
    render();
  }

  /** Called from the flight header — "Compare pours" (§4.2: reachable directly from a session). */
  function fromSession(sessionId) {
    C.mode = "flight"; C.sessionId = sessionId; C.picking = false; C.view = "side";
    showView("compare");
  }

  /** Which spirits have a score at all. Comparing an unscored bottle produces an empty column,
      so offering all 375 in the picker is offering 370 dead ends. /api/analysis is exactly this
      list — one point per scored spirit — so nothing new has to be computed for it. */
  async function loadScored() {
    try {
      const a = await api("/api/analysis");
      C.scored = new Set((a.points || []).map((p) => p.code));
    } catch {
      C.scored = null;     // on failure show everything rather than an empty picker
    }
  }

  function invalidate() { C.data = null; C.scored = null; C.rows = null; }

  // ------------------------------------------------------------- every score, as a table
  /** The switch between the two views. Each loads what it needs the first time it is shown. */
  function viewSwitch() {
    const way = (view, label) => el("button", {
      type: "button", class: `chip${C.view === view ? " active" : ""}`,
      onclick: () => { if (C.view !== view) { C.view = view; open(); } },
    }, label);
    return el("div", { class: "t-modes" }, way("scores", "All scores"), way("side", "Side by side"));
  }

  /** One row per spirit, from the collection table's endpoint: it already joins every spirit —
      bottles, retired ones, bar pours — to its career figures and its exact category means.
      Only the scored ones are kept: this is the record of what was given. */
  async function loadScores() {
    try {
      const data = await api("/api/table/collection");
      C.rows = (data.rows || []).filter((r) => r.n > 0 && r.career_score !== null && r.career_score !== undefined);
    } catch (e) {
      C.rows = [];
      showStatus("err", `Could not load the scores: ${e.body?.error || e.message}`);
    }
  }

  /** Medals sort by standing, not alphabetically — Bronze before Diamond would be nonsense. */
  function medalRank(name) {
    const bands = state.config.rubric.bands || [];
    const i = bands.findIndex((b) => b.name === name);
    return i === -1 ? null : bands.length - i;
  }

  /* The columns, built from the rubric so a category added to it gets a column here. `of` is
     what the sort runs on, `show` what the cell says, `max` what the figure is out of.

     Two totals stand first. "Total" is the whole card. "Flavor total" leaves out the categories
     the rubric marks as not flavour — the bottle's looks and its price — and is offered only
     when the rubric has such categories to leave out. It is summed from the exact means and
     rounded once (SPEC.md §3.6). The medal is always the whole card's. */
  function scoreColumns() {
    const rub = state.config.rubric;
    const cats = rub.categories || [];
    const flavour = flavourKeys();
    const has = (v) => v !== null && v !== undefined;
    const one = (v) => (has(v) ? Number(v).toFixed(1) : null);
    const whole = (v) => (has(v) ? String(v) : null);
    const mean = (r, key) => { const v = (r.category_means || {})[key]; return has(v) ? v : null; };
    const flavourTotal = (r) => (flavour.every((k) => mean(r, k) !== null)
      ? halfUp1(flavour.reduce((a, k) => a + mean(r, k), 0)) : null);
    return [
      { key: "total", label: "Total", max: rub.max_total, of: (r) => r.career_score, show: (r) => one(r.career_score) },
      { key: "medal", label: "Medal", word: true, of: (r) => medalRank(r.medal), show: (r) => r.medal || null },
      ...(flavour.length && flavour.length < cats.length ? [{
        key: "flavour", label: "Flavor total",
        max: cats.filter((c) => c.flavour).reduce((a, c) => a + c.max, 0),
        of: flavourTotal, show: (r) => one(flavourTotal(r)) }] : []),
      ...cats.map((c) => ({ key: `cat:${c.key}`, label: c.label || c.key, max: c.max,
        of: (r) => mean(r, c.key), show: (r) => one(mean(r, c.key)) })),
      { key: "n", label: "Sittings", of: (r) => r.n, show: (r) => whole(r.n) },
      { key: "best", label: "Best", of: (r) => r.best, show: (r) => whole(r.best) },
      { key: "worst", label: "Worst", of: (r) => r.worst, show: (r) => whole(r.worst) },
      { key: "per_dollar", label: "Score / $", of: (r) => r.score_per_dollar,
        show: (r) => (has(r.score_per_dollar) ? Number(r.score_per_dollar).toFixed(3) : null) },
    ];
  }

  const rowName = (r) => r.name || r.display_name || r.code;

  /** Something with no figure is not a low figure: it goes last whichever way the column runs.
      Equal figures are separated by how many sittings stand behind them, so a 90 from four
      nights outranks a 90 from one — and then by code, so the order never shuffles. */
  function orderScores(list, col) {
    const blank = (v) => v === null || v === undefined;
    const tie = (a, b) => (a.n !== b.n ? b.n - a.n
      : String(a.code).localeCompare(String(b.code), undefined, { numeric: true }));
    return list.sort((a, b) => {
      const x = col ? col.of(a) : rowName(a).toLowerCase(), y = col ? col.of(b) : rowName(b).toLowerCase();
      if (blank(x) || blank(y)) { if (blank(x) && blank(y)) return tie(a, b); return blank(x) ? 1 : -1; }
      if (x === y) return tie(a, b);
      if (typeof x === "number") return C.desc ? y - x : x - y;
      const c = String(x).localeCompare(String(y), undefined, { numeric: true });
      return C.desc ? -c : c;
    });
  }

  /** A column picked a second time turns round. A new one starts from the top — the best score,
      the highest medal — and the name from A. */
  function sortScores(key) {
    if (C.sort === key) C.desc = !C.desc;
    else { C.sort = key; C.desc = key !== "name"; }
    paintScores();
  }

  function renderScores() {
    const search = el("input", { class: "t-search", type: "text", value: C.find,
      placeholder: "Search scored spirits…", "aria-label": "Search scored spirits",
      oninput: (e) => { C.find = e.target.value; paintScores(); } });
    // The table is filled by paintScores, which a keystroke and a sort also call; the search box
    // above it is left alone, so typing keeps its place.
    host().replaceChildren(
      el("div", { class: "cmp-bar" }, viewSwitch(), search, el("span", { class: "sc-count", id: "sc-count" })),
      el("div", { class: "sc-wrap", id: "sc-wrap" }),
      el("div", { class: "cmp-foot" },
        "One row for every spirit that has been scored, finished bottles and bar pours included. "
        + "Click a column to sort by it, and again to turn it round; click a row for its sittings. "
        + "The flavor total leaves out the categories that are not flavor, and is summed from "
        + "exact means and rounded once (SPEC.md §3.6)."));
    paintScores();
  }

  function paintScores() {
    const wrap = document.getElementById("sc-wrap");
    const count = document.getElementById("sc-count");
    if (!wrap) return;
    const all = C.rows || [];
    if (!all.length) {
      wrap.replaceChildren(el("div", { class: "table-loading" },
        "Nothing has been scored yet. Score a pour and it is listed here, with its breakdown."));
      if (count) count.textContent = "";
      return;
    }
    const cols = scoreColumns();
    const col = cols.find((c) => c.key === C.sort) || null;        // null: by name
    const terms = C.find.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const list = orderScores(terms.length ? all.filter((r) => {
      const hay = `${r.code} ${r.display_name || ""} ${r.name || ""} ${r.distillery || ""} ${r.type || ""}`.toLowerCase();
      return terms.every((t) => hay.includes(t));
    }) : all.slice(), col);

    const arrow = (key) => (C.sort === key ? el("span", { class: "t-arrow" }, C.desc ? "▼" : "▲") : null);
    const head = el("tr", {},
      el("th", { class: `sc-name${C.sort === "name" ? " sorted" : ""}`, onclick: () => sortScores("name") },
        "Name", arrow("name")),
      ...cols.map((c) => el("th", {
        class: `${c.word ? "sc-word" : ""}${C.sort === c.key ? " sorted" : ""}`, onclick: () => sortScores(c.key) },
        c.label, arrow(c.key), c.max ? el("span", { class: "sc-max" }, `/ ${c.max}`) : null)));

    const body = el("tbody", {});
    if (!list.length) {
      body.append(el("tr", {}, el("td", { class: "sc-name t-null" }, "Nothing scored matches.")));
    }
    for (const r of list) {
      const sub = [r.source === "Encounter" ? "Bar pour" : r.source === "Retired" ? `${r.code} · retired` : r.code,
                   r.distillery, r.proof ? `${r.proof} pf` : null,
                   r.release_year ? String(Math.round(r.release_year)) : null].filter(Boolean).join(" · ");
      const tr = el("tr", { class: r.owned === false ? "gone" : "", title: "Open its sittings",
                            onclick: () => showSittingsFor(r.code) },
        el("td", { class: "sc-name", style: `border-left-color:${accentFor(r.type)}` },
          el("div", { class: "sc-title" }, rowName(r)), el("div", { class: "sc-sub" }, sub)));
      for (const c of cols) {
        const text = c.show(r);
        tr.append(el("td", { class: `${c.word ? "sc-word" : ""}${C.sort === c.key ? " sorted" : ""}${text === null ? " t-null" : ""}` },
          c.key === "medal" && r.medal
            ? el("span", { class: "medal medal-sm", style: `--m:${state.medalColors[r.medal] || "var(--muted)"}` }, r.medal)
            : (text === null ? "—" : text)));
      }
      body.append(tr);
    }
    wrap.replaceChildren(el("table", { class: "sc-table" }, el("thead", {}, head), body));
    if (count) {
      const label = col ? col.label.toLowerCase() : "name";
      count.textContent = `${list.length} scored · by ${label}`
        + (col ? (C.desc ? ", highest first" : ", lowest first") : (C.desc ? ", Z to A" : ", A to Z"));
    }
  }

  // ------------------------------------------------------------- selection
  function addCode(code) {
    if (C.codes.includes(code) || C.codes.length >= MAX) return;
    C.codes.push(code);
    C.picking = false; C.search = "";
    refresh();
  }
  function removeCode(code) {
    C.codes = C.codes.filter((c) => c !== code);
    refresh();
  }

  function selector() {
    const modeChip = (mode, label) => el("button", {
      type: "button", class: `chip${C.mode === mode ? " active" : ""}`,
      onclick: () => { C.mode = mode; refresh(); },
    }, label);

    const bar = el("div", { class: "cmp-bar" },
      el("div", { class: "t-modes" }, modeChip("career", "Career scores"),
                                       modeChip("flight", "Pin a flight")));

    if (C.mode === "flight") {
      const sel = el("select", { class: "t-select", "aria-label": "Flight",
        onchange: (e) => { C.sessionId = e.target.value || null; refresh(); } },
        el("option", { value: "" }, "Choose a flight…"),
        ...C.sessions.map((s) => el("option", { value: s.session_id },
          `${s.title || "Untitled flight"} — ${s.date} (${s.pours} pour${s.pours === 1 ? "" : "s"})`)));
      sel.value = C.sessionId || "";
      const picks = el("div", { class: "cmp-picks" }, sel);
      if (C.sessionId) {
        // A flight is a record like any other: it can be corrected, and it can be taken off the
        // books. Here, beside the one you have chosen, is the only place it is ever named.
        picks.append(
          el("button", { class: "ghost", type: "button",
                         onclick: () => { C.editing = !C.editing; render(); } },
             C.editing ? "Close" : "Edit flight"),
          el("button", { class: "ghost danger", type: "button", onclick: deleteFlight },
             "Delete flight"));
      }
      bar.append(picks);
      if (C.editing && C.sessionId) bar.append(flightForm());
      return bar;
    }

    const picks = el("div", { class: "cmp-picks" },
      ...C.codes.map((code) => {
        const sp = state.spirits.find((s) => s.code === code);
        return el("span", { class: "cmp-pick" },
          el("span", { class: "t-dot", style: `background:${accentFor(sp?.type)}` }),
          sp ? (sp.name || sp.display_name) : code,
          el("button", { type: "button", class: "cmp-x", "aria-label": `Remove ${code}`,
                         onclick: () => removeCode(code) }, "×"));
      }));

    if (C.codes.length < MAX) {
      picks.append(el("button", { class: "add-pour", type: "button",
        onclick: () => { C.picking = !C.picking; render(); } },
        C.codes.length ? "+ Add" : "+ Choose spirits"));
    }
    bar.append(picks);
    if (C.picking) bar.append(searchPanel());
    return bar;
  }

  function searchPanel() {
    const input = el("input", { class: "t-search", type: "text", value: C.search,
      placeholder: "Search by name, distillery or code…", "aria-label": "Search spirits",
      oninput: (e) => {
        C.search = e.target.value;
        const list = document.getElementById("cmp-results");
        if (list) list.replaceChildren(...results());
      } });
    const panel = el("div", { class: "cmp-search" }, input,
      el("ul", { class: "picker-results", id: "cmp-results" }, ...results()));
    setTimeout(() => input.focus(), 0);
    return panel;
  }

  function results() {
    const terms = C.search.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let list = state.spirits.filter((s) => !C.codes.includes(s.code)
                                           && (!C.scored || C.scored.has(s.code)));
    if (terms.length) {
      list = list.filter((s) => {
        const hay = `${s.code} ${s.display_name} ${s.type || ""}`.toLowerCase();
        return terms.every((t) => hay.includes(t));
      });
    }
    if (!list.length) {
      return [el("li", { class: "pr-empty" },
        C.scored && C.scored.size === 0
          ? "Nothing has been scored yet — score a pour and it becomes comparable."
          : "No scored spirit matches.")];
    }
    return list.map((s) => el("li", {
      role: "option", style: `border-left-color:${accentFor(s.type)}`,
      onclick: () => addCode(s.code),
    }, el("span", { class: "pr-name" }, s.name || s.display_name),
       el("span", { class: "pr-dist" }, s.distillery || ""),
       el("span", { class: "pr-meta" },
         [s.type, s.proof ? `${s.proof}pf` : null,
          s.release_year ? String(Math.round(s.release_year)) : null]
           .filter(Boolean).join(" · ")),
       el("span", { class: "pr-code" }, s.code)));
  }

  // ------------------------------------------------------------- the grid
  function render() {
    if (C.view === "scores") { renderScores(); return; }
    const parts = [viewSwitch(), selector()];
    const d = C.data;

    if (!d || !d.items.length) {
      parts.push(el("div", { class: "table-loading" },
        C.mode === "flight" ? "Choose a flight to compare its pours."
                            : "Choose two to four spirits to compare."));
      host().replaceChildren(...parts);
      return;
    }

    const items = d.items;
    const cols = `170px repeat(${items.length}, minmax(130px, 1fr)) 54px`;
    const grid = el("div", { class: "cmp-grid", style: `grid-template-columns:${cols}` });

    grid.append(el("div", { class: "cmp-corner" }));
    for (const it of items) {
      grid.append(el("div", { class: "cmp-head", style: `border-top-color:${accentFor(it.type)}` },
        el("div", { class: "cmp-name" }, it.label),
        // The code, because three bottles here really are called George T. Stagg and two columns
        // under the same heading is precisely the moment you need to tell them apart.
        el("div", { class: "cmp-sub" },
          [it.code,
           it.proof ? `${it.proof} pf` : null,
           it.release_year ? String(Math.round(it.release_year)) : null,
           it.mode === "career" ? `career · n=${it.n}` : (it.date || "one sitting")]
            .filter(Boolean).join("  ·  ")),
        el("div", { class: "cmp-total" },
          it.total === null || it.total === undefined ? "—"
            : (it.mode === "career" ? Number(it.total).toFixed(1) : String(it.total)),
          it.medal ? el("span", { class: "medal medal-sm",
            style: `--m:${state.medalColors[it.medal] || "var(--muted)"}` }, it.medal) : null)));
    }
    grid.append(el("div", { class: "cmp-head cmp-delta-head" }, "Δ"));

    for (const ax of d.axes) {
      grid.append(el("div", { class: "cmp-axis" },
        el("span", {}, ax.label), el("span", { class: "cmp-max" }, `/ ${ax.max}`)));
      for (const it of items) {
        const v = ax.values[it.key];
        const lead = ax.leader === it.key;
        const cell = el("div", { class: `cmp-cell${lead ? " lead" : ""}` });
        if (v === null || v === undefined) {
          cell.append(el("span", { class: "t-null" }, "—"));
        } else {
          // every bar against its own maximum — Flavor/20 reads like Balance/10 (§10)
          cell.append(
            el("div", { class: "cmp-track" },
              el("div", { class: "cmp-fill", style: `width:${(v / ax.max) * 100}%` })),
            el("span", { class: "cmp-val" }, Number.isInteger(v) ? String(v) : v.toFixed(1)));
          const r = it.ranges && it.ranges[ax.key];
          if (r && r[0] !== r[1]) cell.append(el("span", { class: "cmp-range" }, `${r[0]}–${r[1]}`));
        }
        grid.append(cell);
      }
      grid.append(el("div", { class: "cmp-delta" }, ax.spread ? String(ax.spread) : "—"));
    }

    parts.push(el("div", { class: "cmp-scroll" }, grid));
    parts.push(el("div", { class: "cmp-foot" },
      "Totals are the headline figure. The category column holds display values and is deliberately "
      + "not summed — ten roundings drift (SPEC.md §3.6)."));
    parts.push(notesPane(items));
    host().replaceChildren(...parts);
  }

  // ------------------------------------------------------------- shared notes pane
  function notesPane(items) {
    const pane = el("div", { class: "cmp-notes" }, el("div", { class: "cmp-notes-h" }, "Notes"));
    const labels = Object.fromEntries((C.data.categories || []).map((c) => [c.key, c.label]));
    let any = false;
    for (const it of items) {
      const entries = Object.entries(it.notes || {}).filter(([, v]) => v && String(v).trim());
      if (!entries.length && !it.overall_note) continue;
      any = true;
      pane.append(el("div", { class: "cmp-note" },
        el("div", { class: "cmp-note-name", style: `border-left-color:${accentFor(it.type)}` },
          it.label),
        it.overall_note ? el("div", { class: "cmp-note-overall" }, it.overall_note) : null,
        ...entries.map(([k, v]) =>
          el("div", { class: "cmp-note-row" },
            el("span", { class: "cmp-note-cat" }, labels[k] || k), el("span", {}, v)))));
    }
    if (!any) pane.append(el("div", { class: "table-loading" }, "No notes on these cards yet."));
    return pane;
  }

  return { open, fromSession, invalidate };
})();

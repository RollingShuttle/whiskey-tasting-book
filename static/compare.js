/* Whiskey Tasting Book — the compare view (SPEC.md §4.2, §3.6, §10).
   2-4 things side by side, score rows aligned across columns, every bar drawn against its own
   maximum so Flavor/20 reads at the same visual scale as Balance/10, per-axis deltas, and one
   shared notes pane underneath.

   Defaults to career scores. Pinning a single flight is an explicit toggle, so a head-to-head
   from one night still works.

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
  };
  const MAX = 4;
  const host = () => document.getElementById("compare-view");

  async function open(opts = {}) {
    if (opts.session) { C.mode = "flight"; C.sessionId = opts.session; }
    host().hidden = false;
    try { C.sessions = (await api("/api/sessions")).sessions || []; } catch { C.sessions = []; }
    await refresh();
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
    C.mode = "flight"; C.sessionId = sessionId; C.picking = false;
    showView("compare");
  }

  function invalidate() { C.data = null; }

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
      bar.append(el("div", { class: "cmp-picks" }, sel));
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
    let list = state.spirits.filter((s) => !C.codes.includes(s.code));
    if (terms.length) {
      list = list.filter((s) => {
        const hay = `${s.code} ${s.display_name} ${s.type || ""}`.toLowerCase();
        return terms.every((t) => hay.includes(t));
      });
    }
    return list.slice(0, 25).map((s) => el("li", {
      role: "option", style: `border-left-color:${accentFor(s.type)}`,
      onclick: () => addCode(s.code),
    }, el("span", { class: "pr-name" }, s.name || s.display_name),
       el("span", { class: "pr-dist" }, s.distillery || ""),
       el("span", { class: "pr-code" }, s.code)));
  }

  // ------------------------------------------------------------- the grid
  function render() {
    const parts = [selector()];
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
        el("div", { class: "cmp-sub" }, it.mode === "career"
          ? `career · n=${it.n}` : (it.date || "one sitting")),
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

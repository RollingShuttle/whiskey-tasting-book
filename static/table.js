/* Whiskey Tasting Book — the table view (SPEC.md §4.3, §3.6, §10).
   Every spirit or every tasting, sortable and filterable on every field, with the two columns the
   spreadsheet cannot produce: $ / oz and score / $. Column chooser and CSV export.

   Loaded after app.js and reuses its helpers (el, api, state). */
"use strict";

const TableView = (() => {
  const T = {
    mode: "collection",              // "collection" | "tastings"
    data: {},                        // mode -> {columns, rows}
    sort: {},                        // mode -> {key, dir}
    visible: {},                     // mode -> Set of column keys
    filters: { q: "", type: "", region: "", rarity: "", status: "", source: "all", scored: false },
    chooser: false,
    lens: null,                      // {id, keys, custom} — set once config is loaded
  };

  // Collection sorts on the career score, ties broken on n, descending (SPEC.md §3.6).
  const DEFAULT_SORT = {
    collection: { key: "career_score", dir: "desc" },
    tastings: { key: "date", dir: "desc" },
  };
  const FACETS = ["type", "region", "rarity", "status"];

  const cols = () => T.data[T.mode]?.columns || [];
  const rows = () => T.data[T.mode]?.rows || [];
  const isVisible = (key) => T.visible[T.mode]?.has(key);

  async function load(force = false) {
    if (T.data[T.mode] && !force) return;
    const d = await api(`/api/table/${T.mode}`);
    T.data[T.mode] = d;
    if (!T.visible[T.mode]) {
      T.visible[T.mode] = new Set(d.columns.filter((c) => c.default).map((c) => c.key));
    }
    if (!T.sort[T.mode]) T.sort[T.mode] = { ...DEFAULT_SORT[T.mode] };
    if (!T.lens) T.lens = { id: "flavour", keys: flavourKeys(), custom: false };
  }

  async function open() {
    const host = document.getElementById("table-view");
    host.hidden = false;
    host.replaceChildren(el("div", { class: "table-loading" }, "Loading…"));
    try {
      await load();
      render();
    } catch (e) {
      host.replaceChildren(el("div", { class: "table-loading" },
        `Could not load the table: ${e.body?.error || e.message}`));
    }
  }

  async function setMode(mode) {
    T.mode = mode;
    await load();
    render();
  }

  // ------------------------------------------------------------- filter + sort
  function filtered() {
    const f = T.filters;
    const terms = f.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return rows().filter((r) => {
      if (f.source === "owned" && r.owned !== true) return false;
      if (f.source === "encounter" && r.owned !== false) return false;
      if (f.scored && !(r.n > 0 || r.total !== undefined)) return false;
      for (const k of FACETS) if (f[k] && r[k] !== f[k]) return false;
      if (terms.length) {
        const hay = `${r.code || ""} ${r.display_name || ""} ${r.type || ""} ${r.region || ""} `
          + `${r.venue || ""} ${r.medal || ""}`.toLowerCase();
        if (!terms.every((t) => hay.includes(t))) return false;
      }
      return true;
    });
  }

  function sorted(list) {
    const { key, dir } = T.sort[T.mode];
    const mult = dir === "asc" ? 1 : -1;
    const cmp = (a, b) => {
      const scoreCol = key === lensKey();
      const av = scoreCol ? a.lens_score : a[key];
      const bv = scoreCol ? b.lens_score : b[key];
      // Unscored rows always sink, whichever way the column is sorted.
      const an = av === null || av === undefined || av === "";
      const bn = bv === null || bv === undefined || bv === "";
      if (an && bn) return 0;
      if (an) return 1;
      if (bn) return -1;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * mult;
      return String(av).localeCompare(String(bv), undefined, { numeric: true }) * mult;
    };
    return [...list].sort((a, b) => {
      const primary = cmp(a, b);
      if (primary !== 0) return primary;
      if (T.mode === "collection" && key === "career_score") {
        return ((b.n || 0) - (a.n || 0));          // ties break on n, descending (§3.6)
      }
      return String(a.code || "").localeCompare(String(b.code || ""), undefined, { numeric: true });
    });
  }

  /** The score column shows the lens, not the stored total: collection rows total the exact
      career means, tasting rows total that sitting's integers. */
  const lensKey = () => (T.mode === "collection" ? "career_score" : "total");
  function withLens(list) {
    return list.map((r) => ({
      ...r,
      lens_score: lensScore(T.mode === "collection" ? r.category_means : r.scores,
                            T.lens.keys),
    }));
  }

  const visibleRows = () => sorted(withLens(filtered()));

  // ------------------------------------------------------------- formatting
  function fmt(v, type) {
    if (v === null || v === undefined || v === "") return null;
    switch (type) {
      case "money":  return `$${Number(v).toFixed(2)}`;
      case "num":    return Number.isInteger(Number(v)) ? String(Number(v)) : Number(v).toFixed(1);
      case "num3":   return Number(v).toFixed(3);
      case "int":    return String(v);
      case "score":  return String(v);
      case "score1": return Number(v).toFixed(1);
      default:       return String(v);
    }
  }

  function cell(row, col) {
    const raw = col.key === lensKey() ? row.lens_score : row[col.key];
    const text = fmt(raw, col.type);
    if (col.type === "medal" && !lensIsEverything(T.lens.keys)) {
      // A medal band is calibrated to the full 100-point card. Showing one beside a 90-point
      // flavour score would be a different claim wearing the same chip (SPEC.md §3).
      return el("td", { class: "t-null",
        title: "medals come from the full 100-point card; this ranking is a subset" }, "—");
    }
    if (text === null) return el("td", { class: "t-null" }, "—");
    if (col.type === "medal") {
      return el("td", {}, el("span", { class: "medal medal-sm",
        style: `--m:${state.medalColors[raw] || "var(--muted)"}` }, raw));
    }
    if (col.type === "name") {
      return el("td", { class: "t-name" },
        el("span", { class: "t-dot", style: `background:${accentFor(row.type)}` }), text);
    }
    const cls = { code: "t-code", money: "t-num", num: "t-num", num3: "t-num", int: "t-num",
                  score: "t-score", score1: "t-score" }[col.type] || "";
    return el("td", { class: cls }, text);
  }

  // ------------------------------------------------------------- render
  function render() {
    const host = document.getElementById("table-view");
    const list = visibleRows();
    host.replaceChildren(toolbar(list), tableEl(list), footer(list));
  }

  // ------------------------------------------------------------- the ranking lens
  function lensBar() {
    const presets = lensPresets();
    const active = presets.find((p) => sameKeys(p.keys, T.lens.keys));
    const bar = el("div", { class: "lens-bar" },
      el("span", { class: "lens-label" }, "Rank by"),
      ...presets.map((p) => el("button", {
        type: "button", class: `chip${p === active ? " active" : ""}`,
        onclick: () => { T.lens = { id: p.id, keys: p.keys, custom: false }; render(); },
      }, p.label)),
      el("button", {
        type: "button", class: `add-pour${T.lens.custom ? " open" : ""}`,
        onclick: () => { T.lens = { ...T.lens, custom: !T.lens.custom }; render(); },
      }, T.lens.custom ? "Custom ▴" : "Custom ▾"));
    if (T.lens.custom) bar.append(lensPicker());
    return bar;
  }

  function lensPicker() {
    const keys = T.lens.keys;
    return el("div", { class: "lens-picker" },
      ...state.config.rubric.categories.map((c) =>
        el("label", { class: "t-choice" },
          el("input", { type: "checkbox", checked: keys.includes(c.key),
            onchange: (e) => {
              const next = new Set(T.lens.keys);
              if (e.target.checked) next.add(c.key); else next.delete(c.key);
              T.lens = { id: "custom", keys: [...next], custom: true };
              render();
            } }),
          `${c.label} ${c.max}`)),
      el("span", { class: "lens-total" }, keys.length
        ? `${keys.length} selected · out of ${lensMax(keys)}`
        : "nothing selected — pick at least one category"));
  }

  function toolbar(list) {
    const f = T.filters;
    const modeBtn = (mode, label) =>
      el("button", { type: "button", class: `chip${T.mode === mode ? " active" : ""}`,
                     onclick: () => setMode(mode) }, label);

    const facetSelect = (key) => {
      const values = [...new Set(rows().map((r) => r[key]).filter(Boolean))].sort();
      if (!values.length) return null;
      const sel = el("select", { class: "t-select", "aria-label": key,
        onchange: (e) => { f[key] = e.target.value; render(); } },
        el("option", { value: "" }, key[0].toUpperCase() + key.slice(1)),
        ...values.map((v) => el("option", { value: v, selected: f[key] === v }, v)));
      sel.value = f[key];
      return sel;
    };

    const controls = el("div", { class: "t-controls" },
      el("input", { class: "t-search", type: "text", placeholder: "Filter…", value: f.q,
                    "aria-label": "Filter rows",
                    oninput: (e) => { f.q = e.target.value; render(); } }),
      ...(T.mode === "collection" ? FACETS.map(facetSelect).filter(Boolean) : []),
      T.mode === "collection"
        ? el("select", { class: "t-select", "aria-label": "Ownership",
            onchange: (e) => { f.source = e.target.value; render(); } },
            el("option", { value: "all" }, "Owned + tasted"),
            el("option", { value: "owned" }, "Owned only"),
            el("option", { value: "encounter" }, "Tasted only"))
        : null,
      el("label", { class: "blindtoggle" },
        el("input", { type: "checkbox", checked: f.scored,
                      onchange: (e) => { f.scored = e.target.checked; render(); } }), "Scored only"),
      el("button", { type: "button", class: "ghost",
                     onclick: () => { T.chooser = !T.chooser; render(); } }, "Columns"),
      el("button", { type: "button", class: "ghost", onclick: () => exportCSV(list) }, "CSV"));

    const sel = controls.querySelector('select[aria-label="Ownership"]');
    if (sel) sel.value = f.source;

    const bar = el("div", { class: "t-toolbar" },
      el("div", { class: "t-modes" }, modeBtn("collection", "Collection"),
                                       modeBtn("tastings", "Tastings")),
      controls);

    if (T.chooser) bar.append(chooser());
    return el("div", {}, lensBar(), bar);
  }

  function chooser() {
    return el("div", { class: "t-chooser" },
      ...cols().map((c) => el("label", { class: "t-choice" },
        el("input", { type: "checkbox", checked: isVisible(c.key),
          onchange: (e) => {
            const set = T.visible[T.mode];
            if (e.target.checked) set.add(c.key); else set.delete(c.key);
            render();
          } }), c.label)));
  }

  function tableEl(list) {
    const shown = cols().filter((c) => isVisible(c.key));
    const { key: sortKey, dir } = T.sort[T.mode];

    const head = el("tr", {}, ...shown.map((c) => {
      const active = c.key === sortKey;
      return el("th", {
        class: `${active ? "sorted" : ""} ${["money", "num", "num3", "int", "score", "score1"]
          .includes(c.type) ? "t-num" : ""}`,
        onclick: () => {
          const s = T.sort[T.mode];
          if (s.key === c.key) s.dir = s.dir === "asc" ? "desc" : "asc";
          else { s.key = c.key; s.dir = ["text", "name", "code", "medal"].includes(c.type) ? "asc" : "desc"; }
          render();
        },
      }, c.key === lensKey() ? `${c.label} / ${lensMax(T.lens.keys)}` : c.label,
         active ? el("span", { class: "t-arrow" }, dir === "asc" ? "▲" : "▼") : null);
    }));

    const body = el("tbody", {}, ...list.map((r) =>
      el("tr", {}, ...shown.map((c) => cell(r, c)))));

    if (!list.length) {
      body.append(el("tr", {}, el("td", { class: "t-null", colspan: String(shown.length) },
        "Nothing matches those filters.")));
    }
    return el("div", { class: "t-scroll" }, el("table", { class: "t-table" },
      el("thead", {}, head), body));
  }

  function footer(list) {
    const scored = list.filter((r) => (r.n > 0) || r.total !== undefined).length;
    const bits = T.mode === "collection"
      ? `${list.length} of ${rows().length} spirits · ${scored} scored`
      : `${list.length} of ${rows().length} tastings`;
    return el("div", { class: "t-footer" }, bits);
  }

  // ------------------------------------------------------------- CSV
  function csvCell(v) {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function exportCSV(list) {
    const shown = cols().filter((c) => isVisible(c.key));
    const lines = [shown.map((c) => csvCell(c.label)).join(",")];
    for (const r of list) {
      lines.push(shown.map((c) => csvCell(c.key === lensKey() ? r.lens_score : r[c.key]))
                      .join(","));
    }
    const stamp = new Date().toISOString().slice(0, 10);
    const blob = new Blob(["﻿" + lines.join("\r\n")],   // BOM so Excel reads UTF-8
                          { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: `whiskey-${T.mode}-${stamp}.csv` });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /** Called after a submit so the table is not stale next time it is opened. */
  function invalidate() { T.data = {}; }

  return { open, invalidate };
})();

/* Whiskey Tasting Book — compare on the phone (SPEC.md §4.2, §9.2).

   Two views of the same question — how do these stand against each other.

   **All scores** is the tab as it opens: every spirit that has been scored, one row each, with
   the whole breakdown across — the total, the medal, the flavour total, every category, and how
   many sittings stand behind them. Any column sorts it. It is laid out like a spreadsheet,
   because it is one: the header row and the name column stay put while the figures scroll under
   them, so a number forty rows down and eight columns across still says whose it is and what.

   **Side by side** is two to six chosen spirits as bars, one row per category with a bar per
   spirit, each axis drawn against its own maximum — Flavor out of 20 and Balance out of 10 are
   comparable at a glance rather than one looking twice as good for being out of twice as much.

   Per-category means come from the PC in careers.json. The phone cannot derive them for a spirit
   it did not score itself — it holds its own cards, not the journal — so anything scored only on
   the desktop contributes its totals and its categories arrive published. */
"use strict";

const CompareView = (() => {
  let codes = [];
  let picking = false;
  let query = "";

  let view = "table";       // "table": every score · "bars": the chosen few, side by side
  let sortKey = "total";
  let desc = true;
  let find = "";            // the table's own search

  // Wider than four, but not the desktop's dozen: the totals sit side by side across 375
  // points, and past six they are too narrow to read. The per-category bars stack, so they cope.
  const MAX = 6;

  /** Shared with the table, so a comparison and a ranking cannot disagree about the same
      spirit — and so both fold in this phone's own cards the same way. */
  const categoriesFor = (code) => categoryMeansFor(code);

  function chosen() {
    return codes.map((code) => {
      const s = findSpirit(code);
      const career = careerFor(code);
      return s && career
        ? { code, spirit: s, career, categories: categoriesFor(code) }
        : null;
    }).filter(Boolean);
  }

  function repaint() {
    render(document.getElementById("screen-compare"));
  }

  function add(code) {
    if (!codes.includes(code) && codes.length < MAX) codes.push(code);
    picking = false;
    query = "";
    repaint();
  }

  function drop(code) {
    codes = codes.filter((c) => c !== code);
    repaint();
  }

  // ------------------------------------------------------------------ picker
  function picker() {
    const scored = knownSpirits()
      .filter((s) => careerFor(s.code) && !codes.includes(s.code));
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const shown = terms.length
      ? scored.filter((s) => {
        const hay = (s.code + " " + (s.display_name || "") + " " + (s.distillery || "") + " "
                     + (s.name || "") + " " + (s.type || "")).toLowerCase();
        return terms.every((t) => hay.includes(t));
      })
      : scored;

    const search = el("input", {
      class: "search", type: "text", value: query, enterkeyhint: "search",
      placeholder: "Search scored spirits", "aria-label": "Search scored spirits",
      autocapitalize: "none", autocorrect: "off", spellcheck: "false",
      oninput: (e) => { query = e.target.value; paintPickerRows(); },
    });
    const list = el("ul", { class: "rows", id: "cmp-rows" });
    const host = el("div", {}, search, list);
    setTimeout(paintPickerRows, 0);
    return host;

    function paintPickerRows() {
      const rows = document.getElementById("cmp-rows");
      if (!rows) return;
      const terms2 = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const now = terms2.length
        ? scored.filter((s) => {
          const hay = (s.code + " " + (s.display_name || "") + " " + (s.distillery || "") + " "
                       + (s.name || "") + " " + (s.type || "")).toLowerCase();
          return terms2.every((t) => hay.includes(t));
        })
        : shown;
      rows.replaceChildren();
      if (!now.length) {
        rows.append(el("li", { class: "empty" }, "Nothing scored matches."));
        return;
      }
      for (const s of now) {
        const c = careerFor(s.code);
        rows.append(el("li", {
          class: "row", style: "border-left-color:" + accentFor(s.type),
          onclick: () => add(s.code),
        },
          el("div", { class: "row-main" },
            el("div", { class: "row-name" }, s.name || s.display_name || s.code),
            el("div", { class: "row-sub" },
              [s.distillery, s.type, s.proof ? s.proof + " pf" : null,
               s.release_year ? String(Math.round(s.release_year)) : null]
                .filter(Boolean).join(" · "))),
          scoreCell(c)));
      }
    }
  }

  // ------------------------------------------------------------------ every score, as a table
  /** Medals sort by standing, not alphabetically — Bronze before Diamond would be nonsense. */
  function medalRank(name) {
    if (!name || !app.rubric) return null;
    const bands = app.rubric.bands || [];
    const i = bands.findIndex((b) => b.name === name);
    return i === -1 ? null : bands.length - i;
  }

  /* The columns, built from the rubric so a category added to it gets a column here. `of` is
     what the sort runs on, `show` what the cell says, `max` what the figure is out of.

     Two totals stand first. "Total" is the whole card. "Flavor total" leaves out the categories
     the rubric marks as not flavour — the bottle's looks and its price — which is the question
     "which is the better whiskey" rather than "which was the better buy"; it is offered only
     when the rubric has such categories to leave out. The medal is always the whole card's: the
     bands are defined against 100. */
  function columns() {
    const cats = (app.rubric && app.rubric.categories) || [];
    const flavour = flavourKeys();
    const one = (v) => (v === null || v === undefined ? "—" : v.toFixed(1));
    const whole = (v) => (v === null || v === undefined ? "—" : String(v));
    return [
      { key: "total", label: "Total", pick: "Total (the whole card)",
        max: app.rubric ? app.rubric.max_total : 100, of: (r) => r.total, show: (r) => one(r.total) },
      { key: "medal", label: "Medal", word: true, of: (r) => r.medalRank, show: (r) => r.medal || "—" },
      ...(flavour.length && flavour.length < cats.length ? [{
        key: "flavour", label: "Flavor total", pick: "Flavor total (no " + cats.filter((c) => !c.flavour)
          .map((c) => (c.label || c.key).toLowerCase()).join(" or ") + ")",
        max: lensMax(flavour), of: (r) => r.flavour, show: (r) => one(r.flavour) }] : []),
      ...cats.map((c) => ({ key: "cat:" + c.key, label: c.label || c.key, max: c.max,
        of: (r) => (r.cats ? r.cats[c.key] : null), show: (r) => one(r.cats ? r.cats[c.key] : null) })),
      { key: "n", label: "Sittings", of: (r) => r.n, show: (r) => whole(r.n) },
      { key: "best", label: "Best", of: (r) => r.best, show: (r) => whole(r.best) },
      { key: "worst", label: "Worst", of: (r) => r.worst, show: (r) => whole(r.worst) },
      { key: "per_dollar", label: "Score / $", of: (r) => r.perDollar,
        show: (r) => (r.perDollar === null ? "—" : r.perDollar.toFixed(3)) },
    ];
  }

  /** One row for every spirit that has a score: bottles, finished ones, bar pours. This is the
      record of what was given, so nothing scored is left out of it. */
  function scoreRows() {
    const flavour = flavourKeys();
    return knownSpirits().map((s) => {
      const career = careerFor(s.code);
      if (!career) return null;
      const cats = categoriesFor(s.code);
      const paid = Number(s.paid);
      return {
        code: s.code, spirit: s,
        name: s.name || s.display_name || s.code,
        sub: [s._encounter ? "Bar" : s.code, s.proof ? s.proof + " pf" : null,
              s.release_year ? String(Math.round(s.release_year)) : null].filter(Boolean).join(" · "),
        total: career.score, medal: career.medal || null, medalRank: medalRank(career.medal),
        flavour: lensScore(cats, flavour), cats,
        n: career.n || 0,
        best: career.best === undefined ? null : career.best,
        worst: career.worst === undefined ? null : career.worst,
        perDollar: Number.isFinite(paid) && paid > 0 ? Math.round((career.score / paid) * 1000) / 1000 : null,
        owned: s.owned !== false,
      };
    }).filter(Boolean);
  }

  /** Something with no figure is not a low figure: it goes last whichever way the column runs.
      Equal figures are separated by how many sittings stand behind them, so a 90 from four
      nights outranks a 90 from one — and then by code, so the order never shuffles. */
  function ordered(list, col) {
    const blank = (v) => v === null || v === undefined;
    return list.sort((a, b) => {
      const x = col ? col.of(a) : a.name.toLowerCase(), y = col ? col.of(b) : b.name.toLowerCase();
      if (blank(x) || blank(y)) { if (blank(x) && blank(y)) return tie(a, b); return blank(x) ? 1 : -1; }
      if (x === y) return tie(a, b);
      if (typeof x === "number") return desc ? y - x : x - y;
      const c = String(x).localeCompare(String(y), undefined, { numeric: true });
      return desc ? -c : c;
    });
    function tie(a, b) {
      if (a.n !== b.n) return b.n - a.n;
      return a.code.localeCompare(b.code, undefined, { numeric: true });
    }
  }

  /** A column picked a second time turns round. A new one starts from the top — the best score,
      the highest medal — and the name from A. */
  function sortBy(key) {
    if (sortKey === key) desc = !desc;
    else { sortKey = key; desc = key !== "name"; }
    paintScores();
  }

  /** The switch between the tab's two views. */
  function switcher() {
    const way = (id, label) => el("button", {
      type: "button", class: view === id ? "active" : "", "aria-pressed": String(view === id),
      onclick: () => { if (view === id) return; view = id; picking = false; repaint(); },
    }, label);
    return el("div", { class: "viewswitch", role: "group", "aria-label": "Compare as" },
      way("table", "All scores"), way("bars", "Side by side"));
  }

  function renderScores(host) {
    const search = el("input", {
      class: "search", type: "text", value: find, enterkeyhint: "search",
      placeholder: "Search scored spirits", "aria-label": "Search scored spirits",
      autocapitalize: "none", autocorrect: "off", spellcheck: "false",
      oninput: (e) => { find = e.target.value; paintScores(); },
    });
    // The bar and the table are filled by paintScores, which a keystroke and a sort also call;
    // the search box above them is left alone, so typing keeps its place.
    fill(host, el("div", { class: "scores" },
      el("div", { class: "viewbar", id: "cmp-bar" }),
      search,
      el("div", { class: "stable-wrap", id: "cmp-table" }),
      el("div", { class: "chart-basis", id: "cmp-count" })));
    paintScores();
  }

  function paintScores() {
    const bar = document.getElementById("cmp-bar");
    const wrap = document.getElementById("cmp-table");
    const count = document.getElementById("cmp-count");
    if (!bar || !wrap) return;
    const cols = columns();
    const col = cols.find((c) => c.key === sortKey) || null;      // null: by name
    const label = col ? col.label : "Name";

    fill(bar, switcher(), el("span", { class: "grow" }),
      pickerChip(`${label} ${desc ? "▾" : "▴"}`,
        cols.map((c) => [c.key, c.pick || c.label]).concat([["name", "Name"]]), col ? col.key : "name",
        (key) => { if (key !== sortKey) sortBy(key); }, "Sort by"));

    const terms = find.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const all = scoreRows();
    const list = ordered(terms.length ? all.filter((r) => {
      const hay = (r.code + " " + r.name + " " + (r.spirit.distillery || "") + " " + (r.spirit.type || "")).toLowerCase();
      return terms.every((t) => hay.includes(t));
    }) : all, col);

    if (!all.length) {
      fill(wrap, el("div", { class: "muted chart-empty" },
        "Nothing has been scored yet. Score a pour and it is listed here, with its breakdown."));
      if (count) count.textContent = "";
      return;
    }

    const head = el("tr", {},
      el("th", { class: "s-name" + (sortKey === "name" ? " sorted" : ""), onclick: () => sortBy("name") },
        "Name", sortKey === "name" ? el("span", { class: "t-caret" }, desc ? " ▾" : " ▴") : null),
      ...cols.map((c) => el("th", {
        class: (c.word ? "s-word" : "") + (sortKey === c.key ? " sorted" : ""), onclick: () => sortBy(c.key) },
        c.label, sortKey === c.key ? el("span", { class: "t-caret" }, desc ? " ▾" : " ▴") : null,
        c.max ? el("span", { class: "s-max" }, "/ " + c.max) : null)));

    const body = el("tbody", {});
    if (!list.length) {
      body.append(el("tr", {}, el("td", { class: "s-name muted" }, "Nothing scored matches.")));
    }
    for (const r of list) {
      const tr = el("tr", { class: r.owned ? "" : "gone", onclick: () => openDetail(r.code) },
        el("td", { class: "s-name", style: "border-left-color:" + accentFor(r.spirit.type) },
          el("div", { class: "s-title" }, r.name), el("div", { class: "s-sub" }, r.sub)));
      for (const c of cols) {
        tr.append(el("td", { class: (c.word ? "s-word" : "") + (sortKey === c.key ? " sorted" : "") },
          c.key === "medal" && r.medal
            ? el("span", { class: "medal", style: "--m:" + (MEDAL_COLORS[r.medal] || "#9A9086") }, r.medal)
            : c.show(r)));
      }
      body.append(tr);
    }
    fill(wrap, el("table", { class: "stable" }, el("thead", {}, head), body));
    if (count) {
      count.textContent = `${list.length} scored, by ${label.toLowerCase()}`
        + (col && col.key !== "medal" ? (desc ? ", highest first" : ", lowest first")
          : col ? (desc ? ", best first" : ", lowest first") : (desc ? ", Z to A" : ", A to Z"));
    }
  }

  // ------------------------------------------------------------------ render
  function render(host) {
    if (!host) return;
    if (view === "table" && !picking) { renderScores(host); return; }

    if (picking) {
      fill(host,
        el("div", { class: "cmp-head" },
          el("div", { class: "muted" },
            "Pick one to compare (" + codes.length + " of " + MAX + ")"),
          el("button", { class: "btn small", type: "button",
                         onclick: () => { picking = false; repaint(); } }, "Done")),
        picker());
      return;
    }

    const items = chosen();
    const head = el("div", { class: "cmp-head" },
      el("div", { class: "muted" },
        items.length ? items.length + " of " + MAX : "Nothing chosen"),
      codes.length < MAX
        ? el("button", { class: "btn small", type: "button",
                         onclick: () => { picking = true; repaint(); } }, "+ Add")
        : null);

    if (items.length < 2) {
      fill(host, el("div", { class: "viewbar" }, switcher()), head, notice("ok",
        "Choose two or more scored spirits (up to " + MAX + ") and they line up category by category, each axis "
        + "against its own maximum."));
      return;
    }

    // Totals
    const totals = el("div", { class: "cmp-totals" },
      ...items.map((it) => el("div", { class: "cmp-col" },
        el("div", { class: "cmp-name", style: "border-bottom-color:" + accentFor(it.spirit.type) },
          it.spirit.name || it.spirit.display_name || it.code,
          el("span", { class: "cmp-sub" },
            [it.spirit.distillery, it.code,
             it.spirit.proof ? it.spirit.proof + " pf" : null,
             it.spirit.release_year ? String(Math.round(it.spirit.release_year)) : null]
              .filter(Boolean).join(" · "))),
        el("div", { class: "cmp-score" }, it.career.score.toFixed(1)),
        it.career.medal
          ? el("span", { class: "medal", style: "--m:" + (MEDAL_COLORS[it.career.medal] || "#9A9086") },
               it.career.medal)
          : null,
        el("button", { class: "cmp-drop", type: "button", onclick: () => drop(it.code) }, "Remove"))));

    const rows = el("div", { class: "cmp-axes" });
    const anyCategories = items.some((it) => it.categories);
    if (anyCategories) {
      for (const cat of app.rubric.categories) {
        const vals = items.map((it) => (it.categories ? it.categories[cat.key] : null));
        const present = vals.filter((v) => v !== null && v !== undefined);
        const lead = present.length > 1 ? Math.max(...present) : null;
        const row = el("div", { class: "cmp-axis" },
          el("div", { class: "cmp-axis-head" },
            el("span", {}, cat.label || cat.key),
            el("span", { class: "muted" }, "/ " + cat.max)));
        items.forEach((it, i) => {
          const v = vals[i];
          row.append(el("div", { class: "cmp-bar-row" },
            el("div", { class: "cmp-bar-track" },
              el("div", {
                class: "cmp-bar-fill" + (v !== null && v === lead ? " lead" : ""),
                style: "width:" + (v === null || v === undefined ? 0 : (v / cat.max) * 100) + "%;"
                       + "background:" + accentFor(it.spirit.type),
              })),
            el("div", { class: "cmp-bar-val" },
              v === null || v === undefined ? "—" : v.toFixed(1))));
        });
        rows.append(row);
      }
    } else {
      rows.append(el("div", { class: "muted chart-empty" },
        "Only the totals came through. The per-category figures are worked out on the PC: press "
        + "Refresh there first, then Sync here. Refreshing here alone cannot produce them."));
    }

    /* The key: the same bottles in the same order, frozen under the header once the card above
       has scrolled away. The bars carry no names — each is told from the next by its place in
       the group — so two screens down a category was four anonymous bars. It is a short strip
       rather than the card itself held in place: the card is a quarter of the screen. A tap
       goes back to the top, where the medals and Remove are. Past four bottles there is no
       room for the code beside the score, and the name has to do. */
    const key = el("div", {
      class: "cmp-key" + (items.length > 4 ? " many" : ""),
      style: "grid-template-columns:repeat(" + items.length + ",minmax(0,1fr))",
      onclick: () => window.scrollTo({ top: 0, behavior: "smooth" }),
    },
      ...items.map((it) => el("div", {
        class: "cmp-key-col", style: "border-bottom-color:" + accentFor(it.spirit.type) },
        el("div", { class: "cmp-key-name" }, it.spirit.name || it.spirit.display_name || it.code),
        el("div", { class: "cmp-key-sub" },
          el("span", { class: "cmp-key-code" }, (it.spirit._encounter ? "Bar" : it.code) + " · "),
          el("b", {}, it.career.score.toFixed(1))))));
    const frozen = el("div", { class: "cmp-frozen", "aria-hidden": "true" }, key);
    const card = el("div", { class: "card" }, totals);

    fill(host, el("div", { class: "viewbar" }, switcher()), head, frozen, card, el("div", { class: "card" }, rows));
    watch(card, frozen);
  }

  /* The strip shows exactly while the card is out of sight above. The card is watched, not the
     scroll: nothing runs while a finger is dragging, only as its last edge crosses the header.
     The strip hangs from an element of no height, so its coming and going moves nothing on the
     page. One watcher at a time — every repaint makes a new card. */
  let watcher = null;
  function watch(card, frozen) {
    if (watcher) watcher.disconnect();
    watcher = null;
    if (typeof IntersectionObserver === "undefined") return;       // no key; the card is still there
    const bar = document.querySelector(".bar");
    const under = bar ? Math.round(bar.getBoundingClientRect().bottom) : 0;
    watcher = new IntersectionObserver(([entry]) => {
      // Out of sight *above*: a hidden screen is out of sight too, and has no height.
      frozen.classList.toggle("on", !entry.isIntersecting && entry.boundingClientRect.height > 0
        && entry.boundingClientRect.bottom <= under + 1);
    }, { rootMargin: "-" + under + "px 0px 0px 0px" });
    watcher.observe(card);
  }

  /** From a bottle's own page: side by side, with that bottle in — and while it is the only
      one, open straight on the list of what to set beside it. The caller shows the Compare tab,
      which renders. */
  function begin(code) {
    view = "bars";
    if (!codes.includes(code) && codes.length < MAX) codes.push(code);
    picking = codes.length < 2;
    query = "";
  }

  return { render, begin, reset: () => { codes = []; picking = false; query = ""; view = "table"; } };
})();

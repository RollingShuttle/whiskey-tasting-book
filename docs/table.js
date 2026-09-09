/* Whiskey Tasting Book — the table on the phone (SPEC.md §4.3, §9.2).

   The desktop table sorts on every field and has a column chooser to keep that manageable. A phone
   is 375 points across, so the sorting is what comes over and the chooser does not: the sort is
   picked from a scrolling row of chips rather than by hunting for a column header off the right
   edge of the screen.

   The table then always shows the field it is sorted by, even when that is not one of the columns
   it normally carries. Sorting by something you cannot see is the failure this avoids — a list
   silently reordered by a number that is nowhere on screen.

   Two of the columns are worked out here rather than stored: value per ounce and score per dollar,
   the two the spreadsheet could never produce. */
"use strict";

const TableView = (() => {
  const ML_PER_OZ = 29.5735;

  let sort = "score";
  let desc = true;
  let query = "";
  let filter = "all";
  let lens = null;          // {id, keys, custom} — set on first render, once the rubric is loaded

  const num = (v) => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  const round1 = (v) => (v === null ? null : Math.round(v * 10) / 10);

  /** Every field the desktop can sort on, in the order it offers them. `show` is what appears in
      the cell; `of` is the number or string the sort actually runs on. */
  const FIELDS = [
    { key: "score", label: "Score", core: true, num: true,
      of: (r) => r.score, show: (r) => (r.score === null ? "—" : r.score.toFixed(1)) },
    // Suppressed unless the lens is the whole card: the bands are defined against 100, so a
    // medal beside a flavour-only score would be claiming something the number cannot support.

    { key: "name", label: "Name", core: true,
      of: (r) => r.name.toLowerCase(), show: (r) => r.name },
    { key: "code", label: "Code", core: true,
      of: (r) => r.code, show: (r) => r.code },
    { key: "type", label: "Type", core: true,
      of: (r) => (r.spirit.type || "").toLowerCase(), show: (r) => r.spirit.type || "—" },
    { key: "proof", label: "Proof", core: true, num: true,
      of: (r) => r.proof, show: (r) => (r.proof === null ? "—" : r.proof) },
    { key: "year", label: "Year", core: true, num: true,
      of: (r) => r.year, show: (r) => (r.year === null ? "—" : r.year) },
    { key: "n", label: "n", core: true, num: true,
      of: (r) => r.n || null, show: (r) => r.n || "" },

    { key: "region", label: "Region",
      of: (r) => (r.spirit.region || "").toLowerCase(), show: (r) => r.spirit.region || "—" },
    { key: "age", label: "Age", num: true,
      of: (r) => r.age, show: (r) => (r.age === null ? (r.spirit.age_label || "—") : r.age) },
    { key: "paid", label: "Price", num: true,
      of: (r) => r.paid, show: (r) => (r.paid === null ? "—" : "$" + r.paid) },
    { key: "value_per_oz", label: "$ / oz", num: true,
      of: (r) => r.valuePerOz, show: (r) => (r.valuePerOz === null ? "—" : "$" + r.valuePerOz) },
    { key: "score_per_dollar", label: "Score / $", num: true,
      of: (r) => r.scorePerDollar,
      show: (r) => (r.scorePerDollar === null ? "—" : r.scorePerDollar.toFixed(3)) },
    // `high` rather than `num`: a medal is ordered, so the first tap should lead with Diamond,
    // but it is a word and belongs left-aligned with the other words.
    { key: "medal", label: "Medal", high: true,
      of: (r) => r.medalRank, show: (r) => r.medal || "—" },
    { key: "best", label: "Best", num: true,
      of: (r) => r.best, show: (r) => (r.best === null ? "—" : r.best) },
    { key: "worst", label: "Worst", num: true,
      of: (r) => r.worst, show: (r) => (r.worst === null ? "—" : r.worst) },
    { key: "rarity", label: "Rarity",
      of: (r) => (r.spirit.rarity || "").toLowerCase(), show: (r) => r.spirit.rarity || "—" },
    { key: "status", label: "Status",
      of: (r) => (r.spirit.status || "").toLowerCase(), show: (r) => r.spirit.status || "—" },
  ];

  const field = (key) => FIELDS.find((f) => f.key === key) || FIELDS[0];

  /** Medals sort by standing, not alphabetically — Bronze before Diamond would be nonsense. */
  function medalRank(name) {
    if (!name || !app.rubric) return null;
    const bands = app.rubric.bands || [];
    const i = bands.findIndex((b) => b.name === name);
    return i === -1 ? null : bands.length - i;
  }

  function rows() {
    return knownSpirits().map((s) => {
      const career = careerFor(s.code);
      const paid = num(s.paid);
      const oz = num(s.sizeoz) !== null ? num(s.sizeoz)
        : (num(s.size_ml) !== null ? num(s.size_ml) / ML_PER_OZ : null);
      // The score shown and sorted on is the lens, not the stored total.
      const means = categoryMeansFor(s.code);
      const whole = lensIsEverything(lens.keys);
      const score = whole
        ? (career ? career.score : null)
        : lensScore(means, lens.keys);
      return {
        code: s.code,
        spirit: s,
        name: s.name || s.display_name || s.code,
        proof: num(s.proof),
        age: num(s.age),
        year: num(s.release_year) === null ? null : Math.round(num(s.release_year)),
        paid,
        valuePerOz: paid !== null && oz ? round1(paid / oz) : null,
        scorePerDollar: score !== null && paid ? Math.round((score / paid) * 1000) / 1000 : null,
        score,
        medal: whole && career ? career.medal : null,
        medalRank: whole ? medalRank(career && career.medal) : null,
        best: career && career.best !== undefined ? career.best : null,
        worst: career && career.worst !== undefined ? career.worst : null,
        n: career ? career.n : 0,
        owned: s.owned !== false,
      };
    });
  }

  /** Nulls sink whichever way the column points: an unscored bottle is not a low score, and
      floating them to the top of an ascending sort would bury everything that has one. */
  function compare(a, b) {
    const f = field(sort);
    const x = f.of(a), y = f.of(b);
    if (x === null && y === null) return tieBreak(a, b);
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (x === y) return tieBreak(a, b);
    if (typeof x === "number" && typeof y === "number") return desc ? y - x : x - y;
    const c = String(x).localeCompare(String(y), undefined, { numeric: true });
    return desc ? -c : c;
  }

  /** Same rule as the desktop: equal scores are separated by how many sittings stand behind
      them, so a 90 from four nights outranks a 90 from one. */
  function tieBreak(a, b) {
    if (sort === "score" && a.n !== b.n) return b.n - a.n;
    return a.code.localeCompare(b.code, undefined, { numeric: true });
  }

  function shown() {
    let list = rows();
    if (filter === "scored") list = list.filter((r) => r.score !== null);
    else if (filter === "instock") list = list.filter((r) => r.owned);
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length) {
      list = list.filter((r) => {
        const hay = (r.code + " " + r.name + " " + (r.spirit.distillery || "") + " "
                     + (r.spirit.type || "") + " " + (r.spirit.region || "")).toLowerCase();
        return terms.every((t) => hay.includes(t));
      });
    }
    return list.sort(compare);
  }

  /** The columns always carried, plus whatever is being sorted by. */
  function columns() {
    const core = FIELDS.filter((f) => f.core);
    const chosen = field(sort);
    return core.includes(chosen) ? core : core.concat([chosen]);
  }

  function pick(key) {
    if (sort === key) desc = !desc;
    else { sort = key; desc = !!(field(key).num || field(key).high); }
    repaint();
  }

  function repaint() {
    render(document.getElementById("screen-table"));
  }

  /** Which categories the ranking is taken over: flavour alone, flavour plus one of the others,
      everything, or any set you pick. Aesthetics is the bottle and value is the price, so leaving
      them out asks a different question — which is the better whiskey, not the better buy. */
  function lensBar() {
    const presets = lensPresets();
    const active = presets.find((p) => sameKeys(p.keys, lens.keys));
    const bar = el("div", { class: "chips lensbar" },
      el("span", { class: "sortlabel" }, "Rank by"),
      ...presets.map((p) => el("button", {
        type: "button", class: "chip" + (p === active ? " active" : ""),
        onclick: () => { lens = { id: p.id, keys: p.keys, custom: false }; repaint(); },
      }, p.label)),
      el("button", {
        type: "button", class: "chip" + (lens.custom ? " active" : ""),
        onclick: () => { lens = { ...lens, custom: !lens.custom }; repaint(); },
      }, lens.custom ? "Custom ▴" : "Custom ▾"));
    return lens.custom ? el("div", {}, bar, lensPicker()) : bar;
  }

  function lensPicker() {
    return el("div", { class: "lenspicker" },
      ...(app.rubric.categories).map((c) => el("label", { class: "lenschoice" },
        el("input", {
          type: "checkbox", checked: lens.keys.includes(c.key),
          onchange: (e) => {
            const keys = e.target.checked
              ? [...lens.keys, c.key]
              : lens.keys.filter((k) => k !== c.key);
            // Never leave nothing selected: every row would go blank with no way back.
            lens = { ...lens, id: "custom", keys: keys.length ? keys : [c.key] };
            repaint();
          },
        }),
        `${c.label || c.key} ${c.max}`)));
  }

  function render(host) {
    if (!host) return;
    if (!lens) lens = { id: "flavour", keys: flavourKeys(), custom: false };

    const search = el("input", {
      class: "search", type: "text", value: query, enterkeyhint: "search",
      placeholder: "Search name, distillery or code", "aria-label": "Search",
      autocapitalize: "none", autocorrect: "off", spellcheck: "false",
      oninput: (e) => { query = e.target.value; paint(); },
    });

    const filters = el("div", { class: "chips" },
      ...[["all", "All"], ["instock", "In stock"], ["scored", "Scored"]].map(([id, label]) =>
        el("button", {
          type: "button", class: "chip" + (filter === id ? " active" : ""),
          onclick: () => { filter = id; repaint(); },
        }, label)));

    // Every field the desktop sorts on, reachable without scrolling the table sideways to find
    // a header that may not even be shown.
    const sorts = el("div", { class: "chips sortbar" },
      el("span", { class: "sortlabel" }, "Sort"),
      ...FIELDS.map((f) => el("button", {
        type: "button", class: "chip" + (sort === f.key ? " active" : ""),
        onclick: () => pick(f.key),
      }, f.label, sort === f.key ? (desc ? " ▾" : " ▴") : "")));

    const table = el("table", { class: "ptable" },
      el("thead", { id: "t-head" }), el("tbody", { id: "t-body" }));
    fill(host, search, filters, lensBar(), sorts,
      el("div", { class: "ptable-wrap" }, table),
      el("div", { class: "chart-basis", id: "t-count" }, ""));
    paint();

    function paint() {
      const list = shown();
      const cols = columns();
      const thead = document.getElementById("t-head");
      const tbody = document.getElementById("t-body");
      const count = document.getElementById("t-count");
      if (!thead || !tbody) return;

      thead.replaceChildren(el("tr", {},
        ...cols.map((f) => el("th", {
          class: (f.num ? "t-num" : "") + (sort === f.key ? " sorted" : ""),
          onclick: () => pick(f.key),
        }, f.label, sort === f.key ? el("span", { class: "t-caret" }, desc ? " ▾" : " ▴") : null))));

      tbody.replaceChildren();
      if (!list.length) {
        tbody.append(el("tr", {}, el("td", { colspan: String(cols.length), class: "muted" },
          "Nothing matches.")));
      }
      for (const r of list) {
        const tr = el("tr", { class: r.owned ? "" : "gone", onclick: () => openDetail(r.code) });
        for (const f of cols) {
          tr.append(el("td", {
            class: (f.num ? "t-num" : "") + (f.key === "code" ? " t-code" : "")
                   + (f.key === "name" ? " t-name" : ""),
          }, String(f.show(r))));
        }
        tbody.append(tr);
      }
      if (count) {
        count.textContent = `${list.length} shown, sorted by ${field(sort).label.toLowerCase()}`
          + (desc ? ", highest first" : ", lowest first")
          + ` · score out of ${lensMax(lens.keys)}`
          + (lensIsEverything(lens.keys) ? "" : " — medals need the whole card");
      }
    }
  }

  return { render };
})();

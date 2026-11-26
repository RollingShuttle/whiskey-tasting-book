/* Whiskey Tasting Book — the collection as a table, on the phone (SPEC.md §4.3, §9.2).

   The table is the Collection screen's second view, not a tab of its own: the same bottles, the
   same search box and the same filters as the list beside it, drawn as rows of facts. What is in
   it is decided by the collection (app.js, `shownSpirits`); how it is ordered is decided here.

   It is the bottles and nothing about how they scored. Scores, medals and the breakdown behind
   them are the Compare tab's table (compare.js), where every category has a column to sort on;
   one column of score here was a second, thinner copy of that. The one thing kept is `n`, how
   many sittings a bottle has had — a fact about the bottle, and the quickest way to see which
   have never been poured.

   The desktop table sorts on every field and has a column chooser to keep that manageable. A
   phone is 375 points across, so the sorting is what comes over and the chooser does not: the
   sort is picked from the phone's own picker rather than by hunting for a column header off the
   right edge of the screen, and the table then always shows the field it is sorted by, even when
   that is not one of the columns it normally carries. Sorting by something you cannot see is the
   failure this avoids — a list silently reordered by a number that is nowhere on screen.

   One column is worked out here rather than stored: value per ounce, which the spreadsheet could
   never produce. */
"use strict";

const TableView = (() => {
  const ML_PER_OZ = 29.5735;

  let sort = "name";
  let desc = false;

  const num = (v) => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  const round1 = (v) => (v === null ? null : Math.round(v * 10) / 10);

  /** Every fact the desktop can sort a bottle on, in the order it offers them. `show` is what
      appears in the cell; `of` is the number or string the sort actually runs on. */
  const FIELDS = [
    // In the order the columns stand: the code first, as on the shelf label, then what it is.
    { key: "code", label: "Code", core: true,
      of: (r) => r.code, show: (r) => r.code },
    // The name is drawn with its distillery under it: "8 Year" and "10 Year" are a dozen
    // bottles each without one. Equal names fall in distillery order for the same reason.
    { key: "name", label: "Name", core: true,
      of: (r) => `${r.name} ${r.distillery}`.toLowerCase(), show: (r) => r.name },
    // Not a column of its own — the distillery is already in the name's cell — but an order
    // worth having: every bottle of one house together. `within` says whose cell shows it.
    { key: "distillery", label: "Distillery", within: "name",
      of: (r) => (r.distillery ? `${r.distillery} ${r.name}`.toLowerCase() : null),
      show: (r) => r.distillery },
    { key: "year", label: "Year", core: true, num: true,
      of: (r) => r.year, show: (r) => (r.year === null ? "—" : r.year) },
    { key: "type", label: "Type", core: true,
      of: (r) => (r.spirit.type || "").toLowerCase(), show: (r) => r.spirit.type || "—" },
    { key: "proof", label: "Proof", core: true, num: true,
      of: (r) => r.proof, show: (r) => (r.proof === null ? "—" : r.proof) },
    { key: "n", label: "n", core: true, num: true,
      of: (r) => r.n || null, show: (r) => r.n || "" },

    { key: "region", label: "Region",
      of: (r) => (r.spirit.region || "").toLowerCase(), show: (r) => r.spirit.region || "—" },
    { key: "age", label: "Age", num: true,
      of: (r) => r.age, show: (r) => (r.age === null ? (r.spirit.age_label || "—") : r.age) },
    { key: "conc_ratio", label: "Conc. ratio", num: true,
      of: (r) => r.concRatio, show: (r) => (r.concRatio === null ? "—" : r.concRatio.toFixed(2)) },
    { key: "proof_diff", label: "Proof diff", num: true,
      of: (r) => r.proofDiff, show: (r) => (r.proofDiff === null ? "—" : String(r.proofDiff)) },
    { key: "paid", label: "Price", num: true,
      of: (r) => r.paid, show: (r) => (r.paid === null ? "—" : "$" + r.paid) },
    { key: "value_per_oz", label: "$ / oz", num: true,
      of: (r) => r.valuePerOz, show: (r) => (r.valuePerOz === null ? "—" : "$" + r.valuePerOz) },
    { key: "rarity", label: "Rarity",
      of: (r) => (r.spirit.rarity || "").toLowerCase(), show: (r) => r.spirit.rarity || "—" },
    { key: "status", label: "Status",
      of: (r) => (r.spirit.status || "").toLowerCase(), show: (r) => r.spirit.status || "—" },
  ];

  const field = (key) => FIELDS.find((f) => f.key === key) || FIELDS[0];

  function rows(spirits) {
    return spirits.map((s) => {
      const career = careerFor(s.code);
      const paid = num(s.paid);
      const oz = num(s.sizeoz) !== null ? num(s.sizeoz)
        : (num(s.size_ml) !== null ? num(s.size_ml) / ML_PER_OZ : null);
      return {
        code: s.code,
        spirit: s,
        name: s.name || s.display_name || s.code,
        distillery: String(s.distillery || "").trim(),
        proof: num(s.proof),
        age: num(s.age),
        concRatio: num(s.conc_ratio),
        proofDiff: num(s.proof_diff),        // bottling proof less barrel-entry proof; can be negative
        year: num(s.release_year) === null ? null : Math.round(num(s.release_year)),
        paid,
        valuePerOz: paid !== null && oz ? round1(paid / oz) : null,
        n: career ? career.n : 0,
        owned: s.owned !== false,
      };
    });
  }

  /** Nulls sink whichever way the column points: a bottle with no price is not a cheap one, and
      floating the blanks to the top of an ascending sort would bury everything that has a value. */
  function compare(a, b) {
    const f = field(sort);
    const x = f.of(a), y = f.of(b);
    if ((x === null || x === undefined || x === "") && (y === null || y === undefined || y === "")) return tieBreak(a, b);
    if (x === null || x === undefined || x === "") return 1;
    if (y === null || y === undefined || y === "") return -1;
    if (x === y) return tieBreak(a, b);
    if (typeof x === "number" && typeof y === "number") return desc ? y - x : x - y;
    const c = String(x).localeCompare(String(y), undefined, { numeric: true });
    return desc ? -c : c;
  }

  /** Equal values fall back to the code, so the order is the same every time it is drawn. */
  function tieBreak(a, b) {
    return a.code.localeCompare(b.code, undefined, { numeric: true });
  }

  /** The columns always carried, plus whatever is being sorted by. */
  function columns() {
    const core = FIELDS.filter((f) => f.core);
    const chosen = field(sort);
    return chosen.within || core.includes(chosen) ? core : core.concat([chosen]);
  }

  /** A field picked a second time turns round; a new one starts the way it is usually asked —
      numbers from the top, words from A. The collection repaints (app.js, `paintRows`): the bar
      above the table names the sort too. */
  function pick(key) {
    if (sort === key) desc = !desc;
    else { sort = key; desc = !!field(key).num; }
    paintRows();
  }

  /** What the table adds to the collection's bar: what the rows are ordered by, as one chip that
      opens the phone's own picker. It was a scrolling row of chips, one per field, standing
      between the filters and the first row. The picker chooses the field; its direction is
      turned by tapping the column's header, and the sorted column is always on screen to be
      tapped. */
  function controls() {
    return [
      pickerChip(`${field(sort).label} ${desc ? "▾" : "▴"}`,
        FIELDS.map((f) => [f.key, f.label]), sort,
        (key) => { if (key !== sort) pick(key); }, "Sort by"),
    ];
  }

  /** Draw `spirits` — already searched and filtered by the collection — into `host`. */
  function render(host, spirits) {
    if (!host) return;
    const list = rows(spirits).sort(compare);
    const cols = columns();

    // An order kept inside another column's cell (the distillery, in the name's) lights that
    // column's header and is named there, so what the rows are in order of is always on screen.
    const chosen = field(sort);
    const head = el("tr", {},
      ...cols.map((f) => {
        const mine = sort === f.key || chosen.within === f.key;
        return el("th", {
          class: (f.num ? "t-num" : "") + (mine ? " sorted" : ""),
          onclick: () => pick(mine ? sort : f.key),
        }, mine ? chosen.label : f.label,
           mine ? el("span", { class: "t-caret" }, desc ? " ▾" : " ▴") : null);
      }));

    const body = el("tbody", {});
    if (!list.length) {
      body.append(el("tr", {}, el("td", { colspan: String(cols.length), class: "muted" },
        "Nothing matches.")));
    }
    for (const r of list) {
      const tr = el("tr", { class: r.owned ? "" : "gone", onclick: () => openDetail(r.code) });
      for (const f of cols) {
        tr.append(f.key === "name"
          ? el("td", { class: "t-name" },
              el("div", { class: "t-title" }, r.name),
              r.distillery ? el("div", { class: "t-sub" }, r.distillery) : null)
          : el("td", { class: (f.num ? "t-num" : "") + (f.key === "code" ? " t-code" : "") },
              String(f.show(r))));
      }
      body.append(tr);
    }

    fill(host,
      el("div", { class: "ptable-wrap" }, el("table", { class: "ptable" }, el("thead", {}, head), body)),
      el("div", { class: "chart-basis" },
        `${list.length} shown, sorted by ${field(sort).label.toLowerCase()}`
        + (field(sort).num ? (desc ? ", highest first" : ", lowest first") : (desc ? ", Z to A" : ", A to Z"))
        + " · scores are under Compare"));
  }

  return { render, controls };
})();

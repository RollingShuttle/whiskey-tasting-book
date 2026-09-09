/* Whiskey Tasting Book — the table on the phone (SPEC.md §4.3, §9.2).

   The desktop table is wide and sortable with a column chooser. A phone is 375 points across, so
   this keeps the sorting and drops the chooser: a fixed set of columns that answer "which release
   is this and what did I give it" — code, name, type, proof, year, score, n — in a strip that
   scrolls sideways rather than being squeezed until nothing is readable.

   Proof and year earn their place here. Three bottles in a real collection can share a name, and
   those two fields are what separate one release from the next. */
"use strict";

const TableView = (() => {
  let sort = "score";
  let desc = true;
  let query = "";
  let filter = "all";

  const COLUMNS = [
    { key: "code", label: "Code", get: (r) => r.code, cls: "t-code" },
    { key: "name", label: "Name", get: (r) => r.name, cls: "t-name" },
    { key: "type", label: "Type", get: (r) => r.spirit.type || "", cls: "" },
    { key: "proof", label: "Proof", get: (r) => (r.proof === null ? "—" : r.proof), cls: "t-num" },
    { key: "year", label: "Year", get: (r) => (r.year === null ? "—" : r.year), cls: "t-num" },
    { key: "score", label: "Score", get: (r) => (r.score === null ? "—" : r.score.toFixed(1)),
      cls: "t-num" },
    { key: "n", label: "n", get: (r) => (r.n || ""), cls: "t-num" },
  ];

  const num = (v) => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };

  function rows() {
    const out = [];
    for (const s of knownSpirits()) {
      const career = careerFor(s.code);
      out.push({
        code: s.code,
        spirit: s,
        name: s.name || s.display_name || s.code,
        proof: num(s.proof),
        year: num(s.release_year) === null ? null : Math.round(num(s.release_year)),
        score: career ? career.score : null,
        n: career ? career.n : 0,
        owned: s.owned !== false,
      });
    }
    return out;
  }

  /** Nulls sort to the bottom whichever way the column is pointing: an unscored bottle is not a
      low score, and floating them to the top would bury everything that has one. */
  function compare(a, b) {
    const pick = {
      code: (r) => r.code, name: (r) => r.name.toLowerCase(),
      type: (r) => (r.spirit.type || "").toLowerCase(),
      proof: (r) => r.proof, year: (r) => r.year, score: (r) => r.score, n: (r) => r.n,
    }[sort];
    const x = pick(a), y = pick(b);
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    if (x === y) return a.code.localeCompare(b.code, undefined, { numeric: true });
    if (typeof x === "number" && typeof y === "number") return desc ? y - x : x - y;
    const c = String(x).localeCompare(String(y), undefined, { numeric: true });
    return desc ? -c : c;
  }

  function shown() {
    let list = rows();
    if (filter === "scored") list = list.filter((r) => r.score !== null);
    else if (filter === "instock") list = list.filter((r) => r.owned);
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length) {
      list = list.filter((r) => {
        const hay = (r.code + " " + r.name + " " + (r.spirit.distillery || "") + " "
                     + (r.spirit.type || "")).toLowerCase();
        return terms.every((t) => hay.includes(t));
      });
    }
    return list.sort(compare);
  }

  function repaint() {
    render(document.getElementById("screen-table"));
  }

  function head() {
    const tr = el("tr", {});
    for (const col of COLUMNS) {
      tr.append(el("th", {
        class: (col.cls || "") + (sort === col.key ? " sorted" : ""),
        onclick: () => {
          if (sort === col.key) desc = !desc;
          else { sort = col.key; desc = col.key === "score" || col.key === "n"; }
          repaint();
        },
      }, col.label, sort === col.key ? el("span", { class: "t-caret" }, desc ? " ▾" : " ▴") : null));
    }
    return el("thead", {}, tr);
  }

  function render(host) {
    if (!host) return;
    const list = shown();

    const search = el("input", {
      class: "search", type: "text", value: query, enterkeyhint: "search",
      placeholder: "Search name, distillery or code", "aria-label": "Search",
      autocapitalize: "none", autocorrect: "off", spellcheck: "false",
      oninput: (e) => { query = e.target.value; paintBody(); },
    });
    const chips = el("div", { class: "chips" },
      ...[["all", "All"], ["instock", "In stock"], ["scored", "Scored"]].map(([id, label]) =>
        el("button", {
          type: "button", class: "chip" + (filter === id ? " active" : ""),
          onclick: () => { filter = id; repaint(); },
        }, label)));

    const body = el("tbody", { id: "t-body" });
    const table = el("table", { class: "ptable" }, head(), body);
    fill(host, search, chips,
      el("div", { class: "ptable-wrap" }, table),
      el("div", { class: "chart-basis", id: "t-count" }, ""));
    paintBody();

    function paintBody() {
      const rowsNow = shown();
      const tbody = document.getElementById("t-body");
      const count = document.getElementById("t-count");
      if (!tbody) return;
      tbody.replaceChildren();
      if (!rowsNow.length) {
        tbody.append(el("tr", {}, el("td", { colspan: String(COLUMNS.length), class: "muted" },
          "Nothing matches.")));
      }
      for (const r of rowsNow) {
        const tr = el("tr", {
          class: r.owned ? "" : "gone",
          onclick: () => openDetail(r.code),
        });
        for (const col of COLUMNS) tr.append(el("td", { class: col.cls }, String(col.get(r))));
        tbody.append(tr);
      }
      if (count) count.textContent = rowsNow.length + " of " + list.length + " shown";
    }
  }

  return { render };
})();

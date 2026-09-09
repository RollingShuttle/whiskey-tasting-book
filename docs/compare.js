/* Whiskey Tasting Book — compare on the phone (SPEC.md §4.2, §9.2).

   Two to four things side by side. On the desktop this is a wide grid; a phone is 375 points
   across, so the same information is laid out as one row per category with a bar per spirit,
   which reads down the screen instead of across it.

   Each axis is drawn against its own maximum, so Flavor out of 20 and Balance out of 10 are
   comparable at a glance rather than one looking twice as good for being out of twice as much.

   Per-category means come from the PC in careers.json. The phone cannot derive them for a spirit
   it did not score itself — it holds its own cards, not the journal — so anything scored only on
   the desktop contributes its totals and its categories arrive published. */
"use strict";

const CompareView = (() => {
  let codes = [];
  let picking = false;
  let query = "";

  const MAX = 4;

  /** Per-category means for one spirit: what the PC published, else this phone's own cards. */
  function categoriesFor(code) {
    const published = (app.careers[code] || {}).categories;
    if (published && Object.keys(published).length) return published;

    const mine = Store.cardsFor(code).filter((c) => c.include_in_average && c.scores);
    if (!mine.length) return null;
    const out = {};
    for (const cat of app.rubric.categories) {
      const vals = mine.map((c) => c.scores[cat.key]).filter((v) => v !== null && v !== undefined);
      if (vals.length) out[cat.key] = vals.reduce((a, b) => a + b, 0) / vals.length;
    }
    return Object.keys(out).length ? out : null;
  }

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
            el("div", { class: "row-sub" }, [s.distillery, s.type].filter(Boolean).join(" · "))),
          scoreCell(c)));
      }
    }
  }

  // ------------------------------------------------------------------ render
  function render(host) {
    if (!host) return;

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
      fill(host, head, notice("ok",
        "Choose two to four scored spirits and they line up category by category, each axis "
        + "against its own maximum."));
      return;
    }

    // Totals
    const totals = el("div", { class: "cmp-totals" },
      ...items.map((it) => el("div", { class: "cmp-col" },
        el("div", { class: "cmp-name", style: "border-bottom-color:" + accentFor(it.spirit.type) },
          it.spirit.name || it.spirit.display_name || it.code),
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
        "Only totals are available for these. Open Sync and refresh to pull the per-category "
        + "figures the PC works out."));
    }

    fill(host, head, el("div", { class: "card" }, totals), el("div", { class: "card" }, rows));
  }

  return { render, reset: () => { codes = []; picking = false; query = ""; } };
})();

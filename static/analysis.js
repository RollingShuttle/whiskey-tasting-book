/* Whiskey Tasting Book — the analysis view (SPEC.md §4.5, §3.6).
   Score against Conc. Ratio / Age / Paid / Proof, score by Type and by Region, and a calibration
   series of the monthly mean to catch grade drift.

   Everything is hand-drawn inline SVG: no chart library, so this keeps working offline and adds
   no dependency to the bundle the phone will load. Scatters and group means use career scores,
   one point per spirit; the calibration series is one mean per month over sittings, because the
   thing it measures is the scorer rather than the spirit.

   Loaded after app.js and reuses its helpers (el, api, state, accentFor). */
"use strict";

const AnalysisView = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const A = { data: null, axis: "conc_ratio" };
  const host = () => document.getElementById("analysis-view");

  function s(tag, attrs = {}, ...kids) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      n.append(kid.nodeType ? kid : document.createTextNode(kid));
    }
    return n;
  }

  async function open() {
    host().hidden = false;
    if (!A.data) {
      host().replaceChildren(el("div", { class: "table-loading" }, "Loading…"));
      try {
        A.data = await api("/api/analysis");
      } catch (e) {
        host().replaceChildren(el("div", { class: "table-loading" },
          `Could not load the analysis: ${e.body?.error || e.message}`));
        return;
      }
    }
    render();
  }

  function invalidate() { A.data = null; }

  // ---------------------------------------------------------------- maths
  function pearson(xs, ys) {
    const n = xs.length;
    if (n < 3) return null;
    const mx = xs.reduce((a, b) => a + b, 0) / n;
    const my = ys.reduce((a, b) => a + b, 0) / n;
    let sxy = 0, sxx = 0, syy = 0;
    for (let i = 0; i < n; i++) {
      const dx = xs[i] - mx, dy = ys[i] - my;
      sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
    }
    return (sxx && syy) ? sxy / Math.sqrt(sxx * syy) : null;
  }

  function fit(xs, ys) {
    const n = xs.length;
    const mx = xs.reduce((a, b) => a + b, 0) / n;
    const my = ys.reduce((a, b) => a + b, 0) / n;
    let num = 0, den = 0;
    for (let i = 0; i < n; i++) { num += (xs[i] - mx) * (ys[i] - my); den += (xs[i] - mx) ** 2; }
    if (!den) return null;
    const slope = num / den;
    return { slope, intercept: my - slope * mx };
  }

  const nice = (v) => (Number.isInteger(v) ? String(v) : v.toFixed(v < 10 ? 2 : 0));

  /** Medal thresholds, drawn on any score axis so the charts read like the rest of the app. */
  function bands() {
    return (state.config?.rubric?.bands || [])
      .filter((b) => b.min > 0 && b.min < 100).map((b) => b.min);
  }

  // ---------------------------------------------------------------- charts
  function card(title, subtitle, body, controls) {
    return el("section", { class: "an-card" },
      el("div", { class: "an-head" },
        el("div", {},
          el("div", { class: "an-title" }, title),
          el("div", { class: "an-sub" }, subtitle)),
        controls || null),
      body);
  }

  function empty(msg) { return el("div", { class: "an-empty" }, msg); }

  function scatter() {
    const axes = A.data.axes;
    const axis = axes.find((a) => a.key === A.axis) || axes[0];
    const pts = A.data.points.filter((p) => p[axis.key] !== null && p[axis.key] !== undefined);

    const picker = el("div", { class: "an-axes" }, ...axes.map((a) =>
      el("button", {
        type: "button", class: `chip${a.key === axis.key ? " active" : ""}`,
        disabled: a.have === 0,
        title: a.have === 0 ? "no scored spirit has this field yet" : `${a.have} with data`,
        onclick: () => { A.axis = a.key; render(); },
      }, a.label)));

    const sub = `${pts.length} of ${A.data.points.length} scored spirit`
      + `${A.data.points.length === 1 ? "" : "s"} have a value here`;

    if (pts.length < 2) {
      return card("Score vs " + axis.label, sub,
        empty("Not enough scored spirits with this field yet to draw a shape."), picker);
    }

    const W = 720, H = 320, M = { t: 14, r: 16, b: 40, l: 46 };
    const pw = W - M.l - M.r, ph = H - M.t - M.b;
    const xs = pts.map((p) => p[axis.key]);
    const ys = pts.map((p) => p.score);
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    if (x0 === x1) { x0 -= 1; x1 += 1; }
    const xpad = (x1 - x0) * 0.06;
    x0 -= xpad; x1 += xpad;
    let y0 = Math.max(0, Math.floor(Math.min(...ys) - 3));
    let y1 = Math.min(100, Math.ceil(Math.max(...ys) + 3));
    if (y1 - y0 < 10) { y0 = Math.max(0, y0 - 5); y1 = Math.min(100, y1 + 5); }

    const X = (v) => M.l + ((v - x0) / (x1 - x0)) * pw;
    const Y = (v) => M.t + ph - ((v - y0) / (y1 - y0)) * ph;

    const g = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "an-svg", role: "img",
                         "aria-label": `Career score against ${axis.label}` });

    // medal bands first, so points sit on top
    for (const b of bands()) {
      if (b <= y0 || b >= y1) continue;
      g.append(s("line", { x1: M.l, x2: W - M.r, y1: Y(b), y2: Y(b),
                           class: "an-band" }),
               s("text", { x: W - M.r, y: Y(b) - 4, class: "an-bandlabel",
                           "text-anchor": "end" }, String(b)));
    }
    // axes
    g.append(s("line", { x1: M.l, x2: W - M.r, y1: M.t + ph, y2: M.t + ph, class: "an-axis" }),
             s("line", { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ph, class: "an-axis" }));
    for (let i = 0; i <= 4; i++) {
      const v = x0 + ((x1 - x0) * i) / 4;
      g.append(s("text", { x: X(v), y: M.t + ph + 18, class: "an-tick",
                           "text-anchor": "middle" }, nice(v)));
    }
    for (const v of [y0, (y0 + y1) / 2, y1]) {
      g.append(s("text", { x: M.l - 8, y: Y(v) + 4, class: "an-tick",
                           "text-anchor": "end" }, String(Math.round(v))));
    }

    // trend line — only when there is enough to justify one
    const r = pearson(xs, ys);
    const line = xs.length >= 3 ? fit(xs, ys) : null;
    if (line) {
      const ya = line.intercept + line.slope * x0;
      const yb = line.intercept + line.slope * x1;
      const clamp = (v) => Math.max(y0, Math.min(y1, v));
      g.append(s("line", { x1: X(x0), y1: Y(clamp(ya)), x2: X(x1), y2: Y(clamp(yb)),
                           class: "an-trend" }));
    }

    for (const p of pts) {
      g.append(s("circle", { cx: X(p[axis.key]), cy: Y(p.score), r: 5,
                             fill: accentFor(p.type), class: "an-dot" },
        s("title", {}, `${p.name}\n${axis.label}: ${p[axis.key]}\nscore ${p.score} (n=${p.n})`)));
    }

    const note = r === null
      ? el("span", { class: "an-note" }, "Too few points to say anything about a relationship.")
      : el("span", { class: "an-note" },
          `Correlation r = ${r.toFixed(2)} over ${pts.length} spirits — `,
          Math.abs(r) < 0.3 ? "essentially no relationship."
            : Math.abs(r) < 0.6 ? "a weak relationship." : "a fairly strong relationship.",
          " Small samples wander; treat this as a hint, not a finding.");

    return card(`Score vs ${axis.label}`, sub, el("div", {}, g, note), picker);
  }

  /* `colorFn` is explicit: Type bars carry the category accent, but a Region has no accent —
     colouring it would imply an encoding that does not exist, so those get one flat brass. */
  function bars(rows, title, subtitle, colorFn) {
    if (!rows.length) return card(title, subtitle, empty("Nothing scored yet."));
    const W = 720, rowH = 30, M = { t: 8, l: 132, r: 54, b: 24 };
    const H = M.t + rows.length * rowH + M.b;
    const pw = W - M.l - M.r;
    const X = (v) => M.l + (v / 100) * pw;

    const g = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "an-svg", role: "img",
                         "aria-label": title });

    for (const b of bands()) {
      g.append(s("line", { x1: X(b), x2: X(b), y1: M.t, y2: M.t + rows.length * rowH,
                           class: "an-band" }),
               s("text", { x: X(b), y: M.t + rows.length * rowH + 16, class: "an-bandlabel",
                           "text-anchor": "middle" }, String(b)));
    }

    rows.forEach((row, i) => {
      const y = M.t + i * rowH + rowH / 2;
      g.append(s("text", { x: M.l - 10, y: y + 4, class: "an-rowlabel",
                           "text-anchor": "end" }, row.key));
      // the spread behind the mean, so a single lucky bottle does not read as a verdict
      if (row.max !== row.min) {
        g.append(s("line", { x1: X(row.min), x2: X(row.max), y1: y, y2: y, class: "an-spread" }));
      }
      g.append(s("rect", { x: M.l, y: y - 7, width: Math.max(1, X(row.mean) - M.l), height: 14,
                           rx: 3, fill: colorFn(row.key), class: "an-bar" },
        s("title", {}, `${row.key}\nmean ${row.mean} across ${row.spirits} spirit`
          + `${row.spirits === 1 ? "" : "s"} (${row.sittings} sittings)\nrange ${row.min}–${row.max}`)));
      g.append(s("text", { x: X(row.mean) + 8, y: y + 4, class: "an-value" },
        `${row.mean.toFixed(1)}`),
        s("text", { x: W - 6, y: y + 4, class: "an-tick", "text-anchor": "end" },
          `n=${row.spirits}`));
    });
    return card(title, subtitle, g);
  }

  function calibration() {
    const rows = A.data.calibration;
    const sub = "Your own mean score per month, over every counted sitting. A steady climb or "
      + "slide is grade drift, not better whiskey.";
    if (rows.length < 2) {
      return card("Calibration", sub,
        empty("Needs at least two months of scoring before drift means anything."));
    }
    const W = 720, H = 260, M = { t: 16, r: 18, b: 42, l: 46 };
    const pw = W - M.l - M.r, ph = H - M.t - M.b;
    const means = rows.map((r) => r.mean);
    let y0 = Math.max(0, Math.floor(Math.min(...means) - 4));
    let y1 = Math.min(100, Math.ceil(Math.max(...means) + 4));
    if (y1 - y0 < 10) { y0 = Math.max(0, y0 - 5); y1 = Math.min(100, y1 + 5); }
    const X = (i) => M.l + (rows.length === 1 ? pw / 2 : (i / (rows.length - 1)) * pw);
    const Y = (v) => M.t + ph - ((v - y0) / (y1 - y0)) * ph;

    const g = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "an-svg", role: "img",
                         "aria-label": "Monthly mean score" });

    const grand = means.reduce((a, b) => a + b, 0) / means.length;
    g.append(s("line", { x1: M.l, x2: W - M.r, y1: Y(grand), y2: Y(grand), class: "an-mean" }),
             s("text", { x: M.l + 4, y: Y(grand) - 6, class: "an-bandlabel" },
               `overall ${grand.toFixed(1)}`));
    g.append(s("line", { x1: M.l, x2: W - M.r, y1: M.t + ph, y2: M.t + ph, class: "an-axis" }),
             s("line", { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ph, class: "an-axis" }));
    for (const v of [y0, (y0 + y1) / 2, y1]) {
      g.append(s("text", { x: M.l - 8, y: Y(v) + 4, class: "an-tick",
                           "text-anchor": "end" }, String(Math.round(v))));
    }

    g.append(s("polyline", { class: "an-line",
      points: rows.map((r, i) => `${X(i)},${Y(r.mean)}`).join(" ") }));
    rows.forEach((r, i) => {
      g.append(s("circle", { cx: X(i), cy: Y(r.mean), r: 4.5, class: "an-dot an-caldot" },
        s("title", {}, `${r.month}\nmean ${r.mean} over ${r.n} sitting${r.n === 1 ? "" : "s"}`)));
      if (rows.length <= 14 || i % 2 === 0) {
        g.append(s("text", { x: X(i), y: M.t + ph + 18, class: "an-tick",
                             "text-anchor": "middle" }, r.month.slice(2)));
      }
    });
    return card("Calibration", sub, g);
  }

  // ---------------------------------------------------------------- render
  function render() {
    const d = A.data;
    const parts = [];
    parts.push(el("div", { class: "an-summary" },
      `${d.counts.scored} spirit${d.counts.scored === 1 ? "" : "s"} scored across `
      + `${d.counts.sittings} counted sitting${d.counts.sittings === 1 ? "" : "s"}. `
      + "Every point is a career score — the mean of a spirit's counted sittings."));

    if (!d.points.length) {
      parts.push(empty("Score a few spirits and these charts fill in."));
      host().replaceChildren(...parts);
      return;
    }
    parts.push(scatter());
    parts.push(bars(d.by_type, "Score by Type",
      "Mean career score per type; the faint line behind each bar is the range.",
      (k) => accentFor(k)));
    parts.push(bars(d.by_region, "Score by Region",
      "Mean career score per region; the faint line behind each bar is the range.",
      () => "var(--brass)"));
    parts.push(calibration());
    host().replaceChildren(...parts);
  }

  return { open, invalidate };
})();

/* Whiskey Tasting Book — analysis on the phone (SPEC.md §4.5, §9.2).

   Everything here is derived on the phone from two things it already holds: the collection
   snapshot and the career scores the PC publishes. Nothing is fetched, so this works in a bar
   basement with no signal, which is the same rule the rest of the app lives by.

   One exception is published rather than derived. The calibration series — one mean per month
   across every counted sitting — measures the scorer rather than the spirit, and the phone holds
   only its own cards, not the whole journal. So the PC computes it and sends the handful of
   numbers along with the careers.

   Charts are hand-written SVG. A charting library would be a large download for a page whose
   whole point is opening instantly without one. */
"use strict";

const AnalysisView = (() => {
  const NS = "http://www.w3.org/2000/svg";
  let axis = "paid";
  let group = "type";

  /** SVG needs its own namespace; document.createElement quietly makes an inert HTML element. */
  function sv(tag, props = {}, ...kids) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(props)) {
      if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      n.append(kid && kid.nodeType ? kid : document.createTextNode(kid));
    }
    return n;
  }

  const num = (v) => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };

  const AXES = [
    { key: "paid", label: "Price", fmt: (v) => "$" + Math.round(v) },
    { key: "age", label: "Age", fmt: (v) => v + " yr" },
    { key: "proof", label: "Proof", fmt: (v) => String(Math.round(v)) },
  ];

  /** One point per scored spirit — a career score, never a single sitting (§3.6). */
  function points() {
    const out = [];
    for (const s of knownSpirits()) {
      const c = careerFor(s.code);
      if (!c) continue;
      out.push({
        code: s.code,
        name: s.name || s.display_name || s.code,
        type: s.type,
        region: s.region,
        score: c.score,
        n: c.n,
        paid: num(s.paid),
        age: num(s.age),
        proof: num(s.proof),
      });
    }
    return out;
  }

  function chips(items, current, pick) {
    return el("div", { class: "chips" },
      ...items.map(([id, label]) =>
        el("button", {
          type: "button",
          class: "chip" + (current === id ? " active" : ""),
          onclick: () => pick(id),
        }, label)));
  }

  /** A count of how many points a chart actually stands on. Three bottles is not a finding, and
      a chart that does not say so is quietly making a claim it cannot support. */
  const basis = (have, total, what) =>
    el("div", { class: "chart-basis" },
      have + " of " + total + " scored spirits have " + what + " recorded");

  // ---------------------------------------------------------------- scatter
  function scatter(pts) {
    const spec = AXES.find((a) => a.key === axis);
    const have = pts.filter((p) => p[axis] !== null);
    const card = el("div", { class: "card chart-card" },
      el("div", { class: "chart-title" }, "Score against " + spec.label.toLowerCase()),
      chips(AXES.map((a) => [a.key, a.label]), axis, (id) => { axis = id; repaint(); }));

    if (have.length < 2) {
      card.append(el("div", { class: "muted chart-empty" },
        "Not enough scored spirits with a " + spec.label.toLowerCase() + " yet."));
      return card;
    }

    const W = 320, H = 190, L = 30, R = 8, T = 10, B = 22;
    const xs = have.map((p) => p[axis]);
    const lo = Math.min(...xs), hi = Math.max(...xs);
    const span = hi - lo || 1;
    const x = (v) => L + ((v - lo) / span) * (W - L - R);
    const y = (s) => T + (1 - s / 100) * (H - T - B);

    const g = sv("svg", { viewBox: "0 0 " + W + " " + H, class: "chart", role: "img",
                          "aria-label": "Score against " + spec.label });

    // Medal thresholds, so a point can be read against the bands rather than the axis alone.
    for (const band of (app.rubric && app.rubric.bands) || []) {
      if (band.min <= 0 || band.min >= 100) continue;
      g.append(sv("line", { x1: L, x2: W - R, y1: y(band.min), y2: y(band.min), class: "grid" }));
      g.append(sv("text", { x: 2, y: y(band.min) + 3, class: "tick" }, String(band.min)));
    }
    for (const p of have) {
      g.append(sv("circle", {
        cx: x(p[axis]), cy: y(p.score), r: 4,
        fill: accentFor(p.type), "fill-opacity": 0.85,
      }, sv("title", {}, p.name + " — " + p.score.toFixed(1) + " at " + spec.fmt(p[axis]))));
    }
    g.append(sv("text", { x: L, y: H - 6, class: "tick" }, spec.fmt(lo)));
    g.append(sv("text", { x: W - R, y: H - 6, class: "tick", "text-anchor": "end" }, spec.fmt(hi)));

    card.append(g, basis(have.length, pts.length, spec.label.toLowerCase()));
    return card;
  }

  // ------------------------------------------------------------ group means
  function groups(pts) {
    const key = group;
    const buckets = new Map();
    for (const p of pts) {
      const k = p[key];
      if (!k) continue;
      if (!buckets.has(k)) buckets.set(k, []);
      buckets.get(k).push(p.score);
    }
    const rows = [...buckets.entries()]
      .map(([name, scores]) => ({
        name,
        mean: scores.reduce((a, b) => a + b, 0) / scores.length,
        n: scores.length,
      }))
      .sort((a, b) => b.mean - a.mean);

    const card = el("div", { class: "card chart-card" },
      el("div", { class: "chart-title" }, "Mean score by " + key),
      chips([["type", "Type"], ["region", "Region"]], key, (id) => { group = id; repaint(); }));

    if (!rows.length) {
      card.append(el("div", { class: "muted chart-empty" }, "Nothing scored yet."));
      return card;
    }

    const top = Math.max(...rows.map((r) => r.mean));
    const list = el("div", { class: "bars" });
    for (const r of rows) {
      list.append(el("div", { class: "bar-row" },
        el("div", { class: "bar-label" }, r.name),
        el("div", { class: "bar-track" },
          el("div", {
            class: "bar-fill",
            style: "width:" + Math.max(2, (r.mean / top) * 100) + "%;background:"
                   + (key === "type" ? accentFor(r.name) : "var(--brass)"),
          })),
        el("div", { class: "bar-value" }, r.mean.toFixed(1)),
        el("div", { class: "bar-n" }, "n=" + r.n)));
    }
    card.append(list, basis(rows.reduce((a, r) => a + r.n, 0), pts.length, "a " + key));
    return card;
  }

  // ------------------------------------------------------------- calibration
  function calibration() {
    const series = app.calibration || [];
    if (series.length < 2) return null;

    const W = 320, H = 150, L = 30, R = 8, T = 10, B = 22;
    const means = series.map((m) => m.mean);
    const lo = Math.min(...means) - 2, hi = Math.max(...means) + 2;
    const span = hi - lo || 1;
    const x = (i) => L + (series.length === 1 ? 0 : (i / (series.length - 1)) * (W - L - R));
    const y = (v) => T + (1 - (v - lo) / span) * (H - T - B);

    const g = sv("svg", { viewBox: "0 0 " + W + " " + H, class: "chart", role: "img",
                          "aria-label": "Monthly mean score" });
    g.append(sv("polyline", {
      class: "spark",
      points: series.map((m, i) => x(i) + "," + y(m.mean)).join(" "),
    }));
    series.forEach((m, i) => {
      g.append(sv("circle", { cx: x(i), cy: y(m.mean), r: 3, fill: "var(--brass)" },
        sv("title", {}, m.month + " — " + m.mean + " over " + m.n + " sittings")));
    });
    g.append(sv("text", { x: 2, y: y(hi) + 8, class: "tick" }, hi.toFixed(0)));
    g.append(sv("text", { x: 2, y: y(lo), class: "tick" }, lo.toFixed(0)));
    g.append(sv("text", { x: L, y: H - 6, class: "tick" }, series[0].month));
    g.append(sv("text", { x: W - R, y: H - 6, class: "tick", "text-anchor": "end" },
                series[series.length - 1].month));

    return el("div", { class: "card chart-card" },
      el("div", { class: "chart-title" }, "Are you drifting?"),
      el("div", { class: "chart-note" },
        "One mean per month across every counted sitting. This measures you, not the whiskey."),
      g);
  }

  // ------------------------------------------------------------------ render
  function repaint() {
    render(document.getElementById("screen-analysis"));
  }

  function render(host) {
    if (!host) return;
    const pts = points();
    if (!pts.length) {
      fill(host, notice("warn",
        "Nothing scored yet. Score a pour, or open Sync and refresh to pull in what the PC knows."));
      return;
    }
    const scores = pts.map((p) => p.score);
    const best = pts.reduce((a, b) => (b.score > a.score ? b : a));
    fill(host,
      el("div", { class: "card" },
        el("div", { class: "sum-row" },
          el("div", { class: "sum-cell" },
            el("b", {}, String(pts.length)), el("span", {}, "scored")),
          el("div", { class: "sum-cell" },
            el("b", {}, (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1)),
            el("span", {}, "mean")),
          el("div", { class: "sum-cell" },
            el("b", {}, best.score.toFixed(1)), el("span", {}, "best"))),
        el("div", { class: "muted sum-best" }, best.name)),
      scatter(pts),
      groups(pts),
      calibration());
  }

  return { render };
})();

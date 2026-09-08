"""
app.py — the local web server.

Serves the judging sheet and its JSON API at http://127.0.0.1:8765 (SPEC.md §6). The same static
bundle is what the phone client will load; this process is the desktop half.

What this server may and may not touch:
  * `Whiskey Collection.xlsx` — the 147 MB master — is READ ONLY and is never opened on a request.
    The spirit list comes from `snapshot/collection.json`, which collection.py generates. Only an
    explicit /api/refresh reads the master, and it does so through collection.load(), which opens
    read_only and proves the file is byte-identical afterwards. Nothing here can call .save() on it.
  * Tastings are written to the append-only journal (store.py) in the OneDrive app folder. Immutable
    files, one per sitting.

The app is a factory (create_app) so the tests can inject a temp journal root and a fixture
snapshot and never write to real OneDrive. See test_app.py.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml
from flask import Flask, jsonify, request, send_from_directory

import rubric as rubric_mod
import store as store_mod

STATIC_DIR = Path(__file__).resolve().parent / "static"
ML_PER_OZ = 29.5735

# Medal band -> chip palette, keyed by band name (SPEC.md §10: Diamond pale blue, Gold brass,
# Silver grey, Bronze copper, No Medal muted). Served to the front end so the bands stay
# config-driven — add a band in config.yaml, give it a colour here, done.
MEDAL_COLORS = {
    "Diamond":  "#AFC7DE",
    "Gold":     "#C8952F",
    "Silver":   "#B4ADA2",
    "Bronze":   "#B4703A",
    "No Medal": "#9A9086",
}


# Column contracts for the table view (SPEC.md §4.3). The front end builds its column chooser and
# its CSV straight from these, so adding a column is a one-line change here.
COLLECTION_COLUMNS = [
    {"key": "code",             "label": "Code",     "type": "code",  "default": True},
    {"key": "display_name",     "label": "Name",     "type": "name",  "default": True},
    {"key": "type",             "label": "Type",     "type": "text",  "default": True},
    {"key": "region",           "label": "Region",   "type": "text",  "default": True},
    {"key": "rarity",           "label": "Rarity",   "type": "text",  "default": False},
    {"key": "status",           "label": "Status",   "type": "text",  "default": False},
    {"key": "source",           "label": "Source",   "type": "text",  "default": False},
    {"key": "age",              "label": "Age",      "type": "num",   "default": False},
    {"key": "proof",            "label": "Proof",    "type": "num",   "default": False},
    {"key": "abv",              "label": "ABV",      "type": "num",   "default": False},
    {"key": "paid",             "label": "Paid",     "type": "money", "default": True},
    {"key": "size_oz",          "label": "Size oz",  "type": "num",   "default": False},
    {"key": "value_per_oz",     "label": "$ / oz",   "type": "money", "default": True},
    {"key": "career_score",     "label": "Score",    "type": "score1", "default": True},
    {"key": "medal",            "label": "Medal",    "type": "medal", "default": True},
    {"key": "n",                "label": "n",        "type": "int",   "default": True},
    {"key": "best",             "label": "Best",     "type": "int",   "default": False},
    {"key": "worst",            "label": "Worst",    "type": "int",   "default": False},
    {"key": "score_per_dollar", "label": "Score / $", "type": "num3", "default": False},
]

TASTING_COLUMNS = [
    {"key": "date",             "label": "Date",     "type": "text",  "default": True},
    {"key": "code",             "label": "Code",     "type": "code",  "default": True},
    {"key": "display_name",     "label": "Name",     "type": "name",  "default": True},
    {"key": "type",             "label": "Type",     "type": "text",  "default": True},
    {"key": "region",           "label": "Region",   "type": "text",  "default": False},
    {"key": "total",            "label": "Score",    "type": "score", "default": True},
    {"key": "medal",            "label": "Medal",    "type": "medal", "default": True},
    {"key": "counted",          "label": "Counted",  "type": "text",  "default": True},
    {"key": "venue",            "label": "Venue",    "type": "text",  "default": True},
    {"key": "pour_price",       "label": "Pour $",   "type": "money", "default": False},
    {"key": "pour_size_oz",     "label": "Pour oz",  "type": "num",   "default": False},
    {"key": "value_per_oz",     "label": "$ / oz",   "type": "money", "default": True},
    {"key": "score_per_dollar", "label": "Score / $", "type": "num3", "default": False},
    {"key": "blind",            "label": "Blind",    "type": "text",  "default": False},
    {"key": "flight_pos",       "label": "Pour #",   "type": "int",   "default": False},
    {"key": "entered_from",     "label": "Entered",  "type": "text",  "default": False},
    {"key": "tasting_id",       "label": "Tasting id", "type": "text", "default": False},
]


def _group_tastings(journal):
    """One pass over the journal, grouped by spirit.

    journal.careers() re-reads every tasting file once per scored spirit, which is fine for a
    handful and quadratic for a few hundred. The table touches every spirit, so it reads once.
    """
    by = {}
    for t in journal.tastings():
        by.setdefault(t["spirit_id"], []).append(t)
    return by


def _career_bits(rubric, sittings):
    if not sittings:
        return {"career_score": None, "medal": None, "n": 0, "n_total": 0,
                "best": None, "worst": None}
    c = rubric.career(sittings)
    rng = c.get("range") or (None, None)
    return {"career_score": c["mean_total"], "medal": c["medal"], "n": c["n"],
            "n_total": c["n_total"], "best": rng[1], "worst": rng[0]}


def _ratio(numerator, denominator, places=2):
    if not numerator or not denominator:
        return None
    return round(numerator / denominator, places)


def _pour_value_per_oz(sittings):
    """$/oz for something not owned: what the pours actually cost."""
    priced = [(s["pour_price"], s["pour_size_oz"]) for s in sittings
              if s.get("pour_price") and s.get("pour_size_oz")]
    if not priced:
        return None
    return round(sum(p / z for p, z in priced) / len(priced), 2)


def _csv_arg(v):
    return [s.strip() for s in (v or "").split(",") if s.strip()]


def _split_notes(rec):
    """The journal keeps one notes dict; the overall note rides under a reserved key."""
    notes = dict((rec.get("notes") or {}))
    return notes, notes.pop("overall", None)


def _latest_notes(sittings):
    """Notes from the most recent counted sitting — what the compare notes pane shows."""
    counted = [s for s in sittings if s.get("include_in_average", True)] or list(sittings)
    if not counted:
        return {}, None
    latest = max(counted, key=lambda s: (s.get("date") or "", s.get("created_at") or ""))
    return _split_notes(latest)


def _career_item(rubric, code, sp, sittings):
    """One compare card built from every counted sitting — the default (SPEC.md §3.6)."""
    c = rubric.career(sittings) if sittings else None
    notes, overall = _latest_notes(sittings)
    scores, ranges = {}, {}
    if c and c["n"]:
        for k, v in c["categories"].items():
            scores[k] = v["mean"]
            ranges[k] = [v["min"], v["max_seen"]]
    return {
        "key": code, "mode": "career", "code": code,
        "label": sp.get("display_name") or code,
        "sublabel": sp.get("type"), "type": sp.get("type"),
        "scores": scores, "ranges": ranges,
        "total": (c or {}).get("mean_total"), "medal": (c or {}).get("medal"),
        "n": (c or {}).get("n", 0),
        "notes": notes, "overall_note": overall,
        "date": None, "tasting_id": None,
    }


def _sitting_item(t, catalog):
    """One compare card pinned to a single sitting — a head-to-head from one night."""
    sp = catalog.get(t["spirit_id"]) or {}
    notes, overall = _split_notes(t)
    return {
        "key": t["tasting_id"], "mode": "sitting", "code": t["spirit_id"],
        "label": sp.get("display_name") or t["spirit_id"],
        "sublabel": t.get("date"), "type": sp.get("type"),
        "scores": dict(t["scores"]), "ranges": {},
        "total": t["total"], "medal": t["medal"], "n": 1,
        "notes": notes, "overall_note": overall,
        "date": t.get("date"), "tasting_id": t["tasting_id"],
    }


def _encounter_stub(journal, code):
    for e in journal.encounters():
        if e.get("code") == code:
            name = " ".join(p for p in (e.get("distillery"), e.get("name")) if p).strip()
            return {"display_name": name or e.get("name"), "type": e.get("type")}
    return None


def _axes(rubric, items):
    """Per-axis deltas: who leads each category and by how much. Each axis carries its own max so
    the view can draw every bar against it — Flavor/20 reads at the same scale as Balance/10."""
    axes = []
    for c in rubric.categories:
        vals = {i["key"]: i["scores"].get(c.key) for i in items}
        present = {k: v for k, v in vals.items() if v is not None}
        spread = round(max(present.values()) - min(present.values()), 1) if len(present) > 1 else 0
        leader = max(present, key=lambda k: present[k]) if (present and spread > 0) else None
        axes.append({"key": c.key, "label": c.label, "max": c.max,
                     "values": vals, "leader": leader, "spread": spread})
    return axes


def _load_cfg(config_path):
    with open(config_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _display_name(sp: dict) -> str:
    return " ".join(p for p in (sp.get("distillery"), sp.get("name")) if p).strip() or sp.get("code", "")


def _size_oz(sp: dict):
    if sp.get("sizeoz") is not None:
        return float(sp["sizeoz"])
    if sp.get("size_ml") is not None:
        return float(sp["size_ml"]) / ML_PER_OZ
    return None


def _enrich(sp: dict) -> dict:
    """Add the derived fields the sheet needs without duplicating them in the snapshot."""
    out = dict(sp)
    out["display_name"] = _display_name(sp)
    oz = _size_oz(sp)
    out["size_oz"] = round(oz, 2) if oz else None
    paid = sp.get("paid")
    out["value_per_oz"] = round(paid / oz, 2) if (paid and oz) else None
    return out


class SpiritCatalog:
    """The read side of the collection: whatever the last snapshot captured. Never the master."""

    def __init__(self, snapshot_path: Path):
        self.snapshot_path = Path(snapshot_path)
        self.generated_from = None
        self.next_code = {}
        self.first_empty_row = {}
        self._by_code = {}
        self._order = []
        self.mtime = None

    def load(self):
        data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        self.generated_from = data.get("generated_from")
        self.next_code = data.get("next_code", {})
        self.first_empty_row = data.get("first_empty_row", {})
        self._by_code, self._order = {}, []
        for sp in data.get("spirits", []):
            rec = _enrich(sp)
            self._by_code[rec["code"]] = rec
            self._order.append(rec["code"])
        self.mtime = self.snapshot_path.stat().st_mtime
        return self

    def all(self):
        return [self._by_code[c] for c in self._order]

    def get(self, code):
        return self._by_code.get(code)

    def __len__(self):
        return len(self._order)


def create_app(config_path="config.yaml", *, app_folder=None, snapshot_path=None, master=None):
    cfg = _load_cfg(config_path)

    rubric = rubric_mod.Rubric(cfg["rubric"])
    journal = store_mod.Journal(store_mod.resolve_root(cfg, app_folder), rubric).ensure()

    snap = Path(snapshot_path or os.environ.get("WHISKEY_SNAPSHOT")
                or (Path(cfg["paths"]["local_data"]) / "collection.json"))
    catalog = SpiritCatalog(snap)
    try:
        catalog.load()
        catalog_error = None
    except FileNotFoundError:
        catalog_error = (f"No collection snapshot at {snap}. Run `python collection.py "
                         f"--snapshot {snap}` (reads the master read-only) or POST /api/refresh.")

    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0        # dev: always serve fresh static files
    app.config["JSON_SORT_KEYS"] = False
    app.config["_master"] = master

    # -- the page ------------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    # -- config the sheet is built from -------------------------------------
    @app.get("/api/config")
    def api_config():
        return jsonify({
            "rubric": rubric.as_config(),
            "medal_colors": MEDAL_COLORS,
            "medal_rule": ("The medal comes from the career score rounded half-up: "
                           "89.5 is Diamond, 89.4 is Gold."),
            "server": {"bind_lan": cfg.get("server", {}).get("bind_lan", False)},
            "app_name": "Whiskey Tasting Book",
        })

    # -- the spirit list -----------------------------------------------------
    @app.get("/api/spirits")
    def api_spirits():
        if catalog_error and len(catalog) == 0:
            return jsonify({"error": catalog_error, "spirits": []}), 503
        return jsonify({
            "count": len(catalog),
            "generated_from": catalog.generated_from,
            "next_code": catalog.next_code,
            "spirits": catalog.all(),
        })

    @app.get("/api/spirit/<code>")
    def api_spirit(code):
        sp = catalog.get(code)
        if sp is None:
            return jsonify({"error": f"unknown spirit {code}"}), 404
        career = journal.career(code)
        career.pop("sittings", None)                   # the sittings ride in their own key
        return jsonify({"spirit": sp, "career": _json_safe(career),
                        "sittings": journal.career(code)["sittings"]})

    # -- submit a scorecard --------------------------------------------------
    @app.post("/api/tasting")
    def api_tasting():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "expected a JSON object"}), 400

        spirit_id = (body.get("spirit_id") or "").strip()
        if not spirit_id:
            return jsonify({"error": "spirit_id is required"}), 400

        raw_scores = body.get("scores") or {}
        try:                                           # keep real ints; reject 8.5, "8", True
            scores = {k: _as_int(v) for k, v in raw_scores.items()}
        except (TypeError, ValueError):
            return jsonify({"error": "every score must be a whole number"}), 400

        problems = rubric.validate(scores)
        if problems:
            return jsonify({"error": "; ".join(problems), "problems": problems}), 400

        notes = dict(body.get("notes") or {})
        overall = (body.get("overall_notes") or "").strip()
        if overall:                                    # store.py persists one notes dict; the
            notes["overall"] = overall                 # overall note rides under a reserved key

        try:
            rec = journal.write_tasting(
                spirit_id=spirit_id,
                scores=scores,
                notes=notes,
                session_id=body.get("session_id"),
                date=body.get("date"),
                venue=_clean(body.get("venue")),
                pour_price=_as_float_or_none(body.get("pour_price")),
                pour_size_oz=_as_float_or_none(body.get("pour_size_oz")),
                flight_pos=_as_int_or_none(body.get("flight_pos")),
                include_in_average=bool(body.get("include_in_average", True)),
                status=body.get("status") or "submitted",
                entered_from=body.get("entered_from") or "desktop",
                tasting_id=body.get("tasting_id"),
                tags=body.get("tags") or [],
                blind=bool(body.get("blind", False)),
            )
        except FileExistsError as e:
            return jsonify({"error": str(e)}), 409
        except (ValueError, KeyError) as e:
            return jsonify({"error": str(e)}), 400

        return jsonify({"ok": True, "tasting": rec,
                        "next_band": rubric.points_to_next_band(rec["total"])}), 201

    # -- flights / sessions --------------------------------------------------
    @app.get("/api/sessions")
    def api_sessions():
        counts = {}
        for t in journal.tastings():
            if t.get("session_id"):
                counts[t["session_id"]] = counts.get(t["session_id"], 0) + 1
        return jsonify({"sessions": [dict(s, pours=counts.get(s["session_id"], 0))
                                     for s in journal.sessions()]})

    @app.post("/api/session")
    def api_create_session():
        """Start a flight, or amend one by passing its session_id (writes a new revision)."""
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return jsonify({"error": "expected a JSON object"}), 400
        try:
            rec = journal.write_session(
                title=_clean(body.get("title")),
                date=body.get("date"),
                location=_clean(body.get("location")),
                company=_clean(body.get("company")),
                blind=bool(body.get("blind", False)),
                notes=_clean(body.get("notes")),
                session_id=body.get("session_id"),
            )
        except FileExistsError as e:
            return jsonify({"error": str(e)}), 409
        except (ValueError, KeyError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, "session": rec}), 201

    @app.get("/api/session/<sid>")
    def api_session(sid):
        full = journal.session(sid)
        if full is None:
            return jsonify({"error": f"unknown session {sid}"}), 404
        pours = [dict(p, spirit=catalog.get(p["spirit_id"])) for p in full["pours"]]
        return jsonify({
            "session": {k: v for k, v in full.items() if k != "pours"},
            "pours": pours,
            "next_flight_pos": journal.next_flight_pos(sid),
        })

    # -- the table view ------------------------------------------------------
    @app.get("/api/table/collection")
    def api_table_collection():
        """One row per spirit: the master's fields joined to the career score. Encounters are in
        here too, so "have I had this?" is answerable next to "do I own this?" (SPEC.md §2.1)."""
        by_spirit = _group_tastings(journal)
        journal.assign_encounter_codes()          # PC-side, idempotent, writes only on a change
        rows = []

        for sp in catalog.all():
            sits = by_spirit.get(sp["code"], [])
            bits = _career_bits(rubric, sits)
            rows.append({
                "code": sp["code"], "display_name": sp["display_name"],
                "distillery": sp.get("distillery"), "name": sp.get("name"),
                "source": sp.get("_sheet"), "owned": True,
                "type": sp.get("type"), "region": sp.get("region"),
                "rarity": sp.get("rarity"), "status": sp.get("status"),
                "age": sp.get("age"), "age_label": sp.get("age_label"),
                "proof": sp.get("proof"), "abv": sp.get("abv"),
                "paid": sp.get("paid"), "size_oz": sp.get("size_oz"),
                "value_per_oz": sp.get("value_per_oz"),
                "score_per_dollar": _ratio(bits["career_score"], sp.get("paid"), 3),
                **bits,
            })

        for enc in journal.encounters():
            if enc.get("linked_bottle_code"):
                continue      # folded into the bottle it became — not a separate row (§2.1)
            code = enc.get("code")
            sits = by_spirit.get(code, []) if code else []
            bits = _career_bits(rubric, sits)
            name = " ".join(p for p in (enc.get("distillery"), enc.get("name")) if p).strip()
            rows.append({
                "code": code, "display_name": name or enc.get("name"),
                "distillery": enc.get("distillery"), "name": enc.get("name"),
                "source": "Encounter", "owned": False,
                "type": enc.get("type"), "region": enc.get("region"),
                "rarity": None, "status": None,
                "age": enc.get("age"), "age_label": None,
                "proof": enc.get("proof"), "abv": None,
                "paid": None, "size_oz": None,
                "value_per_oz": _pour_value_per_oz(sits),
                "score_per_dollar": None,
                **bits,
            })

        return jsonify({"columns": COLLECTION_COLUMNS, "rows": rows})

    @app.get("/api/table/tastings")
    def api_table_tastings():
        """One row per sitting — every tasting, sortable and filterable on every field."""
        rows = []
        for t in journal.tastings():
            sp = catalog.get(t["spirit_id"]) or {}
            rows.append({
                "tasting_id": t["tasting_id"], "date": t.get("date"), "code": t["spirit_id"],
                "display_name": sp.get("display_name") or t["spirit_id"],
                "type": sp.get("type"), "region": sp.get("region"),
                "total": t["total"], "medal": t["medal"],
                "counted": "yes" if t.get("include_in_average", True) else "no",
                "venue": t.get("venue"),
                "pour_price": t.get("pour_price"), "pour_size_oz": t.get("pour_size_oz"),
                "value_per_oz": (_ratio(t.get("pour_price"), t.get("pour_size_oz"))
                                 or sp.get("value_per_oz")),
                "score_per_dollar": _ratio(t["total"], t.get("pour_price") or sp.get("paid"), 3),
                "blind": "yes" if t.get("blind") else "no",
                "flight_pos": t.get("flight_pos"), "session_id": t.get("session_id"),
                "entered_from": t.get("entered_from"),
            })
        return jsonify({"columns": TASTING_COLUMNS, "rows": rows})

    # -- compare -------------------------------------------------------------
    @app.get("/api/compare")
    def api_compare():
        """2-4 things side by side (SPEC.md §4.2). Defaults to career scores; pass `session` or
        `tastings` to pin single sittings instead, so a head-to-head from one night still works."""
        codes = _csv_arg(request.args.get("codes"))
        tids = _csv_arg(request.args.get("tastings"))
        sid = (request.args.get("session") or "").strip()
        items = []

        if sid:
            full = journal.session(sid)
            if full is None:
                return jsonify({"error": f"unknown session {sid}"}), 404
            items = [_sitting_item(p, catalog) for p in full["pours"]]
        elif tids:
            index = {t["tasting_id"]: t for t in journal.tastings()}
            for tid in tids:
                if tid not in index:
                    return jsonify({"error": f"unknown tasting {tid}"}), 404
                items.append(_sitting_item(index[tid], catalog))
        elif codes:
            by_spirit = _group_tastings(journal)
            for code in codes:
                sp = catalog.get(code) or _encounter_stub(journal, code)
                if sp is None:
                    return jsonify({"error": f"unknown spirit {code}"}), 404
                items.append(_career_item(rubric, code, sp, by_spirit.get(code, [])))
        else:
            return jsonify({"error": "pass codes, tastings or session"}), 400

        if not items:
            return jsonify({"error": "nothing to compare"}), 400
        if len(items) > 4:
            return jsonify({"error": "compare takes at most 4 cards"}), 400

        return jsonify({"categories": rubric.as_config()["categories"],
                        "items": items, "axes": _axes(rubric, items)})

    # -- status pill ---------------------------------------------------------
    @app.get("/api/health")
    def api_health():
        return jsonify({
            "ok": True,
            "journal_root": str(journal.root),
            "catalog": {"spirits": len(catalog), "generated_from": catalog.generated_from,
                        "error": catalog_error if len(catalog) == 0 else None},
            "stats": journal.stats(),
        })

    # -- regenerate the snapshot from the master (read-only) -----------------
    @app.post("/api/refresh")
    def api_refresh():
        import collection as collection_mod
        try:
            coll = collection_mod.load(config_path, master=app.config["_master"])
        except Exception as e:                         # noqa: BLE001 — surface the real reason
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
        if coll.errors:
            return jsonify({"ok": False,
                            "errors": [f"[{i.sheet}] {i.message}" for i in coll.errors]}), 409
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(json.dumps(coll.snapshot(), ensure_ascii=False), encoding="utf-8")
        catalog.load()
        return jsonify({"ok": True, "count": len(catalog), "snapshot": str(snap)})

    return app


# --------------------------------------------------------------------------- coercion helpers
def _as_int(v):
    if isinstance(v, bool):                            # bool is an int subclass; a score is not
        raise ValueError("bool is not a score")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    raise ValueError(f"{v!r} is not a whole number")


def _as_float_or_none(v):
    if v in (None, ""):
        return None
    return float(v)


def _as_int_or_none(v):
    """flight_pos arrives from JSON and must not stay a string — pours are sorted on it."""
    if v in (None, ""):
        return None
    return int(v)


def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _json_safe(career: dict) -> dict:
    """career() returns some tuples (range, next_band); jsonify turns tuples into lists anyway,
    but be explicit so the shape is predictable."""
    out = dict(career)
    if out.get("range") is not None:
        out["range"] = list(out["range"])
    if out.get("next_band") is not None:
        out["next_band"] = list(out["next_band"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Whiskey Tasting Book — local server")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--host", help="override server host")
    ap.add_argument("--port", type=int, help="override server port")
    ap.add_argument("--app-folder", help="journal root (also WHISKEY_APP_FOLDER)")
    ap.add_argument("--snapshot", help="collection snapshot path (also WHISKEY_SNAPSHOT)")
    a = ap.parse_args(argv)

    cfg = _load_cfg(a.config)
    srv = cfg.get("server", {})
    host = a.host or ("0.0.0.0" if srv.get("bind_lan") else srv.get("host", "127.0.0.1"))
    port = a.port or int(srv.get("port", 8765))

    app = create_app(a.config, app_folder=a.app_folder, snapshot_path=a.snapshot)
    print(f"Whiskey Tasting Book  →  http://{host}:{port}")
    print(f"  journal : {store_mod.resolve_root(cfg, a.app_folder)}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()

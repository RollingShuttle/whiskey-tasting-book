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
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask, jsonify, request, send_from_directory

import rubric as rubric_mod
import store as store_mod

def _resource_dir():
    """Where the bundled read-only files live. Packaged, PyInstaller unpacks them to a temp
    folder and points sys._MEIPASS at it; from source they sit beside this file."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent


STATIC_DIR = _resource_dir() / "static"
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
    {"key": "proof",            "label": "Proof",    "type": "num",   "default": True},
    {"key": "release_year",     "label": "Year",     "type": "int",   "default": True},
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
    {"key": "proof",            "label": "Proof",    "type": "num",   "default": True},
    {"key": "release_year",     "label": "Year",     "type": "int",   "default": True},
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


def collection_status_gone(status):
    """Thin wrapper so the vocabulary lives in one place — collection.py — and this module does
    not grow its own opinion about what "Finished" means."""
    import collection as collection_mod
    return collection_mod.is_gone(status)


def _encounter_alias(journal):
    """uid -> X- code, for every encounter that has been given one.

    A pour at a bar is scored on the phone before it has a code: X- numbers are handed out here
    on the PC, afterwards. The card therefore names the encounter by its uid permanently, because
    journal files are immutable and a card is never rewritten to say something else. Translating
    at the point of reading is what lets everything downstream go on keying by the code alone.
    """
    journal.assign_encounter_codes()          # idempotent; writes only when a new one appears
    return {e["encounter_uid"]: e["code"] for e in journal.encounters() if e.get("code")}


def _group_tastings(journal):
    """One pass over the journal, grouped by spirit.

    journal.careers() re-reads every tasting file once per scored spirit, which is fine for a
    handful and quadratic for a few hundred. The table touches every spirit, so it reads once.
    """
    alias = _encounter_alias(journal)
    by = {}
    for t in journal.tastings():
        by.setdefault(alias.get(t["spirit_id"], t["spirit_id"]), []).append(t)
    return by


def _career_bits(rubric, sittings):
    """Career figures for one spirit, plus the per-category means a ranking lens needs.

    `category_means` carries the **exact** means, not the rounded display ones. A lens sums a
    subset of them, and summing ten independently rounded figures drifts by up to 0.5 — with
    the full set that would put the "everything" lens 0.2 away from the career score it is
    supposed to equal (SPEC.md §3.6). Summing exact values and rounding once does not.
    """
    if not sittings:
        return {"career_score": None, "medal": None, "n": 0, "n_total": 0,
                "best": None, "worst": None, "category_means": {}}
    c = rubric.career(sittings)
    rng = c.get("range") or (None, None)
    return {"career_score": c["mean_total"], "medal": c["medal"], "n": c["n"],
            "n_total": c["n_total"], "best": rng[1], "worst": rng[0],
            "category_means": {k: v["mean_exact"] for k, v in c["categories"].items()}}


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
        "proof": sp.get("proof"), "release_year": sp.get("release_year"),
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


def _group_scores(points, key):
    """Mean career score per Type or per Region, best first, with the spread behind it."""
    groups = {}
    for p in points:
        k = p.get(key)
        if k:
            groups.setdefault(k, []).append(p)
    out = []
    for k, ps in groups.items():
        scores = [p["score"] for p in ps]
        out.append({"key": k, "spirits": len(ps), "sittings": sum(p["n"] for p in ps),
                    "mean": round(sum(scores) / len(scores), 1),
                    "min": min(scores), "max": max(scores)})
    return sorted(out, key=lambda g: (-g["mean"], -g["spirits"]))


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


def create_app(config_path="config.yaml", *, app_folder=None, snapshot_path=None, master=None,
               rollup=None, backups=None):
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

    rollup_path = Path(rollup or os.environ.get("WHISKEY_ROLLUP")
                       or cfg["paths"]["rollup_workbook"])
    backup_dir = Path(backups or cfg["paths"]["backups"])

    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0        # dev: always serve fresh static files
    app.config["JSON_SORT_KEYS"] = False
    app.config["_master"] = master

    def _highest_codes(coll):
        """The largest code number on each sheet right now. Recorded so it can never go backwards
        when the row holding it is deleted."""
        import collection as collection_mod
        out = {}
        for sheet, rows in coll.rows.items():
            best = 0
            for r in rows:
                parsed = collection_mod.parse_code(r.get("code"))
                if parsed:
                    best = max(best, parsed[1])
            out[sheet] = best
        return out

    def _record_departures(coll):
        """Notice bottles that have left the workbook, before the snapshot forgets them.

        Finishing a bottle and deleting its row is ordinary housekeeping, but the reviews of it
        are not disposable — they are the point of the whole journal. This is the only moment the
        old list and the new one both exist, so it is the only moment the difference can be seen
        at all: once the snapshot is overwritten there is nothing left to compare against.
        """
        if not snap.exists():
            return []
        try:
            before = {r["code"]: r for r in json.loads(snap.read_text(encoding="utf-8"))["spirits"]}
        except (OSError, ValueError, KeyError, TypeError):
            return []                          # no usable previous list; nothing to compare
        rows = coll.snapshot()["spirits"]
        now = {r["code"] for r in rows}
        gone = []

        # Deleted outright: the row has vanished and this is the last moment it can be described.
        for code, row in before.items():
            if code not in now:
                journal.write_retired(code=code, fields=row, reason="row deleted from the workbook")
                gone.append(code)

        # Marked Finished or Removed: the row is still there, so nothing is at risk — but the
        # workbook records no date, and "when did I finish it?" is worth keeping. Written once,
        # so it is the date the app first saw the mark, not the date of the latest refresh.
        for row in rows:
            status = (row.get("status") or "").strip()
            if collection_status_gone(status):
                journal.write_retired(code=row["code"], fields=row,
                                      reason=f"marked {status}")
                gone.append(row["code"])
        return sorted(set(gone))

    def _publish_for_phone(coll):
        """Write what the phone reads into the OneDrive app folder (SPEC.md §9.1).

        The phone has no access to the master workbook and no server to ask, so the PC is the
        single writer of all three: the bottle list, the rubric it scores against, and the career
        figures it shows beside each bottle. Careers are published rather than derived on the
        phone because deriving them would mean downloading the whole journal over bar wifi.
        """
        out = journal.root / "snapshot"
        out.mkdir(parents=True, exist_ok=True)
        out.joinpath("collection.json").write_text(
            json.dumps(coll.snapshot(), ensure_ascii=False), encoding="utf-8")
        out.joinpath("rubric.json").write_text(
            json.dumps(rubric.as_config(), ensure_ascii=False), encoding="utf-8")
        careers, months, sittings = {}, {}, {}
        for code, sits in _group_tastings(journal).items():
            bits = _career_bits(rubric, sits)
            # The individual cards, so the phone can list and correct a sitting it did not take
            # itself. Without these it can only act on what it scored, which for a collection
            # scored mostly at a desk is nothing at all. Ten small numbers and a date each.
            sittings[code] = [{
                "tasting_id": t["tasting_id"], "revision": t.get("revision", 1),
                "spirit_id": t["spirit_id"], "date": t.get("date"),
                "total": t.get("total"), "medal": t.get("medal"),
                "venue": t.get("venue"), "scores": t.get("scores") or {},
                "notes": t.get("notes") or {},
                "include_in_average": t.get("include_in_average", True),
                "entered_from": t.get("entered_from"),
            } for t in sits]
            if bits["n"]:
                careers[code] = {
                    "score": bits["career_score"], "medal": bits["medal"], "n": bits["n"],
                    # Best and worst sitting, so the phone can sort on them like the desktop can.
                    # It holds its own cards, not the journal, so it cannot work these out.
                    "best": bits["best"], "worst": bits["worst"],
                    # Per-category means, so the phone can compare two whiskies axis by axis
                    # rather than only by their totals. Ten small numbers per scored spirit.
                    "categories": {k: round(v, 2) for k, v in bits["category_means"].items()},
                }
            for t in sits:
                if not t.get("include_in_average", True) or t.get("total") is None:
                    continue
                month = (t.get("date") or "")[:7]
                if len(month) == 7:
                    months.setdefault(month, []).append(t["total"])

        # One mean per month over every counted sitting. This measures the scorer rather than the
        # spirit, and the phone cannot derive it: it holds its own cards, not the whole journal.
        calibration = [{"month": m, "mean": round(sum(v) / len(v), 1), "n": len(v)}
                       for m, v in sorted(months.items())]
        # When these figures were worked out. The phone adds its own cards on top of them, and
        # without a cutoff it cannot tell a card the PC has already counted from one it has not —
        # so every sitting scored on the phone was counted twice once the PC caught up.
        out.joinpath("careers.json").write_text(
            json.dumps({"careers": careers, "calibration": calibration, "sittings": sittings,
                        "generated_at": datetime.now(timezone.utc).isoformat()},
                       ensure_ascii=False),
            encoding="utf-8")
        return {"spirits": len(coll.rows and coll.snapshot()["spirits"]), "careers": len(careers)}

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

    @app.get("/api/tasting/<tasting_id>")
    def api_get_tasting(tasting_id):
        """One sitting, resolved to its latest revision — what the table hands to the sheet when
        an old card is opened for correction."""
        for t in journal.tastings():
            if t["tasting_id"] == tasting_id:
                return jsonify({"tasting": t})
        return jsonify({"error": f"no such tasting {tasting_id}"}), 404

    @app.delete("/api/tasting/<tasting_id>")
    def api_delete_tasting(tasting_id):
        """Remove a sitting. Nothing is unlinked: this writes a tombstone revision, so the card
        stops counting and stops being shown while the history of it survives (SPEC.md §1.3)."""
        try:
            rec = journal.delete_tasting(tasting_id, reason=_clean(
                (request.get_json(silent=True) or {}).get("reason")))
        except KeyError:
            return jsonify({"error": f"no such tasting {tasting_id}"}), 404
        return jsonify({"ok": True, "tasting_id": tasting_id, "revision": rec["revision"]})

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
            scores = {k: (None if v is None else _as_int(v)) for k, v in raw_scores.items()}
        except (TypeError, ValueError):
            return jsonify({"error": "every score must be a whole number"}), 400

        # A draft is the card as it stands, autosaved while it is still being filled in, so it is
        # allowed to be incomplete. Only a submitted card has to be whole.
        draft = str(body.get("status") or "submitted").lower() == "draft"
        if draft:
            scores = {k: v for k, v in scores.items() if v is not None}
            problems = rubric.validate_partial(scores)
        else:
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
                        "next_band": (rubric.points_to_next_band(rec["total"])
                                      if rec.get("total") is not None else None)}), 201

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
        by_spirit = _group_tastings(journal)   # assigns any new X- codes on the way through
        rows = []

        for sp in catalog.all():
            sits = by_spirit.get(sp["code"], [])
            bits = _career_bits(rubric, sits)
            # Status decides this, not mere presence in the workbook. A finished bottle keeps its
            # row — which is the better way to do it, since the reviews keep something to belong
            # to — but it is not something you still have.
            rows.append({
                "code": sp["code"], "display_name": sp["display_name"],
                "distillery": sp.get("distillery"), "name": sp.get("name"),
                "source": sp.get("_sheet"),
                # A snapshot written before the loader derived this will not carry it, so fall
                # back to reading the status directly rather than assuming everything is owned.
                "owned": bool(sp["owned"]) if "owned" in sp
                         else not collection_status_gone(sp.get("status")),
                "type": sp.get("type"), "region": sp.get("region"),
                "rarity": sp.get("rarity"), "status": sp.get("status"),
                "age": sp.get("age"), "age_label": sp.get("age_label"),
                "proof": sp.get("proof"), "abv": sp.get("abv"),
                "release_year": sp.get("release_year"),
                "paid": sp.get("paid"), "size_oz": sp.get("size_oz"),
                "value_per_oz": sp.get("value_per_oz"),
                "score_per_dollar": _ratio(bits["career_score"], sp.get("paid"), 3),
                **bits,
            })

        listed = {r["code"] for r in rows}
        for rec in journal.retired():
            code = rec["code"]
            sits = by_spirit.get(code, [])
            # A retired bottle earns a row only if it was actually scored. One deleted without
            # ever being reviewed is simply gone; there is nothing to preserve.
            if code in listed or not sits:
                continue
            f = rec.get("fields") or {}
            bits = _career_bits(rubric, sits)
            # Older records, and any snapshot that did not carry one, still have the parts.
            name = f.get("display_name") or " ".join(
                p for p in (f.get("distillery"), f.get("name")) if p).strip()
            rows.append({
                "code": code, "display_name": name or code,
                "distillery": f.get("distillery"), "name": f.get("name"),
                "source": "Retired", "owned": False,
                "type": f.get("type"), "region": f.get("region"),
                "rarity": f.get("rarity"), "status": "Finished",
                "age": f.get("age"), "age_label": f.get("age_label"),
                "proof": f.get("proof"), "abv": f.get("abv"),
                "release_year": f.get("release_year"),
                "paid": f.get("paid"), "size_oz": f.get("size_oz"),
                "value_per_oz": f.get("value_per_oz"),
                "score_per_dollar": _ratio(bits["career_score"], f.get("paid"), 3),
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
                "release_year": None,
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
        alias = _encounter_alias(journal)
        for t in journal.tastings():
            # A bar pour's card names its encounter by uid; show the X- code it was given.
            code = alias.get(t["spirit_id"], t["spirit_id"])
            sp = catalog.get(code) or _encounter_stub(journal, code) or {}
            rows.append({
                "tasting_id": t["tasting_id"], "date": t.get("date"), "code": code,
                "scores": t.get("scores") or {},        # so a lens can re-total one sitting
                "display_name": sp.get("display_name") or code,
                "type": sp.get("type"), "region": sp.get("region"),
                "proof": sp.get("proof"), "release_year": sp.get("release_year"),
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

    # -- quick entry: the phone lane --------------------------------------
    @app.get("/api/quickentry")
    def api_quick_rows():
        import quickentry as qe
        import rollup as rollup_mod
        return jsonify({
            "workbook": str(rollup_path),
            "exists": rollup_path.exists(),
            "locked": rollup_mod.is_locked(rollup_path),
            "rows": qe.read_rows(rollup_path),
        })

    @app.post("/api/quickentry/drain")
    def api_quick_drain():
        """Turn typed rows into draft tastings, then rewrite the workbook so the sheet is clear
        and only the flagged rows remain (SPEC.md §1.2)."""
        import quickentry as qe
        import rollup as rollup_mod

        # Refuse before writing anything: the drain is only safe if the rewrite that clears the
        # sheet can follow it, otherwise the same rows would be drained twice on the next run.
        if rollup_mod.is_locked(rollup_path):
            return jsonify({"ok": False, "error": (
                f"{rollup_path.name} is open in Excel. Close it and try again — draining writes "
                "journal records and then rewrites the workbook to clear the sheet.")}), 409

        try:
            summary = qe.drain(rollup_path, journal, catalog.all())
        except Exception as e:                     # noqa: BLE001 — surface the real reason
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

        try:
            rollup_mod.build(journal, rollup_path, backup_dir,
                             int(cfg["paths"].get("backup_keep", 30)),
                             quick_rows=summary["unmatched"])
        except Exception as e:                     # noqa: BLE001
            return jsonify({"ok": False, **summary, "error": (
                f"rows were drained into the journal, but the workbook could not be "
                f"rewritten: {e}")}), 500

        return jsonify({"ok": True, **summary})

    # -- analysis ------------------------------------------------------------
    @app.get("/api/analysis")
    def api_analysis():
        """The questions the spreadsheet cannot answer (SPEC.md §4.5).

        Scatters and group means use **career scores**, one point per spirit (§3.6). The
        calibration series is different in kind: it is one mean per month over every counted
        sitting, because the thing being measured there is the scorer, not the spirit.
        """
        by_spirit = _group_tastings(journal)
        points, months = [], {}

        for code, sits in by_spirit.items():
            counted = [t for t in sits if t.get("include_in_average", True)]
            if not counted:
                continue                       # drafts and excluded-only spirits have no score
            c = rubric.career(sits)
            sp = catalog.get(code) or _encounter_stub(journal, code) or {}
            points.append({
                "code": code, "name": sp.get("display_name") or code,
                "type": sp.get("type"), "region": sp.get("region"),
                "score": c["mean_total"], "medal": c["medal"], "n": c["n"],
                "age": sp.get("age"), "conc_ratio": sp.get("conc_ratio"),
                "paid": sp.get("paid"), "proof": sp.get("proof"),
            })
            for t in counted:
                month = (t.get("date") or "")[:7]
                if len(month) == 7 and t.get("total") is not None:
                    months.setdefault(month, []).append(t["total"])

        calibration = [{"month": m, "mean": round(sum(v) / len(v), 1), "n": len(v)}
                       for m, v in sorted(months.items())]

        return jsonify({
            "points": points,
            "by_type": _group_scores(points, "type"),
            "by_region": _group_scores(points, "region"),
            "calibration": calibration,
            # What can be plotted against score. `have` lets the view say how thin an axis is
            # before it draws a shape that implies more data than there is.
            "axes": [{"key": k, "label": lab,
                      "have": sum(1 for p in points if p.get(k) is not None)}
                     for k, lab in (("age", "Age (years)"), ("conc_ratio", "Conc. Ratio"),
                                    ("paid", "Paid ($)"), ("proof", "Proof"))],
            "counts": {"scored": len(points), "sittings": sum(p["n"] for p in points)},
        })

    # -- pending bottles: the approval gate ---------------------------------
    @app.post("/api/pending")
    def api_pending_create():
        """Queue a new-bottle request. Nothing reaches the master from here (SPEC.md §8.5)."""
        body = request.get_json(silent=True) or {}
        try:
            rec = journal.write_pending_bottle(
                sheet=body.get("sheet") or "Bottle",
                fields=body.get("fields") or {},
                entered_from=body.get("entered_from") or "desktop")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, "pending": rec}), 201

    @app.get("/api/pending")
    def api_pending_list():
        import collection as collection_mod
        master = collection_mod.resolve_master(cfg, app.config["_master"])
        return jsonify({
            "pending": journal.pending(),
            "master": {"path": str(master), "exists": master.exists(),
                       "locked": master.with_name(f"~${master.name}").exists()},
            "next_code": catalog.next_code,
            "first_empty_row": catalog.first_empty_row,
        })

    @app.post("/api/pending/<uid>/approve")
    def api_pending_approve(uid):
        """Approve one row and write it to the master by the surgical append in SPEC.md §8.3.

        The Bottle Code is assigned here, on the PC, at approval time — never on the phone, so
        two queued requests can never claim the same code (§8.5).
        """
        import collection as collection_mod
        import master_write

        rec = next((p for p in journal.pending() if p["pending_uid"] == uid), None)
        if rec is None:
            return jsonify({"error": f"unknown pending request {uid}"}), 404

        body = request.get_json(silent=True) or {}
        fields = dict(rec.get("fields") or {})
        fields.update(body.get("fields") or {})        # the review panel may correct the parse
        sheet = body.get("sheet") or rec.get("sheet") or "Bottle"

        try:
            coll = collection_mod.load(config_path, master=app.config["_master"])
            result = master_write.append_row(
                collection_mod.resolve_master(cfg, app.config["_master"]),
                sheet, fields, backup_dir=backup_dir, keep=10,
                config_path=config_path, coll=coll,
                code_floor=journal.highest_seen(
                    sheet, prefix=collection_mod.CODE_PREFIX.get(sheet)))
        except master_write.MasterWriteError as e:
            return jsonify({"ok": False, "error": str(e)}), 409
        except Exception as e:                         # noqa: BLE001 — surface the real reason
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

        journal.resolve_pending(uid, approved=True, assigned_code=result["code"])

        # The collection changed under us; refresh the snapshot so every view sees the new bottle.
        try:
            fresh = collection_mod.load(config_path, master=app.config["_master"])
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps(fresh.snapshot(), ensure_ascii=False), encoding="utf-8")
            catalog.load()
        except Exception:                              # noqa: BLE001
            pass                                       # the row is written; Refresh fixes the view
        return jsonify({"ok": True, **result})

    @app.post("/api/pending/<uid>/reject")
    def api_pending_reject(uid):
        """Rejected requests move to pending/rejected/ rather than vanishing (SPEC.md §8.5)."""
        body = request.get_json(silent=True) or {}
        try:
            rec = journal.resolve_pending(uid, approved=False,
                                          reason=_clean(body.get("reason")))
        except KeyError:
            return jsonify({"error": f"unknown pending request {uid}"}), 404
        return jsonify({"ok": True, "pending": rec})

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
        retired = _record_departures(coll)
        journal.note_codes_seen(_highest_codes(coll))
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(json.dumps(coll.snapshot(), ensure_ascii=False), encoding="utf-8")
        published = _publish_for_phone(coll)
        catalog.load()
        return jsonify({"ok": True, "count": len(catalog), "snapshot": str(snap),
                        "published": published, "retired": retired})

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

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
                flight_pos=body.get("flight_pos"),
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

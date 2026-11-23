"""Run the desktop app on invented data, to look at its screens.

    python tools/demo_app.py [port]          (default 8799)

Everything it touches is in a temporary folder that is thrown away when it stops: an invented
collection, an invented journal with enough scored bottles to fill a table, a bar pour and a
finished bottle. The master workbook's path is one that does not exist, so nothing here can open
it, and the real journal in OneDrive is never read or written. The front end is served from
`static/` as it is on disk, which is the point: a change to a screen can be looked at without
rebuilding the exe or starting the real app.

`.claude/launch.json` starts it as "desktop".
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]

import app as app_mod                     # noqa: E402
import rubric as rubric_mod               # noqa: E402
import store as store_mod                 # noqa: E402

MAKERS = ["Old Harbor", "Black Fen", "Copper Ridge", "Three Gables", "North Quay", "Stonewater"]
LINES = ["Single Barrel", "Small Batch", "Cask Strength", "Bottled in Bond", "Port Finish",
         "Straight", "Reserve", "Double Oak"]
TYPES = ["Bourbon", "Rye", "Wheated", "Single Malt", "Blended", "Rum"]


def spirits(count=36):
    out = []
    for i in range(1, count + 1):
        sheet = "Bottle" if i <= 28 else "Sample"
        out.append({
            "_sheet": sheet, "_row": 8 + i, "code": f"{sheet[0]}-{i}",
            "distillery": MAKERS[i % len(MAKERS)], "name": f"{LINES[(i * 3) % len(LINES)]} {1 + i % 9}",
            "type": TYPES[(i * 5) % len(TYPES)], "region": ["America", "Scotland", "Japan"][i % 3],
            "age": None if i % 4 == 0 else float(4 + i % 15), "age_label": "NAS" if i % 4 == 0 else None,
            "proof": float(86 + (i * 7) % 50), "abv": (86 + (i * 7) % 50) / 2, "release_year": 2012 + i % 13,
            "size_ml": 750.0, "paid": float(35 + (i * 11) % 150), "rarity": "Common",
            "status": "Finished" if i == 5 else ("Opened" if i % 2 else "Unopened"),
        })
    return out


def card(rubric, i, sitting):
    """A complete scorecard, different for every spirit and a little different each sitting."""
    return {c["key"]: max(0, c["max"] - ((i * 7 + k * 5 + sitting) % 5) - ((i + k) % 2))
            for k, c in enumerate(rubric.as_config()["categories"])}


def build(folder):
    snapshot = folder / "collection.json"
    rows = spirits()
    snapshot.write_text(json.dumps({"generated_from": "demo", "spirits": rows}), encoding="utf-8")
    application = app_mod.create_app(
        str(ROOT / "config.yaml"), app_folder=str(folder / "journal"), snapshot_path=str(snapshot),
        master=str(folder / "no-such-workbook.xlsx"), rollup=str(folder / "Whiskey Tastings.xlsx"),
        backups=str(folder / "backups"))
    cfg = app_mod._load_cfg(str(ROOT / "config.yaml"))
    rubric = rubric_mod.Rubric(cfg["rubric"])
    journal = store_mod.Journal(folder / "journal", rubric).ensure()
    for i, sp in enumerate(rows, start=1):
        if i % 3 == 0:
            continue                                         # a third of the shelf is unscored
        for sitting in range(1 + i % 3):
            journal.write_tasting(spirit_id=sp["code"], scores=card(rubric, i, sitting),
                                  entered_from="pc", date=f"2026-{1 + i % 9:02d}-{10 + sitting:02d}")
    pour = journal.write_encounter(name="Back Bar Single Cask", distillery="A Distillery",
                                   type="Single Malt", age="NAS", venue="A Bar", entered_from="phone")
    journal.write_tasting(spirit_id=pour["encounter_uid"], scores=card(rubric, 50, 0),
                          entered_from="phone", venue="A Bar")
    return application


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv else 8799
    with tempfile.TemporaryDirectory(prefix="tasting-demo-") as tmp:
        application = build(Path(tmp))
        print(f"Demo data in {tmp} — http://127.0.0.1:{port}/")
        application.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()

"""
verify_gate.py — the shipping gate from SPEC.md §7.

Does a full round trip against the REAL master workbook — import the bottles, run a flight of
three pours through the server, submit them, regenerate the rollup, reopen — and then proves the
master is byte-identical to how it started.

    python verify_gate.py

The point is not that the code intends to leave the master alone. It is that after everything the
app actually does in a session, the 147 MB file and all 198 photos are provably untouched. A
SHA-256 either matches or it does not.

Nothing here writes to OneDrive: the journal, the rollup and the backups all go to a temp
directory that is deleted afterwards. The master is opened read-only and never written.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

import app as app_mod
import collection as collection_mod
import rollup as rollup_mod
import rubric as rubric_mod
import store as store_mod

CARD = dict(aroma=8, flavor=15, body=8, complexity=7, balance=8,
            finish=8, uniqueness=7, drinkability=8, aesthetics=4, value=4)   # 77, Silver


def sha256(path):
    d = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def parts_inventory(path):
    """Every zip member with its size — the photos and rich values live in here."""
    with zipfile.ZipFile(path) as z:
        return {i.filename: i.file_size for i in z.infolist()}


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    master = collection_mod.resolve_master(cfg)
    if not master.exists():
        print(f"FAIL  master workbook not found: {master}")
        return 2

    print(f"Master : {master}")
    print(f"Size   : {master.stat().st_size / 1e6:.1f} MB")
    print("Hashing before…", flush=True)
    before_hash = sha256(master)
    before_parts = parts_inventory(master)
    before_mtime = master.stat().st_mtime_ns
    media = {k: v for k, v in before_parts.items() if k.startswith("xl/media/")}
    rich = {k: v for k, v in before_parts.items() if k.startswith("xl/richData/")}
    print(f"  sha256      {before_hash}")
    print(f"  zip parts   {len(before_parts)}  (media {len(media)}, richData {len(rich)})")

    tmp = Path(tempfile.mkdtemp(prefix="gate-"))
    steps = []
    try:
        # 1 — import the bottles, exactly as the app does on a health check
        coll = collection_mod.load("config.yaml")
        counts = {s: len(r) for s, r in coll.rows.items()}
        steps.append(f"loaded the collection: {counts}")
        if coll.errors:
            print("FAIL  loader reported errors:",
                  [f"[{i.sheet}] {i.message}" for i in coll.errors])
            return 1

        # 2 — a full session of three pours through the real server code
        application = app_mod.create_app(
            "config.yaml",
            app_folder=str(tmp / "journal"),
            snapshot_path=str(Path(cfg["paths"]["local_data"]) / "collection.json"),
            rollup=str(tmp / "Whiskey Tastings.xlsx"),
            backups=str(tmp / "backups"),
        )
        c = application.test_client()
        codes = [r["code"] for r in coll.rows["Bottle"][:3]]

        sid = c.post("/api/session", json={"title": "Verification gate",
                                           "location": "Home"}).get_json()["session"]["session_id"]
        steps.append(f"opened flight {sid}")
        for pos, code in enumerate(codes, start=1):
            r = c.post("/api/tasting", json={
                "spirit_id": code, "scores": CARD, "session_id": sid, "flight_pos": pos,
                "notes": {"aroma": "gate run"}, "overall_notes": "verification gate",
            })
            assert r.status_code == 201, r.get_json()
        steps.append(f"submitted 3 pours: {', '.join(codes)}")

        # 3 — regenerate the readable workbook, and drain the (empty) phone lane
        summary = rollup_mod.build(store_mod.Journal(tmp / "journal", rubric_mod.load_rubric()),
                                   tmp / "Whiskey Tastings.xlsx", tmp / "backups")
        steps.append(f"regenerated the rollup: {summary['tastings']} tastings, "
                     f"{summary['sessions']} session")
        drain = c.post("/api/quickentry/drain").get_json()
        steps.append(f"drained Quick Entry: {drain['counts']}")

        # 4 — reopen everything, the way the next run would
        reopened = collection_mod.load("config.yaml")
        assert {s: len(r) for s, r in reopened.rows.items()} == counts
        pours = c.get(f"/api/session/{sid}").get_json()["pours"]
        assert len(pours) == 3, pours
        steps.append(f"reopened: {len(pours)} pours still linked, row counts unchanged")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    for s in steps:
        print(f"  ok  {s}")

    print("\nHashing after…", flush=True)
    after_hash = sha256(master)
    after_parts = parts_inventory(master)

    print(f"  sha256      {after_hash}")
    ok = True
    if after_hash != before_hash:
        print("\nFAIL  THE MASTER WORKBOOK CHANGED. Something wrote to it. "
              "All 198 photos are at risk — restore from OneDrive version history now.")
        ok = False
    if after_parts != before_parts:
        lost = set(before_parts) - set(after_parts)
        print(f"FAIL  zip members changed. Missing: {sorted(lost)[:10]}")
        ok = False
    if master.stat().st_mtime_ns != before_mtime:
        print("FAIL  the file's modification time moved — it was written to.")
        ok = False

    if ok:
        print(f"\nPASS  master byte-identical — {len(media)} photos and {len(rich)} richData "
              f"parts all present, mtime untouched.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

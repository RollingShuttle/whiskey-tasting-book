"""
rollup.py — regenerate Whiskey Tastings.xlsx from the journal.

This workbook is a READ-ONLY-FOR-HUMANS view. It is rewritten whole every time, so nothing should
ever be hand-edited into it: the journal is the source of truth and any manual change here is lost
on the next run. Deleting this file is harmless — it regenerates.

Writing it with openpyxl is safe precisely because it is our own file: no images, no rich values,
no web extensions. The master collection workbook is a completely different matter (SPEC.md §8).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import rubric as rubric_mod
import store as store_mod

HEAD_FILL = PatternFill("solid", fgColor="1B1815")
HEAD_FONT = Font(color="E8B44A", bold=True, size=10)


def _lock_path(target: Path) -> Path:
    return target.with_name(f"~${target.name}")


def is_locked(target) -> bool:
    """True when Excel has the workbook open. Check this before draining Quick Entry: the
    drain writes journal records, and it must not run if the regenerate that clears the
    sheet afterwards is going to fail."""
    return _lock_path(Path(target)).exists()


def _backup(target: Path, backup_dir: Path, keep: int):
    if not target.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Microseconds, not seconds. Three regenerations inside one second produced one backup
    # filename and silently overwrote each other — the third instance of this bug in the project.
    # See SPEC.md §0.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = backup_dir / f"{target.stem}_{stamp}{target.suffix}"
    shutil.copy2(target, dest)
    old = sorted(backup_dir.glob(f"{target.stem}_*{target.suffix}"))
    for p in old[:-keep]:
        p.unlink(missing_ok=True)
    return dest


def _sheet(wb, title, headers, rows, widths=None):
    ws = wb.create_sheet(title) if wb.sheetnames != ["Sheet"] else wb.active
    ws.title = title
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center")
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(10, len(h) + 2))
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    return ws


def build(journal: store_mod.Journal, target: Path, backup_dir: Path, keep=30,
          quick_rows=None) -> dict:
    """Regenerate the workbook. Returns a summary dict.

    `quick_rows` are Quick Entry rows that could not be matched to a bottle: they are written
    back onto the sheet with their reason, rather than dropped (SPEC.md §1.2)."""
    target = Path(target)
    if _lock_path(target).exists():
        raise RuntimeError(
            f"{target.name} is open in Excel ({_lock_path(target).name} present). "
            "Close it and run again — writing now would create a OneDrive conflict copy.")

    rb = journal.rubric
    keys = rb.keys
    tastings = journal.tastings()
    encounters = journal.encounters()
    careers = journal.careers()

    wb = Workbook()

    # --- Tastings: one row per sitting, newest first
    head = (["tasting_id", "revision", "date", "spirit_id", "session_id", "flight_pos", "barrel_id"]
            + [rb.category(k).label for k in keys]
            + ["total", "medal", "counted", "venue", "pour_price", "pour_size_oz",
               "blind", "status", "entered_from", "rubric_version", "created_at"]
            + [f"{rb.category(k).label} notes" for k in keys])
    rows = []
    for t in tastings:
        rows.append([t["tasting_id"], t["revision"], t["date"], t["spirit_id"],
                     t.get("session_id"), t.get("flight_pos"), t.get("barrel_id")]
                    + [t["scores"].get(k) for k in keys]
                    + [t["total"], t["medal"], "yes" if t["include_in_average"] else "no",
                       t.get("venue"), t.get("pour_price"), t.get("pour_size_oz"),
                       "yes" if t.get("blind") else "no", t.get("status"),
                       t.get("entered_from"), t.get("rubric_version"), t.get("created_at")]
                    + [(t.get("notes") or {}).get(k) for k in keys])
    _sheet(wb, "Tastings", head, rows,
           {"tasting_id": 34, "spirit_id": 12, "medal": 11, "venue": 24, "created_at": 28,
            **{f"{rb.category(k).label} notes": 46 for k in keys}})

    # --- Quick Entry: the phone lane. Drained into the journal and cleared on the next PC
    # run; anything that could not be matched comes back with a reason (SPEC.md §1.2).
    qhead = ["date", "display_name", "barrel_id", "nose", "palate", "finish", "notes",
             "problem"]
    qrows = [[q.get("date"), q.get("display_name"), q.get("barrel_id"), q.get("nose"),
              q.get("palate"), q.get("finish"), q.get("notes"), q.get("problem")]
             for q in (quick_rows or [])]
    _sheet(wb, "Quick Entry", qhead, qrows,
           {"display_name": 34, "nose": 26, "palate": 26, "finish": 26, "notes": 40,
            "problem": 52})

    # --- Careers: the number shown everywhere else
    chead = ["spirit_id", "career_score", "medal", "sittings_counted", "sittings_total",
             "best", "worst"] + [f"{rb.category(k).label} mean" for k in keys] \
            + [f"{rb.category(k).label} range" for k in keys]
    crows = []
    for sid, c in sorted(careers.items(),
                         key=lambda kv: (kv[1]["mean_total"] is None, -(kv[1]["mean_total"] or 0))):
        cats = c["categories"]
        crows.append([sid, c["mean_total"], c["medal"], c["n"], c["n_total"],
                      (c["range"] or (None, None))[1], (c["range"] or (None, None))[0]]
                     + [cats[k]["mean"] if cats else None for k in keys]
                     + [(f"{cats[k]['min']}–{cats[k]['max_seen']}" if cats and cats[k]["varies"]
                         else (str(cats[k]["min"]) if cats else None)) for k in keys])
    _sheet(wb, "Careers", chead, crows, {"spirit_id": 12, "career_score": 13, "medal": 11})

    # --- Sessions: the flights, and how many pours each one holds
    shead = ["session_id", "date", "title", "location", "company", "blind", "pours", "notes"]
    pour_counts = {}
    for t in tastings:
        if t.get("session_id"):
            pour_counts[t["session_id"]] = pour_counts.get(t["session_id"], 0) + 1
    srows = [[s["session_id"], s.get("date"), s.get("title"), s.get("location"),
              s.get("company"), "yes" if s.get("blind") else "no",
              pour_counts.get(s["session_id"], 0), s.get("notes")]
             for s in journal.sessions()]
    _sheet(wb, "Sessions", shead, srows,
           {"session_id": 26, "title": 28, "location": 22, "company": 22, "notes": 46})

    # --- Encounters: scored but never owned
    ehead = ["code", "name", "distillery", "type", "region", "age", "proof", "venue",
             "first_tasted", "linked_bottle_code", "encounter_uid", "notes"]
    erows = [[e.get("code"), e.get("name"), e.get("distillery"), e.get("type"), e.get("region"),
              e.get("age"), e.get("proof"), e.get("venue"), e.get("first_tasted"),
              e.get("linked_bottle_code"), e["encounter_uid"], e.get("notes")]
             for e in sorted(encounters, key=lambda x: x["first_tasted"])]
    _sheet(wb, "Encounters", ehead, erows, {"name": 32, "venue": 24, "encounter_uid": 24})

    # --- Pending: what is waiting for approval on the PC
    phead = ["pending_uid", "sheet", "created_at", "entered_from", "fields"]
    prows = [[p["pending_uid"], p["sheet"], p["created_at"], p.get("entered_from"),
              "; ".join(f"{k}={v}" for k, v in p["fields"].items())] for p in journal.pending()]
    _sheet(wb, "Pending", phead, prows, {"pending_uid": 26, "fields": 60})

    # --- About: so the file explains itself to anyone who opens it
    _sheet(wb, "About", ["field", "value"], [
        ["generated", datetime.now().isoformat(timespec="seconds")],
        ["source", "journal at " + str(journal.root)],
        ["rubric", f"{rb.name} v{rb.version}, max {rb.max_total}"],
        ["WARNING", "Generated file. Rewritten from the journal on every sync — "
                    "do not hand-edit, changes will be lost."],
        ["career score", "mean of counted sittings; medal from that mean rounded half-up"],
        ["category means", "display values. Do NOT add the column and compare to the career "
                           "score — ten roundings drift (SPEC.md §3.6)."],
    ], {"field": 16, "value": 90})

    _backup(target, backup_dir, keep)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-", suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    finally:
        wb.close()

    return {"path": str(target), "tastings": len(rows), "careers": len(crows),
            "sessions": len(srows), "encounters": len(erows), "pending": len(prows),
            "quick_entry": len(qrows),
            "bytes": target.stat().st_size}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Regenerate Whiskey Tastings.xlsx from the journal")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--root", help="journal root (also WHISKEY_APP_FOLDER)")
    ap.add_argument("--out", help="override the rollup workbook path")
    a = ap.parse_args(argv)

    with open(a.config, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    j = store_mod.Journal(store_mod.resolve_root(cfg, a.root),
                          rubric_mod.Rubric(cfg["rubric"])).ensure()
    out = Path(a.out or cfg["paths"]["rollup_workbook"])
    summary = build(j, out, Path(cfg["paths"]["backups"]), int(cfg["paths"].get("backup_keep", 30)))
    for k, v in summary.items():
        print(f"  {k:<11} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

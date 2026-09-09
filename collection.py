"""
collection.py — read-only loader for Whiskey Collection.xlsx

The master workbook holds 198 bottle photos as Excel rich values, which openpyxl cannot
round-trip. This module therefore NEVER writes to it. It opens read_only, and verify_untouched()
proves afterwards that the file on disk did not change.

Table ranges and column names are read from the workbook's own table definitions rather than
hard-coded, so re-ordering or extending a table in Excel does not break the loader.

Run it directly for a health report:
    python collection.py                 # report only
    python collection.py --hash          # also SHA-256 the master before/after (slower, stricter)
    python collection.py --snapshot data/collection.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

import openpyxl
import yaml

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CODE_RE = re.compile(r"^([BMS])-(\d+)$")

# Row counts recorded 7 Sep 2026, kept as a reference point rather than an expectation. Bottles
# and samples are deleted from the workbook when they are finished, so a smaller count is ordinary
# housekeeping and not a finding — reporting it every time would only teach you to ignore findings.
# A collapse is different: half the rows gone means the table reference or the read is wrong, not
# that somebody drank seventy bottles in an afternoon.
BASELINE = {"Bottle": 144, "Miniature": 22, "Sample": 209}
COLLAPSE_FRACTION = 0.5

# The Status column on all three sheets. These are typed by hand in Excel, so they are matched
# case-insensitively and trimmed. GONE means the spirit is no longer in the collection: the row
# stays, which is better than deleting it, because the reviews keep a bottle to belong to.
#
# A status outside this vocabulary is reported rather than guessed at. A typo — "Finsihed" — would
# otherwise leave an empty bottle counted as owned for ever, and nothing would ever say so.
GONE_STATUSES = {"finished", "removed"}
HELD_STATUSES = {"opened", "unopened"}
KNOWN_STATUSES = GONE_STATUSES | HELD_STATUSES


def is_gone(status):
    """True when a Status means the spirit has left the collection. A blank status is not gone:
    plenty of rows predate the column, and assuming the worst of them would hide real bottles."""
    return str(status or "").strip().lower() in GONE_STATUSES
CODE_PREFIX = {"Bottle": "B", "Miniature": "M", "Sample": "S"}


# --------------------------------------------------------------------------- model
@dataclass
class TableDef:
    sheet: str
    name: str            # displayName, e.g. "Collection"
    ref: str             # e.g. "B8:R308"
    columns: list         # header names, left to right
    calc_columns: dict = field(default_factory=dict)   # column name -> formula text

    @property
    def bounds(self):
        """(min_col, min_row, max_col, max_row) as 1-based ints."""
        a, b = self.ref.split(":")
        def split(cell):
            m = re.match(r"([A-Z]+)(\d+)", cell)
            col = 0
            for ch in m.group(1):
                col = col * 26 + (ord(ch) - 64)
            return col, int(m.group(2))
        (c1, r1), (c2, r2) = split(a), split(b)
        return c1, r1, c2, r2


@dataclass
class Issue:
    level: str           # "error" | "warn" | "info"
    sheet: str
    message: str


# --------------------------------------------------------------------------- table defs
def _read_table_defs(xlsx_path: Path) -> dict:
    """Parse table definitions straight from the .xlsx zip.

    openpyxl's read_only mode does not expose tables, and loading a 147 MB workbook in normal
    mode is slow and memory-hungry. The zip members we need are a few KB.
    """
    import xml.etree.ElementTree as ET

    defs: dict[str, TableDef] = {}
    with zipfile.ZipFile(xlsx_path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {r.get("Id"): r.get("Target") for r in rels}

        for sheet in wb.find(f"{NS}sheets"):
            title = sheet.get("name")
            rid = sheet.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            target = rid_to_target[rid].lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            rel_path = f"{os.path.dirname(target)}/_rels/{os.path.basename(target)}.rels"
            if rel_path not in z.namelist():
                continue

            sheet_rels = ET.fromstring(z.read(rel_path))
            for r in sheet_rels:
                tgt = r.get("Target")
                if "tables/" not in tgt:
                    continue
                tpath = os.path.normpath(os.path.join("xl", "worksheets", tgt)).replace("\\", "/")
                if tpath not in z.namelist():                       # tolerate ../ forms
                    tpath = "xl/tables/" + os.path.basename(tgt)
                t = ET.fromstring(z.read(tpath))
                cols, calc = [], {}
                for tc in t.find(f"{NS}tableColumns"):
                    cname = tc.get("name")
                    cols.append(cname)
                    f = tc.find(f"{NS}calculatedColumnFormula")
                    if f is not None and f.text:
                        calc[cname] = f.text
                defs[title] = TableDef(
                    sheet=title, name=t.get("displayName"), ref=t.get("ref"),
                    columns=cols, calc_columns=calc,
                )
    return defs


# --------------------------------------------------------------------------- parsing helpers
def _num(v):
    """Numbers that may arrive as 'NAS', 'N/A', '#VALUE!' or blank -> None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.upper() in {"NAS", "N/A", "NA", "-", "—", "#VALUE!", "UNKNOWN"}:
        return None
    try:
        return float(s.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _txt(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_code(code):
    """'B-12' -> ('B', 12). Suffixes are NOT zero-padded, so compare on the int."""
    m = CODE_RE.match(str(code or "").strip())
    return (m.group(1), int(m.group(2))) if m else None


# --------------------------------------------------------------------------- loader
class Collection:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.tables: dict[str, TableDef] = {}
        self.rows: dict[str, list] = {}
        self.issues: list[Issue] = []
        self._fingerprint = None
        self._formula_cache = {}

    # -- integrity -----------------------------------------------------------
    def _fp(self, with_hash=False):
        st = self.path.stat()
        h = None
        if with_hash:
            d = hashlib.sha256()
            with open(self.path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    d.update(chunk)
            h = d.hexdigest()
        return (st.st_size, st.st_mtime_ns, h)

    def verify_untouched(self):
        """Prove the master workbook is byte-for-byte as we found it."""
        now = self._fp(with_hash=self._fingerprint[2] is not None)
        if now != self._fingerprint:
            raise RuntimeError(
                f"MASTER WORKBOOK CHANGED DURING READ — {self.path}\n"
                f"  before {self._fingerprint}\n  after  {now}"
            )
        return True

    # -- load ----------------------------------------------------------------
    def load(self, with_hash=False):
        if not self.path.exists():
            raise FileNotFoundError(f"Master workbook not found: {self.path}")
        self._fingerprint = self._fp(with_hash)
        self.tables = _read_table_defs(self.path)

        wb = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
        try:
            for sheet, tdef in self.tables.items():
                if sheet not in wb.sheetnames:
                    self.issues.append(Issue("error", sheet, "table refers to a missing sheet"))
                    continue
                self.rows[sheet] = self._read_sheet(wb[sheet], tdef)
        finally:
            wb.close()

        self.verify_untouched()
        self._check()
        return self

    def _read_sheet(self, ws, tdef: TableDef):
        c1, r1, c2, r2 = tdef.bounds
        header_row = r1
        idx = {name: i for i, name in enumerate(tdef.columns)}
        code_col = "Bottle Code"
        out = []

        for excel_row, values in enumerate(
            ws.iter_rows(min_row=header_row + 1, max_row=r2, min_col=c1, max_col=c2,
                         values_only=True),
            start=header_row + 1,
        ):
            code = _txt(values[idx[code_col]]) if code_col in idx else None
            name = _txt(values[idx["Name"]]) if "Name" in idx else None
            dist = _txt(values[idx["Distillery"]]) if "Distillery" in idx else None
            if not code and not name and not dist:
                continue                                    # genuinely empty table row

            rec = {"_sheet": tdef.sheet, "_row": excel_row, "code": code}
            for col, i in idx.items():
                if col == code_col:
                    continue
                raw = values[i]
                key = col.lower().replace(" ", "_").replace(".", "").replace("(", "").replace(")", "")
                if col in {"Release Year", "Age", "Proof", "ABV", "Entry Proof", "Proof Diff",
                           "Conc. Ratio", "Size (ml)", "Size(oz)", "Paid", "Quantity"}:
                    rec[key] = _num(raw)
                    if col == "Age" and rec[key] is None and _txt(raw):
                        rec["age_label"] = _txt(raw)         # keep "NAS" rather than losing it
                elif col == "Picture":
                    continue                                # in-cell image, reads as #VALUE!
                else:
                    rec[key] = _txt(raw)
            # Derived once, here, so that every reader — the table, the phone, the analysis —
            # answers "do I still have this?" the same way.
            rec["owned"] = not is_gone(rec.get("status"))
            out.append(rec)
        return out

    def formula_columns(self, sheet):
        """Every column that carries a formula, with the template text and the row it came from.

        NOT the same as the table's declared calculated columns. On the Bottle sheet, ABV is a
        plain filled-down formula (=IF(K9="","",K9/2)) with no <calculatedColumnFormula> entry, so
        Excel will NOT auto-apply it to a row written by an outside tool. The writer must copy the
        formula from the last populated row and shift its row references. Cached after first call.
        """
        if sheet in self._formula_cache:
            return self._formula_cache[sheet]

        tdef = self.tables[sheet]
        c1, r1, c2, _ = tdef.bounds
        rows = self.rows.get(sheet, [])
        if not rows:
            self._formula_cache[sheet] = {}
            return {}
        src_row = max(r["_row"] for r in rows)

        wb = openpyxl.load_workbook(self.path, read_only=True, data_only=False)
        try:
            values = list(next(wb[sheet].iter_rows(
                min_row=src_row, max_row=src_row, min_col=c1, max_col=c2, values_only=True)))
        finally:
            wb.close()
        self.verify_untouched()

        found = {}
        for name, v in zip(tdef.columns, values):
            if isinstance(v, str) and v.startswith("="):
                found[name] = {
                    "formula": v,
                    "source_row": src_row,
                    "declared": name in tdef.calc_columns,
                    "uses_a1_refs": bool(re.search(r"[A-Z]{1,3}%d\b" % src_row, v)),
                }
        self._formula_cache[sheet] = found
        return found

    def first_empty_row(self, sheet):
        """Row number of the first fully empty row inside the table — where a new bottle goes."""
        tdef = self.tables[sheet]
        _, r1, _, r2 = tdef.bounds
        used = {r["_row"] for r in self.rows.get(sheet, [])}
        for r in range(r1 + 1, r2 + 1):
            if r not in used:
                return r
        return None

    def next_code(self, sheet, floor=0):
        prefix = CODE_PREFIX[sheet]
        highest = 0
        for r in self.rows.get(sheet, []):
            p = parse_code(r["code"])
            if p and p[0] == prefix:
                highest = max(highest, p[1])
        # `floor` is the highest number ever issued, which the journal
        # remembers. Without it, deleting the newest bottle would free its
        # code for the next one and re-point old reviews at a new whiskey.
        return f"{prefix}-{max(highest, int(floor or 0)) + 1}"

    # -- checks --------------------------------------------------------------
    def _check(self):
        for sheet, rows in self.rows.items():
            prefix = CODE_PREFIX.get(sheet)
            seen, blanks, malformed = {}, [], []
            for r in rows:
                code = r["code"]
                if not code:
                    blanks.append(r["_row"]); continue
                p = parse_code(code)
                if not p or p[0] != prefix:
                    malformed.append((r["_row"], code)); continue
                seen.setdefault(code, []).append(r["_row"])

            for code, at in seen.items():
                if len(at) > 1:
                    self.issues.append(Issue("error", sheet, f"duplicate Bottle Code {code} at rows {at}"))
            if blanks:
                self.issues.append(Issue("error", sheet, f"{len(blanks)} row(s) with no Bottle Code: {blanks[:8]}"))
            if malformed:
                self.issues.append(Issue("error", sheet, f"malformed code(s): {malformed[:8]}"))

            unknown = sorted({(r.get("status") or "").strip() for r in rows
                              if (r.get("status") or "").strip()
                              and (r.get("status") or "").strip().lower() not in KNOWN_STATUSES})
            if unknown:
                self.issues.append(Issue("warn", sheet,
                    f"status values not recognised: {unknown[:6]} — expected one of "
                    f"{sorted(KNOWN_STATUSES)}; a misspelling leaves an empty bottle counted "
                    "as owned"))

            base = BASELINE.get(sheet)
            if base is not None and len(rows) < base * COLLAPSE_FRACTION:
                self.issues.append(Issue("error", sheet,
                    f"only {len(rows)} rows against a reference of {base} — that is a broken "
                    "read or a changed table range, not a collection that shrank"))

            if self.first_empty_row(sheet) is None:
                self.issues.append(Issue("error", sheet,
                    "table is FULL — extend the table range in Excel before adding bottles"))

    @property
    def errors(self):
        return [i for i in self.issues if i.level == "error"]

    # -- output --------------------------------------------------------------
    def snapshot(self):
        return {
            "generated_from": str(self.path),
            "tables": {s: {"table": t.name, "ref": t.ref, "columns": t.columns,
                           "calculated": list(t.calc_columns)} for s, t in self.tables.items()},
            "counts": {s: len(r) for s, r in self.rows.items()},
            "formula_columns": {s: self.formula_columns(s) for s in self.rows},
            "next_code": {s: self.next_code(s) for s in self.rows},
            "first_empty_row": {s: self.first_empty_row(s) for s in self.rows},
            "spirits": [r for rows in self.rows.values() for r in rows],
        }


def resolve_master(cfg, override=None) -> Path:
    """Config holds the Windows path. WHISKEY_MASTER or --master override it, which is how the
    same code is exercised from a non-Windows checkout."""
    for candidate in (override, os.environ.get("WHISKEY_MASTER"), cfg["paths"]["master_workbook"]):
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return Path(cfg["paths"]["master_workbook"])          # let load() raise with the real path


def load(config_path="config.yaml", with_hash=False, master=None) -> Collection:
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not cfg["paths"].get("read_only_master", True):
        raise RuntimeError("read_only_master must stay true — this module never writes the master.")
    return Collection(resolve_master(cfg, master)).load(with_hash=with_hash)


# --------------------------------------------------------------------------- report
def main(argv=None):
    ap = argparse.ArgumentParser(description="Health report for Whiskey Collection.xlsx")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--master", help="override the master workbook path (also WHISKEY_MASTER)")
    ap.add_argument("--hash", action="store_true", help="SHA-256 the master before and after")
    ap.add_argument("--snapshot", metavar="PATH", help="write the parsed collection as JSON")
    a = ap.parse_args(argv)

    c = load(a.config, with_hash=a.hash, master=a.master)

    print(f"\nMaster : {c.path}")
    print(f"Size   : {c.path.stat().st_size / 1e6:.1f} MB")
    print(f"Intact : yes{' (sha-256 verified)' if a.hash else ' (size + mtime verified)'}\n")

    for sheet, tdef in c.tables.items():
        rows = c.rows.get(sheet, [])
        codes = [parse_code(r["code"]) for r in rows]
        nums = sorted(p[1] for p in codes if p)
        gaps = [n for n in range(1, (nums[-1] if nums else 0)) if n not in set(nums)]
        print(f"  {sheet:<10} table={tdef.name:<12} ref={tdef.ref:<10} "
              f"rows={len(rows):<4} codes={CODE_PREFIX[sheet]}-{nums[0] if nums else '?'}"
              f"..{CODE_PREFIX[sheet]}-{nums[-1] if nums else '?'}")
        print(f"  {'':<10} next={c.next_code(sheet):<8} first empty row={c.first_empty_row(sheet)}"
              f"   spare rows={tdef.bounds[3] - (c.first_empty_row(sheet) or tdef.bounds[3]) + 1}")
        fc = c.formula_columns(sheet)
        declared = [n for n, d in fc.items() if d["declared"]]
        filled = [n for n, d in fc.items() if not d["declared"]]
        print(f"  {'':<10} formula columns: declared={', '.join(declared) or 'none'}"
              f" | filled-down={', '.join(filled) or 'none'}")
        a1 = [n for n, d in fc.items() if d["uses_a1_refs"]]
        if a1:
            print(f"  {'':<10} needs row-ref shifting when writing: {', '.join(a1)}")
        if gaps:
            print(f"  {'':<10} unused code numbers: {gaps[:10]}{' …' if len(gaps) > 10 else ''}")
        print()

    if c.issues:
        print("Findings")
        for i in c.issues:
            mark = {"error": "  ERROR", "warn": "  WARN ", "info": "  info "}[i.level]
            print(f"{mark}  [{i.sheet}] {i.message}")
    else:
        print("Findings: none")

    if a.snapshot:
        p = Path(a.snapshot); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(c.snapshot(), indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nSnapshot written: {p}  ({p.stat().st_size / 1024:.0f} KB)")

    print()
    return 1 if c.errors else 0


if __name__ == "__main__":
    sys.exit(main())

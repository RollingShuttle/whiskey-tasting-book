"""
master_write.py — the only code allowed to add a row to `Whiskey Collection.xlsx` (SPEC.md §8).

The master holds 198 photos as Excel rich values. openpyxl cannot round-trip them, so this module
never opens the workbook for writing at all. It treats the .xlsx as what it is — a zip — and
rebuilds it copying every entry byte-for-byte except the one worksheet part being edited.

Why a string edit rather than an XML parse: the worksheet root carries
`mc:Ignorable="x14ac xr xr2 xr3"`, and re-serialising through ElementTree renames those prefixes
while leaving the Ignorable value pointing at names that no longer exist — which is exactly the
kind of thing that makes Excel offer to "repair" a file. Replacing one `<row>` element in the text
leaves the other ~378 KB of that part, and every namespace declaration in it, untouched.

The row is not appended: the three tables are defined over ranges far larger than their data, so a
new bottle *fills a row that already exists inside the table*. The table ref, the sheet dimension
and every relationship stay exactly as they are (SPEC.md §8.2).

Every write is preceded by a backup and followed by the mandatory verification in §8.4. If any
assertion fails the backup is restored automatically — a half-written master is never left behind.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import collection as collection_mod

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELNS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCREL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

CALC_CHAIN = "xl/calcChain.xml"
CELL_RE = re.compile(r'<c\b[^>]*?\sr="([A-Z]+)\d+"[^>]*?(?:/>|>.*?</c>)', re.DOTALL)


class MasterWriteError(RuntimeError):
    """Raised before anything is written, or after a failed verification and a restore."""


# --------------------------------------------------------------------------- column helpers
def col_to_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n


def index_to_col(n):
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- cell XML
def inline_string_cell(ref, text):
    """Strings go in as inline strings so sharedStrings.xml is never touched (SPEC.md §8.3)."""
    space = ' xml:space="preserve"' if str(text) != str(text).strip() else ""
    return f'<c r="{ref}" t="inlineStr"><is><t{space}>{_esc(text)}</t></is></c>'


def number_cell(ref, value):
    v = int(value) if float(value).is_integer() else float(value)
    return f'<c r="{ref}"><v>{v}</v></c>'


def formula_cell(ref, formula):
    """No cached <v>: the value is deliberately absent so Excel computes it on open."""
    return f'<c r="{ref}"><f>{_esc(formula.lstrip("="))}</f></c>'


def shift_formula(formula, from_row, to_row):
    """`=IF(K9="","",K9/2)` copied from row 9 to row 153 becomes `=IF(K153="","",K153/2)`.

    Only plain A1 references are moved. Structured references like
    Collection[[#This Row],[Proof]] are already row-relative and must be left alone.
    """
    return re.sub(r"(?<![A-Za-z0-9_])([A-Z]{1,3})%d(?![0-9])" % from_row,
                  lambda m: f"{m.group(1)}{to_row}", formula)


# --------------------------------------------------------------------------- worksheet surgery
def _worksheet_part(zf, sheet_name):
    """Map a sheet's display name to its worksheet part, through the workbook relationships."""
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    for sheet in wb.find(f"{{{MAIN}}}sheets"):
        if sheet.get("name") != sheet_name:
            continue
        target = targets[sheet.get(f"{{{DOCREL}}}id")].lstrip("/")
        return target if target.startswith("xl/") else "xl/" + target
    raise MasterWriteError(f"no sheet named {sheet_name!r} in the workbook")


def replace_row(sheet_xml, row_number, cells):
    """Put `cells` ({column letter: cell xml}) into row `row_number`, keeping ascending order.

    Any cell already in that row and not being written is preserved. XML requires cells in
    ascending column order, and Excel is unforgiving about it.
    """
    row_re = re.compile(r'<row\b[^>]*?\sr="%d"(?:\s[^>]*?)?(?:/>|>.*?</row>)' % row_number,
                        re.DOTALL)
    match = row_re.search(sheet_xml)
    if not match:
        # Excel writes a <row> element for every row in a used range, but a workbook produced by
        # other tools may not. Creating it is still filling a row inside the table: the table ref
        # and the sheet dimension do not move. Rows must stay in ascending order.
        ordered = "".join(cells[c] for c in sorted(cells, key=col_to_index))
        fresh = f'<row r="{row_number}">{ordered}</row>'
        for m in re.finditer(r'<row(?=[\s/>])[^>]*?\sr="(\d+)"', sheet_xml):
            if int(m.group(1)) > row_number:
                return sheet_xml[:m.start()] + fresh + sheet_xml[m.start():]
        if "</sheetData>" in sheet_xml:
            at = sheet_xml.rindex("</sheetData>")
            return sheet_xml[:at] + fresh + sheet_xml[at:]
        return re.sub(r"<sheetData\s*/>", f"<sheetData>{fresh}</sheetData>", sheet_xml, count=1)

    original = match.group(0)
    open_tag = original[:original.index(">") + 1]
    self_closing = open_tag.endswith("/>")
    attrs = open_tag[len("<row"):-2 if self_closing else -1].strip()

    existing = {}
    if not self_closing:
        inner = original[original.index(">") + 1:original.rindex("</row>")]
        for m in CELL_RE.finditer(inner):
            existing[m.group(1)] = m.group(0)

    merged = {**existing, **cells}
    ordered = "".join(merged[c] for c in sorted(merged, key=col_to_index))
    rebuilt = f"<row {attrs}>{ordered}</row>" if attrs else f"<row>{ordered}</row>"
    return sheet_xml[:match.start()] + rebuilt + sheet_xml[match.end():]


def _drop_calc_chain(entries):
    """Excel rebuilds calcChain silently; a stale one after an edit is what corrupts a file."""
    entries.pop(CALC_CHAIN, None)

    ct = entries.get("[Content_Types].xml")
    if ct:
        entries["[Content_Types].xml"] = re.sub(
            r'<Override[^>]*PartName="/xl/calcChain\.xml"[^>]*/>', "",
            ct.decode("utf-8")).encode("utf-8")

    rels = entries.get("xl/_rels/workbook.xml.rels")
    if rels:
        entries["xl/_rels/workbook.xml.rels"] = re.sub(
            r'<Relationship[^>]*Target="calcChain\.xml"[^>]*/>', "",
            rels.decode("utf-8")).encode("utf-8")


def _force_full_calc(entries):
    """So the formula columns actually evaluate the next time the workbook is opened."""
    wb = entries["xl/workbook.xml"].decode("utf-8")
    if "<calcPr" in wb:
        wb = re.sub(r"<calcPr\b([^>]*?)/>",
                    lambda m: f'<calcPr{_with_full_calc(m.group(1))}/>', wb, count=1)
    else:
        wb = wb.replace("</workbook>", '<calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
    entries["xl/workbook.xml"] = wb.encode("utf-8")


def _with_full_calc(attrs):
    if "fullCalcOnLoad" in attrs:
        return re.sub(r'fullCalcOnLoad="[^"]*"', 'fullCalcOnLoad="1"', attrs)
    return attrs.rstrip() + ' fullCalcOnLoad="1"'


# --------------------------------------------------------------------------- the write
def append_row(master_path, sheet, fields, *, backup_dir, keep=10, config_path="config.yaml",
               coll=None):
    """Fill the first empty row inside `sheet`'s table. Returns a summary dict.

    `fields` maps header names to values; the Bottle Code is assigned here, on the PC, never by
    the phone (SPEC.md §8.5).
    """
    master = Path(master_path)
    lock = master.with_name(f"~${master.name}")
    if lock.exists():
        raise MasterWriteError(
            f"{master.name} is open in Excel ({lock.name} present). Close it and try again.")

    code = str(fields.get("Bottle Code") or "")
    if code.upper().startswith("X-"):
        raise MasterWriteError(
            "an X- encounter is a bar pour, not inventory — it is never written to the "
            "collection workbook (SPEC.md §2.1, §8.2).")

    coll = coll or collection_mod.load(config_path)
    if sheet not in coll.tables:
        raise MasterWriteError(f"unknown sheet {sheet!r}")
    tdef = coll.tables[sheet]
    _, _, _, max_row = tdef.bounds

    target = coll.first_empty_row(sheet)
    if target is None or target > max_row:
        raise MasterWriteError(
            f"the {sheet} table is full (ref {tdef.ref}). Extend the table range in Excel once, "
            "then try again — this code will not grow a table's ref itself (SPEC.md §8.2).")

    if not fields.get("Bottle Code"):
        fields = dict(fields, **{"Bottle Code": coll.next_code(sheet)})
        code = fields["Bottle Code"]

    before_rows = len(coll.rows.get(sheet, []))
    formulas = coll.formula_columns(sheet)
    col_of = {name: index_to_col(tdef.bounds[0] + i) for i, name in enumerate(tdef.columns)}

    # --- build the cells
    cells = {}
    for name, value in fields.items():
        if name not in col_of or value is None or value == "":
            continue
        letter = col_of[name]
        ref = f"{letter}{target}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cells[letter] = number_cell(ref, value)
        else:
            cells[letter] = inline_string_cell(ref, value)

    for name, info in formulas.items():
        if name not in col_of:
            continue
        letter = col_of[name]
        text = shift_formula(info["formula"], info["source_row"], target)
        cells[letter] = formula_cell(f"{letter}{target}", text)   # a formula always wins

    # --- backup first, always
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_dir / f"{master.stem}_{stamp}{master.suffix}"
    shutil.copy2(master, backup)
    for old in sorted(backup_dir.glob(f"{master.stem}_*{master.suffix}"))[:-keep]:
        old.unlink(missing_ok=True)

    before = _inventory(master)

    # --- rebuild the zip, copying everything we are not deliberately changing
    with zipfile.ZipFile(master) as z:
        part = _worksheet_part(z, sheet)
        infos = z.infolist()
        entries = {i.filename: z.read(i.filename) for i in infos}

    sheet_xml = entries[part].decode("utf-8")
    entries[part] = replace_row(sheet_xml, target, cells).encode("utf-8")
    _drop_calc_chain(entries)
    _force_full_calc(entries)

    tmp = master.with_name(f".tmp-{stamp}-{master.name}")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for info in infos:
                if info.filename not in entries:
                    continue                       # calcChain, deliberately dropped
                fresh = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                fresh.compress_type = info.compress_type
                fresh.external_attr = info.external_attr
                out.writestr(fresh, entries[info.filename])
        _verify(tmp, before, sheet, target, code, before_rows + 1, config_path)
        tmp.replace(master)
    except BaseException as exc:
        tmp.unlink(missing_ok=True)
        if not _same_bytes(master, backup):
            shutil.copy2(backup, master)           # never leave a half-written master
            raise MasterWriteError(
                f"write failed verification and the backup was restored: {exc}") from exc
        raise MasterWriteError(f"write refused: {exc}") from exc

    return {"sheet": sheet, "row": target, "code": code, "backup": str(backup),
            "rows_before": before_rows, "rows_after": before_rows + 1}


# --------------------------------------------------------------------------- verification §8.4
def _inventory(path):
    with zipfile.ZipFile(path) as z:
        sizes = {i.filename: i.file_size for i in z.infolist()}
        rich = {n: z.read(n) for n in sizes if n.startswith("xl/richData/")}
    return {"sizes": sizes, "rich": rich}


def _same_bytes(a, b):
    return Path(a).stat().st_size == Path(b).stat().st_size


def _verify(candidate, before, sheet, row, code, expected_rows, config_path):
    """Every assertion in SPEC.md §8.4. Raising here restores the backup."""
    after = _inventory(candidate)

    lost = (set(before["sizes"]) - {CALC_CHAIN}) - set(after["sizes"])
    if lost:
        raise MasterWriteError(f"zip members went missing: {sorted(lost)[:8]}")
    gained = set(after["sizes"]) - set(before["sizes"])
    if gained:
        raise MasterWriteError(f"unexpected new zip members: {sorted(gained)[:8]}")

    for name, size in before["sizes"].items():
        if name.startswith("xl/media/") and after["sizes"].get(name) != size:
            raise MasterWriteError(f"media part changed size: {name}")
    if len(before["rich"]) != len(after["rich"]):
        raise MasterWriteError("a richData part went missing")
    for name, blob in before["rich"].items():
        if after["rich"].get(name) != blob:
            raise MasterWriteError(f"richData part changed: {name}")

    check = collection_mod.Collection(candidate).load()
    rows = check.rows.get(sheet, [])
    if len(rows) != expected_rows:
        raise MasterWriteError(
            f"expected {expected_rows} rows on {sheet} after the write, found {len(rows)}")
    written = next((r for r in rows if r["_row"] == row), None)
    if written is None or written["code"] != code:
        raise MasterWriteError(f"row {row} did not read back as {code}")
    return True

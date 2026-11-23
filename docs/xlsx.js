/* Whiskey Tasting Book — the collection as an Excel workbook (SPEC.md §9.2).

   A phone-only book has no PC and so no "Whiskey Collection.xlsx". This writes one, laid out the
   way the PC's master workbook is: the same three sheets, each with its table at B8 under the
   same name, the same columns in the same order, the same calculated columns and drop-downs, the
   header frozen above them and the rows pre-allocated to the same depth. collection.py reads the
   result as it reads the master, and master_write.py can append to it — which is what "the same
   structure" has to mean, and what tests/test_phone_js.py holds it to.

   No library: an .xlsx is a zip of XML, and both halves are written out here. Nothing in this
   file touches `document`, so it runs in V8 under the tests, where Python's zipfile, zlib and
   openpyxl check every byte of it. Everything is synchronous, its own DEFLATE included: the
   platform's CompressionStream does not exist in that V8, and a writer the tests cannot run is
   a writer nobody has checked. */
"use strict";

const Xlsx = (() => {
  // ---------------------------------------------------------------- bytes
  /** UTF-8, by hand: TextEncoder is a browser's, and this file has to run without one. A
      surrogate with no partner becomes U+FFFD rather than three bytes no XML parser accepts. */
  function utf8(str) {
    const out = new Uint8Array(str.length * 3);
    let n = 0;
    for (let i = 0; i < str.length; i++) {
      let c = str.charCodeAt(i);
      if (c >= 0xd800 && c < 0xe000) {
        const d = c < 0xdc00 && i + 1 < str.length ? str.charCodeAt(i + 1) : 0;
        if (d >= 0xdc00 && d < 0xe000) { c = 0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00); i++; }
        else c = 0xfffd;
      }
      if (c < 0x80) out[n++] = c;
      else if (c < 0x800) { out[n++] = 0xc0 | (c >> 6); out[n++] = 0x80 | (c & 63); }
      else if (c < 0x10000) {
        out[n++] = 0xe0 | (c >> 12); out[n++] = 0x80 | ((c >> 6) & 63); out[n++] = 0x80 | (c & 63);
      } else {
        out[n++] = 0xf0 | (c >> 18); out[n++] = 0x80 | ((c >> 12) & 63);
        out[n++] = 0x80 | ((c >> 6) & 63); out[n++] = 0x80 | (c & 63);
      }
    }
    return out.subarray(0, n);
  }

  const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();
  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 255] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  // ---------------------------------------------------------------- deflate
  /* RFC 1951, one block, the fixed Huffman codes. The sheets are three hundred rows of the same
     four formulas, so the plainest matcher there is — the last place these three bytes were seen
     — already takes them from most of a megabyte to a few tens of kilobytes. */
  const LEN_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83,
    99, 115, 131, 163, 195, 227, 258];
  const LEN_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
  const DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769,
    1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577];
  const DIST_EXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11,
    12, 12, 13, 13];
  // Huffman codes go out most significant bit first and everything else least: reverse them once.
  const reversed = (code, len) => {
    let r = 0;
    for (let i = 0; i < len; i++) { r = (r << 1) | (code & 1); code >>= 1; }
    return r;
  };
  const LIT_CODE = new Uint16Array(288), LIT_LEN = new Uint8Array(288);
  for (let s = 0; s < 288; s++) {
    const [code, len] = s < 144 ? [0x30 + s, 8] : s < 256 ? [0x190 + (s - 144), 9]
      : s < 280 ? [s - 256, 7] : [0xc0 + (s - 280), 8];
    LIT_CODE[s] = reversed(code, len);
    LIT_LEN[s] = len;
  }
  const DIST_CODE = DIST_BASE.map((_, d) => reversed(d, 5));

  function deflate(src) {
    const n = src.length;
    const out = new Uint8Array(n + (n >> 3) + 64);         // nine bits a byte is the worst case
    let pos = 0, acc = 0, nbits = 0;
    const put = (value, count) => {
      acc |= value << nbits;
      nbits += count;
      while (nbits >= 8) { out[pos++] = acc & 255; acc >>>= 8; nbits -= 8; }
    };
    const head = new Int32Array(1 << 15).fill(-1);
    const hash = (i) => ((src[i] << 10) ^ (src[i + 1] << 5) ^ src[i + 2]) & 32767;

    put(1, 1);                                             // the last block
    put(1, 2);                                             // fixed Huffman
    let i = 0;
    while (i < n) {
      let len = 0, dist = 0;
      if (i + 2 < n) {
        const h = hash(i), cand = head[h];
        head[h] = i;
        if (cand >= 0 && i - cand <= 32768) {
          const max = Math.min(258, n - i);
          let l = 0;
          while (l < max && src[cand + l] === src[i + l]) l++;
          if (l >= 3) { len = l; dist = i - cand; }
        }
      }
      if (!len) { put(LIT_CODE[src[i]], LIT_LEN[src[i]]); i++; continue; }
      let c = 28;
      while (LEN_BASE[c] > len) c--;
      put(LIT_CODE[257 + c], LIT_LEN[257 + c]);
      if (LEN_EXTRA[c]) put(len - LEN_BASE[c], LEN_EXTRA[c]);
      let d = 29;
      while (DIST_BASE[d] > dist) d--;
      put(DIST_CODE[d], 5);
      if (DIST_EXTRA[d]) put(dist - DIST_BASE[d], DIST_EXTRA[d]);
      for (let k = 1; k < len; k++) if (i + k + 2 < n) head[hash(i + k)] = i + k;
      i += len;
    }
    put(LIT_CODE[256], LIT_LEN[256]);                      // end of block
    if (nbits) out[pos++] = acc & 255;
    return out.subarray(0, pos);
  }

  // ---------------------------------------------------------------- zip
  /** A zip of `entries` ({name, data: Uint8Array}), each deflated unless that makes it bigger. */
  function zip(entries, when = new Date()) {
    const time = (when.getHours() << 11) | (when.getMinutes() << 5) | (when.getSeconds() >> 1);
    const date = ((Math.max(1980, when.getFullYear()) - 1980) << 9) | ((when.getMonth() + 1) << 5) | when.getDate();
    const files = entries.map((e) => {
      const packed = deflate(e.data);
      const stored = packed.length >= e.data.length;
      return { name: utf8(e.name), body: stored ? e.data : packed, method: stored ? 0 : 8,
               crc: crc32(e.data), size: e.data.length };
    });
    const total = files.reduce((a, f) => a + 30 + 46 + 2 * f.name.length + f.body.length, 22);
    const out = new Uint8Array(total);
    const view = new DataView(out.buffer);
    let at = 0;
    const u16 = (v) => { view.setUint16(at, v, true); at += 2; };
    const u32 = (v) => { view.setUint32(at, v, true); at += 4; };
    const raw = (b) => { out.set(b, at); at += b.length; };
    const common = (f) => {                                // the fields both headers share
      u16(20); u16(0x0800); u16(f.method); u16(time); u16(date);
      u32(f.crc); u32(f.body.length); u32(f.size); u16(f.name.length); u16(0);
    };
    for (const f of files) {
      f.offset = at;
      u32(0x04034b50); common(f); raw(f.name); raw(f.body);
    }
    const dirAt = at;
    for (const f of files) {
      u32(0x02014b50); u16(20); common(f);
      u16(0); u16(0); u16(0); u32(0); u32(f.offset); raw(f.name);
    }
    const dirSize = at - dirAt;
    u32(0x06054b50); u16(0); u16(0); u16(files.length); u16(files.length);
    u32(dirSize); u32(dirAt); u16(0);
    return out;
  }

  // ---------------------------------------------------------------- the workbook's layout
  /* The master workbook, as far as its shape goes. Every table's header is on row 8 and starts
     in column B; `last` is the row the table is allocated down to. `calc` are the columns the
     table itself declares as calculated — on the Bottle sheet ABV is only a filled-down formula,
     and that difference is kept because collection.py reports it. `widths` run from column B
     and may go on past the table, under the totals beside the title. `prefix` is the letter the
     sheet's Bottle Codes begin with (collection.CODE_PREFIX); a code of any other shape is one
     collection.py calls malformed. */
  const HEADER_ROW = 8, FIRST_COL = 2;
  const SHEETS = [
    { name: "Bottle", prefix: "B", title: "Bottle List", table: "Collection", style: "TableStyleLight18", last: 308,
      columns: ["Bottle Code", "Distillery", "Name", "Type", "Region", "Notes", "Release Year", "Age",
        "Rarity", "Proof", "ABV", "Entry Proof", "Barrel Proof", "Proof Diff", "Conc. Ratio",
        "Size (ml)", "Status", "Paid"],
      calc: ["Proof Diff", "Conc. Ratio"],
      widths: [13.3, 27.3, 43.4, 11.1, 9.9, 47.6, 8.3, 9.4, 11.7, 6.6, 8.6, 11.3, 11.3, 11, 11, 10.3, 11, 11.3,
        7.9, 9.6, 9.6, 9.6, 17.4],                        // …and T to X, under the three totals
      stats: [
        { from: "T", to: "U", label: "Total Bottles",
          formula: 'COUNTA(Collection[Name])-COUNTIF(Collection[Status],"Removed")-COUNTIF(Collection[Status],"Finished")',
          value: (rows) => rows.filter((s) => !blank(s.name) && !gone(s)).length },
        { from: "V", to: "W", label: "Unopened", formula: 'COUNTIF(Collection[Status],"Unopened")',
          value: (rows) => rows.filter((s) => s.status === "Unopened").length },
        { from: "X", to: "X", label: "Total Paid($)",
          formula: 'SUMIFS(Collection[Paid],Collection[Status],"<>Removed",Collection[Status],"<>Finished")',
          value: (rows) => rows.reduce((a, s) => a + (!gone(s) && isNum(s.paid) ? s.paid : 0), 0) },
      ] },
    { name: "Miniature", prefix: "M", title: "Miniature List", table: "Collection4", style: "TableStyleLight18", last: 308,
      columns: ["Bottle Code", "Distillery", "Name", "Notes", "Type", "Region", "Age", "Proof", "ABV",
        "Size (ml)", "Status"],
      calc: ["ABV"],
      widths: [19.6, 42, 38.1, 11.4, 12.4, 9, 6.3, 6.4, 6.4, 9.1, 11.7, 11.6, 9.6],
      stats: [
        { from: "M", to: "N", label: "Total Bottles", formula: "COUNTA(Collection4[Name])",
          value: (rows) => rows.filter((s) => !blank(s.name)).length },
      ] },
    { name: "Sample", prefix: "S", title: "Sample List", table: "SampleList", style: "TableStyleMedium2", last: 603,
      columns: ["Bottle Code", "Distillery", "Name", "Special note", "Comment", "Type", "Region",
        "Release Year", "Age", "Rarity", "Proof", "ABV", "Entry Proof", "Barrel Proof", "Proof Diff",
        "Conc. Ratio", "Size(oz)", "Bottle Type", "Quantity", "Status", "Picture"],
      calc: ["ABV", "Proof Diff", "Conc. Ratio"],
      widths: [12.1, 26.7, 32.9, 32.1, 37, 11.4, 11.3, 6, 9.4, 11.6, 7.4, 10.4, 12.3, 11.7, 11.3, 10.3,
        10, 14.9, 7.9, 11.7, 8.3],
      stats: [] },
  ];
  const RARITY = ["Common", "Uncommon", "Rare", "Unicorn", "Impossible", "Dusty"];
  const STATUS = ["Opened", "Unopened", "Finished", "Removed"];
  const REGIONS = ["America", "Scotland", "Japan", "Canada", "Mexico", "Caribbean", "Ireland", "China",
    "France", "Germany"];
  const TEXT_COLUMNS = new Set(["Distillery", "Name", "Notes", "Special note", "Comment"]);
  const SPARE_ROWS = 25;        // room left below a collection that has outgrown the allocation

  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const blank = (v) => v === null || v === undefined || v === "";
  const gone = (s) => ["finished", "removed"].includes(String(s.status || "").trim().toLowerCase());

  /** Control characters XML 1.0 has no way to carry are dropped; the five that need it, escaped. */
  const text = (v) => String(v).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const esc = (v) => text(v).replace(/"/g, "&quot;");        // inside an attribute
  const letters = (n) => {
    let s = "";
    for (; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
    return s;
  };

  /** The snapshot's name for a column — collection.py's own rule, so a field the PC published
      under a column lands back under it. */
  const keyOf = (col) => col.toLowerCase().replace(/ /g, "_").replace(/[.()]/g, "");
  function valueOf(col, s) {
    if (col === "Age") return blank(s.age) ? s.age_label : s.age;
    if (col === "Picture") return null;                    // an in-cell photo; the phone has none
    return s[keyOf(col)];
  }

  /* The three formulas, as the master writes them. The structured references are per table. */
  const here = (table, col) => `${table}[[#This Row],[${col}]]`;
  const proofDiff = (t) => `IF(${here(t, "Name")}="","",IF(${here(t, "Barrel Proof")}="No","N/A",`
    + `IF(OR(ISTEXT(${here(t, "Proof")}),ISTEXT(${here(t, "Entry Proof")}),${here(t, "Proof")}="",`
    + `${here(t, "Entry Proof")}=""),"N/A",${here(t, "Proof")}-${here(t, "Entry Proof")})))`;
  const concRatio = (t) => `IF(${here(t, "Barrel Proof")}="No","N/A",IF(OR(ISTEXT(${here(t, "Age")}),`
    + `ISTEXT(${here(t, "Entry Proof")})),"N/A",IF(OR(${here(t, "Entry Proof")}="",${here(t, "Age")}=""),"",`
    + `ROUND((${here(t, "Proof")}-${here(t, "Entry Proof")})/${here(t, "Age")},3))))`;
  const abv = (proofCol, row) => `IF(${proofCol}${row}="","",${proofCol}${row}/2)`;
  const rowNumber = (row) => `IF(C${row}="","",ROW()-${HEADER_ROW})`;

  /** What each formula comes to, worked out here as well: a phone's Quick Look and collection.py
      both read the value stored beside a formula, and neither will calculate one. `s` may be
      undefined, for a row allocated but not yet filled. */
  function computed(col, s) {
    if (!s) return "";
    const age = valueOf("Age", s), proof = s.proof, entry = s.entry_proof;
    if (col === "ABV") return isNum(proof) ? proof / 2 : "";
    if (col === "Proof Diff") {
      if (blank(s.name)) return "";
      return s.barrel_proof === "No" || !isNum(proof) || !isNum(entry) ? "N/A" : proof - entry;
    }
    if (s.barrel_proof === "No" || typeof age === "string" || typeof entry === "string") return "N/A";
    if (blank(entry) || blank(age)) return "";
    const ratio = Math.round((((isNum(proof) ? proof : 0) - entry) / age) * 1000) / 1000;
    return isNum(ratio) ? ratio : "";
  }

  /** A drop-down's choices as Excel wants them: one quoted string, commas between, and no more
      than 255 characters of it — a longer one and Excel calls the whole file damaged. A choice
      with a comma or a quote in it cannot be written this way at all, and is left off. */
  function listFormula(items) {
    const seen = new Set();
    let joined = "";
    for (const item of items) {
      const t = String(item ?? "").trim();
      if (!t || /[",]/.test(t) || seen.has(t.toLowerCase())) continue;
      if (joined.length + t.length + 1 > 250) break;
      seen.add(t.toLowerCase());
      joined += (joined ? "," : "") + t;
    }
    return joined ? `"${joined}"` : null;                  // null: nothing to make a drop-down of
  }

  // ---------------------------------------------------------------- the parts
  const XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';
  const MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
  const REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  const PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships";
  const TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml";
  const S_TITLE = 1, S_HEAD = 2, S_CENTRE = 3, S_MONEY = 4;        // indexes into cellXfs below

  const STYLES = `${XML}<styleSheet xmlns="${MAIN}">`
    + '<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    + '<font><b/><sz val="20"/><name val="Calibri"/><family val="2"/></font></fonts>'
    + '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    + '<fill><patternFill patternType="gray125"/></fill></fills>'
    + '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    + '<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    + '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
    + '<alignment horizontal="center" vertical="center"/></xf>'
    // No font of its own on the header: the table style's shows through an untouched one.
    + '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    + '<alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    + '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    + '<alignment horizontal="center"/></xf>'
    + '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1">'
    + '<alignment horizontal="center"/></xf></cellXfs>'
    + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>';

  /** The workbook as its parts, [{name, text}], for `spirits` in the snapshot's shape. A spirit
      goes on the sheet its `_sheet` names, and on Bottle when that is none of the three. */
  function collectionParts(spirits, { types = [] } = {}) {
    const strings = [], index = new Map();
    const shared = (text) => {
      const t = String(text);
      if (!index.has(t)) { index.set(t, strings.length); strings.push(t); }
      return index.get(t);
    };
    const cell = (ref, v, style) => {
      const s = style ? ` s="${style}"` : "";
      if (blank(v)) return "";
      if (isNum(v)) return `<c r="${ref}"${s}><v>${v}</v></c>`;
      return `<c r="${ref}"${s} t="s"><v>${shared(v)}</v></c>`;
    };
    const formulaCell = (ref, formula, v, style) => {
      const s = style ? ` s="${style}"` : "";
      return isNum(v) ? `<c r="${ref}"${s}><f>${text(formula)}</f><v>${v}</v></c>`
        : `<c r="${ref}"${s} t="str"><f>${text(formula)}</f><v>${text(v)}</v></c>`;
    };
    const lists = {
      Type: listFormula(types),
      Region: listFormula(REGIONS.concat(spirits.map((s) => s && s.region))),
      Rarity: listFormula(RARITY), Status: listFormula(STATUS), "Barrel Proof": listFormula(["Yes", "No"]),
    };

    const names = new Set(SHEETS.map((sh) => sh.name));
    const sheets = SHEETS.map((sh, n) => {
      const rows = spirits.filter((s) => s && (names.has(s._sheet) ? s._sheet : "Bottle") === sh.name);
      const last = Math.max(sh.last, HEADER_ROW + rows.length + SPARE_ROWS);
      const col = (name) => letters(FIRST_COL + sh.columns.indexOf(name));
      const end = letters(FIRST_COL + sh.columns.length - 1);
      const ref = `B${HEADER_ROW}:${end}${last}`;
      const formulaFor = { "Proof Diff": proofDiff(sh.table), "Conc. Ratio": concRatio(sh.table) };
      /* A bottle added on the phone is L-n there, a prefix the workbook does not have. Here it
         takes the sheet's own letter and keeps its number — L-3 is B-3 — moved up past any code
         the PC published to this phone, so the two can never be the same bottle. */
      const numberOf = (s, letter) => {
        const m = /^([A-Z])-(\d+)$/.exec(String((s && s.code) || ""));
        return m && m[1] === letter ? Number(m[2]) : 0;
      };
      const taken = rows.reduce((hi, s) => Math.max(hi, numberOf(s, sh.prefix)), 0);
      const codeOf = (s) => (numberOf(s, "L") ? `${sh.prefix}-${taken + numberOf(s, "L")}` : s.code);

      const body = [];
      const stat = (row, cells) => `<row r="${row}">${cells}</row>`;
      body.push(stat(2, cell("B2", sh.title, S_TITLE)
        + sh.stats.map((st) => cell(`${st.from}2`, st.label, S_HEAD)).join("")));
      if (sh.stats.length) {
        body.push(stat(5, sh.stats.map((st) =>
          formulaCell(`${st.from}5`, st.formula, st.value(rows), S_TITLE)).join("")));
      }
      body.push(`<row r="${HEADER_ROW}" ht="30.75" customHeight="1">`
        + sh.columns.map((name, j) => cell(`${letters(FIRST_COL + j)}${HEADER_ROW}`, name, S_HEAD)).join("")
        + "</row>");
      for (let r = HEADER_ROW + 1; r <= last; r++) {
        const s = rows[r - HEADER_ROW - 1];
        let cells = formulaCell(`A${r}`, rowNumber(r), s && !blank(s.distillery) ? r - HEADER_ROW : "", S_CENTRE);
        sh.columns.forEach((name, j) => {
          const at = `${letters(FIRST_COL + j)}${r}`;
          const style = TEXT_COLUMNS.has(name) ? 0 : name === "Paid" ? S_MONEY : S_CENTRE;
          if (name === "ABV") cells += formulaCell(at, abv(col("Proof"), r), computed(name, s), style);
          else if (formulaFor[name]) cells += formulaCell(at, formulaFor[name], computed(name, s), style);
          else if (s) cells += cell(at, name === "Bottle Code" ? codeOf(s) : valueOf(name, s), style);
        });
        body.push(`<row r="${r}" ht="18" customHeight="1">${cells}</row>`);
      }

      const merges = [`B2:${end}7`].concat(sh.stats.flatMap((st) =>
        [`${st.from}2:${st.to}4`, `${st.from}5:${st.to}7`]));
      const validations = sh.columns.filter((name) => lists[name]).map((name) =>
        '<dataValidation type="list" allowBlank="1" showInputMessage="1" showErrorMessage="1"'
        + (name === "Barrel Proof" ? ' errorTitle="Barrel Proof" error="Please select Yes or No."' : "")
        + ` sqref="${col(name)}${HEADER_ROW + 1}:${col(name)}${last}">`
        + `<formula1>${text(lists[name])}</formula1></dataValidation>`);
      const widest = sh.stats.length ? sh.stats[sh.stats.length - 1].to : end;

      const sheet = `${XML}<worksheet xmlns="${MAIN}" xmlns:r="${REL}">`
        + `<dimension ref="A2:${widest}${last}"/>`
        + `<sheetViews><sheetView${n === 0 ? ' tabSelected="1"' : ""} workbookViewId="0">`
        + `<pane ySplit="${HEADER_ROW}" topLeftCell="A${HEADER_ROW + 1}" activePane="bottomLeft" state="frozen"/>`
        + `<selection pane="bottomLeft" activeCell="B${HEADER_ROW + 1}" sqref="B${HEADER_ROW + 1}"/>`
        + "</sheetView></sheetViews>"
        + '<sheetFormatPr defaultRowHeight="15"/>'
        + '<cols><col min="1" max="1" width="4.4" customWidth="1"/>'
        + sh.widths.map((w, j) =>
          `<col min="${FIRST_COL + j}" max="${FIRST_COL + j}" width="${w}" customWidth="1"/>`).join("")
        + `</cols><sheetData>${body.join("")}</sheetData>`
        + `<mergeCells count="${merges.length}">${merges.map((m) => `<mergeCell ref="${m}"/>`).join("")}</mergeCells>`
        + (validations.length
          ? `<dataValidations count="${validations.length}">${validations.join("")}</dataValidations>` : "")
        + '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        + '<tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>';

      const table = `${XML}<table xmlns="${MAIN}" id="${n + 1}" name="${sh.table}" displayName="${sh.table}"`
        + ` ref="${ref}" totalsRowShown="0"><autoFilter ref="${ref}"/>`
        + `<tableColumns count="${sh.columns.length}">`
        + sh.columns.map((name, j) => {
          const f = !sh.calc.includes(name) ? null
            : name === "ABV" ? abv(col("Proof"), HEADER_ROW + 1) : formulaFor[name];
          return `<tableColumn id="${j + 1}" name="${esc(name)}"`
            + (f ? `><calculatedColumnFormula>${text(f)}</calculatedColumnFormula></tableColumn>` : "/>");
        }).join("")
        + `</tableColumns><tableStyleInfo name="${sh.style}" showFirstColumn="0" showLastColumn="0"`
        + ' showRowStripes="1" showColumnStripes="0"/></table>';
      return { sheet, table };
    });

    const count = SHEETS.length;
    const each = (fn) => SHEETS.map((sh, n) => fn(sh, n + 1)).join("");
    const parts = [
      { name: "[Content_Types].xml", text: `${XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">`
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + `<Override PartName="/xl/workbook.xml" ContentType="${TYPE}.sheet.main+xml"/>`
        + each((sh, i) => `<Override PartName="/xl/worksheets/sheet${i}.xml" ContentType="${TYPE}.worksheet+xml"/>`
          + `<Override PartName="/xl/tables/table${i}.xml" ContentType="${TYPE}.table+xml"/>`)
        + `<Override PartName="/xl/styles.xml" ContentType="${TYPE}.styles+xml"/>`
        + `<Override PartName="/xl/sharedStrings.xml" ContentType="${TYPE}.sharedStrings+xml"/></Types>` },
      { name: "_rels/.rels", text: `${XML}<Relationships xmlns="${PKG_REL}">`
        + `<Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>` },
      { name: "xl/workbook.xml", text: `${XML}<workbook xmlns="${MAIN}" xmlns:r="${REL}">`
        + '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="28800" windowHeight="15000"/></bookViews>'
        + `<sheets>${each((sh, i) => `<sheet name="${sh.name}" sheetId="${i}" r:id="rId${i}"/>`)}</sheets>`
        // Recalculate on opening: the values stored beside the formulas are this phone's sums.
        + '<calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>' },
      { name: "xl/_rels/workbook.xml.rels", text: `${XML}<Relationships xmlns="${PKG_REL}">`
        + each((sh, i) => `<Relationship Id="rId${i}" Type="${REL}/worksheet" Target="worksheets/sheet${i}.xml"/>`)
        + `<Relationship Id="rId${count + 1}" Type="${REL}/styles" Target="styles.xml"/>`
        + `<Relationship Id="rId${count + 2}" Type="${REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>` },
    ];
    sheets.forEach((s, n) => {
      parts.push({ name: `xl/worksheets/sheet${n + 1}.xml`, text: s.sheet });
      parts.push({ name: `xl/worksheets/_rels/sheet${n + 1}.xml.rels`, text: `${XML}<Relationships xmlns="${PKG_REL}">`
        + `<Relationship Id="rId1" Type="${REL}/table" Target="../tables/table${n + 1}.xml"/></Relationships>` });
      parts.push({ name: `xl/tables/table${n + 1}.xml`, text: s.table });
    });
    parts.push({ name: "xl/styles.xml", text: STYLES });
    // Last, because the sheets above are what filled it.
    parts.push({ name: "xl/sharedStrings.xml", text: `${XML}<sst xmlns="${MAIN}" count="${strings.length}"`
      + ` uniqueCount="${strings.length}">`
      + strings.map((t) => `<si><t xml:space="preserve">${text(t)}</t></si>`).join("") + "</sst>" });
    return parts;
  }

  /** The finished .xlsx, as bytes. */
  function collectionBook(spirits, opts = {}) {
    return zip(collectionParts(spirits, opts).map((p) => ({ name: p.name, data: utf8(p.text) })), opts.when);
  }

  const MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  return { utf8, crc32, deflate, zip, collectionParts, collectionBook, listFormula, SHEETS, MIME };
})();

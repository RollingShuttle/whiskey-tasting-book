# Whiskey Tasting Scorecard — Build Spec

A desktop app that replaces the user's paper tasting score book. It reads the bottle list
from the existing collection workbook and records scored tastings in a small companion
workbook that syncs through OneDrive to the phone.

Written 2026-09-07. Verified against the live files — column names, ranges and row
counts below were read from the actual workbook, not assumed.

---

## 0. Project rules learned the hard way

**Every generated filename must be unique at sub-second resolution.** Second-resolution timestamps
are not unique enough, and the failure is always silent: two files get the same name and one
destroys the other. This bug appeared three separate times during the build, caught each time by a
test rather than in use:

| Where | What collided | Fix |
|---|---|---|
| `store.write_tasting` | two cards for one spirit in the same second shared a `tasting_id`, so the second overwrote the first | random suffix in the id, plus a hard refusal to overwrite an existing revision |
| `store.assign_encounter_codes` | two encounters created in the same second tied on `first_tasted`, so ordering fell back to a random filename and `X-1` went to the wrong one | microsecond timestamps, deterministic tiebreak |
| `rollup._backup` | three regenerations in one second produced one backup filename | microsecond timestamps |

So: timestamps in identifiers and filenames use microseconds, uniqueness gets a random suffix, and
anything claiming to be immutable refuses to overwrite rather than trusting itself.

---

## 1. Data sources

### 1.1 Master collection — READ ONLY, NEVER SAVE

`C:\Users\<you>\OneDrive\文档\Whiskey File\Whiskey Collection.xlsx`

**Critical constraint.** This file is 147 MB. 198 bottle photos are embedded as Excel
*rich values* (`xl/richData` — the "Place in Cell" picture feature), plus 3 Excel Tables,
3 drawings, printer settings and web extensions. **openpyxl cannot round-trip richData.**
Any `wb.save()` on this file silently destroys all 198 photos and mangles the tables.

Rules, non-negotiable:
- Always open with `load_workbook(path, read_only=True, data_only=True)`.
- **Never call `.save()` on this path.** Add an assertion/guard so it cannot happen by accident.
- Adding new bottles to this file is still possible and is a required feature — but only via the
  surgical zip-level append in §8, never through openpyxl.

Structure (header row is **row 8**, data starts row 9):

| Sheet | Table name | Range | Real rows | First empty row |
|---|---|---|---|---|
| `Bottle` | `Collection` | `B8:R308` | 144 (rows 9–152) | 153 |
| `Miniature` | `Collection4` | `B8:K308` | 22 (rows 9–30) | 31 |
| `Sample` | `SampleList` | `B8:T599` | 209 (rows 9–217) | 218 |

Verified 7 Sep 2026, after the `Bottle Code` column was added.

Columns, left to right:

- **Bottle** — **Bottle Code** · Distillery · Name · Type · Region · Notes · Release Year · Age ·
  Rarity · Proof · ABV · Entry Proof · Proof Diff · Conc. Ratio · Size (ml) · Status · Paid
- **Miniature** — **Bottle Code** · Distillery · Name · Notes · Type · Region · Age · Proof · ABV ·
  Size (ml)
- **Sample** — **Bottle Code** · Distillery · Name · Special note · Comment · Type · Region ·
  Release Year · Age · Rarity · Proof · ABV · Entry Proof · Proof Diff · Conc. Ratio · Size(oz) ·
  Bottle Type · Quantity · Picture

`Bottle Code` is column **B** on all three sheets — the leftmost column of each table, not the last.

Parsing notes:
- Read by **header name**, not column letter. The user may reorder columns.
- On the `Sample` sheet the ABV formula is filled down to row 599; only ~209 rows are real.
  Filter to rows where **both** Distillery and Name are non-empty.
- The `Picture` column reads as `#VALUE!` (it is an in-cell image, not a value). Ignore it.
- `Age` is sometimes the string `NAS`. `Entry Proof` / `Proof Diff` / `Conc. Ratio` are
  sometimes `N/A`. Parse defensively — never coerce these to 0.
- **Formula columns — CORRECTED 8 Sep 2026, verified in code.** There are two different kinds and
  the distinction matters for §8.3:

  | Sheet | Declared calculated columns (`<calculatedColumnFormula>`) | Filled-down plain formulas |
  |---|---|---|
  | `Bottle` | Proof Diff, Conc. Ratio | **ABV** (`=IF(K9="","",K9/2)`) |
  | `Miniature` | ABV (`=IF(I9="","",I9/2)`) | none |
  | `Sample` | ABV (`=L9/2`), Proof Diff, Conc. Ratio | none |

  An earlier draft listed Bottle's ABV as a declared calculated column. It is not — it is an
  ordinary formula copied down the column, with **no** table-level declaration. Excel auto-applies a
  declared calculated column to a new row; it does **not** auto-apply a filled-down one. So a row
  written by an outside tool would land with a blank ABV on the Bottle sheet.
- Every ABV formula uses **plain A1 references** (`K9`, `I9`, `L9`), so the writer must shift the row
  number. Proof Diff and Conc. Ratio use structured references
  (`Collection[[#This Row],[Proof]]`) and are already row-relative.
- `collection.py.formula_columns(sheet)` returns all of this at runtime — declared vs filled-down,
  the formula text, and whether row refs need shifting — so the writer never hard-codes it.

Controlled vocabularies currently in use (drive the filters and category colors from these,
but read them from the file at load so new values appear automatically):
- **Type**: Armagnac, Baijiu, Bitter, Blended, BourRye, Bourbon, Cognac, Gin, Liqueur, Rum,
  Rye, Single Malt, Tequila
- **Region**: America, Caribbean, China, France, Japan, Mexico, Scotland
- **Rarity**: Common, Uncommon, Rare, Dusty, Unicorn, Impossible
- **Status**: Opened, Unopened

### 1.2 Tasting store — an append-only journal, not a spreadsheet

**Source of truth:** a folder of small immutable JSON files in the OneDrive **app folder** (§9.1).
**`Whiskey Tastings.xlsx` is a generated rollup**, rewritten by the PC app from the journal so the
data is readable in Excel. Nothing ever edits the rollup by hand; if it is deleted it regenerates.

The reason is conflicts. Two devices writing one .xlsx over OneDrive produces conflict copies and
silent losses, and generating .xlsx bytes in a phone browser is heavy and clobbers the whole file on
every save. One immutable file per scorecard has no write conflicts at all: two devices can add files
at the same moment and OneDrive merges the folder without thinking about it.

The rollup workbook keeps the sheet shapes below, so the columns remain the contract.

Three sheets, header in row 1:

**`Tastings`** — one row per pour. The permanent log.

| Column | Notes |
|---|---|
| `tasting_id` | `T-0001`, assigned by the app, never reused |
| `session_id` | FK to Sessions, blank for a standalone pour |
| `date` | ISO `YYYY-MM-DD` |
| `spirit_id` | `B-` / `M-` / `S-` code from the master workbook, or an `X-` encounter code (§2.1) |
| `source` | `Bottle` / `Miniature` / `Sample` / `Encounter` |
| `venue` | where it was drunk — blank means home |
| `pour_price` `pour_size_oz` | what this pour cost and how big it was; drives Value for bar pours |
| `include_in_average` | TRUE/FALSE — see §3.6 |
| `flight_pos` | 1, 2, 3… position within the session |
| `display_name` | denormalized for readability in Excel, e.g. `Reserve Cask 15 2000` |
| `barrel_id` | free text — not present in the master workbook |
| `aroma` `flavor` `body` `complexity` `balance` `finish` `uniqueness` `drinkability` `aesthetics` `value` | integer scores, see §3 |
| `aroma_notes` … `value_notes` | one free-text column per category, unbounded length |
| `total` | computed by the app, written as a literal value not a formula |
| `medal` | Diamond / Gold / Silver / Bronze / No Medal, derived from `total` |
| `rubric_version` | which rubric produced these numbers |
| `notes` | overall free text, separate from the per-category notes |
| `tags` | comma-separated flavor tags |
| `blind` | TRUE/FALSE |
| `pour_size_oz` `rest_min` `water` | optional context |
| `status` | `Draft` / `Submitted` |
| `entered_from` | `desktop` / `phone` / `quick-entry` |
| `created_at` `updated_at` | ISO timestamps |

**`Quick Entry`** — the phone lane. Same first columns only, so a row is typeable on a
phone keyboard: `date · display_name · barrel_id · nose · palate · finish · notes`.
On every app start, any non-empty row here is validated, matched to a bottle, appended to
`Tastings` with a real `tasting_id`, and cleared. Rows that fail to match a bottle are left
in place and flagged in the UI rather than dropped.

**`Sessions`** — `session_id · date · title · location · company · blind · notes`.

**`Encounters`** — spirits scored but never owned (§2.1):
`spirit_id (X-n) · name · distillery · type · region · release_year · age · proof · abv ·
venue · first_tasted · linked_bottle_code · notes`.

### 1.3 Journal rules

1. **Every file is written once and never modified.** A correction is a new file with a higher
   revision number; the reader takes the highest revision per `tasting_id`. A deletion is a tombstone
   file. This is what makes concurrent writes safe.
2. Filenames are globally unique and self-describing:
   `tastings/T-20260908-183012-B-18-9f3a-r1.json`.
   **The random suffix is load-bearing.** Timestamps are second-resolution, so without it two
   cards for the same spirit in the same second share an id, share a filename, and the second
   write silently destroys the first. Found by `test_store.py` on its first run. `write_tasting`
   additionally refuses to overwrite any existing revision file.
3. Writes are idempotent — a retry after a dropped connection overwrites the same bytes at the same
   path, so a failed upload can always simply be repeated.
4. The rollup `Whiskey Tastings.xlsx` is regenerated whole by the PC app. Refuse to write it if
   `~$Whiskey Tastings.xlsx` exists (Excel has it open), and keep the last 30 in
   `data/backups/`.

---

## 2. Bottle identity — solved

`Distillery` + `Name` is **not** unique — there are collisions on both the `Bottle` and `Sample`
sheets, where several different bottles share the same Distillery + Name. Never match on name.

The user added a **`Bottle Code`** column in September 2026, and it is clean — verified 7 Sep 2026:

| Sheet | Format | Range in use | Blanks | Duplicates |
|---|---|---|---|---|
| `Bottle` | `B-1` … `B-144` | 144 | 0 | 0 |
| `Miniature` | `M-1` … `M-22` | 22 | 0 | 0 |
| `Sample` | `S-1` … `S-209` | 209 | 0 | 0 |

This is the join key for everything: tasting rows, the phone client, the sync queue.

Rules:
- **The suffix is not zero-padded.** Sort and compare on the parsed integer, never on the string —
  lexically `B-10` sorts before `B-2`.
- Next code for a new row = highest integer suffix on that tab + 1 (`B-145`, `M-23`, `S-210`).
  Read it from the file at write time; never cache it across runs.
- Codes are permanent. Never renumber, never reuse a code from a deleted row.
- On load, assert uniqueness and no blanks on each tab. If either fails, refuse to write anything
  to the master and tell the user which rows are affected.

### 2.1 Spirits that are not in the collection

A large share of scoring happens at bars, on splits, and at tastings — spirits the user drinks once and
does not own. These must be first-class, not shoehorned into the workbook.

- They get their own code series **`X-1`, `X-2`, …**, assigned by the app, in the `Encounters` sheet
  of `Whiskey Tastings.xlsx`.
- **They are never written to `Whiskey Collection.xlsx`.** That workbook is an inventory of what he
  owns; a bar pour is not inventory. The write path in §8 must refuse any `X-` record.
- Capture is deliberately light — name, distillery, type, region, age, proof are all optional except
  the name — because it is being typed one-handed in a bar. Everything else can be filled in later.
- `venue`, `pour_price` and `pour_size_oz` are captured on the tasting. **Value is scored against
  `pour_price / pour_size_oz`** for an encounter, and against the bottle's `Paid / Size` for anything
  owned. Show whichever applies as a hint next to the Value row: "$30.00/oz".
- **Linking.** If he later buys the bottle, `linked_bottle_code` on the encounter points at the new
  `B-` code. From then on the two histories are one: every past `X-` tasting counts toward that
  bottle's career score, and the encounter stops appearing as a separate row. Never rewrite the
  historical tastings' `spirit_id` — resolve through the link, so the provenance of each sitting
  stays visible.
- Filters: `Owned only` / `Tasted only` / both. Default is both, because "have I had this?" matters
  as much as "do I own this?"

---

## 3. The scorecard — Curiosity Public Ultimate Spirits, 100 points

This is the user's actual scorecard, transcribed from the physical book 8 Sep 2026. Build exactly this.

| # | Category | Max | The question on the card |
|---|---|---|---|
| 1 | Aroma | 10 | The experience starts with your nose. What scents do you recognize? Fruits? Spices? Sugars? 5 points is one, maybe two weak flavors; 10 is a rich mélange. |
| 2 | Flavor | **20** | Taste the spirit. What type of flavors do you experience? How deep are the flavors you notice? 10 points means a couple of light notes, 20 is a rich cornucopia. |
| 3 | Body | 10 | Mouthfeel. How does it coat your tongue and mouth? Is it oily? Thick or thin? 5 points means it is on the thin side, 10 is a nice perfectly even coating. |
| 4 | Complexity | 10 | How many different flavor notes do you experience? Do they blend well or stay separated? A 5 means simple and easy; 10 is a beautiful mind-dram. |
| 5 | Balance | 10 | Are the flavors in equilibrium? Is one too loud or too weak? 5 points means something is out of whack; 10 is perfect in every way. |
| 6 | Finish | 10 | What's the ending like? How long does it linger in your mouth and throat? Short, medium, long? 5 is short and poor; 10 is lovely, no burns, and great taste. |
| 7 | Uniqueness | 10 | How special or unusual was the drinking experience? Can include a point or two for presentation. 10 is a unicorn, so don't waste it. |
| 8 | Drinkability | 10 | How much does it make you want to drink more? 5 means you are not excited for your next sip; 10 is a dram you want to jump back into. |
| 9 | Aesthetics | **5** | Don't forget the vessel that contained the spirit. Iconic shape? Unique labeling? Packaging? What's in the boat? |
| 10 | Value | **5** | Price and availability. How much did this cost? Is it worth it? How hard is it to find? 5 means the price and availability are right. |
| | **Total** | **100** | |

Store the question text — it is the tooltip behind the `?` on each row, and it is what keeps scoring
consistent across months.

### Medals

Derived from the total, never entered by hand:

| Medal | Range |
|---|---|
| Diamond | 90–100 |
| Gold | 80–89 |
| Silver | 70–79 |
| Bronze | 60–69 |
| No Medal | < 60 |

### Rules

- **Total is a plain sum**, not an average and not re-weighted. The weighting is already expressed in
  the per-category maximums (Flavor is worth four Aesthetics).
- Integers only. Each score is bounded 0..max for its own category; validate on entry and on import.
- **Every category gets its own notes field, with no length limit.** On paper the user is writing three or
  four words per row; typing, he expects full sentences. Notes boxes must grow with content, never
  truncate on display, and never be capped in the store.
- The overall notes field is separate from and additional to the ten category notes.
- Total and medal update live as scores change. The paper card needed crossing-out and re-tallying;
  the app removes that arithmetic entirely.
- Show distance to the next band ("4 points to Silver") — it is the single most useful derived number
  on the sheet.

### The score control

One control type across all three scales, with segment count equal to the category maximum:
5 segments for Aesthetics and Value, 10 for most, 20 for Flavor. Click, drag along the strip, arrow
keys, or type the number. On the 20-scale, print the numeral only on multiples of 5 and on the
selected segment — otherwise the strip turns into noise.

### Config

```yaml
rubric:
  name: Curiosity Public Ultimate Spirits
  version: 1
  total: sum
  categories:
    - {key: aroma,        label: Aroma,        max: 10}
    - {key: flavor,       label: Flavor,       max: 20}
    - {key: body,         label: Body,         max: 10}
    - {key: complexity,   label: Complexity,   max: 10}
    - {key: balance,      label: Balance,      max: 10}
    - {key: finish,       label: Finish,       max: 10}
    - {key: uniqueness,   label: Uniqueness,   max: 10}
    - {key: drinkability, label: Drinkability, max: 10}
    - {key: aesthetics,   label: Aesthetics,   max: 5}
    - {key: value,        label: Value,        max: 5}
  medals:
    - {name: Diamond,  min: 90}
    - {name: Gold,     min: 80}
    - {name: Silver,   min: 70}
    - {name: Bronze,   min: 60}
    - {name: No Medal, min: 0}
```

Everything above is config-driven so a category, a maximum or a band can change without touching the
UI. Stamp `rubric_version` on every tasting row so old scores stay interpretable if it does.

### 3.6 Repeat sittings and the career score

A spirit is scored many times, on different days. **Every sitting is its own immutable row** — never
overwrite a previous score, never edit a submitted card into a new one. The number shown everywhere
else is the average.

- **Career score = mean of the totals of all counted sittings**, to one decimal (89.0, not 89).
- Per-category display is the **mean of that category across counted sittings**, also to one decimal,
  shown with its **range** (Aroma 8.7, range 8–9). A category that never moves and one that swings by
  two points are different facts, and the range is what tells them apart.
- **CORRECTED 8 Sep 2026 (found by `test_rubric.py`).** An earlier draft said the per-category means
  always add up to the headline. That holds for the *unrounded* means only. Rounding ten categories
  to one decimal each can drift by up to 0.5, and it really does: counting all four sittings of the
  example set, the displayed category figures sum to **86.0** while the true mean of totals is
  **85.8**.
  - The headline is **always the mean of the totals**. It is the authoritative number.
  - Category figures are display values. `rubric.career()` also returns `mean_exact` per category
    for any arithmetic, and asserts the exact invariant on every call.
  - **The UI must never render a total under the category column.** Inviting the reader to add ten
    rounded numbers and compare them to the headline manufactures a contradiction that is not real.
    `career()` returns `display_sum_matches` so a view can tell when the two would look inconsistent.
- **Medal comes from the career score rounded half-up to the nearest integer.** 89.4 is Gold, 89.5 is
  Diamond. Write the rule down in the UI so a borderline case is never a surprise.
- **`include_in_average`** lets a sitting be excluded without being deleted — a bottle scored on the
  day it was opened, a pour at the end of a long night, a sample that had turned. Excluded sittings
  stay visible in the history, greyed, with a reason field. Show what excluding costs: "counting all
  four would drop the mean to 85.8".
- `n` (the count of counted sittings) travels with the score everywhere it is displayed. A 94 from one
  pour and a 94 from six are not the same claim.
- Sort in the collection table is on the career score; ties break on `n`, descending.
- Compare defaults to career scores. Pinning a single session instead is an explicit toggle, so a
  head-to-head barrel comparison from one night is still possible.
- Analysis charts use career scores, and can plot a spirit's sittings over time to expose drift.

### Worked example — the filled paper card

An example card: Aroma 8, Flavor 12, Body 6, Complexity 5, Balance 8,
Finish 5, Uniqueness 6, Drinkability 9, Aesthetics 3, Value 4 → **66, Bronze**. Use this as the
fixture for the scoring and medal unit tests.

## 4. Views

1. **Flight / session** — numbered cards in a column, add-pour button, optional blind mode
   that hides identity until the score is submitted. This is the entry screen.
2. **Compare** — 2 to 4 cards side by side, aligned score rows, per-axis deltas, shared notes
   pane. Reachable directly from a session.
3. **Table** — every tasting, sortable and filterable on every field, including the master's
   Type / Region / Rarity / Status and derived `$ per oz` and `score per $`. This is the
   "easy sorting" the whole project is for. Column chooser, CSV export.
4. **Bottle detail** — one bottle, every tasting of it over time, score trend, notes history.
5. **Analysis** — score vs. Conc. Ratio, score vs. Age, score vs. Paid, score by Type and by
   Region, and a calibration chart of the user's own monthly mean score to catch grade drift.
   These are the questions the spreadsheet cannot answer today.

---

## 5. Visual style

A refined version of the reference cards: same family, tighter execution. Warm near-black
rather than blue-grey slate, brass rather than flat amber.

```css
--bg:            #12100E;   /* page */
--surface:       #1B1815;   /* card */
--surface-2:     #241F1A;   /* inset rows, pill track */
--line:          #332C22;   /* hairlines */
--text:          #F2EDE4;
--muted:         #9A9086;   /* field labels */
--brass:         #C8952F;   /* active pill, accent */
--brass-bright:  #E8B44A;   /* overall score, focus ring */
--ok:            #6FBF73;   /* Submitted pill */
--draft:         #9A9086;
```

Category accent by `Type` — used as a 3 px left edge on cards and as the dot in table rows:
Bourbon `#C8952F` · Rye `#B4703A` · Single Malt `#D9B26A` · Blended `#8E7A52` ·
BourRye `#BE8434` · Rum `#8A5A34` · Tequila `#7E8A5A` · Gin `#6E8A86` ·
Cognac/Armagnac `#A06A3C` · everything else `--muted`.

Type:
- Bottle names — display serif, ~22 px, normal weight. `"Playfair Display", Georgia, serif`.
- Everything else — `"Inter", "Segoe UI", system-ui, sans-serif`.
- All numbers `font-variant-numeric: tabular-nums` so columns align.
- Include a CJK fallback (`"Microsoft YaHei", "Deng"`) — the user produces bilingual tasting
  documents and notes may contain Chinese.

Layout: 12 px card radius, 1 px `--line` borders, no drop shadows, generous row spacing,
labels in `--muted` at 12–13 px against values in `--text`. Cards max ~520 px wide so three
fit side by side in compare mode on a 1440 px screen.

Also ship a **light print theme** — cream ground, ink text, hairline rules — for exporting a
flight sheet to PDF.

---

## 6. Stack and layout

Python, matching the existing `whiskey-monitor` and `discord-message` projects in this
folder (Python + requirements.txt + PyInstaller `.exe` + a `.bat` launcher).

Backend: FastAPI (or Flask) + openpyxl + pandas, serving a local HTML/JS front end at
`http://127.0.0.1:8765`. Not Tkinter — the card design above needs real CSS.

Bind the server to `0.0.0.0` behind a config flag. On the home network that turns the user's
phone into a live entry device at `http://<pc-ip>:8765` during a tasting, which is a far
better phone experience than typing into Excel. The `Quick Entry` tab stays as the offline
fallback for tasting away from home.

```
E:\Claude Code\whiskey-tasting\
  SPEC.md                 this file
  app.py                  server, routes
  collection.py           read-only loader for Whiskey Collection.xlsx
  store.py                read/write Whiskey Tastings.xlsx + tastings.json mirror
  rubric.py               scoring, driven by config.yaml
  config.yaml             paths, rubric, LAN flag, port
  requirements.txt
  static/
    index.html  app.js  style.css
  data/
    tastings.json         local mirror
    backups/              timestamped copies of the tastings workbook
  run.bat
  build_exe.bat
  SETUP.md
```

Config carries both file paths so nothing is hard-coded, and a `read_only_master: true`
flag that the loader asserts on.

---

## 7. Build order

1. `collection.py` — load and normalize the three tables. Print a summary: row counts,
   duplicate-key report, unparseable Age / Entry Proof values. Verify against
   144 / 22 / 209.
2. `store.py` — create the tastings workbook, round-trip a fake tasting, confirm backups,
   lock detection and atomic replace all work.
3. Server + scorecard entry, one bottle at a time.
4. Flight/session view.
5. Table view with sort and filter.
6. Compare view.
7. Quick Entry drain.
8. Analysis charts.

**Verification gate before shipping:** after a full round trip — import bottles, enter a
session of three pours, submit, reopen — confirm `Whiskey Collection.xlsx` is byte-identical
to its starting state (compare a SHA-256 hash taken before and after). If it changed at all,
something wrote to the master and all 198 photos are at risk.


---

## 8. Adding bottles to the master workbook

Required feature: enter a bottle, sample or miniature away from the computer and have it land on
the right tab of `Whiskey Collection.xlsx`, without ever opening Excel by hand.

### 8.1 Why the obvious approach is off the table

openpyxl cannot round-trip `xl/richData` (the 198 in-cell photos), the web extensions, or the
printer settings. Load-and-save on this workbook is data loss, not a formatting nuisance. Nothing
in the app may do it.

### 8.2 What makes this easy: the tables already have empty rows

The three Excel Tables are defined over ranges much larger than their filled data:

| Sheet | Table ref | Header | Filled | First empty row | Spare rows inside the table |
|---|---|---|---|---|---|
| `Bottle` | `B8:R308` | row 8 | 144 | 153 | 156 |
| `Miniature` | `B8:K308` | row 8 | 22 | 31 | 278 |
| `Sample` | `B8:T599` | row 8 | 209 | 218 | 382 |

So a new bottle is not an append at all — it is **filling the first empty row already inside the
table**. The table `ref`, the sheet `<dimension>`, and every relationship stay exactly as they are.
Only cell values inside one worksheet XML part change. This is the single most important fact in
this document: it turns a dangerous rewrite into a small, contained edit.

Guard: if the first empty row would fall outside the table range, **stop** and tell the user to
extend the table in Excel once. Do not attempt to grow a table's `ref` programmatically.

Second guard: **an `X-` encounter is never written here.** Only records the user has said he owns
reach this code path. Assert on the code prefix before doing anything else.

### 8.3 The write

Treat the .xlsx as what it is — a zip — and rebuild it copying every entry byte-for-byte except
the one worksheet part being edited:

1. Locate the target row: first row in the table range where `Bottle Code`, `Distillery` and
   `Name` are all empty (153 / 31 / 218 as of 7 Sep 2026).
2. Parse only `xl/worksheets/sheet{1,2,3}.xml`. Write the new cells into that existing `<row>`
   (creating `<c>` elements in column order — XML requires cells in ascending column order).
3. Strings go in as **inline strings** — `<c r="B153" t="inlineStr"><is><t>B-145</t></is></c>` for
   the code, `<c r="C153" …>Example Co</c>` for the distillery — so `sharedStrings.xml` is never
   touched. Numbers as plain `<v>`.
4. Formula columns come from `collection.py.formula_columns(sheet)`, which reads the **last
   populated row**, not just the table's declared calculated columns — Bottle's ABV is a filled-down
   formula with no declaration and would otherwise be missed, leaving new bottles with a blank ABV.
   Write the `<f>` with A1 row references shifted to the target row and **no cached `<v>`**.
   `test_collection.py::test_abv_is_a_formula_on_every_sheet` guards this.
5. Delete `xl/calcChain.xml` plus its `[Content_Types].xml` override and its workbook relationship.
   Excel rebuilds it silently.
6. Set `fullCalcOnLoad="1"` on `<calcPr>` in `xl/workbook.xml` so the formula columns evaluate on
   next open.
7. Repackage: copy all remaining zip entries verbatim, including all 198 `xl/media/*` and every
   `xl/richData/*` part. Write to a temp file in the same folder, then atomic-replace.

`Bottle Code` is assigned by the app as the next integer in that tab's sequence — `B-145`, `M-23`,
`S-210` — and written into column B.

### 8.4 Preconditions and verification — all mandatory

Before: refuse if `~$Whiskey Collection.xlsx` exists (Excel has it open). Copy the whole workbook to
`data/backups/` first, keeping the last 10.

After every write, reopen the result and assert:
- zip entry count is unchanged apart from `calcChain.xml`;
- `xl/media/` still contains exactly the same file names and byte sizes;
- every `xl/richData/*` part is present and byte-identical;
- the target row now reads back with the expected values;
- filled row counts went up by exactly the number of rows written.

Any assertion fails → restore the backup automatically and report the failure. Never leave a
half-written master workbook in place.

### 8.5 Where the write happens — and the approval gate

**DECIDED 8 Sep 2026.** Adding a bottle from the phone stays, with an approval step.

- The phone writes a request into `pending/` in the app folder. That is the scoring side of the
  world; the phone still has no access whatsoever to `Whiskey Collection.xlsx` (§9.1).
- On its next run the PC app shows the queue and **the user approves each row before it is written**.
  Approval is per row, with the parsed values editable in the review panel. Nothing reaches the
  master workbook unreviewed — a typo made one-handed in a liquor store should not land in a
  144-row inventory silently.
- Approved → the surgical append in §8.3 runs and the pending file is deleted. Rejected → the
  pending file moves to `pending/rejected/` rather than vanishing.
- The `Bottle Code` is assigned at approval time on the PC, not on the phone, so two phones or a
  long-queued request can never claim the same code.

The merge runs **on the PC**, not the phone. A 147 MB file should be rewritten once, from one
place, with a backup beside it — not over a phone connection mid-OneDrive-sync. Entries made away
from the computer queue as pending records and merge on the next PC run. What the phone *shows* is
always the collection snapshot plus the pending queue, so nothing looks missing in the meantime.

**Fallback if 8.3 proves fragile in practice:** write pending bottles to a `New Bottles` sheet in
`Whiskey Tastings.xlsx` and have the PC app show a one-click "copy these rows into Excel" panel.
Slower, zero risk. Build 8.3, but keep this path in the code.

---

## 9. The phone client

Requirements the user stated: browse the collection cleanly with sorting and filtering, add bottles,
score tastings, none of it disturbing the spreadsheet, and all of it working away from the computer.

Read-only collection browsing plus queued writes — the phone never edits either workbook directly.

**DECIDED 8 Sep 2026 — supersedes the 7 Sep decision.** The phone app is a **static web app on a
free static host, storing data in OneDrive through the Microsoft Graph API**. The 7 Sep choice
(a published Claude artifact with its own database) is withdrawn: an artifact page cannot make
outbound network calls, so it can never reach OneDrive, and the user wants their data in their own OneDrive.
See §9.1. Neither option requires a server that runs all the time.

### 9.1 Sync — OneDrive as the transport, no server

**The hard rule: the phone can never touch `Whiskey Collection.xlsx`.** This is enforced by the
OAuth scope, not by app discipline. The phone requests only **`Files.ReadWrite.AppFolder`**, which
grants access to a single folder — `OneDrive/Apps/<app name>/` — and nothing else in the drive. The
collection workbook is not merely off-limits to the phone; it is invisible to it. (`approot` is
supported on both personal and work OneDrive. The folder is named after the Entra app registration.)

**Layout**

```
OneDrive/文档/Whiskey File/            ← PC ONLY. Phone has no access.
  Whiskey Collection.xlsx              master inventory, read-only except §8
  Whiskey Tastings.xlsx                generated rollup, for reading in Excel

OneDrive/Apps/Whiskey Tasting Book/    ← the app folder. Phone + PC.
  snapshot/collection.json             PC writes, phone reads (bottle list for scoring)
  tastings/T-<ts>-<spirit>-<rand>-r<n>.json  immutable scorecards, either device writes
  encounters/X-<n>.json                bar pours, either device writes
  pending/bottle-<ts>.json             new-bottle requests from the phone, awaiting approval
  meta/rollup-state.json               what the PC last folded into the rollup
```

**Who does what**

| | Phone | PC app |
|---|---|---|
| `Whiskey Collection.xlsx` | no access at all | reads always; writes only per §8 |
| `snapshot/collection.json` | reads | writes on every run |
| `tastings/`, `encounters/` | reads and writes | reads and writes |
| `pending/` | writes requests | reads, and clears each on approval |
| `Whiskey Tastings.xlsx` | no access | regenerates from the journal |

**Transport**

- **Phone → OneDrive: Microsoft Graph.** MSAL.js in the browser, authorization-code flow with PKCE,
  scope `Files.ReadWrite.AppFolder` only. Tokens are refreshed silently; sign-in is once.
  Files are a few KB each, so uploads succeed on bad bar wifi and resume trivially.
- **PC → OneDrive: the local synced folder.** No API, no auth, no network. The PC app reads and
  writes `C:\Users\<you>\OneDrive\Apps\Whiskey Tasting Book\` as ordinary files and lets the
  OneDrive client sync them. Note Files On-Demand may leave journal files as cloud placeholders;
  reading one downloads it, which is fine at these sizes.
- **Hosting the phone app:** static files on a free static host (Cloudflare Pages, GitHub Pages,
  Netlify). Static hosting is not a server — nothing runs between visits, nothing to keep alive,
  nothing to patch. The PC app also serves the identical bundle at `127.0.0.1:8765` for desktop use.

**Reconciliation**

On launch, and on demand, the PC app: reads the master workbook → writes a fresh
`snapshot/collection.json` → reads every file in `tastings/` and `encounters/` → resolves highest
revision per id and applies tombstones → regenerates `Whiskey Tastings.xlsx` → records what it folded
in `meta/rollup-state.json`. Idempotent, so running it twice changes nothing.

**Why there are no conflicts**

Nothing is ever edited in place. Two devices adding scorecards at the same moment write two different
filenames into one folder; OneDrive merges folders without conflict copies. The only whole-file
writers are the PC app writing `snapshot/collection.json` and the rollup workbook, and the PC is a
single writer for both.

**Setup cost, one time**

1. A free Microsoft Entra app registration to get a client ID, with the phone app's URL as a
   **Single-page application** redirect URI and `Files.ReadWrite.AppFolder` as the only delegated
   permission. Supported account types: **Personal accounts only** — the user has two Microsoft
   accounts and the whiskey data is on the personal OneDrive, so restricting the registration is
   what prevents signing in as the wrong one. MSAL authority is therefore
   `https://login.microsoftonline.com/consumers`.
2. A free static host for the phone bundle.
3. Sign in once on the phone; once on the PC only if you ever want the PC to use Graph instead of
   the local folder.

Nothing after that runs continuously.

**Fallback if the Graph route stalls:** the phone writes into the `Quick Entry` sheet of the rollup
workbook through Excel for iOS, and the PC drains it. Far worse to use, zero setup, and it still
never touches the collection workbook. Keep it documented, do not build it first.

### 9.2 iOS — the phone client is an iPhone client

The user's phone is an iPhone. Build the phone half to iOS conventions; a web app that ignores them
reads as a website someone bookmarked, and it breaks in specific ways in the bar.

**Home-screen install**
- `<meta name="apple-mobile-web-app-capable" content="yes">` plus `mobile-web-app-capable`, and a
  180×180 `apple-touch-icon`. Installed, it launches without Safari chrome.
- `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`.
- `<meta name="theme-color" content="#12100E">` and `color-scheme: dark` — the design is
  dark-committed, so declare it rather than letting iOS guess.

**Safe areas — required, not cosmetic**
- Pad with `env(safe-area-inset-top / bottom / left / right)`. The bottom inset is the home
  indicator: a tab bar without it sits under the swipe strip and mis-taps.
- Never `100vh` — it is wrong on iOS Safari with a visible URL bar. Use `100dvh`.
- The Dynamic Island eats the top-center. Keep nothing but background under it.

**Touch targets**
- 44×44 pt minimum, per Apple's HIG. The score strip on phone is therefore **44 px tall**, not the
  26 px used on desktop — corrected in the 8 Sep mockups. Ten segments across a 390 pt screen gives
  ~33 pt of width each, which is under the guideline horizontally, so **tap must be backed by
  drag**: press anywhere on the strip and slide to the value. Also accept a direct number entry.

**Type**
- UI text uses the system stack — `-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui` —
  so it renders as SF and matches the rest of the phone. Keep Playfair Display for spirit names only;
  that is the one deliberate departure.
- Respect Dynamic Type at least to the extent of using `rem` and not locking font sizes in `px`
  for body copy.

**Inputs — the ones that bite**
- Every text and number input needs `font-size: 16px` or larger, or Safari zooms the page on focus
  and never zooms back.
- `inputmode="decimal"` on proof, price, pour size, and scores; `inputmode="numeric"` for age.
  Without it iOS shows the full QWERTY keyboard for a number field.
- Notes fields: `autocapitalize="sentences"`, `autocorrect="on"`, `spellcheck="true"`, and a
  `<textarea>` that grows with content rather than scrolling internally.
- When the keyboard opens it covers the lower ~40% of the screen. Scroll the focused field into
  view, and hide the tab bar while the keyboard is up so the Submit control stays reachable.

**Navigation**
- In standalone mode there is **no browser back button and no edge-swipe back**. Every screen that
  can be navigated into needs its own back chevron top-left. The mockups have this; it is not
  optional.
- There is no pull-to-refresh in standalone either — hence the explicit Refresh control on the Sync
  screen.

**Offline — the bar case**
- Bar basements have no signal. A service worker must cache the app shell and the collection
  snapshot, and every scorecard and encounter must be written to local storage first and queued for
  upload. Scoring a pour with no connection has to work exactly as it does with one; the Sync screen
  shows what is waiting.
- Show connection state honestly on the Sync screen — "3 waiting, offline since 21:40" — rather than
  silently failing.

**Export**
- Use the Web Share API where available so a flight sheet goes out through the normal iOS share
  sheet, with a download fallback.

---

## 10. Interface

Mockups: three PC screens (judging sheet, collection table, compare) and five phone screens
(collection, bottle detail, scorecard, add bottle, sync), rebuilt 8 Sep 2026 on the 100-point rubric.
Build to those, using the tokens in §5.

Key decisions visible in them:
- **One spirit per sheet.** Ten categories with real notes will not fit two-up, so the session view is
  a pour switcher across the top and a single full sheet below.
- Every category row is: name + `?` hint · max · segmented strip · score · notes box on the right,
  with the notes box taking roughly half the row width.
- The total carries a threshold bar marked at 60 / 70 / 80 / 90 so the distance to the next medal is
  visible while scoring.
- Medal chips are colored per band (Diamond pale blue, Gold brass, Silver grey, Bronze copper) and
  appear everywhere a score does — sheet, collection table, compare, phone list.
- The career screen shows per-category means with ranges and **no column total** (§3.6).
- The collection table carries two columns the spreadsheet cannot: `$ / oz` and the score with medal.
- Compare draws each bar against its own maximum, so Flavor out of 20 reads at the same visual scale
  as Balance out of 10.
- Sync state is always visible: a pill in the PC top bar, a dedicated tab on the phone.

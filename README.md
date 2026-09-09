# Whiskey Tasting Book

A tasting scorecard app for a personal whiskey collection. Replaces a paper score book.

Scores on the **Curiosity Public Ultimate Spirits** rubric — ten weighted categories out of 100
(Flavor 20, Aesthetics and Value 5 each, the rest 10) with a notes field per category, an automatic
total, and a medal band: Diamond 90+, Gold 80+, Silver 70+, Bronze 60+.

## Shape

- **PC app** (Python) reads the collection workbook, owns every write, and does the analysis.
- **Phone app** (static web, `docs/`) scores pours anywhere — including bar pours of spirits that
  are not in the collection — and browses the collection read-only.
- **Sync** is a folder of immutable JSON files in the OneDrive app folder. No server runs.

## Ground rules

1. `Whiskey Collection.xlsx` holds 198 bottle photos as Excel rich values. openpyxl cannot
   round-trip them, so **nothing ever calls `.save()` on it.** New rows are written by a
   zip-level surgical append (SPEC.md §8), verified afterwards, with automatic rollback.
2. The phone reaches OneDrive with the `Files.ReadWrite.AppFolder` scope only. The collection
   workbook is not merely off-limits to it — it is invisible.
3. Journal files are immutable. Corrections are new revisions; deletions are tombstones.

## Modules

| File | What it does | Tests |
|---|---|---|
| `collection.py` | read-only loader for the master workbook | `test_collection.py` |
| `rubric.py` | scoring, medals, career aggregation | `test_rubric.py` |
| `store.py` | the tasting journal — tastings, flights, encounters, pending bottles | `test_store.py` |
| `rollup.py` | regenerates `Whiskey Tastings.xlsx` from the journal | `test_rollup.py` |
| `quickentry.py` | drains the Quick Entry sheet into draft tastings | `test_quickentry.py` |
| `master_write.py` | the only code that adds a row to the master workbook | `test_master_write.py` |
| `app.py` | local server + JSON API at `127.0.0.1:8765` | `test_app.py` |
| `launch.py` | starts the app, opens its window, and keeps it resident in the tray; packaged by `build_exe.bat` | `test_launch.py` |
| `static/` | the PC front end — `app.js` (sheet + flights), `table.js`, `compare.js`, `analysis.js` | — |
| `docs/` | the iPhone client — offline-first, syncs through OneDrive | `test_phone.py` |

264 tests, all passing. `python verify_gate.py` runs the SPEC §7 shipping gate.

## Views

Built:

- **Judging sheet** — one spirit, ten categories, segmented score strips, live total with a
  threshold bar at 60/70/80/90, medal, and distance to the next band.
- **Flight / session** — several pours in one sitting, a pour switcher across the top and a single
  full sheet below. Blind mode hides each spirit's identity until its own card is submitted.
- **Table** — every spirit or every tasting, sortable and filterable on every field, with a column
  chooser and CSV export. Carries `$ / oz` and `score / $`, which the spreadsheet cannot.
- **Compare** — two to four things side by side, score rows aligned, each bar drawn against its
  own maximum, per-axis deltas and a shared notes pane. Defaults to career scores; a single
  flight can be pinned for a head-to-head from one night.
- **Quick Entry** — rows typed on the phone are drained into unscored drafts against their
  bottle. A name that is not unique is refused rather than guessed at, and refused rows stay
  on the sheet with the reason.
- **Analysis** — score against Conc. Ratio, Age, Paid or Proof; mean score by Type and by
  Region; and a calibration series of your own monthly mean, which is how grade drift shows
  up. Hand-drawn inline SVG, no chart library.

In-progress scorecards survive a reload — they are mirrored to the browser and restored, with a
Discard control.

- **New bottles** — a bottle added away from the computer queues as a request and is written
  to the collection workbook only after you approve it on the PC, with the parsed values
  editable first. The row fills a slot that already exists inside the Excel table, every
  photo and rich value is copied byte-for-byte, and a backup is taken before each write.

The **iPhone client** in `docs/` is a static app for GitHub Pages: browse the collection,
score a pour, queue a new bottle for approval. It scores with no signal — every card is
written to the phone first and queued — and syncs through the OneDrive app folder, which its
OAuth scope limits it to. To deploy: add your client ID to `docs/config.js`, register the
Pages URL as an SPA redirect URI, and enable Pages on the `docs/` folder.

## Run

```
pip install -r requirements.txt
cp config.example.yaml config.yaml   # then edit the paths for your machine
python -m unittest discover -p "test_*.py"
python launch.py              # start it and open its own window
```

For the desktop version, build it once and make an icon for it:

```
build_exe.bat                 # -> "Whiskey Tasting Book.exe", no console window
python make_shortcut.py       # Desktop / Start menu icon, pointing at the exe
```

The exe is gitignored — it is 27 MB of build output. It reads `config.yaml`, `data/` and the
workbooks from the folder it sits in, which is why it builds here rather than into `dist/`.

**`RUNNING.md` is the step-by-step guide to installing and starting both halves** — start there.
`config.yaml` is gitignored (its file paths contain a local username); `config.example.yaml` is
the template. `SPEC.md` is the build spec. `SETUP.md` is the one-time Microsoft account setup.

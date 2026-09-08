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
| `app.py` | local server + JSON API at `127.0.0.1:8765` | `test_app.py` |
| `static/` | the front end — `app.js` (sheet + flights), `table.js`, `compare.js` | — |

152 tests, all passing.

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

In-progress scorecards survive a reload — they are mirrored to the browser and restored, with a
Discard control.

Not yet built: analysis charts and the phone client.

## Run

```
pip install -r requirements.txt
cp config.example.yaml config.yaml   # then edit the paths for your machine
python collection.py          # health report on the master workbook
python -m unittest discover -p "test_*.py"
python app.py                 # the local app at http://127.0.0.1:8765
```

`config.yaml` is gitignored (its file paths contain a local username); `config.example.yaml` is the
template. `SPEC.md` is the build spec. `SETUP.md` is the one-time Microsoft and iPhone setup.

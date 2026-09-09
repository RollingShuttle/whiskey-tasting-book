# Whiskey Tasting Book — project context

Read `SPEC.md` before changing anything. It is the build spec and it is current.
`SETUP.md` covers the one-time Microsoft account setup. `RUNNING.md` is the plain-language
guide to installing and starting both halves (the PC app opens via `launch.py`, not `app.py`, and
ships as a console-free exe built by `build_exe.bat`) — keep it accurate when behaviour changes.

## Non-negotiable

1. **Never call `.save()` on `Whiskey Collection.xlsx`.** It is 147 MB and holds 198 bottle photos
   as Excel rich values. openpyxl cannot round-trip them; a single save destroys all of them.
   Read it with `load_workbook(path, read_only=True, data_only=True)` and nothing else. New rows go
   in by the zip-level surgical append in SPEC.md §8, which fills a row that already exists inside
   the table and verifies every media part afterwards.
2. **Journal files are immutable.** Corrections are new revision files, deletions are tombstones.
   `write_tasting` refuses to overwrite an existing revision — leave that guard in place.
3. **Every generated filename needs sub-second uniqueness.** See SPEC.md §0. This bug appeared
   three times; it is always silent.
4. **The phone never reaches the collection workbook.** Its OAuth scope is
   `Files.ReadWrite.AppFolder` only. New bottles queue in `pending/` and a human approves them on
   the PC.
5. The career score is the mean of counted sittings; the medal comes from that mean rounded
   **half-up** (`rubric.half_up`, not `round()`). Do not print a total under the per-category means
   — ten roundings drift and it would contradict the headline (SPEC.md §3.6).

## Working style

Save each step as it is finished, and run its tests before starting the next. Every module here has
a matching `test_*.py` that runs standalone.

```
pip install -r requirements.txt
python -m unittest discover -p "test_*.py"     # 244 tests, all passing
python verify_gate.py                          # SPEC §7 shipping gate (reads the master)
python collection.py                           # health report, writes nothing
```

`test_collection.py` SHA-256s the master workbook before and after loading. If that test ever
fails, stop everything.

## State as of 8 Sep 2026

Done and tested — `collection.py` (loader), `rubric.py` (scoring), `store.py` (journal),
`rollup.py` (generates `Whiskey Tastings.xlsx`).

Web front end — **in progress.** `app.py` (Flask server at `127.0.0.1:8765`), the judging sheet,
the flight/session view, the table view and compare (`static/index.html`, `app.js`, `table.js`,
`compare.js`, `style.css`) are done and tested — SPEC.md build order steps 3, 4, 5 and 6.
Covered by `test_app.py` (244 tests total). `GET /api/compare` takes `codes` (career scores,
the default), or `session` / `tastings` to pin single sittings; it returns per-axis leaders and
spreads, and every axis carries its own max so the view draws each bar against it.

The table serves two modes from `/api/table/collection` (one row per spirit, master fields
joined to the career score, encounters included as not-owned) and `/api/table/tastings` (one row
per sitting); columns are declared once in `app.py` as `COLLECTION_COLUMNS` / `TASTING_COLUMNS`
and the column chooser and CSV are generated from that contract. Sessions are a
first-class journal record: `store.write_session/sessions/session/next_flight_pos`, immutable with
revisions like tastings, filed under `sessions/` with an **`F-`** prefix (not `S-`, which is
already a Sample code). `rollup.py` grew a `Sessions` sheet. A standalone pour and a flight are the
same thing in the UI — a list of pours with one active — so the sheet renders a pour, not a global
scorecard. The server reads the spirit list from
`snapshot/collection.json` on every request and opens the master only on an explicit
`POST /api/refresh` (read-only, via `collection.load`); it has no path that writes the master.
The rubric now carries per-category `question` text (config.yaml) for the `?` tooltips, and the
overall note is stored under `notes["overall"]` so `store.py` keeps its single notes dict. Flask
is the chosen framework (SPEC.md §6 sanctioned FastAPI or Flask); added to requirements.txt.

Quick Entry (SPEC.md §7 step 7) is done: `quickentry.py` + `test_quickentry.py`. The phone types
into the `Quick Entry` sheet of the rollup workbook; `POST /api/quickentry/drain` files each
matchable row as an **unscored draft** and rewrites the workbook so the sheet is clear.
Two rules hold it together — a name that is not unique is refused rather than guessed at
(§2), and refused rows stay on the sheet with a `problem` reason rather than being dropped
(§1.2). `store.write_tasting` now accepts an unscored card **only** when `status="draft"`,
and forces `include_in_average` false for one, because `rubric.career()` would choke on empty
scores. `barrel_id` is stored and rolled up.

The draft cache closes the old gap: in-progress pours are mirrored to `localStorage` and
restored on load, with a Discard control. Submitted pours and the flight were always safe on
the server; this covers the unsubmitted ones.

Analysis (SPEC.md §7 step 8) is done. `GET /api/analysis` returns one point per scored spirit
(career scores, §3.6), group means by Type and Region, and a calibration series of the monthly
mean over sittings — that last one measures the scorer, not the spirit, which is how grade
drift shows up. `static/analysis.js` draws it as hand-written inline SVG: no chart library, so
it keeps working offline and adds nothing to the bundle the phone will load. The axes carry a
`have` count so a scatter over three points cannot pose as a finding, and the medal thresholds
are drawn on every score axis.

The §8 write path is done: `master_write.py` + `test_master_write.py`, plus the approval gate
(`POST /api/pending/<uid>/approve|reject`) and its review panel. **Read `master_write.py`'s
docstring before touching it.** It never opens the master for writing — it rebuilds the zip
copying every entry byte-for-byte except the one worksheet part, and edits that part as *text*
rather than through ElementTree, because the worksheet root carries
`mc:Ignorable="x14ac xr xr2 xr3"` and re-serialising renames those prefixes into an Ignorable
list that no longer resolves — which is what makes Excel offer to "repair" a file. Every write
takes a backup first and runs the §8.4 verification after; a failed assertion restores the
backup. Codes are assigned at approval time on the PC, never on the phone.

`verify_gate.py` is the SPEC §7 shipping gate: a full round trip against the real master, then
a SHA-256 comparison. It passed on 8 Sep 2026 — 198 photos and 5 richData parts intact.
Re-run it after anything that touches collection.py or master_write.py.

The iPhone client is built: `docs/` is the static bundle for GitHub Pages —
`index.html`, `style.css`, `app.js` (five screens), `store.js` (local cache + upload queue),
`graph.js` (MSAL + Graph), `sw.js`, `manifest.webmanifest`, `rubric.json`, `icon-180.png`.
Guarded by `test_phone.py`, which checks the §9.2 rules that only fail on a phone: safe-area
insets on every edge, 100dvh not 100vh, 16 px inputs, a 44 px score strip with
`touch-action: none`, the tab bar hiding for the keyboard, and every precached file existing.

The phone scores offline by design: a card is written to localStorage **and then** queued, so
losing signal changes nothing. `docs/rubric.json` is a copy of `rubric.as_config()` because
the phone computes its own totals and medals — `test_phone.py` fails if it drifts from
config.yaml, so **regenerate it whenever the rubric changes**. The PC publishes what the
phone reads into `snapshot/` in the app folder (collection, rubric, careers) on
`POST /api/refresh`; careers are published rather than derived so the phone never downloads
the journal over bar wifi.

**Not deployed yet — three steps, all yours:** put the Application (client) ID in
`docs/config.js` (blank on purpose; a test enforces that, so relax it when you fill it in),
add the Pages URL as a Single-page application redirect URI on the Entra registration, and
turn on GitHub Pages for the `docs/` folder on `main`. Until then the app runs and scores;
only uploading waits.

That is the whole of SPEC.md. Nothing in the build order is outstanding.

The front end serves two clients from one codebase: the PC app at `127.0.0.1:8765`, and a
static build in `docs/` deployed to GitHub Pages for the iPhone. Design is settled: see SPEC.md §5
(tokens), §9.2 (iOS rules), §10 (the approved mockups — three PC screens, six phone screens). Build
the phone client to iOS conventions; 44 pt touch targets, safe-area insets, 16 px inputs, its own
back navigation, offline-first with a local queue.

Repo: https://github.com/RollingShuttle/whiskey-tasting-book — **public**, pushed, branch `main`.
The committed tree carries no personal data: `config.yaml`, `data/` and every `.xlsx` are
gitignored, and the test fixtures are synthetic. Commits use a repo-local noreply identity
(`git config user.email` inside this repo) so no personal email lands in public history —
keep it that way. Re-check before adding anything real to a test or a doc.

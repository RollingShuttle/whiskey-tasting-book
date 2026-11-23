# Whiskey Tasting Book — project context

Read `guide/SPEC.md` before changing anything. It is the build spec and it is current.
`guide/IOS-APP.md` is the plan and record for the native iOS app — a hand-written Swift+WKWebView
shell under `ios/` (built 8 Nov 2026, **not yet compiled**; `ios/README.md` is the Mac-day
checklist). Its facts were verified on 2 Nov 2026 and it says which decisions they rest on.
`guide/SETUP.md` covers the one-time Microsoft account setup. `guide/RUNNING.md` is the plain-language
guide to installing and starting both halves (the PC app opens via `launch.py`, not `app.py`, and
ships as a console-free exe built by `build_exe.bat`, staying resident in the notification area
when its window closes) — keep it accurate when behaviour changes.

## Where things are

The eight app modules, the three `.bat` files, `update-mac.command` (double-clicked on the Mac) and
`config.yaml` stay at the root: the exe reads `config.yaml` and `data/` from the folder it sits
in, and the `.bat` files are what gets double-clicked. Everything else has a folder — `tests/`,
`tools/` (scripts run by hand), `guide/` (the documents above), `static/` (the PC front end,
bundled into the exe), `docs/` and `ios/` (the iPhone app's Xcode project; it copies `docs/` into
the app at build time, so `docs/` is the phone client's one source).

**`docs/` is the iPhone app's page, and nothing else** (22 Nov 2026). The app's WKWebView loads
it from inside the bundle at `app://local/`; sign-in, the storage mirror and handing a file to
the share sheet go through `docs/native.js` to Swift (`Native.caps`: `auth`, `backup`, `share`),
and `tests/test_ios.py` pins that the page and `ios/TastingBookShell/Bridge.swift` name the same
messages. The Swift has never been compiled on this machine — `ios/README.md` lists the two most
likely complaints and their fixes.

**There is no browser version.** The phone client used to be a web app on GitHub Pages as well —
Microsoft's browser sign-in library, a service worker, a Home Screen install. All of that is
removed: `docs/graph.js` asks the shell or answers "not signed in", nothing registers a service
worker, and `boot()` in `docs/app.js` shows a notice (`retired()`) at any address that is not the
app. The one exception is `DEV_HOSTS` — `localhost`, `127.0.0.1`, a file opened from disk —
because **a desktop browser is still how the screens are looked at while they are worked on**
(`.claude/launch.json`, "phone"): no shell, so no sign-in, no sync, no mirror and no export, but
every screen draws from seeded `localStorage`. Do not add a feature that only works in a browser,
and do not break the page's ability to open without the shell. `docs/sw.js` is the old address's
last service worker — it empties an old install's cache and unregisters itself — and exists only
while GitHub Pages is still switched on; `ios/copy-web.sh` leaves it out of the app.

Two of those names are fixed by something outside this folder and **should not be renamed**:
the phone client is `docs/` however much it looks like documentation — GitHub Pages, which would
serve only `/` or `/docs`, is why it was named so, and `ios/copy-web.sh`, the Xcode project and
every phone test now point at it; and `static/` is what `build_exe.bat` copies in with
`--add-data "%~dp0static;static"`, which `app.py` then resolves under `sys._MEIPASS`.

Anything under `tools/` or `tests/` resolves the project root as `Path(__file__).parent.parent`.
The tests put `tests/`, the root and `tools/` on `sys.path` themselves, so each still runs
standalone (`python tests/test_app.py`) as well as under discovery.

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
a matching `tests/test_*.py` that runs standalone.

```
pip install -r requirements.txt
python -m unittest discover -s tests    # 684 tests, all passing
python tools/verify_gate.py             # SPEC §7 shipping gate (reads the master)
python collection.py                    # health report, writes nothing
```

`tests/test_collection.py` SHA-256s the master workbook before and after loading. If that test ever
fails, stop everything.

`tests/test_phone_js.py` **executes** the phone scripts in V8 (`pip install mini-racer`, a dev-only
dependency; the tests skip without it). Every other phone test reads source text, which once let a
removed helper ship with its caller intact — green suite, app silently storing nothing. Run it.

## State as of 8 Sep 2026

Done and tested — `collection.py` (loader), `rubric.py` (scoring), `store.py` (journal),
`rollup.py` (generates `Whiskey Tastings.xlsx`).

Web front end — **in progress.** `app.py` (Flask server at `127.0.0.1:8765`), the judging sheet,
the flight/session view, the table view and compare (`static/index.html`, `app.js`, `table.js`,
`compare.js`, `style.css`) are done and tested — SPEC.md build order steps 3, 4, 5 and 6.
Covered by `tests/test_app.py` (264 tests total). `GET /api/compare` takes `codes` (career scores,
the default), or `session` / `tastings` to pin single sittings; it returns per-axis leaders and
spreads, and every axis carries its own max so the view draws each bar against it.

The table serves two modes from `/api/table/collection` (one row per spirit, master fields
joined to the career score, encounters included as not-owned) and `/api/table/tastings` (one row
per sitting); columns are declared once in `app.py` as `COLLECTION_COLUMNS` / `TASTING_COLUMNS`
and the column chooser and CSV are generated from that contract. The row builders under
`/api/table/*` copy spirit fields **by name**, though, so a new column needs the declaration *and*
the copy (in the collection, retired and tastings builders) — a test checks the value, not the
header, because a declared-but-uncopied column renders as a dash for ever.

The page widens with the window, per view: `showView` stamps `view-<name>` on `<body>` and
`#main`'s width is set from that. Score keeps a bounded column (a scorecard reads best that way),
Table and Compare take the whole window, and Analysis lays its 720-unit SVG charts two abreast on
a wide screen rather than scaling one up — the type would scale with it. Sessions are a
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

Quick Entry (SPEC.md §7 step 7) is done: `quickentry.py` + `tests/test_quickentry.py`. The phone types
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

The §8 write path is done: `master_write.py` + `tests/test_master_write.py`, plus the approval gate
(`POST /api/pending/<uid>/approve|reject`) and its review panel. **Read `master_write.py`'s
docstring before touching it.** It never opens the master for writing — it rebuilds the zip
copying every entry byte-for-byte except the one worksheet part, and edits that part as *text*
rather than through ElementTree, because the worksheet root carries
`mc:Ignorable="x14ac xr xr2 xr3"` and re-serialising renames those prefixes into an Ignorable
list that no longer resolves — which is what makes Excel offer to "repair" a file. Every write
takes a backup first and runs the §8.4 verification after; a failed assertion restores the
backup. Codes are assigned at approval time on the PC, never on the phone.

`tools/verify_gate.py` is the SPEC §7 shipping gate: a full round trip against the real master, then
a SHA-256 comparison. It passed on 8 Sep 2026 — 198 photos and 5 richData parts intact.
Re-run it after anything that touches collection.py or master_write.py.

The iPhone client is built: `docs/` is the page the app carries —
`index.html`, `style.css`, `app.js` (screens), `table.js`, `analysis.js`, `compare.js`,
`store.js` (local cache + upload queue), `xlsx.js` (the Excel export), `native.js` (the bridge),
`graph.js` (the app's sign-in + Graph), `sync.js`, `config.js`, `rubric.json`.
Analysis and Compare derive everything from the snapshot and `careers.json` — no fetch, no
charting library, hand-written SVG — so they work with no signal. Two things the phone cannot
derive are published for it: per-category means (it holds its own cards, not the journal) and the
calibration series. `careers.json` is now `{careers: {code: {score, medal, n, categories}},
calibration: [...]}` and is **persisted to localStorage**; it used to live only in memory, so
closing the app blanked every published score until the next sync.
A change in `docs/` reaches a phone with the next build of the app and no other way — there is
no cache to invalidate and no version to bump (the `sw.js` VERSION rule went with the web app).

`docs/sync.js` is the sync engine (8 Nov 2026): every trigger — the Refresh button, boot, the
shell's foreground, the browser's visible and online events, a submitted card — goes through
`Sync.start({reason})`, which is single-flight, skips when offline or signed out, throttles the
automatic pulls to `MIN_INTERVAL`, and keeps a step-by-step account in `Sync.state` that the Sync
screen draws and the header's thin bar follows. It lists the snapshot folder once and downloads
only the files whose eTag changed; `meta.lastSync` moves only when every file came down; a refused
upload stays in the queue carrying its reason instead of blocking the cards behind it. The module
has no `document` and no `app` so `tests/test_phone_js.py` runs it in V8. `Store.read` is memoised
per key (invalidated on write) — the collection screen used to parse the whole snapshot once per
row. The page does not zoom: viewport `maximum-scale=1, user-scalable=no`, which the shell's
WKWebView honours, and `html { touch-action: pan-x pan-y }` besides.
**The phone has two arrangements** (14 Nov 2026), chosen under *Where your book lives* on the
last tab and asked on the first screen of a phone that has neither chosen nor ever synced.
`onedrive` is everything above. `local` ("This phone only") is for someone with no PC and no
OneDrive: `Store.enqueue` is a no-op, `Sync.start` skips, `boot()` never asks for a token, the Add
form's bottle goes straight into the snapshot through `Store.addLocalSpirit` with an **`L-n`**
code (a prefix the PC never issues; `meta.localHigh` keeps it from being reissued), and the tab
is called Data and offers Export / Import (`Store.exportAll` / `importAll`, the same key-by-key
shape as the shell's backup file). `Store.setSnapshot` carries `_local` bottles across every
sync, so nothing typed on the phone disappears; cards scored in local mode carry `_local` and
are never uploaded. `Store.mode()` infers `onedrive` for a phone with a `lastSync` or a snapshot —
the owner's phone must never be asked. On the PC, bar pours ride in `/api/spirits` under
`encounters` (never among `spirits`): the Score picker lists them under a **Bar** chip and finds
them in a search, and a click opens their sittings in the table.

**The Add form's Type is a drop-down and its Age takes a word** (15 Nov 2026). The choices are
the workbook's own Type list: `collection._read_dropdowns` reads the sheets' data validations
from the zip (read-only, like `_read_table_defs`), `Collection.options("Type")` merges the three
sheets in order, and `snapshot()` publishes it as `types`. `Store.types()` is that list — or
`DEFAULT_TYPES`, the same list built in, for a phone-only book and for a snapshot older than the
field — plus any type the phone already holds, which is how a type entered under **Other…** is
offered from then on. `Store.canonicalType` folds a second spelling into the first. **If the
workbook's drop-down changes, press Refresh on the PC**; `DEFAULT_TYPES` in `docs/store.js` is
only the fallback, but keep it in step when you notice. Age is years on the number pad or the
**NAS** / **N/A** buttons. An encounter file keeps whichever it was in its one `age` field;
`Store.asSpirit` on the phone and `_age_parts` in `app.py` split it into `age` + `age_label`
wherever a pour becomes a row, because every list prints `age ? age + " yr" : age_label`.

**A phone-only book exports as an Excel workbook** (15 Nov 2026): `docs/xlsx.js`, behind
**Export to Excel** on the Data tab, `local` mode only. The file has the master's shape — the
three sheets, tables `Collection` / `Collection4` / `SampleList` at `B8`, the same columns,
calculated columns, drop-downs and allocated rows — and `tests/test_phone_js.py` holds it to
that by running the writer in V8 and reading the result with zlib, zipfile, openpyxl,
`collection.Collection(...).load()` and `master_write.append_row`. **If the master's layout
changes, `SHEETS` in `xlsx.js` and the column lists in that test change with it.** It writes its
own zip and DEFLATE and is synchronous throughout, because a share sheet only opens inside the
tap that asked for it. `L-n` codes go out as the sheet's `B-n`. The file is never named
`Whiskey Collection` — a test forbids the phone that name, and beside a real master a
photograph-less twin is a hazard. Inside the iPhone app the file leaves through a third shell
capability, `share` (`file.share` in `Bridge.swift`: written into Documents, then the share
sheet); the Swift for it is, like the rest, not yet compiled here.

**Phone layout, 15 Nov 2026** (these depart from the §10 mockups, with the owner's say-so). The
scorecard folds each category's note behind `+ note` (`.note-add`, whose `::before` is the
44-point hit area) and puts Venue after the scores; a card opened for correction now shows the
notes and venue it holds — it used to keep them and show empty boxes — and `fitNotes()` sizes
them *after* `show()`, because a hidden box measures as nothing. Collection chips are ordered by
use and include `unscored`. `renderSync` ends with `modeCard()` rather than starting with it.
`CompareView.begin(code)` is the way in from a bottle's page.

**Collection and Table are one tab** (approved from a mockup, 15 Nov 2026): five tabs, and the
Collection screen has a `List | Table` switch (`app.view`, remembered in `meta.view`). There is
one search box and one set of chips; `shownSpirits()` in `app.js` is the only place the row set
is decided, and `TableView.render(host, spirits)` draws what it is handed — `docs/table.js` has
no search or filter of its own, and both views show the same rows. `paintRows()` (the name
predates the table) repaints the bar and the body and leaves the search box alone, so typing
keeps its cursor. The table's sort is one `pickerChip` — a chip with an invisible 16 px
`<select>` over it — in place of a scrolling row of chips; a header tap still sorts and turns
the sort round. The list has its own short set of orders (`LIST_SORTS`), opening in shelf order.

**Scores live under Compare, not in the collection's table** (22 Nov 2026). The Compare tab
opens on **All scores** (`renderScores` / `paintScores` in `docs/compare.js`): one row for every
spirit that has a score — finished bottles and bar pours included — with Total, Medal, Flavor
total (the rubric's `flavour` categories only, offered when there are others to leave out),
every category, Sittings, Best, Worst and Score / $. The columns are built from the rubric.
Any header sorts, and so does the picker; blanks always sink, and ties go to the bottle with
more sittings. It is a spreadsheet pane: `.scores` takes the height between the header and the
tab bar, `.stable-wrap` scrolls both ways inside it, and `th` (top) and `.s-name` (left) are
sticky — which needs `border-collapse: separate` and the pane, not the page, as the scroller.
**Side by side** is the tab's second view: the older bars for two to six chosen spirits, with
their frozen key; `CompareView.begin(code)` opens that one. The collection's table
(`docs/table.js`) is now only the bottles — no score, medal, best, worst or score-per-dollar,
and no ranking lens; `lensPresets`, `lensIsEverything` and `sameKeys` went with it, leaving
`flavourKeys`, `lensMax` and `lensScore` for the Flavor total. It opens sorted by name.

**The desktop's Compare opens on the same table** (23 Nov 2026): `renderScores` / `paintScores`
in `static/compare.js`, with **Side by side** — the existing columns of bars — as its second
view. It needed no endpoint: `/api/table/collection` already returns every spirit joined to
`career_score`, `medal`, `n`, `best`, `worst`, `score_per_dollar` and the exact `category_means`,
and the table keeps the rows with a score. The Flavor total is summed from those exact means and
rounded once. A row opens the spirit's sittings (`showSittingsFor`); a flight's *Compare pours*
still opens Side by side. The desktop's own Table view is unchanged — it keeps its score columns,
its ranking lens and its column chooser.

`tools/demo_app.py` runs the desktop app on invented data in a temporary folder (a collection, a
journal, a bar pour, a finished bottle; a master path that does not exist), served from `static/`
as it is on disk. `.claude/launch.json` starts it as **"desktop"** on port 8799 — the way to look
at a desktop screen without rebuilding the exe or touching the real journal, as "phone" is for
`docs/`.

**Compare keeps a key in sight** (21 Nov 2026). The bars carry no names, so once the card of
totals scrolled away a category was anonymous bars. `.cmp-frozen` is a zero-height sticky marker
under the header and `.cmp-key` hangs from it — the same bottles in the same order, name, code
and score — so its arrival moves nothing on the page. An `IntersectionObserver` on the card
(`watch()` in `docs/compare.js`, one at a time) turns it on only while the card is out of sight
above; there is no scroll listener. Past four bottles the code is dropped for room.

Guarded by `tests/test_phone.py`, which checks the §9.2 rules that only fail on a phone: safe-area
insets on every edge, 100dvh not 100vh, 16 px inputs, a 44 px score strip with
`touch-action: none`, the tab bar hiding for the keyboard, and every precached file existing.

The phone scores offline by design: a card is written to localStorage **and then** queued, so
losing signal changes nothing. `docs/rubric.json` is a copy of `rubric.as_config()` because
the phone computes its own totals and medals — `tests/test_phone.py` fails if it drifts from
config.yaml, so **regenerate it whenever the rubric changes**. The PC publishes what the
phone reads into `snapshot/` in the app folder (collection, rubric, careers) on
`POST /api/refresh`; careers are published rather than derived so the phone never downloads
the journal over bar wifi.

**How it is deployed.** The phone gets the app through TestFlight (`ios/README.md`; a push to
`main` is what Xcode Cloud builds, when that workflow is set up). The Application (client) ID is
in `docs/config.js` and the Entra registration carries the app's `msauth.…://auth` redirect.
GitHub Pages is still switched on for `docs/` and now serves only the retirement notice; it can
be turned off in the repository's settings whenever the owner likes, and `docs/sw.js` deleted
with it.

All three sheets carry a **Status** column: `Unopened`/`Opened` mean the spirit is held,
`Finished`/`Removed` mean it has left. The vocabulary lives in `collection.py`
(`GONE_STATUSES`, `is_gone`) and nowhere else; matching is case-insensitive and trimmed because
these are typed by hand, a blank status counts as held (rows predate the column), and an
unrecognised one raises a warning rather than being guessed at — a misspelling would otherwise
leave an empty bottle counted as owned for ever. Every row gains a derived `owned`, which is what
the table, the phone's **In stock** filter and the analysis all read.

Rows may also be **deleted from the workbook when they are finished** — that is the
owner's normal housekeeping, not damage. Three things follow, and none of them are optional:
`POST /api/refresh` diffs the outgoing snapshot against the fresh read and writes `retired/<code>.json`
for anything that left (the only moment both lists exist); the collection table renders a retired
spirit that has sittings as a not-owned "Retired" row so its reviews outlive the bottle; and the
journal keeps a high-water mark in `meta/high_water.json` so a code is **never reissued** —
`next_code` is highest-present + 1, which would otherwise hand a deleted bottle's code to the next
one and silently re-point its reviews. `collection.BASELINE` is a reference point, not an
expectation: only a collapse below half is reported, because a shrinking shelf is normal.

The desktop sheet **autosaves**: six seconds after a change it POSTs the card as `status="draft"`
carrying its `tasting_id`, so finishing it writes a revision of the same sitting rather than a
second one beside it. `cardBody()` builds both the draft and the submitted body so they cannot
drift, and a fingerprint check stops a revision that would say nothing. Drafts may now be
*partially* scored — `rubric.validate_partial` checks ranges but not completeness, and
`store.write_tasting` gives an incomplete card `total: None` and never counts a draft, whatever the
caller asks. Editing and deleting exist on both halves: `DELETE /api/tasting/<id>` on the PC,
`Store.reviseCard`/`deleteCard` on the phone (which writes the revision and tombstone files
straight into the app folder — no endpoint needed, so it works offline). The phone can only touch
the sittings it holds; the PC can touch all of them. **Every category runs 0–max**, and both strips
draw a hollow cell for 0 (lit as a ring only when 0 is the score). Unset is `null`, never 0 —
nothing may read a 0 as "not scored", and the totals use `|| 0` only because a sum needs a number.

**A Finished or Removed bottle takes no new scores.** Nothing can be poured from it. The rule is
enforced once, in `POST /api/tasting`: a card for a spirit whose status `is_gone` is refused with
409 — *unless* its `tasting_id` is already on file (`journal.has_tasting`), because correcting a
sitting from when the bottle was full is what the journal is for. The desktop mints the id before
its first autosave, so "carries an id" is not the test; "id already on file" is. Quick Entry rows
that match a gone bottle stay on the sheet with the reason (§1.2). Both pickers read the verdict
through one line, `goneSpirit = (s) => s.owned === false && !!s.status` — status *and* owned,
because a bar pour is not owned either and is scored all the time. A gone bottle is kept out of
every ordinary list; one that was scored while it lasted sits under a **Finished** chip (the PC
row opens its sittings in the table via `TableView.focusTastings`, the phone's detail screen
offers no Score button), and one never scored appears nowhere, because there is nothing to show.
`/api/spirits` carries a per-spirit `sittings` count for that decision.

`docs/table.js` is the phone's collection table: sortable, no column chooser, scrolling sideways
rather than being squeezed. Proof and release year are shown on the score sheet, the tables and
both compare views, because three bottles in this collection are called George T. Stagg and those two fields are
what separate one release from the next.

`tools/update.py` / `update.bat` (+ `tests/test_update.py`) is the updater: fast-forward pull, reinstall only
if requirements.txt changed, stop the app (it holds its own exe open, so a build cannot replace it),
rebuild, refresh the shortcut. It refuses to run over local changes to tracked files and it skips
the twenty-second rebuild when nothing came down. Its build flags must stay in step with
`build_exe.bat` — a test asserts that, because a silently different build is the worst outcome here.

`tools/setup_machine.py` (+ `tests/test_setup_machine.py`) writes `config.yaml` for a new computer by finding
OneDrive and the workbook itself — config.yaml is gitignored because its paths carry a username, so
it is the only thing stopping a fresh clone from starting. **More than one PC may share the
journal**: cards are per-file with unique names and merge safely, but `meta/high_water.json`,
`meta/codes.json` and `snapshot/*` are whole-file writes and a simultaneous refresh on two machines
can lose one update. `highest_seen(sheet, prefix=...)` therefore also reads the `retired/` records,
which are one file per code and cannot be lost that way, so a code can never be reissued even if
the meta write is. Refresh and bottle approval should stay on one machine: they touch the 147 MB
master, and a conflicted copy of that is the one loss this project cannot undo.

That is the whole of SPEC.md. Nothing in the build order is outstanding.

There are two front ends: the PC app at `127.0.0.1:8765` (`static/`), and the page in `docs/`
that the iPhone app carries. Design is settled: see SPEC.md §5
(tokens), §9.2 (iOS rules), §10 (the approved mockups — three PC screens, six phone screens). Build
the phone client to iOS conventions; 44 pt touch targets, safe-area insets, 16 px inputs, its own
back navigation, offline-first with a local queue.

Repo: https://github.com/RollingShuttle/whiskey-tasting-book — **public**, pushed, branch `main`.
The committed tree carries no personal data: `config.yaml`, `data/` and every `.xlsx` are
gitignored, and the test fixtures are synthetic. Commits use a repo-local noreply identity
(`git config user.email` inside this repo) so no personal email lands in public history —
keep it that way. Re-check before adding anything real to a test or a doc.

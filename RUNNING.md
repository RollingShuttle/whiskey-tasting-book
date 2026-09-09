# Running it — the PC app and the phone app

Written for someone who does not want to think about the technical details. `SETUP.md` covers the
one-time Microsoft account work; this covers everything else, in order.

---

## The idea in one picture

There are two halves and they never talk to each other directly.

```
   YOUR PC                          ONEDRIVE                        YOUR IPHONE
   ───────                          ────────                        ───────────
   the app you run                  a shared folder                 a web app you
   on your own machine        <──>  both halves can          <──>   install to the
                                    reach                           home screen

   reads your collection            small files, a few KB           never sees your
   workbook, writes the             each — no spreadsheet           collection workbook
   readable one                     is ever synced                  at all
```

The PC does the heavy work: it reads the 147 MB collection workbook, writes the readable
`Whiskey Tastings.xlsx`, and publishes a small summary for the phone. The phone only ever reads
that summary and writes small scorecard files back. They meet in one OneDrive folder and OneDrive
does the syncing, which is why neither has to be switched on at the same time.

---

## Part 1 — The PC app

### What it actually is

It is a small program that runs on your computer and shows its pages in your normal web browser.
Nothing is on the internet. The address `http://127.0.0.1:8765` means "this computer, on door
number 8765" — no one else can reach it, even on your own wifi.

### One-time, on this machine — already done

For reference, or if you ever move to a new computer:

1. **Python** — the language the app is written in. Already installed here.
2. **The libraries** — three small add-ons the app needs. Install them by opening a terminal in the
   project folder and running:

   ```
   pip install -r requirements.txt
   ```

3. **The settings file** — `config.yaml`, which tells the app where your files live. It already
   exists on this machine. It is deliberately not in the public repository, because it contains the
   path to your own home folder. If you ever need to recreate it, copy `config.example.yaml` to
   `config.yaml` and fill in the three paths near the top.
4. **The app itself** — `Whiskey Tasting Book.exe`, built once by double-clicking `build_exe.bat`.
   It bundles Python and the pages into a single file so there is no console window. It is not in
   the repository (27 MB of build output does not belong there), so on a new machine, or after the
   code changes, run `build_exe.bat` again and then `python make_shortcut.py`.

### Starting it, every time

**Double-click the "Whiskey Tasting Book" icon** on your Desktop, or find it in the Start menu.

That is the whole thing. It opens in its own window — no address bar, no tabs, no bookmarks bar,
and no black console window behind it. It looks and behaves like an ordinary program.

- **Resize it** by dragging any edge or corner, or maximise it with the button in the title bar.
- **Minimise it** and it keeps running in the background. Click it in the taskbar to come back;
  nothing is lost and nothing needs restarting.
- **Closing the window does not stop it.** The app carries on quietly in the background, which is
  what you want: the next window opens instantly, and anything the phone uploaded still gets
  picked up. To bring the window back, double-click the Desktop icon again.
- **To actually quit**, find the small round app icon in the notification area — the arrow at the
  right-hand end of the taskbar, next to the clock — and **right-click it → Quit**. Double-clicking
  that same icon reopens the window.

The first time you open it, the window may take a second or two to appear. It is waiting until the
app is genuinely ready, rather than showing you an error page.

If it ever fails to start, it will tell you: a message box appears saying what went wrong, and the
same text is written to `error.log` in the project folder. It will not fail silently.

If you ever want a plain console and an address you open yourself, run `python launch.py
--no-window`, or `python app.py`.

If the shortcuts are ever lost, recreate them with:

```
python make_shortcut.py
```

They are ordinary Windows shortcuts. Delete them like any others; nothing is installed or
registered anywhere.

### The very first time you start it

Click **Refresh** in the top right once, while you have an internet connection.

That one button does three jobs:

1. Rereads your collection workbook (read-only — it is never written to) so the app knows about
   any bottles you have added in Excel.
2. Updates its own quick list, so it does not have to open a 147 MB file every time.
3. **Publishes the summary the phone reads.** Until you press Refresh at least once, the phone has
   nothing to show.

It takes a few seconds because the workbook is large. You only need to press it when you have
changed something in Excel, or when you want the phone to see new bottles.

### What the screens do

| Screen | What it is for |
|---|---|
| **Score** | The judging sheet. Pick a spirit, score the ten categories, submit. |
| **New flight** | Several pours in one sitting, with a switcher along the top. Blind mode hides each bottle until its card is submitted. |
| **Table** | Every spirit or every tasting, sortable and filterable. This is the "easy sorting" the whole project was for. |
| **Compare** | Two to four things side by side. |
| **Analysis** | Score against age, price, proof; by type and region; and whether your own scoring is drifting over time. |

### Your scores are saved as you go

You do not have to remember to press Submit for the work to survive. A few seconds after you stop
typing, the card is written into the journal as a **draft** — the footer says "Draft saved" with the
time. A draft never counts towards a score, so an unfinished card cannot move an average.

**Submit** is still what marks it as counted.

### Changing or removing a score afterwards

**On the PC:** open the **Table**, switch it to **one row per tasting**, and every sitting has
**Edit** and **Delete** at the end of its row. Edit loads that card back onto the Score sheet;
Delete removes it after asking.

**On the phone:** open the bottle from Collection or Table. Its sittings are listed at the bottom
of that page, each with **Edit** and **Delete**.

Either way, nothing is destroyed. A correction is saved as a *new revision* and the earlier one
stays in the journal; a deletion writes a tombstone, so the card stops counting and stops being
listed but the record remains. The phone can do both with no signal — the change queues like any
other upload.

One thing you may notice on the phone: after deleting a sitting that was scored on the PC, the
score above it does not change straight away. That number is the PC's, and it still counts the
deleted sitting until the PC has read the tombstone. The page says so underneath. Press Refresh on
the PC and it settles.

### Where your data goes

- **Scores** → one small file per tasting in
  `C:\Users\<you>\OneDrive\Apps\Whiskey Tasting Book\tastings\`.
  They are never edited, only added to — a correction is a new file, so nothing can be lost.
- **The readable spreadsheet** → `Whiskey Tastings.xlsx`, rebuilt from those files. You can open it
  in Excel any time. Do not type into it: it is regenerated and your typing would be lost. The one
  exception is the `Quick Entry` sheet, which is designed to be typed into.
- **Your collection workbook** → only ever read, never written, except when you explicitly approve
  a new bottle. A backup is taken before that happens.

### Finishing a bottle

All three sheets have a **Status** column. The app reads four values:

| Status | Means |
|---|---|
| **Unopened** | you have it, unopened |
| **Opened** | you have it, open |
| **Finished** | drunk — no longer yours |
| **Removed** | gone some other way — sold, given away, a mistake |

**Marking the status is better than deleting the row.** The row stays, so the bottle keeps its
name, its price and everything else, and your reviews of it stay attached to something real. It
drops out of "In stock" on the phone and shows as *not owned* in the Table, and Refresh records the
date you marked it — the workbook has no date of its own, so that is the only way to know how long
a bottle lasted.

Capitalisation and stray spaces do not matter. A **blank** status is treated as still owned, since
plenty of rows predate the column. A **misspelling** is reported rather than guessed at — otherwise
"Finsihed" would leave an empty bottle counted as owned for ever and nothing would ever say so.

### Deleting the row instead

You can still delete rows, and nothing breaks. Press **Refresh** afterwards so the app notices.

What happens to a bottle you had already reviewed:

- **Its reviews are kept.** It stays in the Table marked **Retired** and *not owned*, with its
  score, its name and everything else it was. Drinking a bottle does not erase your notes on it.
- **Its code is retired with it.** If you delete B-144 the next bottle becomes B-145, never B-144
  again. That matters more than it sounds: reusing the number would quietly turn old reviews into
  reviews of a different whiskey.
- A bottle you delete **without ever having reviewed it** simply disappears. There is nothing to keep.

Press Refresh after deleting. That is the moment the app compares the old list with the new one and
records what left — before the old list is overwritten, which is the only time the difference can
be seen at all.

### Proving nothing was damaged

If you ever want reassurance that the app has not touched your collection workbook:

```
python verify_gate.py
```

It does a complete round trip and then compares the workbook against itself, byte for byte. It
should end with `PASS`, confirming all 198 photos are intact.

---

## Part 1b — Running it on a second computer

The app is not tied to one machine. A laptop can run the same thing, and the two stay in step
because both read and write the same OneDrive folder — the same way the phone does.

**On the laptop, once:**

1. Install **Python** if it is not there: https://www.python.org/downloads/ — tick *Add Python to
   PATH* on the first screen.
2. Make sure **OneDrive is signed in and has finished syncing**, so the app folder exists.
3. Get the code and set it up:

   ```
   git clone https://github.com/RollingShuttle/whiskey-tasting-book.git
   cd whiskey-tasting-book
   python setup_machine.py
   pip install -r requirements.txt
   ```

   `setup_machine.py` writes `config.yaml` for that machine. It finds OneDrive and fills in the
   paths itself, because those paths contain your username and so cannot be shared between
   computers — that is the only reason a fresh copy will not start on its own.

4. Then build the app and make its icon:

   ```
   build_exe.bat
   python make_shortcut.py
   ```

### Keeping a machine up to date

**Double-click `update.bat`** in the project folder. That is the whole thing, on either computer.

It fetches the latest version, reinstalls anything new it needs, closes the app if it is running,
rebuilds it and refreshes the Desktop icon. Run it whenever you like: if there is nothing new it
says so and stops in a second.

Two things it will not do:

- It will not overwrite changes you have made yourself to the project's own files. If you have any
  it stops and names them. (`config.yaml`, `data/` and your workbooks are not part of the project,
  so they are never touched.)
- It will not report success after a failed build. If something goes wrong the window stays open
  with the reason.

Closing the app first matters more than it sounds: the running app holds its own program file open,
so a rebuild cannot replace it, and skipping that step is how you end up still running last week's
copy with nothing on screen to say so. `update.bat` handles that for you.

Afterwards, open the app and press **Refresh** once, so the phone gets anything new the PC has
started publishing.

### What works on both, and what to keep on one

**Everything about scoring works on both.** Score, browse, the table, compare, analysis. Scores
travel through OneDrive, and two machines cannot tread on each other's work: every card is its own
file with a name nothing else can take.

**Keep two things on the desktop:**

- **Refresh**, which rereads the collection workbook
- **Approving a new bottle**, which writes to the collection workbook

Both touch the 147 MB workbook. Doing them from two machines at once is how OneDrive ends up making
a *conflicted copy* of it, and that file is the one thing in this project that cannot be
regenerated.

The laptop does not need the collection workbook at all. If it is not there, `setup_machine.py`
says so and carries on: the app still shows all 375 bottles and every score, because it reads the
same published summary the phone does. Only Refresh and approvals need the workbook itself.

Bottle codes are safe either way. A number that has ever been used is never handed out again, and
that is recorded in two independent places so a sync clash cannot lose it.

---

## Part 2 — The phone app

This one needs three things set up once. After that it is just an icon on your home screen.

### Why it needs setting up at all

The phone app is a web page, so it has to live at a web address — that is step A. And it needs
permission to reach your OneDrive folder — that is steps B and C. Nothing runs on a server; the
page is just files, and your phone talks to OneDrive directly.

### Step A — put the app on the web (5 minutes)

1. Go to **https://github.com/RollingShuttle/whiskey-tasting-book**
2. Click **Settings** (the tab along the top of the repository).
3. In the left-hand menu, click **Pages**.
4. Under "Build and deployment", set **Source** to *Deploy from a branch*.
5. Set **Branch** to `main`, and set the folder dropdown next to it to **`/docs`**.
6. Click **Save**.

Wait a minute or two, then reload that Settings → Pages page. It will show the address, which will
be:

```
https://rollingshuttle.github.io/whiskey-tasting-book/
```

Open it on your computer first to check it loads. It will say there is no collection yet — that is
correct, it has not been given permission to fetch one.

### Step B — tell the app who it is (5 minutes)

Your Microsoft app registration from `SETUP.md` Part 3 has an identifier. The app needs it.

1. Go to **https://entra.microsoft.com** and sign in with your **personal** Microsoft account —
   the one that has the whiskey files. Check the avatar in the top right before going further.
2. Left menu → **Applications** → **App registrations** → click **Whiskey Tasting Book**.
3. On the Overview page, copy the **Application (client) ID**. It looks like
   `3f9a2c14-7b21-4c8e-9a55-1d2e3f4a5b6c`.
4. In the project folder, open `docs/config.js` in any text editor. Find this line near the top:

   ```
   CLIENT_ID: "",
   ```

   Paste your ID between the quotes so it reads:

   ```
   CLIENT_ID: "3f9a2c14-7b21-4c8e-9a55-1d2e3f4a5b6c",
   ```

5. Save the file, then commit and push it so the deployed page picks it up:

   ```
   git add docs/config.js && git commit -m "Add the client ID" && git push
   ```

This identifier is **not a password**. Any app of this kind has to include it in plain view, and it
is useless on its own — it only works from your registered web address, only for personal Microsoft
accounts, and only for the one folder. There is no secret anywhere in this project, and there
should never be.

### Step C — let the app sign you in (3 minutes)

Still in the Entra portal, on the same app registration:

1. Left menu → **Authentication**.
2. If there is already a **Single-page application** section, click **Add URI** under it. If not,
   click **Add a platform** → **Single-page application**.
3. Enter exactly:

   ```
   https://rollingshuttle.github.io/whiskey-tasting-book/
   ```

   The trailing slash matters.
4. Click **Save** (or **Configure**).

This is Microsoft's way of making sure sign-ins can only be sent back to a page you control.

### Step D — install it on the iPhone (2 minutes)

1. Open **Safari** on the iPhone. It has to be Safari — installing from Chrome does not give you a
   proper app.
2. Go to `https://rollingshuttle.github.io/whiskey-tasting-book/`
3. Tap the **Share** button (the square with an arrow, at the bottom).
4. Scroll down and tap **Add to Home Screen**, then **Add**.

You now have an icon. Opening it from the icon runs it full screen with no browser bars.

### Step E — first run on the phone

1. Make sure the PC app has been started and **Refresh** pressed at least once, and give OneDrive a
   minute to sync.
2. Open the app from the home screen.
3. Tap **Sync** at the bottom.
4. Tap **Sign in**. Sign in with your **personal** Microsoft account.
5. Back on the Sync screen, tap **Refresh**.

Your collection appears under the Collection tab. You are done.

---

## Part 3 — Living with it

### The daily rhythm

- **On the phone, anywhere:** tap a bottle, tap *Score a pour*, drag along each row, submit. It
  works with no signal at all — the card is saved to the phone first and uploaded later. The number
  on the Sync tab tells you how many are waiting.
- **On the PC, when you are at the computer:** start the app. It picks up whatever the phone sent.
  Press **Refresh** when you want the phone to see newly added bottles.
- **New bottles:** add them on the phone under *Add bottle*. They do not go straight into your
  collection — they wait, and the PC app shows them at the top of the Score screen for you to check
  and approve. Only then is the row written, and only after a backup.

### Things that are normal, not faults

- **Double-clicking the icon twice does not open two copies.** The second one just brings the
  window you already have to the front. There is only ever one app running.
- **The app is still in the notification area after you close its window.** That is deliberate,
  not a leak. It uses almost nothing while it sits there. Quit it from that icon when you want it
  gone, or leave it — it stops when you shut the computer down either way.
- **The phone asks you to sign in about once a day.** Microsoft limits how long a browser app can
  stay signed in, and it is not adjustable. It never stops you scoring — only uploading.
- **Scores you entered on the PC do not instantly appear on the phone.** The phone sees them after
  you press Refresh on the PC and OneDrive has synced.
- **The Sync screen says "offline".** That is just the phone reporting honestly. Keep scoring.

### If something looks wrong

| What you see | What to do |
|---|---|
| The window closes at once, or never appears | Read `error.log` in the project folder — the reason is written there. |
| You closed the app but want it back | Double-click the icon again — or double-click the app icon in the notification area by the clock. |
| You want it fully stopped | Right-click the app icon in the notification area → **Quit**. |
| A window says "The app has been closed" | It is a leftover window from a copy you quit. Close it and open the app again. |
| "No collection yet" on the phone | Start the PC app and press Refresh, then Refresh on the phone. |
| "Workbook open in Excel" | Close `Whiskey Tastings.xlsx` (or the collection workbook) in Excel and try again. This is deliberate — writing while Excel has it open would create a conflicting copy. |
| Sign-in fails on the phone | Check you used the personal Microsoft account, and that the address in Step C matches exactly, trailing slash included. |
| You want to be sure nothing broke | Run `python verify_gate.py`. |

---

## A simpler option, at home only

If you are on the same wifi as the PC, you can skip the phone app entirely and use the PC app from
the phone's browser.

In `config.yaml`, change `bind_lan: false` to `bind_lan: true`, restart the app, and visit
`http://<your PC's address>:8765` on the phone. You get the full desktop app on the phone screen.

It only works at home, and it needs the PC switched on — which is exactly why the separate phone
app exists for everywhere else.

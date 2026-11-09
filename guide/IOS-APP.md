# The iPhone app — what was built, and the Mac steps

**Status, 8 November 2026:** the app is built and committed under `ios/TastingBookShell`, with
its web half in `docs/`. It has **not been compiled yet** — there is no Xcode on the Windows PC —
so the first Mac session is the compile. `ios/README.md` is the step-by-step for that day; this
file is the why, the decisions, and what still needs you.

The facts in Part 2 were verified against Apple's, Microsoft's and WebKit's own documentation and
source on 2 November; the plan was then revised on 8 November when the workout app's shell turned
out to be a better template than the one originally proposed. Where only a phone can settle
something, it says so.

## 0. The decision

The phone client stays what it is — the web code in `docs/` — inside a **hand-written Swift +
WKWebView shell**, the same design as the workout app's `ios/WorkoutShell`, which already compiles
on the MacBook with Xcode 27.2 and runs on your phone. The web files are **copied into the app at
build time**, so it opens and scores with no signal at all, nothing cached, nothing to expire.
Sign-in moves out of the page into the **system browser sheet**, which is what lets a Microsoft
login last months rather than a day. Builds come from **Xcode Cloud** (included with your
membership) and land on both phones through **TestFlight**, so the borrowed Mac is needed once —
to compile, run the three acceptance tests, and create the cloud workflow. After that every update
is a push to GitHub. GitHub Pages keeps serving the PWA unchanged from the same `docs/` folder.

### Why not the Capacitor route the 2 November plan proposed

Three things changed when the workout app was looked at:

- **A proven template existed for this exact Mac.** Its shell was written on Windows, compiled on
  the first try on Xcode 27.2, and had its one runtime crash fixed. Copying a working shell beats
  adopting a framework whose Xcode 27 compatibility was undocumented.
- **No toolchain anywhere.** Capacitor needs Node.js on the PC and in every cloud build; the Swift
  shell needs only Xcode. Nothing to install, nothing to keep in step.
- **Your paid account was already active** (Team ID `4RLD77Y492` in the workout project), so
  TestFlight and Xcode Cloud were available immediately rather than after an enrollment wait.

Everything else the 2 November plan concluded still holds and is still used: bundled files for
offline, the system-browser sign-in with a native-style redirect, Xcode Cloud as the update path.

## 1. What changes for you, day to day

| | Today (PWA) | After |
|---|---|---|
| Opening it | Home-screen icon that is really Safari | A real app, from TestFlight |
| Signing in | About once a day | Rarely — a native login lasts months of normal use |
| No signal in a bar | Works if Safari's cache has not been cleared | Always: the files are inside the app |
| Getting an update | Automatic after a push and a reload | TestFlight shows an update; one tap. About 20–30 minutes after a push |
| Your wife's phone | Same Pages address | Same app via TestFlight; no cable, no Developer Mode |
| What needs a Mac | Nothing | Nothing routine. Once, to compile and set the cloud build up |

Both phones sign in with the same Microsoft account, as the PWA does: the data is your OneDrive
app folder.

## 2. How it works, and the facts it rests on

- **The page runs at `app://local/`**, served from the bundle by `AppSchemeHandler.swift`. That is
  not an `https` origin, which has two consequences the design follows from: Microsoft's browser
  sign-in (MSAL in the page, redirect flow) cannot work there — Entra accepts only `https` redirect
  URIs for browser clients — and a service worker cannot register there (WebKit requires an
  HTTP(S) script URL). Neither is needed: the files are local, and sign-in is done natively.
- **Sign-in** (`MicrosoftAuth.swift`): authorization-code flow with PKCE in
  `ASWebAuthenticationSession`, a system sheet that shares Safari's sign-in state. The redirect
  URI is `msauth.com.mikeyang.whiskey://auth`, registered in Entra under *Mobile and desktop
  applications*. Tokens live in the iOS keychain, never in the web view. Native refresh tokens
  default to 90 days of inactivity; the browser's are 24 hours — that is the daily sign-in, gone.
- **The bridge** (`Bridge.swift` ↔ `docs/native.js`): the page posts requests
  (`auth.status`, `auth.signIn`, `auth.token`, `auth.signOut`, `backup.save`, `backup.load`) and
  Swift answers through `window.nativeEvent`. Without the shell, `Native.available` is false and
  `docs/graph.js` / `docs/store.js` take their old paths unchanged — a test pins that.
- **Backup** (`BackupStore.swift`): every write to the page's storage is mirrored, debounced, to
  `tasting-book.json` in the app's Documents folder (visible in the Files app); at boot an empty
  store is restored from it. Capacitor documents web-view storage as reclaimable under storage
  pressure, and this is the answer to that.
- **The client id** stays in `docs/config.js` and is passed to Swift on every request — one source
  of truth, nothing duplicated in the shell.
- **Apple:** internal TestFlight builds need no App Review and last 90 days; Xcode Cloud is
  included (25 compute hours a month), requires an App Store Connect app record, automatic signing
  and a *shared* scheme committed to the repo (it is), and its first workflow can only be created
  in Xcode. `ITSAppUsesNonExemptEncryption = NO` is in the project so no build waits on the
  export-compliance question. A privacy manifest (`PrivacyInfo.xcprivacy`) declares the
  file-timestamp API the backup uses, which Apple otherwise flags on every upload.
- **The Mac:** Xcode 27.2 is installed and the phone is paired and in Developer Mode, from the
  workout app. Nothing about the machine needs to change.

## 3. Only you can do these (before the Mac day; no Mac needed)

1. **Register the native sign-in with Microsoft** (10 minutes, on the PC). Entra admin centre →
   App registrations → *Whiskey Tasting Book* → Authentication → **Add a platform** → **Mobile
   and desktop applications** → *Custom redirect URIs*: `msauth.com.mikeyang.whiskey://auth` →
   Configure. Not under *Web* (that expects a secret) and not under *Single-page application*
   (that expects a browser origin). Leave the existing Pages entry — the PWA still uses it. No
   secret is created. Without this step the sign-in sheet opens and Microsoft refuses with
   AADSTS50011.
2. **App Store Connect** (20 minutes, on the PC): *My Apps → + → New App*: iOS, name (globally
   unique even for TestFlight-only apps — have alternatives ready), bundle id
   `com.mikeyang.whiskey` (register it first under Certificates, Identifiers & Profiles →
   Identifiers → App IDs, explicit), SKU anything. *Users and Access*: add your wife with the
   **Marketing** role so she can be an internal tester. *TestFlight → Internal Testing*: a group
   with both of you, automatic distribution on. Install the **TestFlight** app on both phones.
3. **Sync the PWA once** before the first app install, so its upload queue is empty. The app has
   its own storage.

## 4. Prepared on the PC (done)

`ios/` (the Xcode project, eight Swift files, the icon, the privacy manifest, the shared scheme,
`copy-web.sh`, `README.md`), `update-mac.command`, `docs/native.js` and the native branches in
`docs/graph.js`, `docs/store.js` and `docs/app.js`, and the tests: `tests/test_ios.py` (project
integrity, settings, icon, and that the page and the shell name the same messages) and
`TestNativeShell` in `tests/test_phone.py`. The suite is green. Two reviewers who could not compile
either went through it for what Xcode would reject; their findings are folded in. The Swift
identifiers and API signatures were checked against the iOS 17 SDK by reading, not by compiling —
Part 5 says what to do if Xcode still objects.

## 5. The Mac session (about an hour)

Follow `ios/README.md`; this is the shape of it.

1. **Get the code.** First time: Terminal, `git clone https://github.com/RollingShuttle/whiskey-tasting-book.git`
   into Documents. Later: double-click `update-mac.command` (make it executable once:
   `chmod +x update-mac.command`).
2. **Open and run.** Open `ios/TastingBookShell.xcodeproj`. The Team and bundle id are already in
   the project; if Xcode asks about signing, choose your team. Pick your iPhone as the destination
   and press Run. The first build also runs `copy-web.sh`, which copies `docs/` into the app.
   - If Xcode reports a Swift error, the two most likely are named in `ios/README.md` with their
     one-line fixes; anything else, push the error text and the fix lands by `git pull`.
3. **The three tests, on the phone, in this order:** (a) force-quit, airplane mode, open: it must
   show the collection and let you score; (b) airplane mode off, Sync → Sign in: the system sheet
   opens, you sign in, it returns, Sync pulls the collection; (c) score something, Sync, and check
   the card arrived in the OneDrive app folder on the PC.
4. **TestFlight, first upload:** destination *Any iOS Device (arm64)* → Product → Archive →
   Distribute App → **TestFlight Internal Only**. With the app record and group from Part 3 in
   place, both phones get it from the TestFlight app within about half an hour.
5. **Xcode Cloud** (the step that cannot leave the Mac): Product → Xcode Cloud → Create Workflow;
   grant GitHub access when asked; archive as *TestFlight (Internal Testing Only)*; post-action
   *TestFlight Internal Testing* → your group; start conditions *Branch changes* on `main` and a
   weekly schedule; let Xcode Cloud manage the build number. Start the first build; it runs in
   Apple's cloud.

From then on your phone can also turn Developer Mode off: the TestFlight copy does not need it.

## 6. Afterwards

- **Any change** (web code, Swift, settings): commit, push to `main`. Xcode Cloud builds,
  TestFlight processes, both phones get an *Update*. `docs/` changes still want the `sw.js`
  VERSION bump for the PWA, as always.
- **Clocks that no longer matter:** TestFlight's 90-day build expiry (the weekly build renews
  it); the one-year development profile (that copy is only the day-one proof).
- **When a Mac is needed again:** only to debug on a cabled phone, or if the cloud workflow itself
  has to be recreated. Not for updates.
- **If the sign-in sheet says AADSTS50011:** the redirect URI in Part 3 is missing or misspelt.
- **If a sign-in prompt comes back daily:** the keychain is not keeping the tokens — a code fix,
  by push.
- **If TestFlight shows "Missing Compliance" or emails ITMS-91053:** the generated Info.plist lost
  the encryption key or the privacy manifest — check the built app's Info.plist as the README
  says; both are one-click answers in App Store Connect meanwhile.
- **If the app opens to a blank page:** `copy-web.sh` did not run or `docs/` moved — the build log
  shows the copy step.

## 7. What was verified, and what only the phone can settle

Verified by reading primary sources: everything in Part 2; Xcode 27.2's requirements; the
TestFlight and Xcode Cloud rules; Entra's redirect-URI rules and the token lifetimes; WebKit's
service-worker and custom-scheme behaviour.

Only the phone can settle: that the hand-written PKCE sign-in completes end to end (every piece
is standard; the composition is new code); that Microsoft Graph accepts requests from the
`app://local` origin (expected — the PWA's origin works, and Graph answers every origin; the
fallback is routing those two calls through Swift); that the refresh token actually lasts months
for a personal Microsoft account (Microsoft documents the 90-day default for work accounts and is
silent for personal ones — watch for prompts); keyboard behaviour over the score strips in a bare
web view; and that Xcode 27.2 compiles the shell without a complaint — the workout shell did, and
this one is cut from it.

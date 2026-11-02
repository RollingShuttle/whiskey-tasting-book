# The iPhone app — plan for turning `docs/` into a real iOS app

Written 2 November 2026, before any of it is built. The facts below were checked against Apple's,
Microsoft's, Capacitor's and WebKit's own documentation and source on that date; where only a
build on a real phone can settle something, it says so. Read the whole thing once now, then use
Part 5 as the checklist on the day.

## 0. The decision, in one paragraph

The phone client stays what it is — the web code in `docs/` — wrapped in a native shell by
**Capacitor**, with the files **bundled inside the app** so it opens and scores with no signal at
all, nothing cached, nothing to expire. Sign-in moves from the page to the **system browser**,
which is what lets a Microsoft login survive for months rather than a day. Builds come from
**Xcode Cloud** (included with the Developer Program) and land on both phones through
**TestFlight**, so the borrowed MacBook is needed **once**, for about an hour, to create the cloud
workflow; after that every update is a push to GitHub. The GitHub Pages site keeps serving the
PWA unchanged: `docs/` remains the single source for both.

## 1. What changes for you, day to day

| | Today (PWA) | After |
|---|---|---|
| Opening it | Home-screen icon that is really Safari | A real app, from TestFlight |
| Signing in | About once a day | Rarely — a native login lasts months of normal use |
| No signal in a bar | Works if Safari's cache has not been cleared | Always works: the files are inside the app |
| Getting an update | Automatic, after a push and a reload | TestFlight shows an update; one tap. About 20 minutes after a push |
| Your wife's phone | Same Pages address | Same app via TestFlight; no cable, no Developer Mode |
| What needs a Mac | Nothing | Nothing routine. Once, to set the cloud build up |

Both phones sign in with the same Microsoft account, exactly as the PWA does, because the data
lives in your OneDrive app folder.

## 2. Why it is built this way (the facts that decide it)

These are the findings that rule out the obvious shortcuts. Each was verified; the few that
could not be are marked.

- **Bundled files run at `capacitor://localhost`, and that cannot be changed to `https`.**
  Capacitor's own code silently resets any scheme WKWebView handles natively. Microsoft requires
  `https` redirect URIs for browser sign-in (localhost excepted), and a token request from a
  `capacitor://` origin is refused. So the sign-in code in `docs/graph.js` — MSAL in the page,
  redirect flow — cannot work inside the app. It must become a system-browser flow with a
  custom-scheme redirect (`msauth.<bundle id>://auth`) registered on the *Mobile and desktop
  applications* platform in Entra. Nothing about the existing Pages registration changes.
- **That rework is also the improvement.** Browser (SPA) refresh tokens expire after 24 hours,
  which is the daily sign-in. Native-client refresh tokens default to 90 days of inactivity.
- **A service worker cannot register at `capacitor://localhost`** (WebKit requires an HTTP(S)
  script URL). It is not needed: the bundle is the cache. The registration in `docs/app.js`
  already swallows the failure; it will be gated on `https:` for clarity.
- **The thin-shell alternative** (the app loads the live Pages site, so sign-in and updates work
  as today) was examined and kept as Plan B only. It depends on a service worker running inside
  the app's web view under Apple's App-Bound Domains, which is confirmed only by a WebKit
  engineer's bug comment, never by Apple's documentation, and it keeps the daily sign-in.
- **Over-the-air web updaters (Capgo) were examined and not chosen.** They work, but Xcode Cloud
  makes them unnecessary: a push rebuilds the whole app in Apple's cloud, so there is no second
  update mechanism to maintain and no rollback timer to get wrong.
- **Xcode Cloud** requires the paid Program, an App Store Connect app record, automatic signing,
  and the Xcode project committed to the repo. Its first workflow can only be created in Xcode —
  that is the one Mac step that cannot be moved. TestFlight builds for *internal* testers need no
  App Review and last 90 days; a weekly scheduled build means neither that clock nor the
  one-year development-profile clock ever matters.
- **A free Apple account is not enough**: its installs stop after 7 days and it has no TestFlight
  or Xcode Cloud. Do not borrow the Mac until the enrollment has come through.
- **The current Xcode (27.2) needs macOS Tahoe 26.6 or later** and an Apple-silicon Mac (hers
  is). Xcode 26.0–26.3 still install on Sequoia 15.6, but whether they can debug a phone running
  iOS 27 is undocumented. Xcode Cloud always builds with the latest Xcode regardless.
- **Web storage in an app's web view is documented by Capacitor as transient** — iOS may reclaim
  it under storage pressure. The PWA has lived with the same risk in Safari. The plan mirrors the
  queue and cards to a file inside the app so a night's scoring cannot be lost that way.

## 3. Before anything else — things only you can do (no Mac needed)

1. **Finish the Apple Developer Program enrollment** ($99/year). Use your legal name, your own
   card, two-factor on. Apple publishes no turnaround; enrollments that trigger an identity check
   have taken days to weeks in 2026. Nothing below should be scheduled until the membership
   confirmation arrives.
2. **Check the MacBook.** Apple menu → About This Mac: the macOS version and free disk. Xcode
   needs **macOS 26.6 or later** and, realistically, **40 GB free** (3.1 GB download, far more
   installed). If the Mac is older than that, the upgrade is a large unattended download — ask
   your wife to run it **days before** the session, from her own account. This is the single most
   likely thing to eat the whole session if left to the day.
3. **Ask your wife to install Xcode from the Mac App Store beforehand** and open it once (accept
   the licence; when the component sheet appears, tick **iOS** and install). Those steps may ask
   for an administrator password, which you will not have if you use a separate user.
4. **Choose the bundle identifier now and never change it**: `io.github.rollingshuttle.whiskeytastingbook`.
   It is baked into the sign-in redirect, the URL scheme and the keychain, and it freezes the
   moment a build is uploaded.
5. **Register the app's native sign-in with Microsoft** (10 minutes, on the PC):
   Entra admin centre → App registrations → *Whiskey Tasting Book* → Authentication → **Add a
   platform** → **Mobile and desktop applications** → *Custom redirect URIs*:
   `msauth.io.github.rollingshuttle.whiskeytastingbook://auth` → Configure.
   Do **not** put it under *Web* (that expects a client secret) or *Single-page application*
   (that expects a browser origin). Leave the existing Pages entry alone — the PWA still uses it.
   Keep *Personal accounts only*. No secret is created, same as before.
6. **Open the PWA and sync once** before the first app install, so its upload queue is empty. The
   app has its own storage; anything still queued in the PWA stays in the PWA.

## 4. Prepared on the PC with Claude, before the Mac day (about half a day)

Everything here is done on Windows and committed, so the Mac needs only Xcode and git. It needs
Node.js 22 or later on the PC (the Capacitor command line is a Node tool; its iOS steps were
checked to run on Windows because the project uses Swift Package Manager, not CocoaPods).

| Step | What it produces |
|---|---|
| `package.json`, Capacitor 8 (`@capacitor/core`, `@capacitor/ios`, `@capacitor/cli`), `capacitor.config.json` with `webDir: "docs"` | The project definition. Never the `next` tag: 9.0 is in beta. |
| `npx cap add ios` → `npx cap sync ios` | The Xcode project under `ios/`. `ios/App/App/public` (the copied web files) and `capacitor.config.json` are force-added to git, because the template ignores them and we want the Mac to need no Node. |
| Official plugins: `@capacitor/browser`, `@capacitor/app`, `@capacitor/preferences`, `@capacitor/filesystem` | System-browser sign-in, the URL-scheme return, durable storage. Official and SPM-ready; no third-party or CocoaPods plugin. |
| `docs/graph.js`: a native branch | PKCE sign-in in the system browser; code exchange through Capacitor's native HTTP (no browser origin, which is what the mobile platform type expects); tokens in Preferences; refresh with `offline_access`. The Pages path stays byte-for-byte as it is. |
| `docs/store.js`: a mirror | Every write to the queue and cards also lands in a file in the app's own folder; restored at boot if the web storage was reclaimed. |
| `docs/app.js`: the service-worker guard | Registers only on `https:`. |
| `ios/App/App/Info.plist` | The `msauth.…` URL scheme; `ITSAppUsesNonExemptEncryption = NO` so TestFlight never stalls on the export-compliance question; `WKAppBoundDomains` listed so Plan B is a one-file switch. |
| `ios/ci_scripts/ci_post_clone.sh` | Installs Node with Homebrew and runs `npm ci` + `npx cap sync ios` in Xcode Cloud, so cloud builds pick up plugin and web changes from the repo. |
| Icon and launch screen via `@capacitor/assets` | From a new 1024-px icon in `assets/` (`docs/icon-180.png` is too small to be the source). |
| `capacitor.config.shell.json` | Plan B, see Part 5. |
| Tests | The PKCE pieces, the storage mirror and the guard, under a fake plugin layer; a test that the Pages path is unchanged; `sw.js` VERSION bumped. |

None of this touches the PC app or the journal.

## 4b. On the web, after enrollment (20 minutes, on the PC)

- App Store Connect → **Users and Access**: add your wife with the **Marketing** role (an
  internal tester must be an App Store Connect user; she can create an Apple Account during
  activation; the invitation lasts 3 days). Marketing can see nothing that matters.
- Certificates, Identifiers & Profiles → Identifiers → register an **explicit App ID** with the
  bundle identifier above.
- App Store Connect → Apps → **New App**: iOS, the bundle ID, a SKU (anything), and a name. The
  name must be unique across the whole App Store even for an app that is never published — have
  two or three alternatives ready. Creating the record publishes nothing.
- TestFlight → **Internal Testing** → create a group, add both of you.
- Install the **TestFlight** app on both phones.

## 5. The Mac session — the checklist (60–90 minutes if Parts 3 and 4 are done)

Do this with the checklist, not with Claude open: the steps are clicks in Xcode. Install Claude
Code on the Mac only if something fails and needs a code change.

**0–10 min · get in.** Your wife creates a Standard user for you (optional, but it keeps your
keychain and sign-ins out of her account; she can delete it afterwards with "Delete the home
folder"). Log in. Open Xcode. Xcode → Settings → Apple Accounts → sign in with the enrolled
Apple Account (two-factor code on your phone).

**10–20 min · the project.** Terminal: `git clone https://github.com/RollingShuttle/whiskey-tasting-book.git`.
Open `ios/App/App.xcodeproj`. Let Swift Package Manager resolve (needs network; a minute or two).
Select the App target → Signing & Capabilities: *Automatically manage signing* on, Team = yours,
bundle identifier as above. Do not commit the signing change.

**20–35 min · your phone.** Cable the iPhone (a data cable — a charge-only one shows no prompt).
Tap *Trust* on the phone. In Xcode's Device Hub click *Pair*. Settings → Privacy & Security →
**Developer Mode** on → the phone restarts → confirm. Pick the phone as the run destination and
press Run. First build takes a few minutes; Xcode registers the device and makes the profile.

**35–50 min · the three tests, on the phone, before anything else.**
1. Force-quit the app, put the phone in airplane mode, open it: it must open to the collection
   and let you score a pour. (This is the test that would have decided Plan B; with bundled files
   it should simply pass.)
2. Airplane mode off. **Sync → Sign in**: a browser sheet opens, Microsoft sign-in, back to the
   app. Sync pulls the collection.
3. Score something, Sync, and check the card arrived in the OneDrive app folder on the PC.
If test 2 fails and the cause is not obvious in 30 minutes, **Plan B**: in Finder replace
`ios/App/App/capacitor.config.json` with the contents of `capacitor.config.shell.json` from the
repo root (no Node needed — that file is exactly what the sync step writes), Run again. The app
now loads the live Pages site in its web view: sign-in and updates work exactly as the PWA, the
daily login stays, and the fix for the native sign-in reaches the phone later through a push
and a cloud build. Repeat test 1 with Plan B — offline there depends on the service worker.

**50–75 min · the cloud build, the step that cannot leave the Mac.** Xcode: Product → Xcode Cloud
→ Create Workflow. Grant Xcode Cloud access to the GitHub repo in the browser when asked (you
own it, so the admin permission is yours). Workflow settings: archive for **TestFlight (Internal
Testing Only)**; post-action **TestFlight Internal Testing** → your group; start conditions:
**Branch changes** on `main` and **On a schedule** weekly. Start the first build. It runs in
Apple's cloud; you do not wait for it. (If `ci_post_clone.sh` fails in the cloud, the log says
why; the fix is a push.)

**75–90 min · leave nothing behind.** Xcode → Settings → Apple Accounts → remove the account.
Log out. Your wife deletes the user with *Delete the home folder*, or keeps it for next time
(saves ten minutes). Xcode itself stays installed; the phone keeps its trust record for her Mac
until *Reset Location & Privacy*.

Within about half an hour the TestFlight app on both phones shows the build. From then on your
phone can also drop Developer Mode: the TestFlight copy does not need it.

## 6. Afterwards — the routine, and what can go wrong

- **Any change** (web code, plugin, native setting): commit, push to `main`. Xcode Cloud builds
  (10–15 minutes), TestFlight processes it, both phones get an *Update* in the TestFlight app.
  `docs/` changes still need the `sw.js` VERSION bump for the PWA, as always.
- **Clocks that no longer matter:** TestFlight's 90-day build expiry (the weekly build renews
  it), the one-year development profile (that copy is only the day-one proof).
- **Budget:** 25 Xcode Cloud compute hours a month are included; a weekly build plus occasional
  pushes uses a fraction of that. The membership is the only cost.
- **When a Mac is needed again:** only if the Xcode Cloud workflow itself has to be recreated,
  or to debug something on a cabled phone. Not for updates.
- **If a sign-in prompt comes back daily** in the app, the native token cache is not persisting
  (Preferences) — a code fix, by push.
- **If TestFlight shows "Missing Compliance"** on a build, the Info.plist key was lost; it is a
  one-click answer in App Store Connect meanwhile.

## 7. What was verified, and what only the phone can settle

Verified against primary sources: everything in Part 2; Capacitor 8.6's requirements (Node 22+,
Xcode 26.0+, iOS 15+, SPM default); that `cap add ios` and `cap sync ios` run on Windows for an
SPM project; Entra's redirect-URI rules and the 24-hour SPA versus 90-day native token
lifetimes; Developer Mode, pairing, automatic signing, TestFlight internal testing without
review, the 90-day expiry, Xcode Cloud's inclusion, requirements and scheduled builds; the
7-day limit of a free account; Xcode 27.2's macOS requirement.

Only a build on the phone can settle: that the hand-written PKCE sign-in completes end to end
(the components are standard; the composition is new code); whether Microsoft Graph accepts
requests from the app's origin (expected, since the PWA's origin works; the fix is native HTTP
for those two calls); keyboard behaviour over the score strips in a bare web view (the
`Keyboard` plugin exists if it misbehaves); whether Xcode 27.x opens a Capacitor 8.6 project
untouched (Capacitor states only "26.0+"); and, for Plan B only, whether the service worker
serves a cold start offline.

## 8. Usage, kept small

- The Mac day is a checklist. Claude is not needed for clicks in Xcode.
- All code lands on Windows first, with tests, where the session is cheap and unhurried.
- The one thing worth Claude on the Mac is a failing test 2 that is not obviously the Entra
  registration — and even then the fix is a push and a cloud build, not a second Mac session.

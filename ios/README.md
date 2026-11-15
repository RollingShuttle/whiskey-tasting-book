# Tasting Book — the iPhone shell (TastingBookShell)

Swift + WKWebView. The app is the phone client in `docs/` — the very files GitHub Pages serves —
copied into the bundle at build time and served to the web view from `app://local/`. The native
side does only two things the page cannot: a **Microsoft sign-in in the system browser sheet**,
which lasts months instead of a day (the PWA's browser tokens are capped at 24 hours), and a
**backup file** the Files app can see. Scoring, the upload queue, the offline cache, every screen:
all the page, unchanged. The background for the decision is `guide/IOS-APP.md`.

Page ↔ shell messages are listed at the top of `TastingBookShell/Bridge.swift`; the page's half is
`docs/native.js`. The web page holds the Microsoft client id (`docs/config.js`) and passes it in
every sign-in message, so there is one source of truth for both the PWA and the app.

## Before the Mac: the Entra step (10 minutes, on the PC, only you can do it)

The app signs in with a redirect URI of its own, and Microsoft refuses one it has not been told about
(the error is `AADSTS50011`, redirect URI mismatch, and the sheet never comes back).

Entra admin centre → **App registrations** → *Whiskey Tasting Book* → **Authentication** →
**Add a platform** → **iOS / macOS** → Bundle ID

    com.mikeyang.whiskey

→ **Configure**. Entra generates the redirect URI from the bundle id; check that it reads

    msauth.com.mikeyang.whiskey://auth

and ignore the MSAL configuration snippets it shows below (this app does not use that library).
The *Mobile and desktop applications* tile with that URI typed by hand makes the identical
native-client entry; the iOS tile only saves the typing. Not *Web* (that expects a client secret)
and not *Single-page application* (that expects a browser origin). Leave the existing GitHub
Pages entry alone — the PWA still uses it. Keep *Personal Microsoft accounts only*. No secret is
created; there was never one.

The scheme is `msauth.` + the bundle identifier. It needs no URL-type entry in the app, because
`ASWebAuthenticationSession` captures the callback itself.

## On the Mac, the first time

1. `git clone https://github.com/RollingShuttle/whiskey-tasting-book.git` (or, in an existing
   checkout, double-click `update-mac.command`). Open `ios/TastingBookShell.xcodeproj` in Xcode.
2. Select the project → TARGETS → **TastingBookShell** → **Signing & Capabilities**. The Team
   (`4RLD77Y492`) and the bundle identifier (`com.mikeyang.whiskey`) are already in the project;
   *Automatically manage signing* is on. Xcode registers the App ID on the first run. Nothing to
   change unless the account changes.
3. Cable the iPhone (a data cable — a charge-only one shows no prompt). Tap *Trust* on the phone.
   Settings → Privacy & Security → **Developer Mode** on; the phone restarts. Pick the phone as
   the run destination at the top of Xcode and press **Run** (Cmd-R).
4. First install from Xcode only: on the phone, Settings → General → VPN & Device Management →
   trust the developer certificate. A TestFlight install never needs this.

Every build runs `copy-web.sh`, which copies `docs/` into the app's `web/` folder, so after a web
change you just press Cmd-R again; nothing is copied by hand and nothing in `ios/` is a second copy
of the page.

## The three acceptance tests, on the phone, in this order

1. **Offline.** Force-quit the app, put the phone in airplane mode, open it. It must open to the
   collection and let you score a pour, with nothing cached and nothing to expire — the files are
   inside the app.
2. **Sign in.** Airplane mode off. **Sync → Sign in**: the system sign-in sheet opens, Microsoft
   signs you in, the sheet closes and the app shows the account. Sync pulls the collection. If it
   fails with a redirect-URI error, the Entra step above was skipped or mistyped.
3. **Round trip.** Score something and Sync; on the PC, the card appears in the OneDrive app
   folder (and in the PC app after Refresh).

## Later updates to the Mac

Finder → the repository folder → double-click `update-mac.command`. It pulls `main` with
`--ff-only --autostash` (so a Team or bundle id picked in Xcode is set aside and put back), prints
the signing lines of the project, and tells you to press Cmd-R. Do not use Xcode's own
Integrate → Pull; it has a habit of loading for ever.

## Seeing the web console

Debug builds set `isInspectable`. With the phone cabled: Safari on the Mac → **Develop** → your
iPhone → **Tasting Book** → the page. That is the page's console, network list and storage.

## TestFlight release (paid account; internal testing needs no App Review; a build lasts 90 days)

Once:

1. https://appstoreconnect.apple.com → **My Apps** → **+** → **New App**: iOS; a name (unique
   across the whole App Store even though this one is never published — have alternatives ready);
   primary language; bundle ID `com.mikeyang.whiskey` (if it is not in the list, run on the phone
   once from Xcode with the paid Team and it will be); any SKU; full access → Create.
2. **Users and Access** → **+** → add your wife by name and Apple ID email, with a role
   (Marketing is enough; she needs to be an App Store Connect user to be an internal tester). She
   accepts from the invitation email. Both phones install the **TestFlight** app from the App Store.
3. In the app record → **TestFlight** tab → **Internal Testing** → **+** → a group → add both of
   you → turn on **automatic distribution**, so every new build reaches the group by itself.

Every release:

1. `update-mac.command`, then in Xcode choose **Any iOS Device (arm64)** as the destination.
2. **Product → Archive**. The Organizer opens when it is done.
3. Select the archive → **Distribute App** → **TestFlight Internal Only** → Distribute. Leave
   *Manage Version and Build Number* ticked: it bumps the build number for you. Wait for *Uploaded*.
4. App Store Connect → the app → TestFlight. The build appears in a few minutes, processing first.
   It should never show *Missing Compliance*: the project writes
   `ITSAppUsesNonExemptEncryption = NO` into Info.plist, because the app does no encryption of its
   own beyond the system's HTTPS. If it does appear anyway, tap Manage → *None of the algorithms
   mentioned above* → Save.
5. The TestFlight app on both phones offers *Tasting Book*. A TestFlight install replaces an Xcode
   install of the same bundle id signed by the same Team, and keeps its data.

## The Xcode Cloud option (so the Mac is never needed for an update)

Xcode Cloud builds only a *shared* scheme. This project's is committed under
`ios/TastingBookShell.xcodeproj/xcshareddata/xcschemes/`; if Xcode ever lists it as not shared,
Product → Scheme → Manage Schemes… → tick **Shared**, and commit that folder.

In Xcode: **Product → Xcode Cloud → Create Workflow**. Grant Xcode Cloud access to the GitHub
repository in the browser when it asks. Workflow: archive for **TestFlight (Internal Testing
Only)**; a post-action **TestFlight Internal Testing** → the internal group; start conditions
**Branch changes** on `main` and **On a schedule**, weekly — the weekly build renews the 90-day
TestFlight clock without anyone touching anything. Let Xcode Cloud manage the build number. No
`ci_scripts` are needed: this project has no dependencies, nothing to install, nothing to
generate; `copy-web.sh` runs inside the build as it does locally. After that, every push to `main`
becomes a TestFlight update on both phones about twenty minutes later.

## Giving the app to a friend

The app needs nothing of yours to run: on first launch it asks where the book should live, and
**On this phone only** needs no Microsoft account and no PC (guide/RUNNING.md, "Sharing it with a
friend"). What a friend needs is a way to install it. Two routes; the first is the one to use for
one or two people.

**Route 1 — add him as an internal tester (no review, about five minutes).** The same thing that
was done for the second phone, and it works with the builds as they are made now.

1. appstoreconnect.apple.com → **Users and Access** → **+**. His name and the email of **his Apple
   ID**; role **Marketing**; under *Apps* tick **only Tasting Book**. Invite.
2. He accepts the email invitation (it asks him to sign in with that Apple ID).
3. The app → **TestFlight** → **Internal Testing** → your group → **Testers +** → tick him.
4. He installs **TestFlight** from the App Store and opens the invitation email on the phone →
   **View in TestFlight** → **Install**. Updates then arrive on their own, including the weekly
   build that renews the 90-day clock.

What it costs: he becomes a user on your App Store Connect team, with the Marketing role, for
this one app. He can see the app's pages there; he cannot see your other apps, your agreements or
your money. Up to 100 people can be added this way.

**Route 2 — external testing (for people who should not be on your team, or more than a few).**
Builds made as *TestFlight Internal Only* **cannot** be given to external testers, so this route
changes how the build is made:

1. Xcode Cloud workflow → the Archive action → *Distribution Preparation*: **App Store Connect**
   instead of *TestFlight (Internal Testing Only)*. (By hand: Organizer → Distribute App →
   **TestFlight & App Store**.) Run a build.
2. TestFlight → **External Testing** → **+** → a group → add that build. Fill in **Test
   Information**: a feedback email, and a description such as: *"A personal whiskey tasting
   scorecard. On first launch choose 'On this phone only' — no account or sign-in is needed. Add a
   bottle under Add, then score it."* Untick **Sign-in required**.
3. **Submit for Review**. The first build of a version waits for Beta App Review, usually a day or
   so; later builds of the same version normally go straight through.
4. Add him by email, or turn on the group's **Public Link** and send it.
5. In the Xcode Cloud workflow add a post-action **TestFlight External Testing** → that group, or
   his copy stops opening when its build turns 90 days old.

Either way his data is his phone's: the app keeps a copy as `tasting-book.json` in Files → On My
iPhone → Tasting Book, and the Data tab exports one.

## Files

- `TastingBookShell/TastingBookShellApp.swift` — entry; the web view full-screen on #12100E
- `AppDelegate.swift` — tells the page when the app comes back to the foreground
- `WebView.swift` — WKWebView, JavaScript dialogs as native alerts, http(s) links in Safari
- `AppSchemeHandler.swift` — serves `web/` as `app://local/`
- `Bridge.swift` — the message router (the protocol is in its header)
- `MicrosoftAuth.swift` — PKCE sign-in in `ASWebAuthenticationSession`, refresh, the keychain
- `BackupStore.swift` — the backup file in Documents, visible in the Files app
- `PrivacyInfo.xcprivacy` — Apple's privacy manifest, declaring the file-timestamp API the backup
  uses; without it every TestFlight upload is flagged ITMS-91053
- `NativeHelpers.swift` — the top-most view controller, for presenting over the page
- `Assets.xcassets` — the icon (a rocks glass on the page's dark background)
- `copy-web.sh` — copies `docs/` into the bundle on every build, leaving out `sw.js`
- `../update-mac.command` — pulls the latest code onto the Mac
- `../tests/test_ios.py` — what can be checked without Xcode: the project file's integrity, the
  settings above, the icon, and that the page and the shell name the same messages

## What the shell deliberately does not do

No notifications, no Siri, no haptics, no share sheet, no App-Bound Domains, no live site: the page
is bundled, so there is no second copy to keep in step and nothing that stops working when the wifi
does. A service worker is not registered inside the app — the bundle is the cache — which is why
`sw.js` stays out of it; the PWA on GitHub Pages keeps using it, and the `VERSION` bump rule in
`CLAUDE.md` still applies to `docs/` changes.

# Setup — do this before the build starts

Roughly 35 minutes across your PC and your iPhone. Nothing here installs a server or costs money.
Work through it in order; Part 3 is the only fiddly one.

---

## Part 1 — Confirm which Microsoft account owns your OneDrive (5 min)

The phone and the PC must be the same account, or the app folder they share will not be the same
folder.

1. Click the OneDrive cloud icon in the Windows system tray.
2. Gear → **Settings** → **Account** tab.
3. Write down the email address shown, and whether it says **OneDrive – Personal** or
   **OneDrive – <something>** (work/school).

**ANSWERED 8 Sep 2026: OneDrive – Personal.** The user has more than one Microsoft account; the
Whiskey File folder lives on the personal one. Everything below assumes that account. The folder is already set
to *Always keep on this device*, so Part 2 is done.

> **The trap, with two accounts:** entra.microsoft.com will sign you in with whichever Microsoft
> account your browser already has a session for — often the wrong one. Before you click
> *New registration*, check the avatar at the top right and confirm it is the **personal** account.
> If it is not, sign out, or open the site in a private window and sign in deliberately.
> A registration created under the wrong account cannot reach the personal OneDrive at all.

---

## Part 2 — Pin the Whiskey File folder to the PC (2 min)

OneDrive's Files On-Demand can leave files as cloud placeholders. The app reading a 147 MB workbook
that is only a placeholder will stall on first open.

1. File Explorer → `C:\Users\<you>\OneDrive\文档\Whiskey File`
2. Right-click the folder → **Always keep on this device**.

While you are there, check you have OneDrive space to spare. The collection workbook is 147 MB and
OneDrive keeps version history of it.

---

## Part 3 — Register the app with Microsoft (15 min)

This is what lets the phone reach OneDrive without a server, and what makes
`Whiskey Collection.xlsx` invisible to the phone.

1. Go to **https://entra.microsoft.com** and sign in with the account from Part 1.
2. Left nav → **Applications** → **App registrations** → **New registration**.
3. **Name:** `Whiskey Tasting Book`
   > This exact name becomes the OneDrive folder name — `OneDrive/Apps/Whiskey Tasting Book`.
   > Renaming the registration later can orphan the data. Get it right now.
4. **Supported account types:** choose **"Personal accounts only"**.

   > **CORRECTED 8 Sep 2026.** This originally said to pick *"Any Entra ID Tenant + Personal
   > Microsoft accounts"*, chosen before we knew which account held the folder. Now that Part 1
   > confirms the data is on the personal OneDrive, the narrower option is better: with two
   > Microsoft accounts in play, "Personal accounts only" makes signing in with the wrong one
   > impossible. It can be changed later under Authentication if that ever needs to widen.

   Note the portal's wording differs from Microsoft's docs. The four options you will see are
   *Single tenant only*, *Multiple Entra ID tenants*, *Any Entra ID Tenant + Personal Microsoft
   accounts*, and *Personal accounts only*. Take the last one.

   Consequence for the build: the MSAL authority becomes
   `https://login.microsoftonline.com/consumers`, not `common`.
5. **Redirect URI:** change the platform dropdown to **Single-page application (SPA)** — **not**
   "Web". SPA is what enables PKCE without a client secret.
   Enter `http://localhost:8765` for now. We add the real hosted URL once it exists.
6. Click **Register**.
7. On the overview page, copy the **Application (client) ID** — a GUID like
   `3f9a2c14-…`. Save it. **This is not a secret**; it is safe to paste to me.
8. Left nav of the registration → **API permissions** → **Add a permission** →
   **Microsoft Graph** → **Delegated permissions** → search for and tick
   **`Files.ReadWrite.AppFolder`** → **Add permissions**.
   - You may see `User.Read` already there. Leave it, it is harmless.
   - No admin consent button is needed for a personal account.
9. **Do not create a client secret.** A single-page app must not have one, and adding one would
   actually break the sign-in flow we are using.

### Two things that are not problems
- **`OneDrive/Apps/Whiskey Tasting Book` will not exist yet.** The app folder is created the first
  time the app writes to it, not when you register. Nothing is wrong if you go looking and it is
  not there.
- **If entra.microsoft.com bounces a personal account**, use https://portal.azure.com instead →
  search "Microsoft Entra ID" → App registrations. Same screens, same result.

### Sanity check
Under **API permissions** you should see exactly `Files.ReadWrite.AppFolder` (and possibly
`User.Read`). If you see `Files.ReadWrite` or `Files.ReadWrite.All`, remove it — those grant access
to your whole drive, including the collection workbook, which is precisely what we are avoiding.

---

## Part 4 — A place to host the phone app (10 min)

Static hosting only. Nothing runs between visits; there is no server to keep alive or patch.

- **Cloudflare Pages** (recommended) or **GitHub Pages**. Both free, both need a GitHub account.
- If you do not have a GitHub account, create one now: https://github.com/signup
- You do **not** need to create the project or repo yet. Just have the account.

---

## Part 5 — iPhone (5 min)

Nothing to install. Three things to check:

1. **iOS up to date** — Settings → General → Software Update.
2. **Use Safari to install the app.** Add to Home Screen is a Safari feature; installing from
   Chrome on iOS does not give you the standalone app.
3. **Settings → Apps → Safari** → confirm **Block All Cookies is OFF**. It breaks Microsoft
   sign-in. *Prevent Cross-Site Tracking* can stay on; it does not affect us.

Optional, only if you want to open the rollup spreadsheet on the phone: the OneDrive app and Excel
for iOS.

---

## Known friction: you will sign in about once a day

Worth knowing before it surprises you. Microsoft caps refresh tokens for browser-based apps at
**24 hours**, and it is not adjustable. On top of that, Safari blocks the hidden-iframe trick that
would otherwise renew silently. So roughly once a day the phone app will ask you to sign in again —
usually a single "Continue as <you>" tap rather than typing a password, because the Microsoft session
cookie lives inside the installed app.

The important part: **this never blocks scoring.** Every scorecard writes to the phone's local
storage the instant you submit it. Sign-in only affects *uploading*. If the token has expired while
you are in a bar with no signal anyway, you score normally, and it all flushes the next time you
open the app with a connection.

If that daily tap turns out to annoy you, the alternative is a small always-on service holding a
long-lived token — which is exactly the thing you said you did not want to run. My read is the tap
is the better trade.

---

## What to send me when you are done

1. The **Application (client) ID** from Part 3 step 7.
2. Whether Part 1 said **Personal** or **work/school**.
3. Which static host you picked, and your GitHub username.

---

## What happens then

I build in the order in SPEC.md §7, and per your standing preference: each step gets saved as it is
finished and tested on its own before the next one starts, so a long build never has to be redone
from scratch and never runs away with your usage.

Step 1 is `collection.py` — load the three tables, print row counts and a duplicate report, and
verify against 144 / 22 / 209. It touches nothing and proves the hardest assumption first.

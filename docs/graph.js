/* Whiskey Tasting Book — OneDrive transport (SPEC.md §9.1).

   Exactly one scope: Files.ReadWrite.AppFolder. That scope is the enforcement, not app discipline
   — it grants access to OneDrive/Apps/<app name>/ and nothing else in the drive, so
   `Whiskey Collection.xlsx` is not merely off-limits to this app, it is invisible to it.

   Signing in is the iPhone app's job (guide/IOS-APP.md): the shell signs in with
   ASWebAuthenticationSession, keeps the tokens in the keychain, and is asked for them over the
   bridge in native.js. Nothing here is on the critical path — the app opens and scores a pour
   with no network at all, and only uploading waits on a token.

   There is no browser sign-in. The page once carried Microsoft's browser library for a web
   version served from GitHub Pages; that version is retired (boot() in app.js), and the library
   went with it. Without the shell every function below answers "not signed in" — which is all a
   desktop browser opening the page for development needs to be told. */
"use strict";

const Graph = (() => {
  let account = null;

  const configured = () => !!(CONFIG.CLIENT_ID && CONFIG.CLIENT_ID.trim());

  /* The shell's sign-in redirect. ASWebAuthenticationSession hands the callback straight back to
     the app, so this is a custom scheme rather than a URL: "msauth." + the iOS bundle id, which
     is com.mikeyang.whiskey. It is registered in Entra as an "iOS / macOS" platform entry
     as msauth.com.mikeyang.whiskey://auth. Rename the bundle and this string and the Entra entry
     must both follow, or sign-in fails with a redirect_uri mismatch. */
  const NATIVE_REDIRECT_SCHEME = "msauth.com.mikeyang.whiskey";

  /* True inside the shell, once it has said it does sign-in. Checked on every call rather than
     once: the hello that fills Native.caps arrives after this script has loaded. */
  const native = () => typeof Native !== "undefined" && Native.caps.has("auth");

  /* The shell reports an account as a display name; who() reads it from this shape. */
  const nativeAccount = (name) => ({ username: name || "Microsoft account" });

  /** Safe to call on boot: resolves to false when not configured, not signed in, or not in the
      app at all. */
  async function resume() {
    if (!configured() || !native()) return false;
    try {
      const r = await Native.request("auth.status");
      account = r.signedIn ? nativeAccount(r.account) : null;
    } catch { account = null; }
    return !!account;
  }

  async function signIn() {
    if (!native()) throw new Error("signing in needs the iPhone app");
    const r = await Native.request("auth.signIn", {
      clientId: CONFIG.CLIENT_ID,
      authority: CONFIG.AUTHORITY,
      scopes: CONFIG.SCOPES,
      redirectScheme: NATIVE_REDIRECT_SCHEME,
    }, { timeout: 0 });                   // a person is typing a password; this one may take minutes
    if (!r.ok) {
      // Cancelling the sheet is not a failure to report; anything else is.
      const e = new Error(r.cancelled ? "sign-in cancelled" : (r.error || "sign-in failed"));
      e.cancelled = !!r.cancelled;
      throw e;
    }
    account = nativeAccount(r.account);
  }

  async function signOut() {
    if (native()) await Native.request("auth.signOut");
    account = null;
  }

  /* One token per burst. The three snapshot files are fetched together, and each token() is a
     round trip to the keychain and sometimes to Microsoft; concurrent callers share the one in
     flight. It is not kept past that — the shell is the one that knows when a token has expired. */
  let tokenInflight = null;
  function token() {
    if (!tokenInflight) tokenInflight = acquireToken().finally(() => { tokenInflight = null; });
    return tokenInflight;
  }

  async function acquireToken() {
    // The queue tells a lapsed sign-in from a dropped connection by this message, and with no
    // shell there is no sign-in to lapse: the answer is the same one.
    if (!native()) { account = null; throw new Error("sign-in required"); }
    const r = await Native.request("auth.token",
      { clientId: CONFIG.CLIENT_ID, authority: CONFIG.AUTHORITY, scopes: CONFIG.SCOPES });
    if (r.ok) return r.token;
    // interactive means the refresh token is gone — expired, revoked, password changed — and
    // only a person can put that right, so the sync screen goes back to offering Sign in.
    // Anything else is the network, and the queue simply waits for the next attempt.
    if (r.interactive) { account = null; throw new Error("sign-in required"); }
    throw new Error(r.error || "could not get a token");
  }

  const url = (path) =>
    `${CONFIG.GRAPH}/me/drive/special/approot:/${path.split("/").map(encodeURIComponent).join("/")}:/content`;
  const folderUrl = (path) =>
    `${CONFIG.GRAPH}/me/drive/special/approot:/${path.split("/").map(encodeURIComponent).join("/")}:/children`
    + "?$select=name,eTag,size,lastModifiedDateTime";

  /** What one folder of the app folder holds, with each file's eTag: one small request that
      says whether any of the published files has changed since the last pull, so an unchanged
      day costs a listing rather than three downloads. Null when the folder is not there yet,
      like getJSON. Paging is not a concern — the snapshot folder holds three files. */
  async function listFolder(path) {
    const t = await token();
    let r;
    try {
      r = await fetch(folderUrl(path), { headers: { Authorization: `Bearer ${t}` } });
    } catch (e) {
      throw new Error(`${e.message || e} — could not reach OneDrive for ${path}/`);
    }
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`${r.status} listing ${path}/`);
    return (await r.json()).value || [];
  }

  /** WebKit reports a blocked or failed request as nothing more than "Load failed"; a Graph
      refusal carries its reason in the body. Both are put into the error, with the path, because
      the Sync screen shows this text and it has to be enough to act on. */
  async function send(path, init) {
    let r;
    try {
      r = await fetch(url(path), init);
    } catch (e) {
      throw new Error(`${e.message || e} — could not reach OneDrive for ${path}`);
    }
    if (!r.ok && r.status !== 404) {
      const body = await r.text().catch(() => "");
      const verb = init.method === "PUT" ? "writing" : "reading";
      throw new Error(`${r.status} ${verb} ${path}${body ? `: ${body.slice(0, 240)}` : ""}`);
    }
    return r;
  }

  async function getJSON(path) {
    const t = await token();
    const r = await send(path, { headers: { Authorization: `Bearer ${t}` } });
    if (r.status === 404) return null;                 // nothing written there yet
    return r.json();
  }

  /** Idempotent by design: a retry after a dropped connection writes the same bytes to the same
      path, so a failed upload can always simply be repeated (SPEC.md §1.3). */
  async function putJSON(path, body) {
    const t = await token();
    const r = await send(path, {
      method: "PUT",
      headers: { Authorization: `Bearer ${t}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.status === 404) throw new Error(`404 writing ${path}: the app folder is missing`);
    return r.json();
  }

  return {
    configured,
    resume,
    signIn,
    signOut,
    getJSON,
    putJSON,
    listFolder,
    isSignedIn: () => !!account,
    who: () => account?.username || null,
  };
})();

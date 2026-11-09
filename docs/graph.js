/* Whiskey Tasting Book — OneDrive transport (SPEC.md §9.1).

   MSAL in the browser, authorization-code flow with PKCE, and exactly one scope:
   Files.ReadWrite.AppFolder. That scope is the enforcement, not app discipline — it grants access
   to OneDrive/Apps/<app name>/ and nothing else in the drive, so `Whiskey Collection.xlsx` is not
   merely off-limits to this app, it is invisible to it.

   Sign-in uses redirect rather than a popup: installed to the home screen there is no browser
   chrome, and popups in a standalone web app are unreliable at best.

   MSAL is loaded on demand. The app shell must open and score a pour with no network at all, so
   nothing here is on the critical path until you actually sign in.

   Inside the native iOS app (guide/IOS-APP.md) the browser library is not used at all: the shell
   signs in with ASWebAuthenticationSession, keeps the tokens in the keychain, and is asked for
   them over the bridge in native.js. That branch is taken only when the shell has said it can
   ("auth" in Native.caps); on GitHub Pages every line below behaves exactly as it did. */
"use strict";

const Graph = (() => {
  let msal = null;
  let client = null;
  let account = null;
  let loading = null;

  const configured = () => !!(CONFIG.CLIENT_ID && CONFIG.CLIENT_ID.trim());

  /* The shell's sign-in redirect. ASWebAuthenticationSession hands the callback straight back to
     the app, so this is a custom scheme rather than a URL: "msauth." + the iOS bundle id, which
     is com.mikeyang.whiskey. It is registered in Entra as an "iOS / macOS" platform entry
     as msauth.com.mikeyang.whiskey://auth. Rename the bundle and this string and the Entra entry
     must both follow, or sign-in fails with a redirect_uri mismatch. */
  const NATIVE_REDIRECT_SCHEME = "msauth.com.mikeyang.whiskey";

  /* True only inside the shell, and only once it has said it does sign-in. Checked on every call
     rather than once: native.js is loaded in the browser too, where caps simply stays empty. */
  const native = () => typeof Native !== "undefined" && Native.caps.has("auth");

  /* The shell reports an account as a display name; MSAL reports one as an object. Both are
     kept in the one shape who() reads. */
  const nativeAccount = (name) => ({ username: name || "Microsoft account" });

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error("could not load the Microsoft sign-in library"));
      document.head.append(s);
    });
  }

  async function ensure() {
    if (native()) throw new Error("MSAL is not used inside the native shell");
    if (client) return client;
    if (!configured()) throw new Error("not configured");
    if (!loading) {
      loading = (async () => {
        if (!window.msal) await loadScript(CONFIG.MSAL_SRC);
        msal = window.msal;
        client = new msal.PublicClientApplication({
          auth: {
            clientId: CONFIG.CLIENT_ID,
            authority: CONFIG.AUTHORITY,
            redirectUri: location.href.split("#")[0].split("?")[0],
          },
          cache: { cacheLocation: "localStorage" },
        });
        await client.initialize();
        const result = await client.handleRedirectPromise();
        if (result?.account) client.setActiveAccount(result.account);
        account = client.getActiveAccount() || client.getAllAccounts()[0] || null;
        if (account) client.setActiveAccount(account);
        return client;
      })();
    }
    return loading;
  }

  /** Safe to call on boot: resolves to false when not configured or not signed in. */
  async function resume() {
    if (!configured()) return false;
    if (native()) {
      try {
        const r = await Native.request("auth.status");
        account = r.signedIn ? nativeAccount(r.account) : null;
      } catch { account = null; }
      return !!account;
    }
    try {
      await ensure();
      return !!account;
    } catch { return false; }
  }

  async function signIn() {
    if (native()) {
      const r = await Native.request("auth.signIn", {
        clientId: CONFIG.CLIENT_ID,
        authority: CONFIG.AUTHORITY,
        scopes: CONFIG.SCOPES,
        redirectScheme: NATIVE_REDIRECT_SCHEME,
      }, { timeout: 0 });                 // a person is typing a password; this one may take minutes
      if (!r.ok) {
        // Cancelling the sheet is not a failure to report; anything else is.
        const e = new Error(r.cancelled ? "sign-in cancelled" : (r.error || "sign-in failed"));
        e.cancelled = !!r.cancelled;
        throw e;
      }
      account = nativeAccount(r.account);
      return;
    }
    await ensure();
    // Refresh tokens for browser apps are capped at 24 hours and Safari blocks the hidden-iframe
    // renewal, so this reappears about once a day. It never blocks scoring — only uploading.
    await client.loginRedirect({ scopes: CONFIG.SCOPES });
  }

  async function signOut() {
    if (native()) {
      await Native.request("auth.signOut");
      account = null;
      return;
    }
    if (!client) return;
    await client.logoutRedirect({ account });
  }

  /* One token per burst. The three snapshot files are fetched together, and inside the shell
     each token() is a round trip to the keychain and sometimes to Microsoft; concurrent callers
     share the one in flight. It is not kept past that — the shell is the one that knows when a
     token has expired, and the browser's MSAL caches its own. */
  let tokenInflight = null;
  function token() {
    if (!tokenInflight) tokenInflight = acquireToken().finally(() => { tokenInflight = null; });
    return tokenInflight;
  }

  async function acquireToken() {
    if (native()) {
      const r = await Native.request("auth.token",
        { clientId: CONFIG.CLIENT_ID, authority: CONFIG.AUTHORITY, scopes: CONFIG.SCOPES });
      if (r.ok) return r.token;
      // interactive means the refresh token is gone — expired, revoked, password changed — and
      // only a person can put that right, so the sync screen goes back to offering Sign in.
      // Anything else is the network, and the queue simply waits for the next attempt.
      if (r.interactive) { account = null; throw new Error("sign-in required"); }
      throw new Error(r.error || "could not get a token");
    }
    await ensure();
    if (!account) throw new Error("not signed in");
    try {
      const r = await client.acquireTokenSilent({ scopes: CONFIG.SCOPES, account });
      return r.accessToken;
    } catch {
      // No redirect from here. A token is asked for by automatic syncs — on boot, on coming back
      // to the page — and a redirect at that moment would navigate away from a half-scored card,
      // which lives only in memory. Forget the account instead, so the Sync screen offers Sign in,
      // and only that button (loginRedirect, above) ever leaves the page.
      account = null;
      throw new Error("sign-in required");
    }
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

  /** Safari reports a blocked or failed request as nothing more than "Load failed"; a Graph
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

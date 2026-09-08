/* Whiskey Tasting Book — OneDrive transport (SPEC.md §9.1).

   MSAL in the browser, authorization-code flow with PKCE, and exactly one scope:
   Files.ReadWrite.AppFolder. That scope is the enforcement, not app discipline — it grants access
   to OneDrive/Apps/<app name>/ and nothing else in the drive, so `Whiskey Collection.xlsx` is not
   merely off-limits to this app, it is invisible to it.

   Sign-in uses redirect rather than a popup: installed to the home screen there is no browser
   chrome, and popups in a standalone web app are unreliable at best.

   MSAL is loaded on demand. The app shell must open and score a pour with no network at all, so
   nothing here is on the critical path until you actually sign in. */
"use strict";

const Graph = (() => {
  let msal = null;
  let client = null;
  let account = null;
  let loading = null;

  const configured = () => !!(CONFIG.CLIENT_ID && CONFIG.CLIENT_ID.trim());

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
    try {
      await ensure();
      return !!account;
    } catch { return false; }
  }

  async function signIn() {
    await ensure();
    // Refresh tokens for browser apps are capped at 24 hours and Safari blocks the hidden-iframe
    // renewal, so this reappears about once a day. It never blocks scoring — only uploading.
    await client.loginRedirect({ scopes: CONFIG.SCOPES });
  }

  async function signOut() {
    if (!client) return;
    await client.logoutRedirect({ account });
  }

  async function token() {
    await ensure();
    if (!account) throw new Error("not signed in");
    try {
      const r = await client.acquireTokenSilent({ scopes: CONFIG.SCOPES, account });
      return r.accessToken;
    } catch {
      await client.acquireTokenRedirect({ scopes: CONFIG.SCOPES, account });
      throw new Error("sign-in required");
    }
  }

  const url = (path) =>
    `${CONFIG.GRAPH}/me/drive/special/approot:/${path.split("/").map(encodeURIComponent).join("/")}:/content`;

  async function getJSON(path) {
    const t = await token();
    const r = await fetch(url(path), { headers: { Authorization: `Bearer ${t}` } });
    if (r.status === 404) return null;                 // nothing written there yet
    if (!r.ok) throw new Error(`${r.status} reading ${path}`);
    return r.json();
  }

  /** Idempotent by design: a retry after a dropped connection writes the same bytes to the same
      path, so a failed upload can always simply be repeated (SPEC.md §1.3). */
  async function putJSON(path, body) {
    const t = await token();
    const r = await fetch(url(path), {
      method: "PUT",
      headers: { Authorization: `Bearer ${t}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${r.status} writing ${path}`);
    return r.json();
  }

  return {
    configured,
    resume,
    signIn,
    signOut,
    getJSON,
    putJSON,
    isSignedIn: () => !!account,
    who: () => account?.username || null,
  };
})();

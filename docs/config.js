/* Whiskey Tasting Book — phone client configuration.

   CLIENT_ID is the Application (client) ID from the Entra app registration in SETUP.md Part 3.
   It is a public identifier, not a secret: any browser-based Microsoft sign-in ships it in plain
   JavaScript, and it is useless on its own — the registration is locked to the
   Files.ReadWrite.AppFolder scope, to personal accounts, and to its own redirect URIs.

   Deployed 9 Sep 2026 against https://rollingshuttle.github.io/whiskey-tasting-book/ , which is
   registered as a Single-page application redirect URI. Changing the Pages address means adding
   the new one there first, or sign-in stops with a redirect_uri mismatch. */
const CONFIG = {
  CLIENT_ID: "f72db714-1e89-40ae-bd15-3d2552d7f64e",
  AUTHORITY: "https://login.microsoftonline.com/consumers",
  SCOPES: ["Files.ReadWrite.AppFolder"],
  MSAL_SRC: "https://cdn.jsdelivr.net/npm/@azure/msal-browser@4/lib/msal-browser.min.js",
  GRAPH: "https://graph.microsoft.com/v1.0",
};

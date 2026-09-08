/* Whiskey Tasting Book — phone client configuration.

   CLIENT_ID is the Application (client) ID from the Entra app registration in SETUP.md Part 3.
   It is a public identifier, not a secret: any browser-based Microsoft sign-in ships it in plain
   JavaScript, and it is useless on its own — the registration is locked to the
   Files.ReadWrite.AppFolder scope, to personal accounts, and to its own redirect URIs.

   It is left blank here on purpose. Fill it in and commit when you are ready to deploy; until
   then the app runs in read-only demo mode against whatever snapshot it has cached. */
const CONFIG = {
  CLIENT_ID: "",
  AUTHORITY: "https://login.microsoftonline.com/consumers",
  SCOPES: ["Files.ReadWrite.AppFolder"],
  MSAL_SRC: "https://cdn.jsdelivr.net/npm/@azure/msal-browser@4/lib/msal-browser.min.js",
  GRAPH: "https://graph.microsoft.com/v1.0",
};

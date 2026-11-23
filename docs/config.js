/* Whiskey Tasting Book — phone client configuration.

   CLIENT_ID is the Application (client) ID from the Entra app registration in guide/SETUP.md Part 3.
   It is a public identifier, not a secret: it is useless on its own — the registration is locked
   to the Files.ReadWrite.AppFolder scope, to personal accounts, and to its own redirect URIs.

   The iPhone app hands the first three to its native sign-in over the bridge (graph.js), so this
   file is the one place they live; GRAPH is where the files are read and written once it has a
   token. */
const CONFIG = {
  CLIENT_ID: "f72db714-1e89-40ae-bd15-3d2552d7f64e",
  AUTHORITY: "https://login.microsoftonline.com/consumers",
  SCOPES: ["Files.ReadWrite.AppFolder"],
  GRAPH: "https://graph.microsoft.com/v1.0",
};

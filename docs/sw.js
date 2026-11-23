/* Whiskey Tasting Book — the last version of the web app's service worker.

   The phone client was once a web app served from GitHub Pages, and this file cached it so it
   opened with no signal. That version is retired: the phone client is the iPhone app
   (guide/IOS-APP.md), which never loads this file — ios/copy-web.sh leaves it out.

   A service worker outlives the page that registered it. An old install would go on serving the
   copy it cached for ever, and that copy can no longer sign in — so anything scored in it would
   sit in a queue with nowhere to go. This worker is what a browser still holding the old one
   fetches in its place: it takes over at once, empties what was cached, removes itself, and
   reloads the page, which then says where the app went (retired() in app.js).

   It exists only for the old address. Once GitHub Pages is switched off, delete it. */
self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) await caches.delete(key);
    await self.registration.unregister();
    for (const client of await self.clients.matchAll({ type: "window" })) client.navigate(client.url);
  })());
});

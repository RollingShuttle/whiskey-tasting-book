/* Bar basements have no signal. The shell is cached so the app opens and scores a pour with no
   connection at all; the collection snapshot is cached separately because it is the one piece of
   data scoring needs (SPEC.md §9.2). Uploads are never cached — they go through the queue. */
const VERSION = "v1";
const SHELL = "shell-" + VERSION;
const SHELL_FILES = [
  "./", "./index.html", "./style.css", "./config.js",
  "./store.js", "./graph.js", "./app.js", "./rubric.json",
  "./manifest.webmanifest", "./icon-180.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;                 // never cache a write
  if (url.origin !== self.location.origin) return;        // Graph and the CDN go straight out

  e.respondWith(
    caches.match(e.request).then((hit) => {
      const live = fetch(e.request).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => hit);
      return hit || live;                                 // cache first, refresh behind
    })
  );
});

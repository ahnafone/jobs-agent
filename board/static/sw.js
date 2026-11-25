/* Applications Board service worker: caches the app shell only. Never caches /api/* (your data). */
const CACHE = "applications-board-shell-v6";
const SHELL = [
  "/", "/index.html", "/app.js", "/app.css", "/features.css", "/tokens.css", "/manifest.webmanifest",
  "/icons/icon.svg", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/api")) return;
  if (!SHELL.includes(url.pathname)) return;
  // Network first so a fresh deploy shows up; fall back to the cached copy when offline.
  event.respondWith(
    fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(url.pathname === "/" ? "/index.html" : url.pathname, copy));
      }
      return res;
    }).catch(() => caches.match(url.pathname === "/" ? "/index.html" : url.pathname))
  );
});

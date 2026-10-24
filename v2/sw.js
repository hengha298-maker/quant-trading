/* Only the bundled PUBLIC FICTIONAL DEMO is cached. No private data or APIs. */
"use strict";

const SCOPE = self.registration.scope;
const PREFIX = `goose-v2-demo:${encodeURIComponent(SCOPE)}:`;
const CACHE = `${PREFIX}20261021-v3`;
const ASSETS = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./model.js",
  "./demo-data.js",
  "./manifest.webmanifest",
  "./assets/icon.svg",
  "./assets/192.png",
  "./assets/512.png",
  "./assets/apple-touch-icon.png",
].map((path) => new URL(path, SCOPE).href);
const ALLOWED = new Set(ASSETS);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(PREFIX) && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  // Hash routes identify screens, not different documents. Some browsers retain
  // the fragment in navigation Request.url, including offline reloads.
  url.hash = "";
  // Query parameters and future /api, holdings, reports, or sync URLs are never cached.
  if (request.method !== "GET" || url.search || !ALLOWED.has(url.href)) return;
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(url.href);
      if (cached) return cached;
      const response = await fetch(request);
      if (response.ok && response.type === "basic")
        await cache.put(url.href, response.clone());
      return response;
    }),
  );
});

// App-shell only. Financial records are never handled by the service worker.
const CACHE = 'goose-v3-shell-0.3.0-research5';
const FILES = ['./', './index.html', './styles.css', './app.js', './domain.js', './io.js', './planning.js', './investment-ui.js', './manifest.webmanifest', './icons/icon.svg', './icons/icon-192.png', './icons/icon-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(FILES)));
  // No skipWaiting: do not replace an application mid-edit.
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key.startsWith('goose-v3-shell-') && key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.search) return;
  const allowed = new Set(FILES.map(path => new URL(path, self.registration.scope).pathname));
  if (!allowed.has(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(event.request, {ignoreVary: true});
    return hit || fetch(event.request);
  })());
});
self.addEventListener('message', event => {
  if (event.data?.type !== 'VERIFY_OFFLINE') return;
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    const results = await Promise.all(FILES.map(file => cache.match(new URL(file, self.registration.scope).href)));
    event.ports[0]?.postMessage({ready: results.every(Boolean), cache: CACHE});
  })());
});

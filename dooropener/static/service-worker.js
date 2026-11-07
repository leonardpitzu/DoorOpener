/* DoorOpener service worker: the keypad shell loads at once, even on a flaky network. */
// The page registers this script as /service-worker.js?v=<version>, so each
// release gets a cache of its own and the previous one is dropped on activation.
const VERSION = new URL(self.location.href).searchParams.get('v') || 'dev';
const CACHE_NAME = 'dooropener-' + VERSION;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.add('/')).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => (key !== CACHE_NAME ? caches.delete(key) : undefined)))
    ).then(() => self.clients.claim())
  );
});

function cacheCopy(req, res) {
  if (res.ok) {
    const copy = res.clone();
    caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
  }
  return res;
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;

  // The keypad page: network first, so it is always current, with an offline fallback.
  if (url.pathname === '/') {
    event.respondWith(fetch(req).then((res) => cacheCopy(req, res)).catch(() => caches.match(req)));
    return;
  }

  // Static assets: cache first. Their URLs carry the version, so a release brings new ones.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => cacheCopy(req, res))));
  }

  // Everything else (/admin/*, /battery, /health, ...) goes straight to the network
  // and is never cached: user lists, audit logs and login state must not linger in
  // Cache Storage or be replayed offline after logout.
});

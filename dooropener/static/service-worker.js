/* DoorOpener Service Worker */
// Bump the version to purge old caches. v3 also evicts admin and API responses
// that earlier versions cached by mistake.
const CACHE_NAME = 'dooropener-cache-v3';
const ASSETS = [
  '/',
  '/static/background.jpg',
  '/static/favicon.png',
  '/static/gear.png',
  '/static/manifest.webmanifest'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
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
  const copy = res.clone();
  caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
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

  // Static assets: cache first.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => cacheCopy(req, res))));
  }

  // Everything else (/admin/*, /battery, /health, ...) goes straight to the network
  // and is never cached: user lists, audit logs and login state must not linger in
  // Cache Storage or be replayed offline after logout.
});

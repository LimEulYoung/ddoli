const CACHE_NAME = 'ddori-v11';
const STATIC_ASSETS = [
  '/',
  '/static/favicon.svg',
  '/static/manifest.json'
];

// Cache static files on install
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// Delete old caches on activate
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

// Network first, cache on failure
self.addEventListener('fetch', (event) => {
  // Don't cache API requests
  if (event.request.url.includes('/chat') ||
      event.request.url.includes('/code') ||
      event.request.url.includes('/stream') ||
      event.request.url.includes('/sessions')) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Update cache on success
        if (response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, clone);
          });
        }
        return response;
      })
      .catch(() => {
        // Return from cache when offline
        return caches.match(event.request);
      })
  );
});

const CACHE_NAME = 'tgdrive-v12';
const STATIC_ASSETS = [
  '/',
  '/static/home.css?v=12.0',
  '/static/js/extra.js?v=12.0',
  '/static/js/apiHandler.js?v=12.0',
  '/static/js/sidebar.js?v=12.0',
  '/static/js/fileClickHandler.js?v=12.0',
  '/static/js/main.js?v=12.0',
  '/static/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Only cache GET requests for static resources, never API or file downloads
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/file') || url.pathname.startsWith('/stream') || url.pathname.startsWith('/downloadZip') || url.pathname.startsWith('/thumbnail')) {
    return;
  }

  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});

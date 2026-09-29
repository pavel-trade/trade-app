// Офлайн-кэш: приложение открывается без интернета после первого запуска.
// Файлы приложения берутся из сети (чтобы сразу получать обновления), без сети — из кэша.
// Запросы к базе (Supabase) не кэшируются.
const CACHE = 'poscalc-v7';
const SHELL = ['./', './index.html', './manifest.webmanifest', './app-icon-180.png', './app-icon-192.png', './app-icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.hostname.endsWith('fonts.googleapis.com') || url.hostname.endsWith('fonts.gstatic.com')) {
    e.respondWith(caches.open(CACHE).then(async c => {
      const hit = await c.match(req);
      const net = fetch(req).then(r => { c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  if (url.origin !== location.origin) return;
  const key = req.mode === 'navigate' ? './index.html' : req;
  e.respondWith(
    fetch(req).then(r => { if (r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put(key, cp)); } return r; })
      .catch(() => caches.match(key, {ignoreSearch: true}))
  );
});

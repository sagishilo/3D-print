// The phone app's service worker (W2-D). It keeps the dashboard's shell (the page, its scripts and styles) so the
// app opens at once and says so when the farm is out of reach. It never keeps the API or the live feed: printers,
// orders and money always come fresh from the farm. Relative paths only, so the app works at any address the farm
// is reached on (the LAN name, its IP, or a Tailscale name) without a rebuild.
const CACHE = 'pf-shell-v1'
const SHELL = ['./', './offline.html', './manifest.webmanifest', './icons/icon-192.png']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()))
})

const live = (url) => url.pathname.startsWith('/api/') || url.pathname === '/ws' || url.pathname === '/ca.crt'

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)
  if (req.method !== 'GET' || url.origin !== self.location.origin || live(url)) return

  if (req.mode === 'navigate') {
    // the page: the farm's newest version when it answers, the kept one (or the offline note) when it does not
    event.respondWith(fetch(req)
      .then((res) => {
        const copy = res.clone()
        caches.open(CACHE).then((c) => c.put('./', copy))
        return res
      })
      .catch(() => caches.match('./').then((r) => r || caches.match('./offline.html'))))
    return
  }

  if (url.pathname.startsWith('/assets/') || url.pathname.startsWith('/icons/')) {
    // built files have their hash in the name: kept once, never stale
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone()
        caches.open(CACHE).then((c) => c.put(req, copy))
      }
      return res
    })))
  }
})

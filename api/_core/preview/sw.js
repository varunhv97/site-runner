// Preview service worker for p-<local id>.<content domain> (scope /). Serves the files /app handed to the
// boot page, kept in this origin's Cache Storage, with the same path rules as shared sites.
// /__sr/ is ours and always goes to the network; nothing under it is served from the site's files.
// Needs lib.js (served in front of this file). SR_SANDBOX is filled in by the server.
const SR_SANDBOX = __SANDBOX_CSP__;
const SR_META = "sr-meta"; // holds /__sr/manifest: {cache, entry, paths, version}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/__sr/")) return;
  if (event.request.method !== "GET" && event.request.method !== "HEAD") return;
  event.respondWith(serve(url));
});

function srHeaders(extra) {
  return new Headers({
    "content-security-policy": SR_SANDBOX,
    "origin-agent-cluster": "?1",
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
    ...extra,
  });
}

function page(status, html) {
  return new Response(html, { status, headers: srHeaders({ "content-type": "text/html; charset=utf-8" }) });
}

const encodePath = (path) => path.split("/").map(encodeURIComponent).join("/");

async function serve(url) {
  try {
    let path;
    try {
      path = url.pathname.slice(1).split("/").map(decodeURIComponent).join("/");
    } catch {
      return page(404, "<h1>404</h1><p>Not found in this site.</p>");
    }
    const meta = await caches.open(SR_META);
    const saved = await meta.match("/__sr/manifest");
    if (!saved) return page(404, "<h1>Preview not loaded</h1><p>Open it again from Site Runner.</p>");
    const manifest = await saved.json();
    const files = new Set(manifest.paths);
    const found = srResolvePath(path, manifest.entry, (p) => files.has(p));
    if (found.redirect) {
      const location = new URL("/" + encodePath(found.redirect) + url.search, self.location.origin).href;
      return new Response(null, { status: found.status, headers: srHeaders({ location }) });
    }
    if (found.missing) return page(404, "<h1>404</h1><p>Not found in this site.</p>");
    const cache = await caches.open(manifest.cache);
    const hit = await cache.match("/__sr/f/" + encodeURIComponent(found.file));
    if (!hit) return page(404, "<h1>404</h1><p>Not found in this site.</p>");
    return new Response(hit.body, { headers: srHeaders({ "content-type": srContentType(found.file) }) });
  } catch (err) {
    return new Response(`Preview error: ${err}`, { status: 500, headers: srHeaders({ "content-type": "text/plain" }) });
  }
}

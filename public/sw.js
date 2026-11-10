// Serves locally stored sites from IndexedDB at /preview/<siteId>/<version>/<path>.
// Schema must match lib/localStore.ts.
const DB_NAME = "site-runner";
const DB_VERSION = 1;

const MIME = {
  html: "text/html; charset=utf-8",
  htm: "text/html; charset=utf-8",
  css: "text/css; charset=utf-8",
  js: "text/javascript; charset=utf-8",
  mjs: "text/javascript; charset=utf-8",
  json: "application/json; charset=utf-8",
  txt: "text/plain; charset=utf-8",
  md: "text/plain; charset=utf-8",
  xml: "application/xml",
  svg: "image/svg+xml",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  avif: "image/avif",
  ico: "image/x-icon",
  woff: "font/woff",
  woff2: "font/woff2",
  ttf: "font/ttf",
  otf: "font/otf",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  mp4: "video/mp4",
  webm: "video/webm",
  wasm: "application/wasm",
  pdf: "application/pdf",
  webmanifest: "application/manifest+json",
};

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin === self.location.origin && url.pathname.startsWith("/preview/")) {
    event.respondWith(handle(url));
  }
});

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("sites")) db.createObjectStore("sites", { keyPath: "id" });
      if (!db.objectStoreNames.contains("files")) db.createObjectStore("files");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function idbGet(db, store, key) {
  return new Promise((resolve, reject) => {
    const req = db.transaction(store).objectStore(store).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

const notFound = () =>
  new Response("<h1>404</h1><p>Not found in this site.</p>", {
    status: 404,
    headers: { "content-type": "text/html; charset=utf-8" },
  });

async function handle(url) {
  const [siteId, n, ...rest] = url.pathname.slice("/preview/".length).split("/");
  const base = `/preview/${siteId}/${n}/`;
  if (rest.length === 0) return Response.redirect(base + url.search, 308);

  let path = rest.map(decodeURIComponent).join("/");
  try {
    const db = await openDb();
    const site = await idbGet(db, "sites", siteId);
    const version = site?.versions[Number(n) - 1];
    if (!version) return notFound();

    if (path === "") {
      if (version.entry.includes("/")) return Response.redirect(base + version.entry, 307);
      path = version.entry;
    } else if (path.endsWith("/")) {
      path += "index.html";
    }

    const hash = version.manifest[path];
    if (!hash) {
      if (version.manifest[`${path}/index.html`]) return Response.redirect(`${base}${path}/`, 308);
      return notFound();
    }
    const blob = await idbGet(db, "files", hash);
    if (!blob) return notFound();
    const ext = path.split(".").pop().toLowerCase();
    return new Response(blob, {
      headers: { "content-type": MIME[ext] || "application/octet-stream", "cache-control": "no-store" },
    });
  } catch (err) {
    return new Response(`Preview error: ${err}`, { status: 500 });
  }
}

// Shared by the preview boot page and service worker, served on p-<local id>.<content domain>/__sr/.
// srResolvePath must match api/_core/paths.py: tests/path_rules.json has the cases both pass
// (tests/test_content.py for the server, tests/path_rules.test.mjs for this file).

const SR_MIME = {
  html: "text/html; charset=utf-8",
  htm: "text/html; charset=utf-8",
  css: "text/css; charset=utf-8",
  js: "text/javascript; charset=utf-8",
  mjs: "text/javascript; charset=utf-8",
  json: "application/json; charset=utf-8",
  txt: "text/plain; charset=utf-8",
  md: "text/plain; charset=utf-8",
  csv: "text/csv; charset=utf-8",
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
  ogg: "audio/ogg",
  mp4: "video/mp4",
  webm: "video/webm",
  wasm: "application/wasm",
  pdf: "application/pdf",
  webmanifest: "application/manifest+json",
};

function srContentType(path) {
  const name = path.split("/").pop();
  const dot = name.lastIndexOf(".");
  return (dot > 0 && SR_MIME[name.slice(dot + 1).toLowerCase()]) || "application/octet-stream";
}

// path: relative to the site's base, URL-decoded. has(path) -> bool.
// Returns {file} | {redirect (relative to the base), status} | {missing: true}.
function srResolvePath(path, entry, has) {
  if (path === "") {
    if (entry.includes("/")) return { redirect: entry, status: 307 }; // 307: the start page can change
    path = entry;
  } else if (path.endsWith("/")) {
    path += "index.html";
  }
  if (has(path)) return { file: path };
  if (has(`${path}/index.html`)) return { redirect: `${path}/`, status: 308 };
  return { missing: true };
}

if (typeof module !== "undefined") module.exports = { SR_MIME, srContentType, srResolvePath };

"""Saved storage for shared sites, without giving them this app's origin.

/s/<ref>/page.html returns a wrapper page (this app's origin, our code only) that frames
/r/<ref>/page.html. The framed page keeps the sandbox CSP (opaque origin), and gets a small
script that runs first: it puts an in-memory localStorage in place, seeded by the wrapper, and
sends every write back up. The wrapper keeps each site's data in its own localStorage under
the site's id.

Rules (tested in tests/test_frames.py and by hand in headless Chrome):
- The wrapper only listens to its own frame, and only while that frame has the opaque origin ("null").
- The wrapper picks the storage key from the server-rendered site id; the framed page can't name another.
- A framed wrapper (window.top !== window) never sends data: a page that frames it could otherwise
  navigate the inner frame to its own sandboxed page and ask for the seed.
- Saved data only ever reaches the sandboxed frame, never the wrapper's own document.
- Each site is capped at STORE_LIMIT characters, and all sites together at STORE_TOTAL_LIMIT, so shared
  sites can't fill the app origin's quota.
"""

import json
import secrets

STORE_LIMIT = 512 * 1024  # characters (keys + values), the same unit browsers count in
# All shared sites together, so between them they can't fill this app's own localStorage (~5M characters).
STORE_TOTAL_LIMIT = 2 * 1024 * 1024


def _js(value) -> str:
    """JSON safe to put inside a <script> element."""
    return json.dumps(value).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


_WRAPPER_JS = r"""
(() => {
  const C = __CONFIG__;
  const KEY = "sr-site-data:" + C.site;
  const framed = window.top !== window;
  const frame = document.getElementById("site");

  const load = () => {
    try {
      const d = JSON.parse(localStorage.getItem(KEY));
      return d && typeof d === "object" && !Array.isArray(d) ? d : {};
    } catch {
      return {};
    }
  };
  const size = (d) => Object.entries(d).reduce((n, [k, v]) => n + k.length + v.length, 0);
  // Read, change, write on every message, so two open tabs merge per key like real localStorage.
  // Characters every other shared site keeps in this origin's localStorage.
  const others = () => {
    let n = 0;
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith("sr-site-data:") && k !== KEY) n += k.length + (localStorage.getItem(k) || "").length;
    }
    return n;
  };
  const write = (change) => {
    const d = load();
    change(d);
    if (size(d) > C.limit) return;
    try {
      const json = JSON.stringify(d);
      if (others() + KEY.length + json.length > C.total) {
        console.warn("Shared sites' saved data is full in this browser; this change wasn't saved.");
        return;
      }
      localStorage.setItem(KEY, json);
    } catch {
      /* storage unavailable or full: the site keeps working, it just won't persist */
    }
  };

  window.addEventListener("message", (e) => {
    if (e.source !== frame.contentWindow || e.origin !== "null") return;
    const m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.sr === "hello") {
      frame.contentWindow.postMessage({ sr: "seed", data: framed ? null : load() }, "*");
    } else if (m.sr === "set" && !framed && typeof m.key === "string" && typeof m.value === "string") {
      write((d) => (d[m.key] = m.value));
    } else if (m.sr === "remove" && !framed && typeof m.key === "string") {
      write((d) => delete d[m.key]);
    } else if (m.sr === "clear" && !framed) {
      write((d) => Object.keys(d).forEach((k) => delete d[k]));
    } else if (m.sr === "nav" && typeof m.path === "string" && typeof m.hash === "string") {
      // Mirror the frame's address into ours, but only within this site.
      const u = new URL(m.path, location.origin);
      if (u.origin === location.origin && u.pathname.startsWith(C.frameBase) && (!m.hash || m.hash[0] === "#")) {
        history.replaceState(null, "", C.base + u.pathname.slice(C.frameBase.length) + u.search + m.hash);
      }
      if (typeof m.title === "string") document.title = m.title.slice(0, 200) || "Shared site";
      // The tab shows the top page's icon, so carry the site's own icon up (web or data: images only).
      if (typeof m.icon === "string" && m.icon.length < 65536 && /^(https?:|data:image\/)/i.test(m.icon)) {
        let link = document.querySelector("link[rel='icon']");
        if (!link) {
          link = document.createElement("link");
          link.rel = "icon";
          document.head.appendChild(link);
        }
        if (link.href !== m.icon) link.href = m.icon;
      }
    }
  });
  // Someone edited the #hash in the address bar: pass it down.
  window.addEventListener("hashchange", () => frame.contentWindow.postMessage({ sr: "hash", hash: location.hash }, "*"));
  frame.src = C.frame + location.search + location.hash;
})();
"""

_WRAPPER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shared site</title>
<style nonce="__NONCE__">html,body{margin:0;height:100%;overflow:hidden}iframe{display:block;border:0;width:100%;height:100%}</style>
</head>
<body>
<iframe id="site" title="Shared site" sandbox="__SANDBOX__" allow="fullscreen; clipboard-write"></iframe>
<script nonce="__NONCE__">__SCRIPT__</script>
</body>
</html>
"""

# Runs first inside the sandboxed page, then writes the original page into the document.
_FRAME_JS = r"""
(() => {
  const PAGE = __PAGE__;
  const BASE = __BASE__;
  const LIMIT = __LIMIT__;
  const embedded = window.parent !== window;
  let bridged = false;
  let started = false;

  const send = (m) => bridged && window.parent.postMessage(m, "*");

  function makeStorage(seed, persist) {
    const map = new Map(Object.entries(seed || {}).filter(([, v]) => typeof v === "string"));
    let used = 0;
    map.forEach((v, k) => (used += k.length + v.length));
    const api = {
      get length() {
        return map.size;
      },
      key: (i) => [...map.keys()][i] ?? null,
      getItem: (k) => (map.has(String(k)) ? map.get(String(k)) : null),
      setItem(k, v) {
        k = String(k);
        v = String(v);
        const next = used - (map.has(k) ? k.length + map.get(k).length : 0) + k.length + v.length;
        if (next > LIMIT) throw new DOMException("This site's saved data is full.", "QuotaExceededError");
        map.set(k, v);
        used = next;
        if (persist) send({ sr: "set", key: k, value: v });
      },
      removeItem(k) {
        k = String(k);
        if (!map.has(k)) return;
        used -= k.length + map.get(k).length;
        map.delete(k);
        if (persist) send({ sr: "remove", key: k });
      },
      clear() {
        map.clear();
        used = 0;
        if (persist) send({ sr: "clear" });
      },
    };
    // Named access (localStorage.theme = "dark") works on real Storage, so support it too.
    return new Proxy(api, {
      get: (t, p) => (p in t || typeof p !== "string" ? t[p] : map.has(p) ? map.get(p) : undefined),
      set: (t, p, v) => (p in t || typeof p !== "string" ? false : (t.setItem(p, v), true)),
      deleteProperty: (t, p) => (typeof p === "string" && t.removeItem(p), true),
      has: (t, p) => p in t || map.has(p),
      ownKeys: () => [...map.keys()],
      getOwnPropertyDescriptor: (t, p) =>
        map.has(p) ? { value: map.get(p), enumerable: true, configurable: true, writable: true } : undefined,
    });
  }

  function report() {
    const icon = document.querySelector("link[rel~='icon']");
    send({
      sr: "nav",
      path: location.pathname + location.search,
      hash: location.hash,
      title: document.title,
      icon: icon ? icon.href : "",
    });
  }

  function start(data) {
    if (started) return;
    started = true;
    bridged = data != null;
    for (const [name, store] of [["localStorage", makeStorage(data, true)], ["sessionStorage", makeStorage({}, false)]]) {
      try {
        Object.defineProperty(window, name, { configurable: true, enumerable: true, get: () => store });
      } catch {
        /* leave the browser's own (throwing) storage in place */
      }
    }
    document.open();
    document.write(PAGE);
    document.close();
    // document.open() dropped earlier listeners, so add ours now.
    window.addEventListener("message", (e) => {
      if (e.source === window.parent && e.data && e.data.sr === "hash" && e.data.hash !== location.hash) {
        location.hash = e.data.hash;
      }
    });
    window.addEventListener("hashchange", report);
    window.addEventListener("popstate", report);
    window.addEventListener("load", report);
    for (const fn of ["pushState", "replaceState"]) {
      const orig = history[fn].bind(history);
      history[fn] = (...args) => {
        orig(...args);
        report();
      };
    }
    // Links that leave this site open in the whole tab, as they would outside the frame.
    document.addEventListener("click", (e) => {
      const a = e.target instanceof Element && e.target.closest("a[href]");
      if (!a || a.target || e.defaultPrevented) return;
      const u = new URL(a.href, location.href);
      if (u.protocol + "//" + u.host !== location.protocol + "//" + location.host || !u.pathname.startsWith(BASE)) {
        a.target = "_top";
      }
    });
    report();
  }

  if (!embedded) return start(null);
  window.addEventListener("message", function onSeed(e) {
    if (e.source !== window.parent || !e.data || e.data.sr !== "seed") return;
    window.removeEventListener("message", onSeed);
    start(e.data.data && typeof e.data.data === "object" ? e.data.data : null);
  });
  window.parent.postMessage({ sr: "hello" }, "*");
  setTimeout(() => start(null), 3000); // no wrapper answered: run without saving
})();
"""


def wrapper_page(site_id: str, ref: str, path: str, sandbox: str) -> tuple[bytes, str]:
    """The /s/ page for an HTML path. Returns (html, nonce for the CSP)."""
    nonce = secrets.token_urlsafe(18)
    config = {
        "site": site_id,
        "base": f"/s/{ref}/",
        "frameBase": f"/r/{ref}/",
        "frame": f"/r/{ref}/{path}",
        "limit": STORE_LIMIT,
        "total": STORE_TOTAL_LIMIT,
    }
    html = (
        _WRAPPER_HTML.replace("__NONCE__", nonce)
        .replace("__SANDBOX__", sandbox)
        .replace("__SCRIPT__", _WRAPPER_JS.replace("__CONFIG__", _js(config)))
    )
    return html.encode(), nonce


def frame_page(page: bytes, ref: str) -> bytes:
    """The /r/ version of an HTML file: the storage script, which then writes the original page."""
    # The page goes in last, so placeholder-like text inside it is never replaced.
    script = (
        _FRAME_JS.replace("__BASE__", _js(f"/r/{ref}/"))
        .replace("__LIMIT__", str(STORE_LIMIT))
        .replace("__PAGE__", _js(page.decode("utf-8", errors="replace")))
    )
    return f'<!doctype html><meta charset="utf-8"><script>{script}</script>'.encode()

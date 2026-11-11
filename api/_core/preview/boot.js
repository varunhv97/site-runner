// Preview boot page (p-<local id>.<content domain>/__sr/boot). Our code only; the site never runs here.
// 1. register the preview service worker and wait until it controls this page
// 2. tell /app (our parent frame, or the tab that opened us) we're ready, only at the app's exact origin
// 3. take the files from /app only (exact origin and window), store them in Cache Storage, open the site
// Needs lib.js (inlined in front of this file). SR_APP is filled in by the server.
(() => {
  const SR_APP = __APP_ORIGIN__;
  const SR_META = "sr-meta";
  const peer = window.parent !== window ? window.parent : window.opener;
  const status = document.getElementById("status");
  const show = (text) => (status.textContent = text);
  const say = (message) => peer && peer.postMessage(message, SR_APP);
  const fail = (reason) => {
    show(reason);
    say({ sr: "error", reason });
  };
  let done = false;

  const controlled = () =>
    navigator.serviceWorker.controller
      ? Promise.resolve()
      : new Promise((resolve) => navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true }));

  async function receive(event) {
    if (done || event.origin !== SR_APP || event.source !== peer) return;
    const data = event.data;
    if (!data || data.sr !== "files" || typeof data.entry !== "string" || !data.files) return;
    done = true;
    try {
      const paths = Object.keys(data.files).filter((p) => data.files[p] instanceof ArrayBuffer);
      if (!paths.includes(data.entry)) throw new Error("The start page is missing from the files.");
      const name = `sr-files-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const cache = await caches.open(name);
      for (const p of paths) {
        const headers = { "content-type": srContentType(p) };
        await cache.put("/__sr/f/" + encodeURIComponent(p), new Response(data.files[p], { headers }));
      }
      const manifest = { cache: name, entry: data.entry, paths, version: Number(data.version) || null };
      const meta = await caches.open(SR_META);
      await meta.put("/__sr/manifest", new Response(JSON.stringify(manifest), { headers: { "content-type": "application/json" } }));
      for (const key of await caches.keys()) {
        if (key.startsWith("sr-files-") && key !== name) await caches.delete(key);
      }
      say({ sr: "loaded" });
      window.opener = null; // the previewed site gets no handle on the app's tab
      location.replace("/");
    } catch (err) {
      fail(`The preview couldn't load: ${(err && err.message) || err}`);
    }
  }

  async function start() {
    if (!peer) return show("Open this preview from Site Runner.");
    if (!("serviceWorker" in navigator)) return fail("This browser doesn't allow service workers here.");
    try {
      await navigator.serviceWorker.register("/__sr/sw.js", { scope: "/" });
      await controlled();
    } catch (err) {
      return fail(`This browser blocked the preview's service worker: ${(err && err.message) || err}`);
    }
    window.addEventListener("message", receive);
    say({ sr: "ready" });
  }

  start();
})();

// Retired. Local previews used to run here, at /preview/, on the app's own origin. They now run on their own
// origin (p-<site id>.<content domain>, see lib/preview.ts). Browsers that still have the old worker fetch this
// file on their next update check: it removes itself and reloads any page it was controlling.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      await self.registration.unregister();
      const pages = await self.clients.matchAll({ type: "window" });
      for (const page of pages) page.navigate(page.url);
    })(),
  );
});

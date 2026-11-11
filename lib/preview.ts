// Local previews run on their own origin, https://p-<local id>.<content domain>, never on this app's origin:
// a previewed site can't reach this app's IndexedDB (edit tokens), cookies or API.
// The preview's boot page (served by the API on that origin) registers a service worker, says "ready", and
// gets the version's files from here by postMessage. The files never leave the browser.
import { readVersionBuffers, type LocalVersion } from "./localStore";

export type ContentConfig = { content_scheme: string; content_domain: string };

let config: Promise<ContentConfig> | null = null;

/** Where user content lives (GET /api/config), fetched once. */
export function contentConfig(): Promise<ContentConfig> {
  config ??= fetch("/api/config")
    .then(async (res) => {
      if (!res.ok) throw new Error("Previews aren't set up on this server (USERCONTENT_BASE_DOMAIN).");
      return (await res.json()) as ContentConfig;
    })
    .catch((e) => {
      config = null;
      throw e instanceof Error && e.message.includes("USERCONTENT") ? e : new Error("Share server unreachable. Is the API running?");
    });
  return config;
}

export function previewOrigin(cfg: ContentConfig, localId: string): string {
  if (!/^[0-9a-f]{12}$/.test(localId)) throw new Error("This site's id can't be previewed.");
  return `${cfg.content_scheme}://p-${localId}.${cfg.content_domain}`;
}

export const bootUrl = (origin: string) => `${origin}/__sr/boot`;

type BootMessage = { sr?: string; reason?: string };

/** Hand a version's files to the boot page in `target()` (the preview iframe, or a tab we opened).
 * Only messages from that exact window and origin count, and the files go to that origin only.
 * Resolves once the preview has its files; rejects when the boot page reports an error or says nothing
 * within `readyMs` (Safari can block service workers in frames). */
export function handOff(
  target: () => Window | null | undefined,
  origin: string,
  version: LocalVersion,
  readyMs = 8000,
): { done: Promise<void>; cancel: () => void } {
  let finish: (err?: Error) => void = () => {};
  const done = new Promise<void>((resolve, reject) => {
    let sent = false;
    let timer = setTimeout(() => finish(new Error("The preview didn't start.")), readyMs);
    const onMessage = async (event: MessageEvent) => {
      const win = target();
      if (!win || event.source !== win || event.origin !== origin) return;
      const data = (event.data ?? {}) as BootMessage;
      if (data.sr === "ready" && !sent) {
        sent = true;
        clearTimeout(timer);
        timer = setTimeout(() => finish(new Error("The preview didn't load its files.")), 60000);
        try {
          const files = await readVersionBuffers(version);
          // only what the preview needs: no ids, tokens or account details
          win.postMessage({ sr: "files", version: version.n, entry: version.entry, files }, origin, Object.values(files));
        } catch (e) {
          finish(e instanceof Error ? e : new Error(String(e)));
        }
      } else if (data.sr === "loaded") {
        finish();
      } else if (data.sr === "error") {
        finish(new Error(data.reason || "The preview couldn't start."));
      }
    };
    let settled = false;
    finish = (err?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      if (err) reject(err);
      else resolve();
    };
    window.addEventListener("message", onMessage);
  });
  done.catch(() => {}); // callers that cancel don't wait on it
  return { done, cancel: () => finish() };
}

/** Open the preview in a new tab (top level: its own storage, and service workers work in every browser).
 * The tab keeps its opener until it has the files; the boot page then drops it. */
export function openPreviewTab(origin: string, version: LocalVersion): Promise<void> {
  const tab = window.open(bootUrl(origin), "_blank");
  if (!tab) return Promise.reject(new Error("The new tab was blocked. Allow pop-ups for this site and try again."));
  return handOff(() => tab, origin, version, 20000).done;
}

/** Things the old same-origin preview and the localStorage bridge left in this origin. */
export function cleanUpOldPreview() {
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (key?.startsWith("sr-site-data:")) localStorage.removeItem(key);
    }
  } catch {
    // storage blocked: nothing to clean
  }
  navigator.serviceWorker
    ?.getRegistrations()
    .then((regs) => regs.forEach((r) => r.unregister()))
    .catch(() => {});
}

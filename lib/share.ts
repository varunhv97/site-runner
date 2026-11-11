// Talks to the Python share API (/api/*).
import { zipSync } from "fflate";
import type { FileMap } from "./ingest";
import { readVersionFiles, type LocalSite, type LocalVersion } from "./localStore";

const MAX_ZIP = 4 * 1024 * 1024;

type RemoteSite = { id: string; slug: string | null; live: number | null; versions: { n: number }[] };

/** A suggestion for a link name from a site's name: "Trail Map v2!" -> "trail-map-v2". */
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/, "");
}

/** Lower-cases and swaps spaces as you type, without trimming hyphens mid-word. */
export const normalizeSlug = (v: string) => v.toLowerCase().replace(/[\s_]+/g, "-").replace(/[^a-z0-9-]/g, "");

/** null when the name can be used, else why not. Blank is fine: the site gets a random link. */
export async function checkSlug(slug: string, current?: string | null): Promise<string | null> {
  if (!slug || slug === current) return null;
  try {
    const r = await call<{ available: boolean; reason: string | null }>(`/api/slugs/${encodeURIComponent(slug)}`, {});
    return r.available ? null : r.reason;
  } catch {
    return null; // let the server decide on submit
  }
}

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

// The browser remembers which shared site a local site is linked to. That site can disappear
// (unpublished elsewhere, taken down, or created before a storage move): the server then says 404.
const isGone = (e: unknown) => e instanceof ApiError && e.status === 404;

async function call<T>(url: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new Error("Share server unreachable. Is the API running?");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(body.error ?? `Request failed (${res.status})`, res.status);
  return body as T;
}

async function callExisting<T>(url: string, init: RequestInit): Promise<T> {
  try {
    return await call<T>(url, init);
  } catch (e) {
    if (isGone(e)) throw new Error("This shared site no longer exists. Click Unpublish to clear it, then share again.");
    throw e;
  }
}

/** Upload a local version. Creates the remote site on first share. Returns the updated site. */
export async function shareVersion(original: LocalSite, version: LocalVersion, slug?: string): Promise<LocalSite> {
  const site = structuredClone(original);
  const zip = zipSync(await readVersionFiles(version), { level: 6 });
  if (zip.byteLength > MAX_ZIP) throw new Error("This version is over 4MB zipped, too large to share.");

  const form = new FormData();
  form.append("file", new Blob([zip as BlobPart], { type: "application/zip" }), "site.zip");
  form.append("note", version.note || `local v${version.n}`);
  form.append("entry", version.entry); // the start page picked here, so shared links open the same page

  if (site.remote) {
    try {
      const r = await call<{ site: RemoteSite }>(`/api/sites/${site.remote.siteId}/versions`, {
        method: "POST",
        body: form,
        headers: { Authorization: `Bearer ${site.remote.token}` },
      });
      site.remote.live = r.site.live;
      site.remote.pushed[version.n] = r.site.live!;
      return site;
    } catch (e) {
      if (!isGone(e)) throw e;
      delete site.remote; // the shared copy is gone: share this version as a new site below
    }
  }
  form.append("name", site.name);
  if (slug) form.append("slug", slug);
  const r = await call<{ site: RemoteSite; edit_token: string; url: string }>("/api/sites", {
    method: "POST",
    body: form,
  });
  site.remote = { siteId: r.site.id, slug: r.site.slug, token: r.edit_token, url: r.url, live: r.site.live, pushed: {} };
  site.remote.pushed[version.n] = r.site.live!;
  return site;
}

export async function setRemoteLive(original: LocalSite, remoteN: number): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  const r = await callExisting<{ site: RemoteSite }>(`/api/sites/${site.remote.siteId}/live`, {
    method: "POST",
    headers: { Authorization: `Bearer ${site.remote.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ version: remoteN }),
  });
  site.remote.live = r.site.live;
  return site;
}

/** Give the shared site a new link name. Its earlier links keep working and forward to the new one. */
export async function setSlug(original: LocalSite, slug: string): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  const r = await callExisting<{ site: RemoteSite; url: string }>(`/api/sites/${site.remote.siteId}/slug`, {
    method: "POST",
    headers: { Authorization: `Bearer ${site.remote.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ slug }),
  });
  site.remote.url = r.url;
  site.remote.slug = r.site.slug;
  return site;
}

/** Unpublish the shared copy: its links stop working. The local site and its versions stay. */
export async function unpublish(original: LocalSite): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  try {
    await call(`/api/sites/${site.remote.siteId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${site.remote.token}` },
    });
  } catch (e) {
    if (!isGone(e)) throw e; // already gone: forgetting the link is all that's left to do
  }
  delete site.remote;
  return site;
}

/** Delete one shared version that isn't live: its pinned link (@n) stops working and the number is never
 * reused. The local version stays in this browser and can be published again (as a new number). */
export async function deleteRemoteVersion(original: LocalSite, remoteN: number): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  try {
    const r = await call<{ site: RemoteSite }>(`/api/sites/${site.remote.siteId}/versions/${remoteN}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${site.remote.token}` },
    });
    site.remote.live = r.site.live;
  } catch (e) {
    if (!isGone(e)) throw e;
    // "no such version": already deleted (elsewhere). Any other 404 means the whole shared site is gone.
    if ((e as ApiError).message !== "no such version") {
      throw new Error("This shared site no longer exists. Click Unpublish to clear it, then share again.");
    }
  }
  for (const [localN, n] of Object.entries(site.remote.pushed)) {
    if (n === remoteN) delete site.remote.pushed[Number(localN)];
  }
  return site;
}

/** Replace the edit token, for when it has leaked: the old one stops working. */
export async function rotateToken(original: LocalSite): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  const r = await callExisting<{ edit_token: string }>(`/api/sites/${site.remote.siteId}/token`, {
    method: "POST",
    headers: { Authorization: `Bearer ${site.remote.token}` },
  });
  site.remote.token = r.edit_token;
  return site;
}

export function pinnedUrl(site: LocalSite, remoteN: number): string {
  return site.remote!.url.replace(/\/$/, `@${remoteN}/`);
}

export type Me = { enabled: boolean; user: { email: string; name: string } | null };

export async function fetchMe(): Promise<Me> {
  try {
    const res = await fetch("/api/auth/me");
    if (!res.ok) return { enabled: false, user: null };
    return (await res.json()) as Me;
  } catch {
    return { enabled: false, user: null };
  }
}

export async function signOut(): Promise<void> {
  await call("/api/auth/logout", { method: "POST" });
}

/** Storage the signed-in account's shared sites use (unique files), and its quota. null when signed out. */
export type Usage = { used_bytes: number; quota_bytes: number };

export async function fetchUsage(): Promise<Usage | null> {
  try {
    const res = await fetch("/api/me/usage");
    return res.ok ? ((await res.json()) as Usage) : null;
  } catch {
    return null;
  }
}

export const signInUrl = "/api/auth/login?next=/app";

/** Change the page a shared version opens on. */
export async function setRemoteEntry(site: LocalSite, remoteN: number, entry: string): Promise<void> {
  if (!site.remote) return;
  await callExisting(`/api/sites/${site.remote.siteId}/versions/${remoteN}/entry`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${site.remote.token}` },
    body: JSON.stringify({ entry }),
  });
}

/** A site the signed-in account shared, as /api/me/sites lists it. */
export type AccountSite = { id: string; slug: string | null; name: string; live: number | null; url: string };

export async function fetchAccountSites(): Promise<AccountSite[]> {
  try {
    return (await call<{ sites: AccountSite[] }>("/api/me/sites", {})).sites;
  } catch {
    return [];
  }
}

/** The live version's original files and start page, for its owner to open in this browser. */
export async function downloadLiveVersion(site: AccountSite): Promise<{ files: FileMap; entry: string; n: number }> {
  const n = site.live!;
  const v = await call<{ entry: string; files: Record<string, string> }>(`/api/sites/${site.id}/versions/${n}/files`, {});
  const byDigest = new Map<string, Promise<Uint8Array>>(); // a file used twice downloads once
  const get = (digest: string, path: string) => {
    if (!byDigest.has(digest)) {
      byDigest.set(
        digest,
        fetch(`/api/sites/${site.id}/files/${digest}`).then(async (res) => {
          if (!res.ok) throw new Error(`Couldn't download ${path} (${res.status}).`);
          return new Uint8Array(await res.arrayBuffer());
        }),
      );
    }
    return byDigest.get(digest)!;
  };
  const files: FileMap = {};
  const entries = Object.entries(v.files);
  for (let i = 0; i < entries.length; i += 6) {
    await Promise.all(entries.slice(i, i + 6).map(async ([path, digest]) => (files[path] = await get(digest, path))));
  }
  return { files, entry: v.entry, n };
}

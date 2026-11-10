// Talks to the Python share API (/api/*).
import { zipSync } from "fflate";
import { readVersionFiles, type LocalSite, type LocalVersion } from "./localStore";

const MAX_ZIP = 4 * 1024 * 1024;

type RemoteSite = { id: string; live: number | null; versions: { n: number }[] };

async function call<T>(url: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new Error("Share server unreachable. Is the API running?");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error ?? `Request failed (${res.status})`);
  return body as T;
}

/** Upload a local version. Creates the remote site on first share. Returns the updated site. */
export async function shareVersion(original: LocalSite, version: LocalVersion): Promise<LocalSite> {
  const site = structuredClone(original);
  const zip = zipSync(await readVersionFiles(version), { level: 6 });
  if (zip.byteLength > MAX_ZIP) throw new Error("This version is over 4MB zipped, too large to share.");

  const form = new FormData();
  form.append("file", new Blob([zip as BlobPart], { type: "application/zip" }), "site.zip");
  form.append("note", version.note || `local v${version.n}`);

  if (!site.remote) {
    form.append("name", site.name);
    const r = await call<{ site: RemoteSite; edit_token: string; url: string }>("/api/sites", {
      method: "POST",
      body: form,
    });
    site.remote = { siteId: r.site.id, token: r.edit_token, url: r.url, live: r.site.live, pushed: {} };
    site.remote.pushed[version.n] = r.site.live!;
  } else {
    const r = await call<{ site: RemoteSite }>(`/api/sites/${site.remote.siteId}/versions`, {
      method: "POST",
      body: form,
      headers: { Authorization: `Bearer ${site.remote.token}` },
    });
    site.remote.live = r.site.live;
    site.remote.pushed[version.n] = r.site.live!;
  }
  return site;
}

export async function setRemoteLive(original: LocalSite, remoteN: number): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  const r = await call<{ site: RemoteSite }>(`/api/sites/${site.remote.siteId}/live`, {
    method: "POST",
    headers: { Authorization: `Bearer ${site.remote.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ version: remoteN }),
  });
  site.remote.live = r.site.live;
  return site;
}

/** Unpublish the shared copy: its links stop working. The local site and its versions stay. */
export async function unpublish(original: LocalSite): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  await call(`/api/sites/${site.remote.siteId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${site.remote.token}` },
  });
  delete site.remote;
  return site;
}

/** Replace the edit token, for when it has leaked: the old one stops working. */
export async function rotateToken(original: LocalSite): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  const r = await call<{ edit_token: string }>(`/api/sites/${site.remote.siteId}/token`, {
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

export const signInUrl = "/api/auth/login?next=/app";

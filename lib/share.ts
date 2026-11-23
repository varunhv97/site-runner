// Talks to the Python share API (/api/*).
import { zipSync } from "fflate";
import { executableMessage, findExecutable } from "./executables";
import type { FileMap } from "./ingest";
import { addVersion, readVersionFiles, type LocalSite, type LocalVersion, type RemoteLink } from "./localStore";

const MAX_ZIP = 4 * 1024 * 1024;

type RemoteSite = { id: string; slug: string | null; live: number | null; versions: { n: number }[] };
/** Share responses say whether the site is held for a safety review (its links answer 451 until approved). */
type HoldStatus = { held?: boolean; reasons?: string[] };

/** The edit token when this browser has one; otherwise the signed-in session (owner or member) is what counts. */
const tokenHeader = (remote: RemoteLink): Record<string, string> =>
  remote.token ? { Authorization: `Bearer ${remote.token}` } : {};

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

/** A publish the server refused with 409: a newer version went live after `base` (or two publishes raced). */
export const isConflict = (e: unknown) => e instanceof ApiError && e.status === 409;

/** Upload a local version. Creates the remote site on first share. Returns the updated site.
 * base: the shared version this browser last pulled or published; the server refuses (409, see isConflict) when a
 * newer one went live since. Leave it out to replace whatever is live ("Publish anyway"). */
export async function shareVersion(
  original: LocalSite,
  version: LocalVersion,
  slug?: string,
  base?: number,
): Promise<LocalSite> {
  const site = structuredClone(original);
  const files = await readVersionFiles(version);
  const executable = findExecutable(files);
  if (executable) throw new Error(executableMessage(executable));
  const zip = zipSync(files, { level: 6 });
  if (zip.byteLength > MAX_ZIP) throw new Error("This version is over 4MB zipped, too large to share.");

  const form = new FormData();
  form.append("file", new Blob([zip as BlobPart], { type: "application/zip" }), "site.zip");
  form.append("note", version.note || `local v${version.n}`);
  form.append("entry", version.entry); // the start page picked here, so shared links open the same page

  if (site.remote) {
    if (base) form.append("base_version", String(base));
    try {
      const r = await call<{ site: RemoteSite } & HoldStatus>(`/api/sites/${site.remote.siteId}/versions`, {
        method: "POST",
        body: form,
        headers: tokenHeader(site.remote),
      });
      site.remote.live = r.site.live;
      site.remote.pushed[version.n] = r.site.live!;
      site.remote.held = r.held ? r.reasons : undefined;
      return site;
    } catch (e) {
      if (!isGone(e)) throw e;
      if (site.remote.role === "member") {
        throw new Error("This shared site no longer exists. Leave it to unlink this copy, then share it as your own.");
      }
      delete site.remote; // the shared copy is gone: share this version as a new site below
    }
  }
  form.append("name", site.name);
  if (slug) form.append("slug", slug);
  const r = await call<{ site: RemoteSite; edit_token: string; url: string } & HoldStatus>("/api/sites", {
    method: "POST",
    body: form,
  });
  site.remote = { siteId: r.site.id, slug: r.site.slug, token: r.edit_token, url: r.url, live: r.site.live, pushed: {} };
  site.remote.pushed[version.n] = r.site.live!;
  site.remote.held = r.held ? r.reasons : undefined;
  return site;
}

/** Ask whether a held site has been reviewed. Returns the updated site, or the same one when nothing changed. */
export async function refreshHold(original: LocalSite): Promise<LocalSite> {
  if (!original.remote?.held) return original;
  try {
    const r = await call<HoldStatus>(`/api/sites/${original.remote.siteId}/hold`, {
      headers: tokenHeader(original.remote),
    });
    if (r.held) return original;
    const site = structuredClone(original);
    delete site.remote!.held;
    return site;
  } catch {
    return original; // rejected sites answer 404: Publish/Unpublish then explain
  }
}

export async function setRemoteLive(original: LocalSite, remoteN: number): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  const r = await callExisting<{ site: RemoteSite }>(`/api/sites/${site.remote.siteId}/live`, {
    method: "POST",
    headers: { ...tokenHeader(site.remote), "Content-Type": "application/json" },
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
    headers: { ...tokenHeader(site.remote), "Content-Type": "application/json" },
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
      headers: tokenHeader(site.remote),
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
      headers: tokenHeader(site.remote),
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
    headers: tokenHeader(site.remote),
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
    headers: { "Content-Type": "application/json", ...tokenHeader(site.remote) },
    body: JSON.stringify({ entry }),
  });
}

/** A shared version as /api/me/sites lists it. published_by_email: null for edit-token or older publishes. */
export type AccountVersion = {
  n: number;
  created_at: number;
  note: string;
  entry: string;
  published_by_email?: string | null;
};

/** A site the signed-in account owns or is a member of, as /api/me/sites lists it (own sites first). */
export type AccountSite = {
  id: string;
  slug: string | null;
  name: string;
  live: number | null;
  url: string;
  role: "owner" | "member";
  owner_email: string | null;
  versions: AccountVersion[];
};

export async function fetchAccountSites(): Promise<AccountSite[]> {
  try {
    return (await call<{ sites: AccountSite[] }>("/api/me/sites", {})).sites;
  } catch {
    return [];
  }
}

/** A shared site's live version and versions from the public API (no publisher emails), or null if it's gone. */
export async function fetchPublicSite(
  siteId: string,
): Promise<{ live: number | null; versions: AccountVersion[] } | null> {
  try {
    return (await call<{ site: { live: number | null; versions: AccountVersion[] } }>(`/api/sites/${siteId}`, {})).site;
  } catch {
    return null;
  }
}

/** A shared version's original files and start page, for its owner or a member to open in this browser. */
export async function downloadVersion(
  siteId: string,
  n: number,
  headers: Record<string, string> = {},
): Promise<{ files: FileMap; entry: string; n: number }> {
  const v = await callExisting<{ entry: string; files: Record<string, string> }>(
    `/api/sites/${siteId}/versions/${n}/files`,
    { headers },
  );
  const byDigest = new Map<string, Promise<Uint8Array>>(); // a file used twice downloads once
  const get = (digest: string, path: string) => {
    if (!byDigest.has(digest)) {
      byDigest.set(
        digest,
        fetch(`/api/sites/${siteId}/files/${digest}`, { headers }).then(async (res) => {
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

export const downloadLiveVersion = (site: AccountSite) => downloadVersion(site.id, site.live!);

/** Download shared version n into a new local version of the linked site (saved). Returns the updated site. */
export async function pullVersion(original: LocalSite, n: number, live: number | null): Promise<LocalSite> {
  const remote = original.remote!;
  const { files, entry } = await downloadVersion(remote.siteId, n, tokenHeader(remote));
  const site = await addVersion(files, original, original.name, `Pulled shared version ${n}`, entry);
  site.remote!.pushed[site.versions.at(-1)!.n] = n;
  site.remote!.live = live;
  return site;
}

/** An invitation to collaborate on someone else's site. It grants nothing until accepted (by this account only). */
export type Invite = {
  site_id: string;
  site_name: string;
  slug: string | null;
  owner_email: string | null;
  created_at: number;
};

export async function fetchInvites(): Promise<Invite[]> {
  try {
    return (await call<{ invites: Invite[] }>("/api/me/invites", {})).invites;
  } catch {
    return [];
  }
}

/** Accept: the site then appears among the account's sites as a member. */
export const acceptInvite = (siteId: string) =>
  call<{ site: AccountSite }>(`/api/me/invites/${encodeURIComponent(siteId)}/accept`, { method: "POST" });

export async function declineInvite(siteId: string): Promise<void> {
  await call(`/api/me/invites/${encodeURIComponent(siteId)}`, { method: "DELETE" });
}

/** A site's owner and members (owner only). status "pending": invited, hasn't accepted yet. */
export type Member = { email: string; status: "pending" | "accepted"; created_at: number };
/** invite_links: unused invite links, which take a seat (members + links ≤ max_members). */
export type Members = {
  owner: { email: string | null };
  members: Member[];
  max_members: number;
  invite_links?: number;
};

export const fetchMembers = (siteId: string) => call<Members>(`/api/sites/${siteId}/members`, {});

export const inviteMember = (siteId: string, email: string) =>
  call<Members>(`/api/sites/${siteId}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });

export async function removeMember(siteId: string, email: string): Promise<void> {
  await call(`/api/sites/${siteId}/members/${encodeURIComponent(email)}`, { method: "DELETE" });
}

/** A member leaves a shared site: removes itself, and this browser forgets the link. The local versions stay. */
export async function leaveSite(original: LocalSite, email: string): Promise<LocalSite> {
  const site = structuredClone(original);
  if (!site.remote) return site;
  try {
    await removeMember(site.remote.siteId, email);
  } catch (e) {
    // 404: the site is gone; 403: no longer a member (the owner removed us). Unlinking is all that's left.
    if (!(e instanceof ApiError && (e.status === 404 || e.status === 403))) throw e;
  }
  delete site.remote;
  return site;
}

/** Personal API tokens: a CLI or MCP client acts as this account with one. Managed only while signed in here. */
export type ApiToken = { id: string; name: string; hint: string; created_at: number; last_used_at: number | null };

export async function fetchApiTokens(): Promise<ApiToken[]> {
  try {
    return (await call<{ tokens: ApiToken[] }>("/api/me/tokens", {})).tokens;
  } catch {
    return [];
  }
}

/** Returns the new token's secret, which the server never shows again. */
export async function createApiToken(name: string): Promise<{ token: ApiToken; secret: string }> {
  return call("/api/me/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function revokeApiToken(id: string): Promise<void> {
  await call(`/api/me/tokens/${id}`, { method: "DELETE" });
}

/** One-time invite links (owner). The url, with the code in its #fragment, comes back only from create. */
export type InviteLink = { id: string; created_at: number; expires_at: number };
export type UsedInviteLink = { id: string; created_at: number; used_at: number; used_by_email: string | null };
export type InviteLinks = { links: InviteLink[]; used: UsedInviteLink[] };

export const fetchInviteLinks = (siteId: string) => call<InviteLinks>(`/api/sites/${siteId}/invite-links`, {});

export const createInviteLink = (siteId: string) =>
  call<{ link: InviteLink; url: string }>(`/api/sites/${siteId}/invite-links`, { method: "POST" });

export async function revokeInviteLink(siteId: string, id: string): Promise<void> {
  await call(`/api/sites/${siteId}/invite-links/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** /join: what a code is for, then joining with it. The code goes in the POST body, never in a URL. 404 = the generic
 * "invalid, expired or already used" (see isInvalidLink). */
export type InviteLinkInfo = { site_name: string; owner_email: string | null; expires_at: number };

const postCode = <T>(url: string, code: string) =>
  call<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });

export const lookupInviteLink = (code: string) => postCode<InviteLinkInfo>("/api/invite-links/lookup", code);
export const redeemInviteLink = (code: string) => postCode<{ site: AccountSite }>("/api/invite-links/redeem", code);
export const isInvalidLink = (e: unknown) => isGone(e);

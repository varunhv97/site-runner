// IndexedDB persistence for local sites (this app's origin only; previews get copies of the files).
import { openDB, type IDBPDatabase } from "idb";
import { pickEntry, type FileMap } from "./ingest";

export type LocalVersion = {
  n: number;
  createdAt: number;
  note: string;
  entry: string;
  size: number;
  manifest: Record<string, string>; // path -> sha256
};

export type RemoteLink = {
  siteId: string;
  slug?: string | null; // the custom link name, when it has one
  token: string;
  url: string;
  live: number | null;
  pushed: Record<number, number>; // local version n -> remote version n
  held?: string[]; // held for a safety review: why (its links answer 451 until an admin approves it)
};

export type LocalSite = {
  id: string;
  name: string;
  createdAt: number;
  versions: LocalVersion[];
  remote?: RemoteLink;
};

let dbPromise: Promise<IDBPDatabase> | null = null;
function db() {
  dbPromise ??= openDB("site-runner", 1, {
    upgrade(d) {
      if (!d.objectStoreNames.contains("sites")) d.createObjectStore("sites", { keyPath: "id" });
      if (!d.objectStoreNames.contains("files")) d.createObjectStore("files");
    },
  });
  return dbPromise;
}

async function sha256(data: Uint8Array): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", data as BufferSource);
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function listSites(): Promise<LocalSite[]> {
  const sites: LocalSite[] = await (await db()).getAll("sites");
  return sites.sort((a, b) => b.createdAt - a.createdAt);
}

export async function saveSite(site: LocalSite) {
  await (await db()).put("sites", site);
}

export async function deleteSite(id: string) {
  // Files are content-addressed and may be shared; orphans are left behind (see tasks.md).
  await (await db()).delete("sites", id);
}

/** Store files as a new version of `site` (or a brand-new site when site is null). */
export async function addVersion(
  files: FileMap,
  site: LocalSite | null,
  name: string,
  note = "",
  start?: string,
): Promise<LocalSite> {
  const entry = start ?? pickEntry(Object.keys(files));
  if (!entry) throw new Error("No .html file found in what you dropped.");

  const d = await db();
  const manifest: Record<string, string> = {};
  let size = 0;
  for (const [path, data] of Object.entries(files)) {
    const hash = await sha256(data);
    manifest[path] = hash;
    size += data.byteLength;
    await d.put("files", new Blob([data as BlobPart]), hash);
  }

  const target: LocalSite = site ? structuredClone(site) : {
    id: crypto.randomUUID().replace(/-/g, "").slice(0, 12),
    name,
    createdAt: Date.now(),
    versions: [],
  };
  target.versions.push({ n: target.versions.length + 1, createdAt: Date.now(), note, entry, size, manifest });
  await d.put("sites", target);
  return target;
}

/** Change which page a local version opens on. */
export async function setVersionEntry(original: LocalSite, n: number, entry: string): Promise<LocalSite> {
  const site = structuredClone(original);
  const version = site.versions.find((v) => v.n === n);
  if (!version || !(entry in version.manifest)) throw new Error("That page isn't in this version.");
  version.entry = entry;
  await saveSite(site);
  return site;
}

export async function readVersionFiles(version: LocalVersion): Promise<FileMap> {
  const d = await db();
  const out: FileMap = {};
  for (const [path, hash] of Object.entries(version.manifest)) {
    const blob: Blob | undefined = await d.get("files", hash);
    if (!blob) throw new Error(`Missing stored file: ${path}`);
    out[path] = new Uint8Array(await blob.arrayBuffer());
  }
  return out;
}

/** A version's files as separate ArrayBuffers, ready to transfer to the preview origin (lib/preview.ts). */
export async function readVersionBuffers(version: LocalVersion): Promise<Record<string, ArrayBuffer>> {
  const d = await db();
  const out: Record<string, ArrayBuffer> = {};
  for (const [path, hash] of Object.entries(version.manifest)) {
    const blob: Blob | undefined = await d.get("files", hash);
    if (!blob) throw new Error(`Missing stored file: ${path}`);
    out[path] = await blob.arrayBuffer(); // a fresh buffer per path, so each can be transferred
  }
  return out;
}

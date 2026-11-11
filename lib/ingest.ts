// Turns dropped/picked files, folders and zips into a flat { path: bytes } map.
import { unzipSync } from "fflate";

export type FileMap = Record<string, Uint8Array>;

const IGNORED = ["__MACOSX/", ".DS_Store", "Thumbs.db"];

// Hidden files and folders (.git, .env, .DS_Store) are never part of a site, and sharing them could
// leak history or secrets, so they are dropped here and again on the server.
function cleanPath(p: string): string | null {
  const parts: string[] = [];
  for (const seg of p.replace(/\\/g, "/").split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === ".." || seg.startsWith(".")) return null;
    parts.push(seg);
  }
  const out = parts.join("/");
  if (!out || IGNORED.some((i) => out.includes(i))) return null;
  return out;
}

async function addFile(map: FileMap, file: File, path: string) {
  if (file.name.toLowerCase().endsWith(".zip")) {
    const entries = unzipSync(new Uint8Array(await file.arrayBuffer()));
    const prefix = path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "";
    for (const [name, data] of Object.entries(entries)) {
      if (name.endsWith("/")) continue;
      const p = cleanPath(prefix + name);
      if (p) map[p] = data;
    }
    return;
  }
  const p = cleanPath(path);
  if (p) map[p] = new Uint8Array(await file.arrayBuffer());
}

function stripCommonRoot(map: FileMap): FileMap {
  const paths = Object.keys(map);
  const tops = new Set(paths.map((p) => p.split("/")[0]));
  if (tops.size !== 1 || !paths.every((p) => p.includes("/"))) return map;
  const cut = [...tops][0].length + 1;
  return Object.fromEntries(Object.entries(map).map(([p, d]) => [p.slice(cut), d]));
}

export async function fromFileList(files: FileList | File[]): Promise<FileMap> {
  const map: FileMap = {};
  for (const f of Array.from(files)) await addFile(map, f, f.webkitRelativePath || f.name);
  return stripCommonRoot(map);
}

// Drag-and-drop: walk directories via the FileSystemEntry API.
export async function fromDataTransfer(dt: DataTransfer): Promise<FileMap> {
  const entries = Array.from(dt.items)
    .map((i) => i.webkitGetAsEntry?.())
    .filter((e): e is FileSystemEntry => !!e);
  if (entries.length === 0) return fromFileList(dt.files);

  const map: FileMap = {};
  const walk = async (entry: FileSystemEntry, prefix: string): Promise<void> => {
    if (entry.name.startsWith(".")) return; // don't read .git and friends at all
    if (entry.isFile) {
      const file = await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej));
      await addFile(map, file, prefix + entry.name);
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      let batch: FileSystemEntry[];
      do {
        batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        for (const child of batch) await walk(child, `${prefix}${entry.name}/`);
      } while (batch.length > 0);
    }
  };
  for (const e of entries) await walk(e, "");
  return stripCommonRoot(map);
}

/** The site's HTML pages, shallowest first. */
export function htmlFiles(paths: string[]): string[] {
  return paths
    .filter((p) => /\.html?$/i.test(p))
    .sort((a, b) => a.split("/").length - b.split("/").length || a.localeCompare(b));
}

export function pickEntry(paths: string[]): string | null {
  if (paths.includes("index.html")) return "index.html";
  return htmlFiles(paths)[0] ?? null;
}

/** No index.html and more than one page: ask which one opens first. */
export function needsStartPage(paths: string[]): boolean {
  return !paths.includes("index.html") && htmlFiles(paths).length > 1;
}

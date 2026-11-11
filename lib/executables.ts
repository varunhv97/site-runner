// Executable files can't be shared (the server refuses them too: api/_core/scan.py, same list).
// Local previews still work; only Share/Publish is blocked.
import { unzipSync } from "fflate";
import blocked from "./blocked-files.json";

const MACHO = [0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe];

function u32(b: Uint8Array, at: number, little: boolean): number {
  return new DataView(b.buffer, b.byteOffset, b.byteLength).getUint32(at, little);
}

function isAppArchive(b: Uint8Array): boolean {
  if (!(b[0] === 0x50 && b[1] === 0x4b && b[2] === 0x03 && b[3] === 0x04)) return false;
  let found = false;
  try {
    // list names only: the filter returns false, so nothing is inflated
    unzipSync(b, {
      filter: (f) => {
        if (["AndroidManifest.xml", "META-INF/MANIFEST.MF", "classes.dex"].includes(f.name)) found = true;
        return false;
      },
    });
  } catch {
    return false;
  }
  return found;
}

/** Is this file's content a program (Windows, Linux, Mac, Android/Java)? Mirrors scan.executable_kind. */
export function isExecutableBytes(b: Uint8Array): boolean {
  if (b.length >= 0x40 && b[0] === 0x4d && b[1] === 0x5a) {
    const pe = u32(b, 0x3c, true);
    if (pe + 4 <= b.length && b[pe] === 0x50 && b[pe + 1] === 0x45 && b[pe + 2] === 0 && b[pe + 3] === 0) return true;
  }
  if (b.length < 8) return false;
  const magic = u32(b, 0, false);
  if (magic === 0x7f454c46) return true; // ELF
  if (MACHO.includes(magic)) return true;
  if ((magic === 0xcafebabe || magic === 0xcafebabf) && u32(b, 4, false) < 20) return true; // fat Mach-O, not a Java class
  return isAppArchive(b);
}

export function isBlockedPath(path: string): boolean {
  const lower = path.toLowerCase();
  const segs = lower.split("/");
  const name = segs[segs.length - 1];
  const dot = name.lastIndexOf(".");
  if (dot > 0 && blocked.extensions.includes(name.slice(dot))) return true;
  return segs.slice(0, -1).some((seg) => blocked.bundles.some((ext) => seg.endsWith(ext)));
}

/** The first executable file (in path order), or null. */
export function findExecutable(files: Record<string, Uint8Array>): string | null {
  for (const path of Object.keys(files).sort()) {
    if (isBlockedPath(path) || isExecutableBytes(files[path])) return path;
  }
  return null;
}

export const executableMessage = (path: string) => `Executable files can't be shared: ${path}`;

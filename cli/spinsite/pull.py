"""Pull a shared version into a folder, and deploy back from it without clobbering someone else's newer publish.

A pulled folder has `.spinsite.json` ({site_id, url, version, entry, server}). `deploy` reads it to know the site
and refuses when the site's live version is newer than the one pulled (last publish wins; there is no merging).
It's a hidden file, so bundle.py never uploads it.
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from .client import Client

STATE_FILE = ".spinsite.json"
CONCURRENCY = 8  # downloads at once
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
# Files that coding agents load as instructions, or that build/test tools run, when someone works in the folder.
# A pulled site may come from another member, so pull points them out ("review_before_running").
AGENT_FILES = {"claude.md", "agents.md", "gemini.md"}  # loaded from any folder
TOOL_FILES = {
    "package.json",
    "makefile",
    "gnumakefile",
    "justfile",
    "conftest.py",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
    "noxfile.py",
}  # top level only


class PullError(Exception):
    pass


# ---- .spinsite.json ----


def read_state(folder: str | Path) -> dict | None:
    """The folder's .spinsite.json, or None when it has none (or it's unreadable / not ours)."""
    try:
        data = json.loads((Path(folder) / STATE_FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("site_id"), str):
        return None
    return data


def write_state(folder: str | Path, data: dict) -> None:
    (Path(folder) / STATE_FILE).write_text(json.dumps(data, indent=2) + "\n")


# ---- pulling ----


def safe_target(root: Path, path: str) -> Path | None:
    """Where a server-supplied path may be written under root, or None when it must be skipped: hidden segments
    (".env", ".git/..."), absolute or drive paths, "..", backslashes, control characters, or anything that would
    resolve outside root (say through a symlink already in the folder)."""
    if not isinstance(path, str) or not path or "\\" in path or any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        return None
    if path.startswith("/") or PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive:
        return None
    parts = path.split("/")
    if any(not part or part.startswith(".") for part in parts):  # also catches "." and ".."
        return None
    base = root.resolve()
    target = (base / PurePosixPath(*parts)).resolve()
    if target == base or not target.is_relative_to(base):
        return None
    return target


def needs_review(paths) -> list[str]:
    """The pulled paths an agent or a build tool would load or run on its own (see AGENT_FILES, TOOL_FILES)."""
    out = []
    for path in paths:
        name = path.rsplit("/", 1)[-1].lower()
        if name in AGENT_FILES or ("/" not in path and name in TOOL_FILES):
            out.append(path)
    return out


def default_folder(site: dict) -> Path:
    return Path(site.get("slug") or site["id"])


def foreign_contents(folder: Path, site_id: str) -> bool:
    """The folder has files and isn't an earlier pull of this site."""
    if not folder.is_dir() or not any(folder.iterdir()):
        return False
    state = read_state(folder)
    return not (state and state.get("site_id") == site_id)


def _check_folder(folder: Path, site_id: str, force: bool) -> None:
    if folder.exists() and not folder.is_dir():
        raise PullError(f"{folder} exists and isn't a folder")
    if not folder.exists() or force or not any(folder.iterdir()):
        return
    if not foreign_contents(folder, site_id):
        return  # an earlier pull of the same site: update it in place
    raise PullError(f"{folder} isn't empty; pull into a new folder, or pass --force to write into it anyway")


async def pull(
    c: Client, site: dict, folder: str | Path | None = None, version: int | None = None, force: bool = False
) -> dict:
    """Download a version (default: the live one) of `site` (an entry from /api/me/sites) into folder (default:
    ./<link name or id>) and record it in .spinsite.json. Unsafe paths are skipped and listed in "skipped"."""
    n = version or site.get("live")
    if not n:
        raise PullError(f"{site['name']} has no live version; pick one with --version")
    root = Path(folder) if folder else default_folder(site)
    _check_folder(root, site["id"], force)

    listing = await c.version_files(site["id"], n)
    wanted: dict[str, tuple[Path, str]] = {}  # path -> (target, digest)
    skipped = []
    for path, digest in sorted((listing.get("files") or {}).items()):
        target = safe_target(root, path)
        if target is None or not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            skipped.append(path)
        else:
            wanted[path] = (target, digest)
    if not wanted:
        raise PullError(f"v{n} of {site['name']} has no files that can be written here")

    limit = asyncio.Semaphore(CONCURRENCY)

    async def fetch(digest: str) -> tuple[str, bytes]:
        async with limit:
            data = await c.file_bytes(site["id"], digest)
        if hashlib.sha256(data).hexdigest() != digest:
            raise PullError(f"a file of v{n} didn't match its checksum; try again")
        return digest, data

    # everything is downloaded before anything is written, so a failed download leaves the folder as it was
    blobs = dict(await asyncio.gather(*(fetch(d) for d in sorted({d for _, d in wanted.values()}))))
    written = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        for path, (target, digest) in wanted.items():
            if safe_target(root, path) != target:  # checked again now that root exists (a symlinked folder)
                skipped.append(path)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blobs[digest])
            written.append(path)
        state = {"site_id": site["id"], "url": site.get("url"), "version": n, "entry": listing.get("entry")}
        write_state(root, state | {"server": c.base_url})
    except OSError as e:
        raise PullError(f"couldn't write the files into {root}: {e}") from e
    return {
        "site_id": site["id"],
        "name": site["name"],
        "version": n,
        "entry": listing.get("entry"),
        "url": site.get("url"),
        "dir": str(root),
        "files": len(written),
        "skipped": skipped,
        # loaded by coding agents or run by build tools: written by whoever published, so read them first
        "review_before_running": needs_review(written),
    }


# ---- deploying from a pulled folder ----


def stale_message(site: dict, state: dict | None, folder: str | Path) -> str | None:
    """Why publishing over `site` would discard someone's newer version, or None when it's safe: only when the
    folder was pulled from this site and the live version is newer than the pulled one."""
    if not state or state.get("site_id") != site["id"]:
        return None
    pulled, live = state.get("version") or 0, site.get("live") or 0
    if live <= pulled:
        return None
    v = next((v for v in site.get("versions", []) if v["n"] == live), {})
    who = v.get("published_by_email") or "someone"
    ref = site.get("slug") or site["id"]
    return (
        f"{site['name']} is live on v{live}, published by {who}, which is newer than v{pulled} you pulled. "
        f"Publishing now would replace their changes (there is no merging). Run `spinsite pull {ref} {folder}` "
        "to get the latest first, or use force to publish anyway."
    )


async def target_site(c: Client, site_ref: str, state: dict | None) -> dict | None:
    """The site to publish to: --site, else the one the folder was pulled from, else None (a new site)."""
    if site_ref:
        return await c.find_site(site_ref)
    if state:
        if state.get("server") and state["server"].rstrip("/") != c.base_url.rstrip("/"):
            raise PullError(f"this folder was pulled from {state['server']}, not {c.base_url}; pass --site")
        return await c.find_site(state["site_id"])
    return None


def default_entry(folder: Path, state: dict | None, entry: str) -> str:
    """The pulled version's start page, when none is given and the file is still there."""
    if entry or not state or not isinstance(state.get("entry"), str):
        return entry
    target = safe_target(folder, state["entry"])
    return state["entry"] if target and target.is_file() else ""


def after_deploy(folder: Path, state: dict | None, body: dict) -> bool:
    """Record the newly published version in the folder's .spinsite.json (when it was pulled from that site)."""
    s = body["site"]
    if not state or state.get("site_id") != s["id"]:
        return False
    v = next((v for v in s.get("versions", []) if v["n"] == s["live"]), {})
    try:
        write_state(folder, state | {"version": s["live"], "url": body.get("url"), "entry": v.get("entry")})
    except OSError:
        return False
    return True

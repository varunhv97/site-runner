"""Compare two versions of a shared site, or a version with a folder on this machine (what a deploy would change).

Files are matched by path and compared by sha256, so only changed files are downloaded. Text files get a unified
diff; binary or very large ones are only reported as changed. The output is capped so it fits in an agent's context.
"""

import asyncio
import difflib
import hashlib
from pathlib import Path

from .bundle import site_files
from .client import Client
from .pull import CONCURRENCY, DIGEST_RE, PullError

MAX_TEXT_BYTES = 512 * 1024  # larger files are reported as changed, without a line diff
MAX_DIFF_CHARS = 100_000  # across all files; the rest are listed with "truncated"


def _text(data: bytes) -> str | None:
    """The file as text, or None when it looks binary (a NUL byte, or not UTF-8)."""
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _local_files(folder: Path) -> dict[str, bytes]:
    """What `deploy` would upload from folder (same rules: no hidden files, no symlinks), path -> bytes."""
    if not folder.is_dir():
        raise PullError(f"{folder} isn't a folder")
    return {f.as_posix(): (folder / f).read_bytes() for f in site_files(folder)}


def _version(site: dict, n: int | None, what: str) -> int:
    n = n or site.get("live")
    if not n:
        raise PullError(f"{site['name']} has no live version; give the {what} version")
    return n


async def diff(
    c: Client,
    site: dict,
    old: int | None = None,
    new: int | None = None,
    folder: str | Path | None = None,
    path: str = "",
    context: int = 3,
) -> dict:
    """Changes from version `old` (default: live) to version `new` (default: live), or to `folder` when given.
    `path` limits it to one file or folder (a prefix). Returns added/removed/changed lists and per-file diffs."""
    old_n = _version(site, old, "old")
    old_files = (await c.version_files(site["id"], old_n)).get("files") or {}
    if folder is not None:
        local = _local_files(Path(folder))
        new_files = {p: hashlib.sha256(b).hexdigest() for p, b in local.items()}
        new_label: int | str = "local"
    else:
        new_n = _version(site, new, "new")
        new_files = (await c.version_files(site["id"], new_n)).get("files") or {}
        new_label, local = new_n, {}

    prefix = path.strip().lstrip("/")
    if prefix:
        old_files = {p: d for p, d in old_files.items() if p == prefix or p.startswith(prefix.rstrip("/") + "/")}
        new_files = {p: d for p, d in new_files.items() if p == prefix or p.startswith(prefix.rstrip("/") + "/")}

    added = sorted(set(new_files) - set(old_files))
    removed = sorted(set(old_files) - set(new_files))
    changed = sorted(p for p in set(old_files) & set(new_files) if old_files[p] != new_files[p])

    # download each changed remote file once (by digest), checked against its sha256 like pull does
    wanted = {old_files[p] for p in changed} | ({new_files[p] for p in changed} if folder is None else set())
    if any(not isinstance(d, str) or not DIGEST_RE.fullmatch(d) for d in wanted):
        raise PullError("the server sent a malformed file list; try again")
    limit = asyncio.Semaphore(CONCURRENCY)

    async def fetch(digest: str) -> tuple[str, bytes]:
        async with limit:
            data = await c.file_bytes(site["id"], digest)
        if hashlib.sha256(data).hexdigest() != digest:
            raise PullError("a downloaded file didn't match its checksum; try again")
        return digest, data

    blobs = dict(await asyncio.gather(*(fetch(d) for d in sorted(wanted))))

    budget, truncated, entries = MAX_DIFF_CHARS, False, []
    for p in changed:
        a = blobs[old_files[p]]
        b = local[p] if folder is not None else blobs[new_files[p]]
        entry = {"path": p, "old_size": len(a), "new_size": len(b)}
        ta, tb = (_text(a), _text(b)) if max(len(a), len(b)) <= MAX_TEXT_BYTES else (None, None)
        if ta is None or tb is None:
            entry["kind"] = "binary" if max(len(a), len(b)) <= MAX_TEXT_BYTES else "too_large"
        else:
            lines = list(
                difflib.unified_diff(
                    ta.splitlines(keepends=True),
                    tb.splitlines(keepends=True),
                    f"v{old_n}/{p}",
                    f"{new_label if folder is not None else 'v' + str(new_label)}/{p}",
                    n=max(0, context),
                )
            )
            text = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
            entry |= {
                "kind": "text",
                "added_lines": sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")),
                "removed_lines": sum(1 for line in lines if line.startswith("-") and not line.startswith("---")),
            }
            if len(text) <= budget:
                entry["diff"] = text
                budget -= len(text)
            else:
                truncated = True  # counts stay; the diff itself is left out
        entries.append(entry)

    return {
        "site_id": site["id"],
        "name": site["name"],
        "from": old_n,
        "to": new_label,
        "path": prefix or None,
        "added": added,
        "removed": removed,
        "changed": entries,
        "unchanged": len(set(old_files) & set(new_files)) - len(changed),
        "truncated": truncated,
    }

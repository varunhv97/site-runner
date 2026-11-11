"""Turn a folder (or an existing .zip) into the zip the share API takes, checking its limits first."""

import io
import os
import zipfile
from pathlib import Path

# The server's limits (api/_core/sites.py): refusing here saves an upload that would fail.
MAX_ZIP_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_FILES = 2000


class BundleError(Exception):
    pass


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f}MB"


def site_files(root: Path) -> list[Path]:
    """Files to publish, relative to root. Hidden files and folders are skipped (the server drops them too), and
    so are symlinks, so a link to something outside the folder (say ~/.ssh) is never uploaded."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not (here / d).is_symlink())
        for name in sorted(filenames):
            full = here / name
            if name.startswith(".") or full.is_symlink() or not full.is_file():
                continue
            found.append(full.relative_to(root))
    return found


def bundle(path: str | os.PathLike) -> tuple[bytes, int]:
    """(zip bytes, number of files) for a folder or a .zip file."""
    p = Path(path)
    if p.is_file():
        if not zipfile.is_zipfile(p):
            raise BundleError(f"{p} is not a folder or a .zip file")
        data = p.read_bytes()
        count = len([i for i in zipfile.ZipFile(io.BytesIO(data)).infolist() if not i.is_dir()])
    elif p.is_dir():
        files = site_files(p)
        if not files:
            raise BundleError(f"{p} has no files to publish")
        if len(files) > MAX_FILES:
            raise BundleError(f"{len(files)} files; a version can have at most {MAX_FILES}")
        total = sum((p / f).stat().st_size for f in files)
        if total > MAX_TOTAL_BYTES:
            raise BundleError(f"{_mb(total)} of files; a version can be at most {_mb(MAX_TOTAL_BYTES)} unzipped")
        if not any(f.suffix.lower() in (".html", ".htm") for f in files):
            raise BundleError(f"{p} has no .html file")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(p / f, f.as_posix())
        data, count = buf.getvalue(), len(files)
    else:
        raise BundleError(f"{p} doesn't exist")
    if len(data) > MAX_ZIP_BYTES:
        raise BundleError(f"the zipped site is {_mb(len(data))}; uploads can be at most {_mb(MAX_ZIP_BYTES)} zipped")
    return data, count

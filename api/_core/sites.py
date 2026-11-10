"""Site/version logic: zip validation, content-addressed file storage, metadata."""

import hashlib
import hmac
import io
import json
import mimetypes
import os
import posixpath
import secrets
import string
import time
import zipfile

MAX_ZIP_BYTES = 4 * 1024 * 1024  # Vercel function body limit is 4.5MB
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # zip-bomb guard (uncompressed)
MAX_FILES = 2000
# per-account limits, so one account can't fill the storage or the bill
MAX_SITES_PER_USER = 50
MAX_VERSIONS_PER_SITE = 200
MAX_UPLOADS_PER_DAY = 100

_ID_ALPHABET = string.ascii_lowercase + string.digits
_IGNORED = ("__MACOSX/", ".DS_Store", "Thumbs.db")

_EXTRA_TYPES = {
    ".mjs": "text/javascript",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".woff2": "font/woff2",
}


class SiteError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def content_type(path: str) -> str:
    ext = posixpath.splitext(path)[1].lower()
    ctype = _EXTRA_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
        ctype += "; charset=utf-8"
    return ctype


def _clean_path(name: str) -> str | None:
    """Normalize a zip entry name; None for entries to skip. Raises on unsafe paths.

    Hidden files and folders (.git, .env, .DS_Store, ...) are skipped: they are never part of a
    site, and publishing them could leak history or secrets."""
    name = name.replace("\\", "/")
    if name.endswith("/") or any(part in name for part in _IGNORED):
        return None
    norm = posixpath.normpath(name)
    if norm.startswith("/") or norm == ".." or norm.startswith("../"):
        raise SiteError(f"unsafe path in zip: {name}")
    if any(seg.startswith(".") for seg in norm.split("/")):
        return None
    return norm


def extract_zip(data: bytes) -> dict[str, bytes]:
    if len(data) > MAX_ZIP_BYTES:
        raise SiteError("upload too large (max 4MB zipped)", 413)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise SiteError("not a valid zip file") from e

    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise SiteError(f"too many files (max {MAX_FILES})", 413)
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        raise SiteError("site too large when unzipped (max 50MB)", 413)

    files: dict[str, bytes] = {}
    for info in infos:
        path = _clean_path(info.filename)
        if path:
            files[path] = zf.read(info)
    if not files:
        raise SiteError("zip contains no files")

    # Zips of a folder usually wrap everything in one top-level dir; strip it.
    tops = {p.split("/", 1)[0] for p in files}
    if len(tops) == 1 and all("/" in p for p in files):
        prefix = tops.pop() + "/"
        files = {p[len(prefix) :]: b for p, b in files.items()}
    return files


def pick_entry(paths) -> str:
    htmls = sorted((p for p in paths if p.lower().endswith((".html", ".htm"))), key=lambda p: (p.count("/"), p))
    if "index.html" in paths:
        return "index.html"
    if not htmls:
        raise SiteError("no .html file found")
    return htmls[0]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_id() -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(10))


def is_admin(user: dict | None) -> bool:
    admins = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
    return bool(user and user.get("email", "").lower() in admins)


class Sites:
    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def _meta_key(site_id: str) -> str:
        return f"sites/{site_id}/meta.json"

    async def load_meta(self, site_id: str) -> dict:
        if not site_id.isalnum():
            raise SiteError("site not found", 404)
        raw = await self.storage.get(self._meta_key(site_id), fresh=True)
        if raw is None:
            raise SiteError("site not found", 404)
        meta = json.loads(raw)
        if meta.get("deleted"):
            raise SiteError("site not found", 404)
        return meta

    async def _save_meta(self, meta: dict) -> None:
        await self.storage.put(self._meta_key(meta["id"]), json.dumps(meta).encode(), "application/json")

    def check_access(self, meta: dict, token: str | None, user: dict | None = None) -> None:
        """The site's owner (signed in) or anyone holding its edit token (API, MCP) may change it."""
        if user and meta.get("owner") == user["sub"]:
            return
        if token and hmac.compare_digest(_hash_token(token), meta["token_hash"]):
            return
        raise SiteError("sign in as the site's owner or send its edit token", 403)

    @staticmethod
    def _user_key(sub: str) -> str:
        return f"users/{hashlib.sha256(sub.encode()).hexdigest()}.json"

    async def _load_user(self, sub: str) -> dict:
        raw = await self.storage.get(self._user_key(sub), fresh=True)
        doc = json.loads(raw) if raw else {}
        return {"sites": doc.get("sites", []), "day": doc.get("day", ""), "uploads": doc.get("uploads", 0)}

    async def _save_user(self, sub: str, doc: dict) -> None:
        await self.storage.put(self._user_key(sub), json.dumps(doc).encode(), "application/json")

    async def list_user_sites(self, sub: str) -> list[str]:
        return (await self._load_user(sub))["sites"]

    async def _add_user_site(self, sub: str, site_id: str) -> None:
        doc = await self._load_user(sub)
        if site_id not in doc["sites"]:
            doc["sites"].append(site_id)
        await self._save_user(sub, doc)

    async def _count_upload(self, user: dict | None) -> None:
        """Enforce the daily upload limit for signed-in uploads."""
        if not user:
            return
        doc = await self._load_user(user["sub"])
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if doc["day"] != today:
            doc["day"], doc["uploads"] = today, 0
        if doc["uploads"] >= MAX_UPLOADS_PER_DAY:
            raise SiteError(f"daily upload limit reached ({MAX_UPLOADS_PER_DAY}); try again tomorrow", 429)
        doc["uploads"] += 1
        await self._save_user(user["sub"], doc)

    async def _store_version(self, meta: dict, zip_bytes: bytes, note: str) -> dict:
        files = extract_zip(zip_bytes)
        manifest = {}
        for path, data in files.items():
            digest = hashlib.sha256(data).hexdigest()
            manifest[path] = digest
            await self.storage.put(f"blobs/{digest}", data, content_type(path))
        version = {
            "n": len(meta["versions"]) + 1,
            "created_at": int(time.time()),
            "note": note[:200],
            "entry": pick_entry(manifest),
            "size": sum(len(b) for b in files.values()),
            "manifest": manifest,
        }
        meta["versions"].append(version)
        meta["live"] = version["n"]
        await self._save_meta(meta)
        return version

    async def create(self, name: str, zip_bytes: bytes, note: str = "", owner: dict | None = None) -> tuple[dict, str]:
        if owner:
            if len(await self.list_user_sites(owner["sub"])) >= MAX_SITES_PER_USER:
                raise SiteError(f"you have {MAX_SITES_PER_USER} shared sites, the most an account can have", 429)
            await self._count_upload(owner)
        token = secrets.token_urlsafe(24)
        meta = {
            "id": _new_id(),
            "name": (name or "Untitled site")[:100],
            "token_hash": _hash_token(token),
            "owner": owner["sub"] if owner else None,
            "created_at": int(time.time()),
            "live": None,
            "versions": [],
        }
        await self._store_version(meta, zip_bytes, note)
        if owner:
            await self._add_user_site(owner["sub"], meta["id"])
        return meta, token

    async def add_version(
        self, site_id: str, token: str | None, zip_bytes: bytes, note: str = "", user: dict | None = None
    ) -> dict:
        meta = await self.load_meta(site_id)
        self.check_access(meta, token, user)
        if len(meta["versions"]) >= MAX_VERSIONS_PER_SITE:
            raise SiteError(f"this site has {MAX_VERSIONS_PER_SITE} versions, the most a site can have", 429)
        await self._count_upload(user)
        await self._store_version(meta, zip_bytes, note)
        return meta

    async def set_live(self, site_id: str, token: str | None, n: int, user: dict | None = None) -> dict:
        meta = await self.load_meta(site_id)
        self.check_access(meta, token, user)
        if not 1 <= n <= len(meta["versions"]):
            raise SiteError("no such version", 404)
        meta["live"] = n
        await self._save_meta(meta)
        return meta

    async def claim(self, site_id: str, token: str | None, user: dict) -> dict:
        meta = await self.load_meta(site_id)
        if not token or not hmac.compare_digest(_hash_token(token), meta["token_hash"]):
            raise SiteError("invalid or missing edit token", 403)
        if meta.get("owner") and meta["owner"] != user["sub"]:
            raise SiteError("this site already belongs to another account", 409)
        meta["owner"] = user["sub"]
        await self._save_meta(meta)
        await self._add_user_site(user["sub"], site_id)
        return meta

    async def delete(self, site_id: str, token: str | None, user: dict | None = None) -> None:
        """Unpublish a site: its links stop working. The owner, its token or an admin (takedown) may do it.
        Files stay in storage (content-addressed blobs can be shared between sites)."""
        meta = await self.load_meta(site_id)
        if not is_admin(user):
            self.check_access(meta, token, user)
        tombstone = {"id": site_id, "deleted": True, "deleted_at": int(time.time()), "by_admin": is_admin(user)}
        await self.storage.put(self._meta_key(site_id), json.dumps(tombstone).encode(), "application/json")
        if meta.get("owner"):
            doc = await self._load_user(meta["owner"])
            doc["sites"] = [i for i in doc["sites"] if i != site_id]
            await self._save_user(meta["owner"], doc)

    async def rotate_token(self, site_id: str, token: str | None, user: dict | None = None) -> str:
        """Replace a leaked edit token: the old one stops working."""
        meta = await self.load_meta(site_id)
        self.check_access(meta, token, user)
        new = secrets.token_urlsafe(24)
        meta["token_hash"] = _hash_token(new)
        await self._save_meta(meta)
        return new

    async def cleanup(self, min_age_seconds: int = 3600) -> dict:
        """Delete stored files that no live or unpublished-but-kept site version uses.

        Files younger than min_age_seconds are kept: an upload writes its files before its metadata,
        so a brand-new file may not be referenced yet."""
        referenced: set[str] = set()
        metas = [key for key, _ in await self.storage.list("sites/") if key.endswith("/meta.json")]
        for key in metas:
            raw = await self.storage.get(key, fresh=True)
            meta = json.loads(raw) if raw else {}
            for version in meta.get("versions", []):
                referenced.update(version["manifest"].values())
        now = time.time()
        orphans = [
            key
            for key, uploaded in await self.storage.list("blobs/")
            if key.removeprefix("blobs/") not in referenced and now - uploaded > min_age_seconds
        ]
        if orphans:
            await self.storage.delete(orphans)
        return {"sites": len(metas), "files_referenced": len(referenced), "files_deleted": len(orphans)}

    async def read_file(self, version: dict, path: str) -> bytes | None:
        digest = version["manifest"].get(path)
        if digest is None:
            return None
        return await self.storage.get(f"blobs/{digest}")


def public_meta(meta: dict) -> dict:
    return {
        "id": meta["id"],
        "name": meta["name"],
        "live": meta["live"],
        "versions": [
            {k: v for k, v in ver.items() if k != "manifest"} | {"files": len(ver["manifest"])}
            for ver in meta["versions"]
        ],
    }

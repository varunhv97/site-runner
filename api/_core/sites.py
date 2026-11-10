"""Site/version logic: zip validation, files in Blob storage (by sha256), metadata in the database."""

import hashlib
import hmac
import io
import mimetypes
import os
import posixpath
import secrets
import string
import time
import zipfile

from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

from .db import create_schema, sites, uploads, users, version_files, versions

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
    def __init__(self, storage, engine):
        self.storage = storage
        self.engine = engine
        self._schema_ready = False

    async def _ready(self) -> None:
        if not self._schema_ready:
            await create_schema(self.engine)
            self._schema_ready = True

    def _upsert(self, table):
        dialect = postgresql if self.engine.dialect.name == "postgresql" else sqlite
        return dialect.insert(table)

    # ---- users and limits ----

    async def touch_user(self, user: dict) -> None:
        """Record a signed-in user (email kept for admin and abuse handling)."""
        await self._ready()
        stmt = self._upsert(users).values(sub=user["sub"], email=user.get("email", ""), created_at=int(time.time()))
        async with self.engine.begin() as conn:
            await conn.execute(stmt.on_conflict_do_update(index_elements=["sub"], set_={"email": stmt.excluded.email}))

    async def _count_upload(self, conn, user: dict | None) -> None:
        """Atomically count a signed-in upload against today's limit."""
        if not user:
            return
        day = time.strftime("%Y-%m-%d", time.gmtime())
        stmt = self._upsert(uploads).values(sub=user["sub"], day=day, count=1)
        stmt = stmt.on_conflict_do_update(index_elements=["sub", "day"], set_={"count": uploads.c.count + 1}).returning(
            uploads.c.count
        )
        count = (await conn.execute(stmt)).scalar_one()
        if count > MAX_UPLOADS_PER_DAY:
            raise SiteError(f"daily upload limit reached ({MAX_UPLOADS_PER_DAY}); try again tomorrow", 429)

    # ---- reading ----

    async def _site(self, conn, site_id: str, lock: bool = False):
        if not site_id.isalnum():
            raise SiteError("site not found", 404)
        q = select(sites).where(sites.c.id == site_id, sites.c.deleted_at.is_(None))
        row = (await conn.execute(q.with_for_update() if lock else q)).first()
        if row is None:
            raise SiteError("site not found", 404)
        return row

    async def public_meta(self, site_id: str, conn=None) -> dict:
        await self._ready()
        if conn is None:
            async with self.engine.connect() as c:
                return await self.public_meta(site_id, c)
        site = await self._site(conn, site_id)
        rows = (await conn.execute(select(versions).where(versions.c.site_id == site_id).order_by(versions.c.n))).all()
        return {
            "id": site.id,
            "name": site.name,
            "live": site.live,
            "versions": [
                {
                    "n": v.n,
                    "created_at": v.created_at,
                    "note": v.note,
                    "entry": v.entry,
                    "size": v.size,
                    "files": v.files,
                }
                for v in rows
            ],
        }

    async def user_sites(self, sub: str) -> list[dict]:
        await self._ready()
        async with self.engine.connect() as conn:
            ids = (
                await conn.execute(
                    select(sites.c.id)
                    .where(sites.c.owner == sub, sites.c.deleted_at.is_(None))
                    .order_by(sites.c.created_at)
                )
            ).scalars()
            return [await self.public_meta(i, conn) for i in ids]

    async def list_user_sites(self, sub: str) -> list[str]:
        return [s["id"] for s in await self.user_sites(sub)]

    async def resolve(self, site_id: str, n: int | None) -> dict:
        """The version to serve: version n, or the live one. 404 when there is none."""
        await self._ready()
        async with self.engine.connect() as conn:
            site = await self._site(conn, site_id)
            n = site.live if n is None else n
            row = (await conn.execute(select(versions).where(versions.c.site_id == site_id, versions.c.n == n))).first()
        if row is None:
            raise SiteError("version not found", 404)
        return {"n": row.n, "entry": row.entry}

    async def file_digest(self, site_id: str, n: int, path: str) -> str | None:
        async with self.engine.connect() as conn:
            q = select(version_files.c.digest).where(
                version_files.c.site_id == site_id, version_files.c.n == n, version_files.c.path == path
            )
            return (await conn.execute(q)).scalar()

    async def read_blob(self, digest: str) -> bytes | None:
        return await self.storage.get(f"blobs/{digest}")

    # ---- writing ----

    def check_access(self, site, token: str | None, user: dict | None = None) -> None:
        """The site's owner (signed in) or anyone holding its edit token (API, MCP) may change it."""
        if user and site.owner == user["sub"]:
            return
        if token and hmac.compare_digest(_hash_token(token), site.token_hash):
            return
        raise SiteError("sign in as the site's owner or send its edit token", 403)

    async def _upload_files(self, zip_bytes: bytes) -> tuple[dict, str, int]:
        """Validate the zip and put its files in storage. Returns (path -> digest, entry, total size).
        Files are written before the metadata, so a version never points at a missing file."""
        files = extract_zip(zip_bytes)
        manifest = {}
        for path, data in files.items():
            digest = hashlib.sha256(data).hexdigest()
            manifest[path] = digest
            await self.storage.put(f"blobs/{digest}", data, content_type(path))
        return manifest, pick_entry(manifest), sum(len(b) for b in files.values())

    async def _add_version(self, conn, site_id: str, manifest: dict, entry: str, size: int, note: str) -> int:
        n = (
            await conn.execute(select(func.coalesce(func.max(versions.c.n), 0)).where(versions.c.site_id == site_id))
        ).scalar_one() + 1
        await conn.execute(
            versions.insert().values(
                site_id=site_id,
                n=n,
                created_at=int(time.time()),
                note=note[:200],
                entry=entry,
                size=size,
                files=len(manifest),
            )
        )
        await conn.execute(
            version_files.insert(), [{"site_id": site_id, "n": n, "path": p, "digest": d} for p, d in manifest.items()]
        )
        await conn.execute(update(sites).where(sites.c.id == site_id).values(live=n))
        return n

    async def create(self, name: str, zip_bytes: bytes, note: str = "", owner: dict | None = None) -> tuple[dict, str]:
        await self._ready()
        manifest, entry, size = await self._upload_files(zip_bytes)
        token = secrets.token_urlsafe(24)
        site_id = _new_id()
        async with self.engine.begin() as conn:
            if owner:
                count = (
                    await conn.execute(
                        select(func.count()).where(sites.c.owner == owner["sub"], sites.c.deleted_at.is_(None))
                    )
                ).scalar_one()
                if count >= MAX_SITES_PER_USER:
                    raise SiteError(f"you have {MAX_SITES_PER_USER} shared sites, the most an account can have", 429)
                await self._count_upload(conn, owner)
            await conn.execute(
                sites.insert().values(
                    id=site_id,
                    name=(name or "Untitled site")[:100],
                    token_hash=_hash_token(token),
                    owner=owner["sub"] if owner else None,
                    created_at=int(time.time()),
                    deleted_by_admin=False,
                )
            )
            await self._add_version(conn, site_id, manifest, entry, size, note)
            meta = await self.public_meta(site_id, conn)
        return meta, token

    async def add_version(
        self, site_id: str, token: str | None, zip_bytes: bytes, note: str = "", user: dict | None = None
    ) -> dict:
        await self._ready()
        async with self.engine.connect() as conn:  # check access before spending storage on the upload
            self.check_access(await self._site(conn, site_id), token, user)
        manifest, entry, size = await self._upload_files(zip_bytes)
        try:
            async with self.engine.begin() as conn:
                site = await self._site(conn, site_id, lock=True)  # serialises publishes to one site
                self.check_access(site, token, user)
                count = (await conn.execute(select(func.count()).where(versions.c.site_id == site_id))).scalar_one()
                if count >= MAX_VERSIONS_PER_SITE:
                    raise SiteError(f"this site has {MAX_VERSIONS_PER_SITE} versions, the most a site can have", 429)
                await self._count_upload(conn, user)
                await self._add_version(conn, site_id, manifest, entry, size, note)
                return await self.public_meta(site_id, conn)
        except IntegrityError as e:  # two publishes raced for the same version number (SQLite has no row lock)
            raise SiteError("another version was published at the same moment; try again", 409) from e

    async def set_live(self, site_id: str, token: str | None, n: int, user: dict | None = None) -> dict:
        await self._ready()
        async with self.engine.begin() as conn:
            self.check_access(await self._site(conn, site_id, lock=True), token, user)
            exists = (
                await conn.execute(select(versions.c.n).where(versions.c.site_id == site_id, versions.c.n == n))
            ).first()
            if not exists:
                raise SiteError("no such version", 404)
            await conn.execute(update(sites).where(sites.c.id == site_id).values(live=n))
            return await self.public_meta(site_id, conn)

    async def delete(self, site_id: str, token: str | None, user: dict | None = None) -> None:
        """Unpublish a site: its links stop working. The owner, its token or an admin (takedown) may do it.
        Its files are removed by the cleanup job once no other site uses them."""
        await self._ready()
        async with self.engine.begin() as conn:
            site = await self._site(conn, site_id, lock=True)
            if not is_admin(user):
                self.check_access(site, token, user)
            await conn.execute(
                update(sites)
                .where(sites.c.id == site_id)
                .values(deleted_at=int(time.time()), deleted_by_admin=is_admin(user))
            )

    async def rotate_token(self, site_id: str, token: str | None, user: dict | None = None) -> str:
        """Replace a leaked edit token: the old one stops working."""
        await self._ready()
        new = secrets.token_urlsafe(24)
        async with self.engine.begin() as conn:
            self.check_access(await self._site(conn, site_id, lock=True), token, user)
            await conn.execute(update(sites).where(sites.c.id == site_id).values(token_hash=_hash_token(new)))
        return new

    async def claim(self, site_id: str, token: str | None, user: dict) -> dict:
        await self._ready()
        async with self.engine.begin() as conn:
            site = await self._site(conn, site_id, lock=True)
            if not token or not hmac.compare_digest(_hash_token(token), site.token_hash):
                raise SiteError("invalid or missing edit token", 403)
            if site.owner and site.owner != user["sub"]:
                raise SiteError("this site already belongs to another account", 409)
            await conn.execute(update(sites).where(sites.c.id == site_id).values(owner=user["sub"]))
            return await self.public_meta(site_id, conn)

    # ---- maintenance ----

    async def cleanup(self, min_age_seconds: int = 3600) -> dict:
        """Delete stored files that no published site version uses.

        Files younger than min_age_seconds are kept: an upload writes its files before its metadata,
        so a brand-new file may not be referenced yet."""
        await self._ready()
        async with self.engine.connect() as conn:
            q = (
                select(version_files.c.digest)
                .join(sites, sites.c.id == version_files.c.site_id)
                .where(sites.c.deleted_at.is_(None))
                .distinct()
            )
            referenced = set((await conn.execute(q)).scalars())
        now = time.time()
        orphans = [
            key
            for key, uploaded in await self.storage.list("blobs/")
            if key.removeprefix("blobs/") not in referenced and now - uploaded > min_age_seconds
        ]
        if orphans:
            await self.storage.delete(orphans)
        return {"files_referenced": len(referenced), "files_deleted": len(orphans)}

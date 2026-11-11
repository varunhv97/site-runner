"""Site/version logic: zip validation, files in Blob storage (by sha256), metadata in the database."""

import hashlib
import hmac
import io
import math
import mimetypes
import os
import posixpath
import re
import secrets
import string
import time
import zipfile

from sqlalchemy import delete, func, select, union_all, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

from . import holds, hosts, scan
from .db import (
    blobs,
    create_schema,
    deleted_versions,
    sites,
    slugs,
    uploads,
    users,
    version_files,
    versions,
)

MAX_ZIP_BYTES = 4 * 1024 * 1024  # Vercel function body limit is 4.5MB
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # zip-bomb guard (uncompressed)
MAX_FILES = 2000
# per-account limits, so one account can't fill the storage or the bill
MAX_SITES_PER_USER = 50
MAX_VERSIONS_PER_SITE = 200
MAX_UPLOADS_PER_DAY = 100
# Storage per account: the unique files used by all versions of its sites (a shared file counts once).
DEFAULT_STORAGE_QUOTA_MB = 250
MB = 1024 * 1024

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


def storage_quota_bytes() -> int:
    """STORAGE_QUOTA_MB (may be fractional), else 250 MB."""
    try:
        mb = float(os.environ.get("STORAGE_QUOTA_MB") or DEFAULT_STORAGE_QUOTA_MB)
    except ValueError:
        mb = DEFAULT_STORAGE_QUOTA_MB
    return int(mb * MB)


def _mb(n: int) -> str:
    """Bytes as MB for messages, rounded up to 0.1 so 250.01 MB never reads as "250 MB"."""
    return f"{math.ceil(n / MB * 10) / 10:g} MB"


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
    check_reserved_names(files)
    return files


_PINNED_NAME = re.compile(r"@\d+", re.ASCII)


def check_reserved_names(paths) -> None:
    """A top-level "@3" would be hidden behind the pinned-version links (/@3/ opens version 3)."""
    for path in paths:
        top = path.split("/", 1)[0]
        if _PINNED_NAME.fullmatch(top):
            raise SiteError(
                f'Rename "{top}": a top-level folder or file can\'t be named @ and a number, '
                "because links like /@3/ open version 3 of a shared site."
            )


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


SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
RESERVED_SLUGS = {
    "api",
    "app",
    "s",
    "admin",
    "login",
    "logout",
    "signin",
    "signup",
    "auth",
    "assets",
    "static",
    "www",
    "help",
    "about",
    "settings",
    "account",
    "new",
    "preview",
    "share",
    "docs",
    "terms",
    "privacy",
    "support",
    "spinsite",
    "site-runner",
    "siterunner",
    "vercel",
}


def slug_problem(slug: str) -> str | None:
    """Why a link name can't be used (format and reserved words), or None."""
    if not SLUG_RE.match(slug):
        return "Use 3 to 40 lowercase letters, numbers or hyphens, starting and ending with a letter or number."
    if "--" in slug:
        return "Use single hyphens only."
    if slug in RESERVED_SLUGS:
        return "That name is reserved."
    if slug.startswith("p-"):
        return "Names starting with p- are reserved."
    return None


def is_admin(user: dict | None) -> bool:
    """Admin work needs a browser sign-in: an API token (api_tokens.py) never counts, even an admin's."""
    if user and user.get("via") == "api_token":
        return False
    admins = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
    return bool(user and user.get("email", "").lower() in admins)


def check_entry(paths, entry: str) -> str:
    """A chosen start page: one of the version's .html files."""
    entry = entry.strip().lstrip("/")
    if entry not in paths or not entry.lower().endswith((".html", ".htm")):
        raise SiteError("the start page must be one of the site's .html files", 400)
    return entry


class Upload:
    """A validated zip: path -> bytes, path -> digest, the entry page, total size and digest -> size.
    entry: the start page the uploader picked, else the usual guess (index.html, then the shallowest page)."""

    def __init__(self, zip_bytes: bytes, entry: str | None = None):
        self.files = extract_zip(zip_bytes)
        if bad := scan.first_executable(self.files):
            raise SiteError(f"Executable files can't be shared: {bad}", 400)
        self.findings = scan.scan_upload(self.files)  # reasons to hold the site for review
        self.manifest = {path: hashlib.sha256(data).hexdigest() for path, data in self.files.items()}
        self.entry = check_entry(self.manifest, entry) if entry else pick_entry(self.manifest)
        self.size = sum(len(b) for b in self.files.values())
        self.sizes = {self.manifest[path]: len(data) for path, data in self.files.items()}


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

    async def _count_upload(self, conn, key: str | None) -> None:
        """Atomically count an upload against today's limit. key: a user's sub, or "site:<id>" for an
        edit-token upload to a site with no owner."""
        if not key:
            return
        day = time.strftime("%Y-%m-%d", time.gmtime())
        stmt = self._upsert(uploads).values(sub=key, day=day, count=1)
        stmt = stmt.on_conflict_do_update(index_elements=["sub", "day"], set_={"count": uploads.c.count + 1}).returning(
            uploads.c.count
        )
        count = (await conn.execute(stmt)).scalar_one()
        if count > MAX_UPLOADS_PER_DAY:
            raise SiteError(f"daily upload limit reached ({MAX_UPLOADS_PER_DAY}); try again tomorrow", 429)

    # ---- reading ----

    async def _site_id_for(self, conn, ref: str) -> tuple[str, str | None]:
        """(site id, the slug it was reached by or None) for a random id or a link name. 404 when unknown."""
        if ref.isalnum() and (await conn.execute(select(sites.c.id).where(sites.c.id == ref))).first():
            return ref, None
        row = (await conn.execute(select(slugs.c.site_id).where(slugs.c.slug == ref))).first()
        if row is None:
            raise SiteError("site not found", 404)
        return row.site_id, ref

    async def _current_slug(self, conn, site_id: str) -> str | None:
        q = select(slugs.c.slug).where(slugs.c.site_id == site_id).order_by(slugs.c.created_at.desc(), slugs.c.slug)
        return (await conn.execute(q.limit(1))).scalar()

    async def _name_used(self, conn, name: str) -> bool:
        """Ids and link names share the /s/ namespace, so each must be unused as either."""
        if (await conn.execute(select(slugs.c.slug).where(slugs.c.slug == name))).first():
            return True
        return bool((await conn.execute(select(sites.c.id).where(sites.c.id == name))).first())

    async def slug_available(self, slug: str) -> tuple[bool, str | None]:
        problem = slug_problem(slug)
        if problem:
            return False, problem
        await self._ready()
        async with self.engine.connect() as conn:
            return (False, "That name is taken.") if await self._name_used(conn, slug) else (True, None)

    async def _claim_slug(self, conn, site_id: str, slug: str) -> None:
        problem = slug_problem(slug)
        if problem:
            raise SiteError(problem, 400)
        if (await conn.execute(select(sites.c.id).where(sites.c.id == slug))).first():
            raise SiteError("That name is taken.", 409)
        newest = (await conn.execute(select(func.max(slugs.c.created_at)).where(slugs.c.site_id == site_id))).scalar()
        stamp = max(int(time.time()), (newest or 0) + 1)  # strictly newest, even for two renames in one second
        try:
            async with conn.begin_nested():  # a savepoint, so a taken name fails cleanly inside the transaction
                await conn.execute(slugs.insert().values(slug=slug, site_id=site_id, created_at=stamp))
        except IntegrityError as e:
            raise SiteError("That name is taken.", 409) from e

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
            "slug": await self._current_slug(conn, site_id),
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

    async def resolve(self, ref: str, n: int | None, slugs: bool = True) -> dict:
        """The version to serve for a random id or link name: version n, or the live one. 404 when none,
        451 while the site is held for a safety review (holds.py).
        "current_slug" lets the caller redirect an old link name to the site's current one.
        slugs=False: ref must be the site's id (content hosts are named by id only)."""
        await self._ready()
        async with self.engine.connect() as conn:
            site_id, via_slug = await self._site_id_for(conn, ref) if slugs else (ref, None)
            site = await self._site(conn, site_id)
            if await holds.is_held(conn, site_id):
                raise SiteError(holds.HELD_MESSAGE, 451)
            n = site.live if n is None else n
            q = select(versions).where(versions.c.site_id == site_id, versions.c.n == n)
            row = (await conn.execute(q)).first()
            newest = await self._current_slug(conn, site_id)
        if row is None:
            raise SiteError("version not found", 404)
        return {
            "site_id": site_id,
            "n": row.n,
            "entry": row.entry,
            "via_slug": via_slug,
            "current_slug": newest if via_slug else None,
            "label": hosts.site_label(site_id, newest),  # the one content host this site is served on
        }

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

    async def _store_files(self, upload: Upload) -> None:
        """Put the upload's files in storage. Done before the metadata, so a version never points at a
        missing file (files of an upload that then fails are removed by the cleanup job)."""
        written = set()
        for path, data in upload.files.items():
            digest = upload.manifest[path]
            if digest not in written:
                written.add(digest)
                await self.storage.put(f"blobs/{digest}", data, content_type(path))

    # ---- storage quota ----

    async def _owned_blob_sizes(self, sub: str) -> dict[str, int]:
        """digest -> size of every file the user's non-deleted sites use, each counted once."""
        q = (
            select(version_files.c.digest, blobs.c.size)
            .select_from(
                version_files.join(sites, sites.c.id == version_files.c.site_id).outerjoin(
                    blobs, blobs.c.digest == version_files.c.digest
                )
            )
            .where(sites.c.owner == sub, sites.c.deleted_at.is_(None))
            .distinct()
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).all()
        sizes = {r.digest: r.size for r in rows}
        missing = [d for d, size in sizes.items() if size is None]
        if missing:
            sizes |= await self._backfill_sizes(missing)
        return sizes

    async def _backfill_sizes(self, digests: list[str]) -> dict[str, int]:
        """Sizes of files stored before the blobs table existed, read from one storage listing (which
        returns every file's size), then saved for every listed file so this runs about once.
        A file missing from storage counts as 0 bytes; uploading it again records its real size."""
        listed = {key.removeprefix("blobs/"): size for key, _, size in await self.storage.list("blobs/")}
        await self._record_sizes(listed | {d: listed.get(d, 0) for d in digests}, overwrite=False)
        return {d: listed.get(d, 0) for d in digests}

    async def _record_sizes(self, sizes: dict[str, int], conn=None, overwrite: bool = True) -> None:
        if not sizes:
            return
        if conn is None:
            async with self.engine.begin() as c:
                return await self._record_sizes(sizes, c, overwrite)
        stmt = self._upsert(blobs)
        stmt = (
            stmt.on_conflict_do_update(index_elements=["digest"], set_={"size": stmt.excluded.size})
            if overwrite
            else stmt.on_conflict_do_nothing(index_elements=["digest"])
        )
        await conn.execute(stmt, [{"digest": d, "size": n} for d, n in sizes.items()])

    async def usage(self, sub: str) -> dict:
        await self._ready()
        return {"used_bytes": sum((await self._owned_blob_sizes(sub)).values()), "quota_bytes": storage_quota_bytes()}

    async def _check_quota(self, owner: str | None, upload: Upload) -> None:
        """Refuse an upload whose new files would take the owner's account over its storage quota.
        Sites without an owner (token-only, local dev without sign-in) aren't checked."""
        if not owner:
            return
        owned = await self._owned_blob_sizes(owner)
        used = sum(owned.values())
        new = sum(size for digest, size in upload.sizes.items() if digest not in owned)
        quota = storage_quota_bytes()
        if used + new > quota:
            raise SiteError(
                f"This would use {_mb(used + new)} of your {_mb(quota)}. Delete old versions or sites to make room.",
                413,
            )

    async def _add_version(self, conn, site_id: str, upload: Upload, note: str) -> int:
        # numbers of deleted versions are never reused, so a pinned link can't change what it shows
        used = union_all(
            select(versions.c.n).where(versions.c.site_id == site_id),
            select(deleted_versions.c.n).where(deleted_versions.c.site_id == site_id),
        ).subquery()
        n = (await conn.execute(select(func.coalesce(func.max(used.c.n), 0)))).scalar_one() + 1
        manifest, entry, size = upload.manifest, upload.entry, upload.size
        await self._record_sizes(upload.sizes, conn)
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
        if upload.findings:
            await holds.add(conn, site_id, n, upload.findings)
        return n

    async def create(
        self,
        name: str,
        zip_bytes: bytes,
        note: str = "",
        owner: dict | None = None,
        slug: str | None = None,
        entry: str | None = None,
    ) -> tuple[dict, str]:
        await self._ready()
        if slug and slug_problem(slug):
            raise SiteError(slug_problem(slug), 400)
        upload = Upload(zip_bytes, entry)
        await self._check_quota(owner["sub"] if owner else None, upload)
        await self._store_files(upload)
        token = secrets.token_urlsafe(24)
        site_id = await self._unused_id()
        async with self.engine.begin() as conn:
            if owner:
                count = (
                    await conn.execute(
                        select(func.count()).where(sites.c.owner == owner["sub"], sites.c.deleted_at.is_(None))
                    )
                ).scalar_one()
                if count >= MAX_SITES_PER_USER:
                    raise SiteError(f"you have {MAX_SITES_PER_USER} shared sites, the most an account can have", 429)
                await self._count_upload(conn, owner["sub"] if owner else None)
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
            if slug:
                await self._claim_slug(conn, site_id, slug)
            await self._add_version(conn, site_id, upload, note)
            meta = await self.public_meta(site_id, conn)
        return meta, token

    async def _unused_id(self) -> str:
        async with self.engine.connect() as conn:
            while True:
                candidate = _new_id()
                if not await self._name_used(conn, candidate):
                    return candidate

    async def set_slug(self, site_id: str, token: str | None, slug: str, user: dict | None = None) -> dict:
        """Give a site a (new) link name. Its earlier names keep pointing at it, so shared links never break."""
        await self._ready()
        async with self.engine.begin() as conn:
            self.check_access(await self._site(conn, site_id, lock=True), token, user)
            if await self._current_slug(conn, site_id) != slug:
                owner = (await conn.execute(select(slugs.c.site_id).where(slugs.c.slug == slug))).first()
                if owner and owner.site_id == site_id:  # switching back to one of this site's earlier names
                    newest = select(func.max(slugs.c.created_at)).where(slugs.c.site_id == site_id)
                    stamp = (await conn.execute(newest)).scalar_one() + 1
                    await conn.execute(update(slugs).where(slugs.c.slug == slug).values(created_at=stamp))
                else:
                    await self._claim_slug(conn, site_id, slug)
            return await self.public_meta(site_id, conn)

    async def add_version(
        self,
        site_id: str,
        token: str | None,
        zip_bytes: bytes,
        note: str = "",
        user: dict | None = None,
        entry: str | None = None,
    ) -> dict:
        await self._ready()
        async with self.engine.connect() as conn:  # check access before spending storage on the upload
            site = await self._site(conn, site_id)
            self.check_access(site, token, user)
        upload = Upload(zip_bytes, entry)
        await self._check_quota(site.owner, upload)  # the owner's quota, also when a token holder publishes
        await self._store_files(upload)
        try:
            async with self.engine.begin() as conn:
                site = await self._site(conn, site_id, lock=True)  # serialises publishes to one site
                self.check_access(site, token, user)
                count = (await conn.execute(select(func.count()).where(versions.c.site_id == site_id))).scalar_one()
                if count >= MAX_VERSIONS_PER_SITE:
                    raise SiteError(
                        f"this site has {MAX_VERSIONS_PER_SITE} versions, the most a site can have. "
                        "Delete old versions to make room.",
                        429,
                    )
                # the publisher, else (an edit token) the site's owner, else the site itself
                await self._count_upload(conn, user["sub"] if user else site.owner or f"site:{site_id}")
                await self._add_version(conn, site_id, upload, note)
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

    async def version_files(self, site_id: str, token: str | None, n: int, user: dict | None = None) -> dict:
        """A shared version's files (path -> digest) and start page, for its owner to take back into a browser."""
        await self._ready()
        async with self.engine.connect() as conn:
            self.check_access(await self._site(conn, site_id), token, user)
            row = (await conn.execute(select(versions).where(versions.c.site_id == site_id, versions.c.n == n))).first()
            if row is None:
                raise SiteError("no such version", 404)
            q = select(version_files.c.path, version_files.c.digest).where(
                version_files.c.site_id == site_id, version_files.c.n == n
            )
            files = {r.path: r.digest for r in (await conn.execute(q)).all()}
        return {"n": n, "entry": row.entry, "files": files}

    async def site_blob(self, site_id: str, token: str | None, digest: str, user: dict | None = None) -> bytes:
        """One file's original bytes, only when one of this site's versions uses it."""
        await self._ready()
        async with self.engine.connect() as conn:
            self.check_access(await self._site(conn, site_id), token, user)
            q = select(version_files.c.digest).where(
                version_files.c.site_id == site_id, version_files.c.digest == digest
            )
            if (await conn.execute(q.limit(1))).first() is None:
                raise SiteError("no such file in this site", 404)
        data = await self.read_blob(digest)
        if data is None:
            raise SiteError("no such file in this site", 404)
        return data

    async def set_entry(self, site_id: str, token: str | None, n: int, entry: str, user: dict | None = None) -> dict:
        """Change which page a shared version opens on (its live and pinned links)."""
        await self._ready()
        async with self.engine.begin() as conn:
            self.check_access(await self._site(conn, site_id, lock=True), token, user)
            q = select(version_files.c.path).where(version_files.c.site_id == site_id, version_files.c.n == n)
            paths = set((await conn.execute(q)).scalars())
            if not paths:
                raise SiteError("no such version", 404)
            entry = check_entry(paths, entry)
            await conn.execute(
                update(versions).where(versions.c.site_id == site_id, versions.c.n == n).values(entry=entry)
            )
            return await self.public_meta(site_id, conn)

    async def delete_version(self, site_id: str, token: str | None, n: int, user: dict | None = None) -> dict:
        """Remove one shared version: its pinned link stops working and its number is never reused.
        The live version can't be removed. Its files are freed by the cleanup job if nothing else uses them."""
        await self._ready()
        async with self.engine.begin() as conn:
            site = await self._site(conn, site_id, lock=True)
            self.check_access(site, token, user)
            exists = (
                await conn.execute(select(versions.c.n).where(versions.c.site_id == site_id, versions.c.n == n))
            ).first()
            if not exists:
                raise SiteError("no such version", 404)
            if site.live == n:
                raise SiteError("This is the live version. Make another version live first.", 409)
            await conn.execute(delete(version_files).where(version_files.c.site_id == site_id, version_files.c.n == n))
            await conn.execute(delete(versions).where(versions.c.site_id == site_id, versions.c.n == n))
            await conn.execute(deleted_versions.insert().values(site_id=site_id, n=n, deleted_at=int(time.time())))
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
            known = set((await conn.execute(select(blobs.c.digest))).scalars())
        now = time.time()
        listed = await self.storage.list("blobs/")
        orphans = [
            key
            for key, uploaded, _ in listed
            if key.removeprefix("blobs/") not in referenced and now - uploaded > min_age_seconds
        ]
        if orphans:
            await self.storage.delete(orphans)
        gone = {key.removeprefix("blobs/") for key in orphans}
        if gone:
            async with self.engine.begin() as conn:
                await conn.execute(delete(blobs).where(blobs.c.digest.in_(gone)))
        # sizes of files stored before the blobs table existed (the quota also fills these in lazily)
        skip = known | gone
        await self._record_sizes(
            {d: size for key, _, size in listed if (d := key.removeprefix("blobs/")) not in skip}, overwrite=False
        )
        return {"files_referenced": len(referenced), "files_deleted": len(orphans)}

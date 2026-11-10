"""Key/value byte storage. LocalStorage for dev and tests, BlobStorage (Vercel Blob) in production.

Every store offers: put, get (fresh=True for keys that get overwritten), delete, list.
"""

import asyncio
import calendar
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote

import httpx


class StorageError(Exception):
    pass


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        def write():
            p = self._path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)

        await asyncio.to_thread(write)

    async def get(self, key: str, fresh: bool = False) -> bytes | None:
        def read():
            p = self._path(key)
            return p.read_bytes() if p.is_file() else None

        return await asyncio.to_thread(read)

    async def delete(self, keys: list[str]) -> None:
        def remove():
            for key in keys:
                self._path(key).unlink(missing_ok=True)

        await asyncio.to_thread(remove)

    async def list(self, prefix: str) -> list[tuple[str, float]]:
        """(key, uploaded at as a unix time) for every key under prefix."""

        def walk():
            base = self._path(prefix)
            if not base.is_dir():
                return []
            return [
                (p.relative_to(self.root).as_posix(), p.stat().st_mtime)
                for p in base.rglob("*")
                if p.is_file() and not p.name.endswith(".tmp")
            ]

        return await asyncio.to_thread(walk)


class BlobStorage:
    """Vercel Blob over its REST API, the same calls @vercel/blob makes.

    With a private store (BLOB_ACCESS=private, the default) every read needs the token, so files are
    reachable only through this app. Reads go through Vercel's CDN cache unless fresh=True (?cache=0),
    which keys that get overwritten (site and user metadata) must use.
    """

    API = "https://vercel.com/api/blob"
    API_VERSION = "11"

    def __init__(self, token: str, access: str = "private"):
        if access not in ("private", "public"):
            raise StorageError("BLOB_ACCESS must be private or public")
        # Token format: vercel_blob_rw_<storeId>_<secret>
        parts = token.split("_")
        if len(parts) < 5:
            raise StorageError("BLOB_READ_WRITE_TOKEN has an unexpected format")
        self.token = token
        self.access = access
        self.base = f"https://{parts[3].lower()}.{access}.blob.vercel-storage.com"

    def _auth(self) -> dict:
        return {"authorization": f"Bearer {self.token}", "x-api-version": self.API_VERSION}

    def _url(self, key: str) -> str:
        return f"{self.base}/{quote(key)}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        headers = self._auth() | {
            "x-vercel-blob-access": self.access,
            "x-content-type": content_type,
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-cache-control-max-age": "60",
        }
        try:
            # Client per call: a shared client breaks if the event loop changes between requests.
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.put(self.API, params={"pathname": key}, content=data, headers=headers)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise StorageError(f"blob put failed for {key}: {e}") from e

    async def get(self, key: str, fresh: bool = False) -> bytes | None:
        params = {}
        if fresh:
            params = {"cache": "0"} if self.access == "private" else {"v": secrets.token_hex(4)}
        headers = {"authorization": f"Bearer {self.token}"} if self.access == "private" else {}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(self._url(key), params=params, headers=headers)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except httpx.HTTPError as e:
            raise StorageError(f"blob get failed for {key}: {e}") from e

    async def delete(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 1000):
            urls = [self._url(k) for k in keys[start : start + 1000]]
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    r = await client.post(f"{self.API}/delete", json={"urls": urls}, headers=self._auth())
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise StorageError(f"blob delete failed: {e}") from e

    async def list(self, prefix: str) -> list[tuple[str, float]]:
        out, cursor = [], None
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                while True:
                    params = {"prefix": prefix, "limit": "1000"} | ({"cursor": cursor} if cursor else {})
                    r = await client.get(self.API, params=params, headers=self._auth())
                    r.raise_for_status()
                    body = r.json()
                    for b in body.get("blobs", []):
                        uploaded = calendar.timegm(time.strptime(b["uploadedAt"][:19], "%Y-%m-%dT%H:%M:%S"))
                        out.append((b["pathname"], float(uploaded)))
                    cursor = body.get("cursor")
                    if not body.get("hasMore") or not cursor:
                        return out
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise StorageError(f"blob list failed for {prefix}: {e}") from e


def make_storage():
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if token:
        return BlobStorage(token, os.environ.get("BLOB_ACCESS", "private"))
    return LocalStorage(os.environ.get("LOCAL_DATA_DIR", ".data"))

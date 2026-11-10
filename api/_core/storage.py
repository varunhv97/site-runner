"""Key/value byte storage. LocalStorage for dev, BlobStorage (Vercel Blob) for prod."""

import asyncio
import os
import secrets
from pathlib import Path

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

    async def get(self, key: str) -> bytes | None:
        def read():
            p = self._path(key)
            return p.read_bytes() if p.is_file() else None

        return await asyncio.to_thread(read)


class BlobStorage:
    """Vercel Blob via its REST API (public store, no random suffix, overwrite allowed)."""

    API = "https://blob.vercel-storage.com"

    def __init__(self, token: str):
        self.token = token
        # Token format: vercel_blob_rw_<storeId>_<secret>
        parts = token.split("_")
        if len(parts) < 5:
            raise StorageError("BLOB_READ_WRITE_TOKEN has an unexpected format")
        self.public_base = f"https://{parts[3].lower()}.public.blob.vercel-storage.com"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        headers = {
            "authorization": f"Bearer {self.token}",
            "x-api-version": "7",
            "x-content-type": content_type,
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-cache-control-max-age": "60",
        }
        try:
            # Client per call: a shared client breaks if the event loop changes between requests.
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.put(f"{self.API}/{key}", content=data, headers=headers)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise StorageError(f"blob put failed for {key}: {e}") from e

    async def get(self, key: str) -> bytes | None:
        # Cache-bust so mutable keys (meta.json) are fresh.
        url = f"{self.public_base}/{key}?v={secrets.token_hex(4)}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except httpx.HTTPError as e:
            raise StorageError(f"blob get failed for {key}: {e}") from e


def make_storage():
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if token:
        return BlobStorage(token)
    return LocalStorage(os.environ.get("LOCAL_DATA_DIR", ".data"))

"""Async client for the Site Runner share API, authenticated with an account API token (srk_...)."""

import httpx

from . import __version__


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, base_url: str, token: str | None, transport: httpx.AsyncBaseTransport | None = None):
        headers = {"User-Agent": f"spinsite/{__version__}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=60, transport=transport)
        self.base_url = base_url

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _call(self, method: str, url: str, **kw) -> dict:
        try:
            res = await self._http.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise ApiError(f"can't reach {self.base_url}: {e}") from e
        try:
            body = res.json()
        except ValueError:
            body = {}
        if res.status_code >= 400:
            message = body.get("error") if isinstance(body, dict) else None
            raise ApiError(message or f"request failed ({res.status_code})", res.status_code)
        return body

    # ---- account ----

    async def me(self) -> dict | None:
        return (await self._call("GET", "/api/auth/me")).get("user")

    async def sites(self) -> list[dict]:
        return (await self._call("GET", "/api/me/sites"))["sites"]

    async def usage(self) -> dict:
        return await self._call("GET", "/api/me/usage")

    async def find_site(self, ref: str) -> dict:
        """One of the account's sites by id, link name or (when unique) display name."""
        sites = await self.sites()
        ref_l = ref.strip().lower()
        for s in sites:
            if s["id"] == ref or (s.get("slug") or "") == ref_l:
                return s
        named = [s for s in sites if s["name"].lower() == ref_l]
        if len(named) == 1:
            return named[0]
        if named:
            raise ApiError(f'several of your sites are called "{ref}"; use its id or link name', 409)
        raise ApiError(f'no site "{ref}" in your account', 404)

    # ---- publishing ----

    async def create_site(self, zipped: bytes, name: str = "", slug: str = "", note: str = "", entry: str = "") -> dict:
        data = {"name": name, "slug": slug, "note": note, "entry": entry}
        return await self._call("POST", "/api/sites", files={"file": ("site.zip", zipped)}, data=data)

    async def add_version(self, site_id: str, zipped: bytes, note: str = "", entry: str = "") -> dict:
        files = {"file": ("site.zip", zipped)}
        return await self._call(
            "POST", f"/api/sites/{site_id}/versions", files=files, data={"note": note, "entry": entry}
        )

    async def set_live(self, site_id: str, n: int) -> dict:
        return (await self._call("POST", f"/api/sites/{site_id}/live", json={"version": n}))["site"]

    async def set_slug(self, site_id: str, slug: str) -> dict:
        return await self._call("POST", f"/api/sites/{site_id}/slug", json={"slug": slug})

    async def delete_version(self, site_id: str, n: int) -> dict:
        return (await self._call("DELETE", f"/api/sites/{site_id}/versions/{n}"))["site"]

    async def delete_site(self, site_id: str) -> None:
        await self._call("DELETE", f"/api/sites/{site_id}")

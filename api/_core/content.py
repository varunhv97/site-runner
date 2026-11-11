"""The user-content origin: shared sites on <site id>.<content domain> and local previews on p-<id>.<content domain>.

Only reachable through HostRouter (api/index.py) for hosts under USERCONTENT_BASE_DOMAIN. None of the app's
routes exist here, the server never reads cookies here and never sets one.
"""

import logging
import re
from collections.abc import Callable
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import OperationalError

from . import hosts
from .paths import resolve_path
from .sites import SiteError, content_type
from .storage import StorageError

log = logging.getLogger("site-runner")

# Every response on the content origin. allow-same-origin gives each site its own real origin (its own
# localStorage, IndexedDB and cookies); the origin is a subdomain that holds nothing of the app's.
SANDBOX_CSP = (
    "sandbox allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox "
    "allow-modals allow-downloads allow-pointer-lock allow-top-navigation-by-user-activation"
)
CONTENT_HEADERS = {
    "content-security-policy": SANDBOX_CSP,
    "origin-agent-cluster": "?1",
    "x-content-type-options": "nosniff",
}

PINNED = re.compile(r"^@(\d{1,9})(/|$)", re.ASCII)


def not_found(message: str = "Not found in this site.") -> Response:
    return Response(f"<h1>404</h1><p>{message}</p>", 404, media_type="text/html")


def _query(request: Request) -> str:
    return f"?{request.url.query}" if request.url.query else ""


def make_content_app(get_sites: Callable) -> FastAPI:
    """get_sites returns the Sites instance (looked up per request, so tests can swap it)."""
    content = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @content.exception_handler(StorageError)
    @content.exception_handler(OperationalError)
    async def unavailable(_: Request, exc: Exception):
        log.error("content: %s", exc)
        return Response("Unavailable right now. Try again in a minute.", 503, media_type="text/plain")

    @content.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error on content %s %s", request.method, request.url.path)
        return Response("Something went wrong on the server.", 500, media_type="text/plain")

    @content.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def serve(path: str, request: Request):
        label = hosts.content_label(request.headers.get("host")) or ""
        if hosts.SITE_LABEL.match(label):
            return await serve_site(get_sites(), request, label, path)
        return not_found("There's no site here.")

    return content


async def serve_site(sites, request: Request, site_id: str, path: str) -> Response:
    """/ follows the live version, /@<n>/ pins version n. Same origin for both, so they share storage."""
    if request.headers.get("service-worker") == "script":
        # a site's own service worker could keep serving it after a takedown
        return Response("Service workers aren't allowed on shared sites.", 403, media_type="text/plain")

    pinned = PINNED.match(path)
    if pinned and not pinned.group(2):
        return RedirectResponse(f"/{path}/{_query(request)}", status_code=308)
    base = f"/{pinned.group(0)}" if pinned else "/"
    rest = path[pinned.end() :] if pinned else path
    try:
        version = await sites.resolve(site_id, int(pinned.group(1)) if pinned else None, slugs=False)
    except SiteError:
        return not_found()

    digests: dict[str, str] = {}

    async def exists(p: str) -> bool:
        digest = await sites.file_digest(version["site_id"], version["n"], p)
        if digest:
            digests[p] = digest
        return digest is not None

    found = await resolve_path(rest, version["entry"], exists)
    if found[0] == "redirect":
        return RedirectResponse(f"{base}{quote(found[1])}{_query(request)}", status_code=found[2])
    if found[0] == "missing":
        return not_found()
    file_path = found[1]
    digest = digests[file_path]

    # Files never change for a digest, so its hash is the ETag: a revisit gets a 304 instead of the file.
    etag = f'"{digest}"'
    ctype = content_type(file_path)
    headers = {
        # a pinned version's files never change; its start page (rest == "") can be changed by the owner
        "Cache-Control": "public, max-age=3600" if pinned and rest else "no-cache",
        "ETag": etag,
    }
    if not ctype.startswith("text/html"):
        headers["Access-Control-Allow-Origin"] = "*"
    if etag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    data = await sites.read_blob(digest)
    if data is None:
        return not_found()
    return Response(data, media_type=ctype, headers=headers)

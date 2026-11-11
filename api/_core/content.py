"""The user-content origin: shared sites on <site id>.<content domain> and local previews on p-<id>.<content domain>.

Only reachable through HostRouter (api/index.py) for hosts under USERCONTENT_BASE_DOMAIN. None of the app's
routes exist here, the server never reads cookies here and never sets one.
"""

import functools
import json
import logging
import pathlib
import re
import secrets
from collections.abc import Callable
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import OperationalError

from . import holds, hosts
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

_PREVIEW = pathlib.Path(__file__).parent / "preview"


@functools.cache
def _asset(name: str) -> str:
    """lib.js, sw.js, boot.js. Read on first use, so a missing file only breaks previews, not the server."""
    return (_PREVIEW / name).read_text()


def _js(value) -> str:
    """JSON safe to put inside a <script> element."""
    return json.dumps(value).replace("<", "\\u003c")


BOOT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Loading preview</title>
<style nonce="__NONCE__">
body{margin:0;min-height:100vh;display:grid;place-items:center;font:15px/1.5 system-ui,sans-serif;color:#555;background:#fff}
</style>
</head>
<body>
<p id="status">Loading preview…</p>
<script nonce="__NONCE__">
__SCRIPT__
</script>
</body>
</html>
"""


def not_found(message: str = "Not found in this site.") -> Response:
    return Response(f"<h1>404</h1><p>{message}</p>", 404, media_type="text/html")


def held() -> Response:
    """A site held for a safety review (holds.py): nothing of it is served until an admin approves it."""
    return Response(
        f"<h1>451</h1><p>{holds.HELD_MESSAGE}</p>", 451, media_type="text/html", headers={"Cache-Control": "no-store"}
    )


def _query(request: Request) -> str:
    return f"?{request.url.query}" if request.url.query else ""


def make_content_app(get_sites: Callable) -> FastAPI:
    """get_sites returns the Sites instance (looked up per request, so tests can swap it)."""
    content = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @content.exception_handler(StorageError)
    @content.exception_handler(OperationalError)
    @content.exception_handler(hosts.ConfigError)
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
        if hosts.PREVIEW_LABEL.match(label):  # first: p-<id> is never a site
            return serve_preview(request, path)
        if hosts.SITE_LABEL.match(label) and "--" not in label:
            return await serve_site(get_sites(), request, label, path)
        return not_found("There's no site here.")

    return content


def serve_preview(request: Request, path: str) -> Response:
    """p-<local id> hosts: only the boot page and the service worker come from here. Once the worker is in,
    it serves everything else from the files /app handed over (they never reach this server)."""
    if path == "__sr/sw.js":
        return Response(
            _asset("lib.js") + "\n" + _asset("sw.js").replace("__SANDBOX_CSP__", _js(SANDBOX_CSP)),
            media_type="text/javascript; charset=utf-8",
            headers={
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-cache",
                "Content-Security-Policy": f"{SANDBOX_CSP}; default-src 'none'",
            },
        )
    if request.headers.get("service-worker") == "script":
        return Response("Only the preview's own service worker runs here.", 403, media_type="text/plain")
    if path == "__sr/boot":
        app = hosts.app_origin()
        nonce = secrets.token_urlsafe(16)
        script = _asset("lib.js") + "\n" + _asset("boot.js").replace("__APP_ORIGIN__", _js(app))
        html = BOOT_HTML.replace("__SCRIPT__", script).replace("__NONCE__", nonce)
        return Response(
            html,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": (
                    f"{SANDBOX_CSP}; default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                    f"worker-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors {app}"
                ),
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )
    return not_found("This preview isn't loaded. Open it again from Site Runner.")


async def serve_site(sites, request: Request, label: str, path: str) -> Response:
    """/ follows the live version, /@<n>/ pins version n. Same origin for both, so they share storage.
    label: the site's link name or id. Each site is served on one host only (its current name, else its id), so
    visitors' saved data is never split; its other hosts (the id, earlier names) redirect there."""
    if request.headers.get("service-worker") == "script":
        # a site's own service worker could keep serving it after a takedown
        return Response("Service workers aren't allowed on shared sites.", 403, media_type="text/plain")

    pinned = PINNED.match(path)
    if pinned and not pinned.group(2):
        return RedirectResponse(f"/{path}/{_query(request)}", status_code=308)
    base = f"/{pinned.group(0)}" if pinned else "/"
    rest = path[pinned.end() :] if pinned else path
    try:
        version = await sites.resolve(label, int(pinned.group(1)) if pinned else None)
    except SiteError as e:
        return held() if e.status == 451 else not_found()
    if version["label"] != label:  # the id or an earlier name: go to the site's one host, same path and query
        return RedirectResponse(
            f"{hosts.site_origin(version['label'])}/{quote(path, safe='/@')}{_query(request)}", status_code=308
        )

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

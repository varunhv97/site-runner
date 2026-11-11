"""FastAPI app: share API (/api/*) and shared-site serving (/s/<id>[@<version>]/...).

Runs as a Vercel Python function in prod; `uvicorn api.index:app --port 8000` in dev.
"""

import hmac
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, File, Form, Header, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from _core import auth  # noqa: E402
from _core.frames import FRAME_VERSION, frame_page, wrapper_page  # noqa: E402
from _core.db import make_engine  # noqa: E402
from _core.sites import SiteError, Sites, content_type  # noqa: E402
from _core.storage import StorageError, make_storage, oidc_token  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("site-runner")

app = FastAPI(title="Site Runner", docs_url=None, redoc_url=None, openapi_url=None)
sites = Sites(make_storage(), make_engine())


@app.middleware("http")
async def remember_oidc_token(request: Request, call_next):
    """Blob storage authenticates with the per-request OIDC token Vercel attaches to function calls."""
    oidc_token.set(request.headers.get("x-vercel-oidc-token"))
    return await call_next(request)


# Shared content runs with an opaque origin: it can't read this app's cookies/storage.
# Top navigation (on a click only) lets links leave the /s/ wrapper's frame, as they would at top level.
SANDBOX_FLAGS = (
    "allow-scripts allow-forms allow-popups allow-modals allow-downloads allow-pointer-lock "
    "allow-top-navigation-by-user-activation"
)
SANDBOX_CSP = f"sandbox {SANDBOX_FLAGS}"


@app.exception_handler(SiteError)
async def site_error_handler(_: Request, exc: SiteError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


@app.exception_handler(StorageError)
async def storage_error_handler(_: Request, exc: StorageError):
    log.error("storage error: %s", exc)
    return JSONResponse({"error": "Storage is unavailable right now. Try again in a minute."}, status_code=502)


@app.exception_handler(OperationalError)
async def database_error_handler(_: Request, exc: OperationalError):
    log.error("database error: %s", exc)
    return JSONResponse({"error": "The database is unavailable right now. Try again in a minute."}, status_code=503)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": "Something went wrong on the server."}, status_code=500)


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _base_url(request: Request) -> str:
    base = os.environ.get("PUBLIC_BASE_URL")
    if not base:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        base = f"{proto}://{host}"
    return base.rstrip("/")


def _share_url(request: Request, ref: str, version: int | None = None) -> str:
    """ref: the site's link name when it has one, else its random id."""
    return f"{_base_url(request)}/s/{ref}{f'@{version}' if version else ''}/"


def _ref(meta: dict) -> str:
    return meta.get("slug") or meta["id"]


def _current_user(request: Request) -> dict | None:
    if not auth.enabled():
        return None
    return auth.verify(request.cookies.get(auth.SESSION_COOKIE))


def _check_origin(request: Request) -> None:
    """Browser writes must come from this app. Shared sites run with an opaque origin and send
    "Origin: null", so they can never act with the visitor's session. API and MCP clients send no Origin."""
    origin = request.headers.get("origin")
    if origin is not None and origin != _base_url(request):
        raise SiteError("request from another origin", 403)


@app.get("/api/health")
async def health(request: Request):
    """Liveness plus whether storage credentials are usable (true/false only, never the values)."""
    storage_ok = True
    try:
        await sites.storage.get("health/probe")
    except StorageError:
        storage_ok = False
    if os.environ.get("VERCEL_ENV") == "production":
        return {"ok": True, "storage_reachable": storage_ok}  # no details about the setup in public
    return {
        "ok": True,
        "storage": type(sites.storage).__name__,
        "storage_reachable": storage_ok,
        "oidc_header": "x-vercel-oidc-token" in request.headers,
        "database": sites.engine.dialect.name,
    }


# ---- Google sign-in ----


@app.get("/api/auth/login")
async def login(request: Request, next: str = "/app"):
    if not auth.enabled():
        return JSONResponse({"error": "Google sign-in is not set up on this server"}, status_code=503)
    state, cookie = auth.new_state(next)
    res = RedirectResponse(auth.login_url(state, f"{_base_url(request)}/api/auth/callback"), status_code=302)
    res.set_cookie(
        auth.STATE_COOKIE,
        cookie,
        max_age=auth.STATE_TTL,
        httponly=True,
        secure=_base_url(request).startswith("https"),
        samesite="lax",
        path="/api/auth",
    )
    return res


@app.get("/api/auth/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    saved = auth.verify(request.cookies.get(auth.STATE_COOKIE))
    if error or not code or not saved or not state or saved.get("state") != state:
        return RedirectResponse("/app?signin=failed", status_code=302)
    try:
        user = await auth.fetch_google_user(code, f"{_base_url(request)}/api/auth/callback")
    except auth.AuthError:
        return RedirectResponse("/app?signin=failed", status_code=302)
    await sites.touch_user(user)
    res = RedirectResponse(saved["next"], status_code=302)
    res.delete_cookie(auth.STATE_COOKIE, path="/api/auth")
    res.set_cookie(
        auth.SESSION_COOKIE,
        auth.session_value(user),
        max_age=auth.SESSION_TTL,
        httponly=True,
        secure=_base_url(request).startswith("https"),
        samesite="lax",
        path="/",
    )
    return res


@app.post("/api/auth/logout")
async def logout(request: Request):
    _check_origin(request)
    res = JSONResponse({"ok": True})
    res.delete_cookie(auth.SESSION_COOKIE, path="/")
    return res


@app.get("/api/auth/me")
async def me(request: Request):
    user = _current_user(request)
    return {"enabled": auth.enabled(), "user": {"email": user["email"], "name": user["name"]} if user else None}


@app.get("/api/me/sites")
async def my_sites(request: Request):
    user = _current_user(request)
    if not user:
        raise SiteError("sign in to list your sites", 401)
    return {"sites": [meta | {"url": _share_url(request, _ref(meta))} for meta in await sites.user_sites(user["sub"])]}


@app.get("/api/me/usage")
async def my_usage(request: Request):
    """Storage the signed-in account uses: unique files across its shared sites, against its quota."""
    user = _current_user(request)
    if not user:
        raise SiteError("sign in to see your storage", 401)
    return await sites.usage(user["sub"])


# ---- Sites ----


@app.post("/api/sites")
async def create_site(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(""),
    note: str = Form(""),
    slug: str = Form(""),
    entry: str = Form(""),
):
    _check_origin(request)
    user = _current_user(request)
    if auth.enabled() and not user:
        raise SiteError("sign in with Google to share a site", 401)
    if not auth.enabled() and os.environ.get("VERCEL_ENV") == "production":
        # no anonymous public hosting: production shares only once Google sign-in is configured
        raise SiteError("Sharing opens once Google sign-in is set up on this site", 503)
    meta, token = await sites.create(
        name, await file.read(), note, owner=user, slug=slug.strip().lower() or None, entry=entry or None
    )
    return {
        "site": meta,
        "edit_token": token,
        "url": _share_url(request, _ref(meta)),
        "version_url": _share_url(request, _ref(meta), meta["live"]),
    }


@app.post("/api/sites/{site_id}/versions")
async def add_version(
    request: Request,
    site_id: str,
    file: UploadFile = File(...),
    note: str = Form(""),
    entry: str = Form(""),
    authorization: str | None = Header(None),
):
    _check_origin(request)
    meta = await sites.add_version(
        site_id, _bearer(authorization), await file.read(), note, user=_current_user(request), entry=entry or None
    )
    return {
        "site": meta,
        "url": _share_url(request, _ref(meta)),
        "version_url": _share_url(request, _ref(meta), meta["live"]),
    }


class LiveBody(BaseModel):
    version: int


class EntryBody(BaseModel):
    entry: str


class SlugBody(BaseModel):
    slug: str


@app.get("/api/slugs/{slug}")
async def slug_available(slug: str):
    """Is this link name free? For the share dialog's live check."""
    available, reason = await sites.slug_available(slug.strip().lower())
    return {"available": available, "reason": reason}


@app.post("/api/sites/{site_id}/slug")
async def set_slug(request: Request, site_id: str, body: SlugBody, authorization: str | None = Header(None)):
    """Give a site a link name (/s/<slug>/). Earlier names keep working and forward to the new one."""
    _check_origin(request)
    meta = await sites.set_slug(site_id, _bearer(authorization), body.slug.strip().lower(), _current_user(request))
    return {"site": meta, "url": _share_url(request, _ref(meta))}


@app.post("/api/sites/{site_id}/live")
async def set_live(request: Request, site_id: str, body: LiveBody, authorization: str | None = Header(None)):
    _check_origin(request)
    meta = await sites.set_live(site_id, _bearer(authorization), body.version, user=_current_user(request))
    return {"site": meta}


@app.post("/api/sites/{site_id}/versions/{n}/entry")
async def set_entry(request: Request, site_id: str, n: int, body: EntryBody, authorization: str | None = Header(None)):
    """Change the page a shared version opens on."""
    _check_origin(request)
    meta = await sites.set_entry(site_id, _bearer(authorization), n, body.entry, _current_user(request))
    return {"site": meta}


@app.delete("/api/sites/{site_id}/versions/{n}")
async def delete_version(request: Request, site_id: str, n: int, authorization: str | None = Header(None)):
    """Remove a shared version that isn't live: its pinned link (/s/<ref>@n/) stops working."""
    _check_origin(request)
    meta = await sites.delete_version(site_id, _bearer(authorization), n, user=_current_user(request))
    return {"site": meta}


@app.delete("/api/sites/{site_id}")
async def delete_site(request: Request, site_id: str, authorization: str | None = Header(None)):
    """Unpublish: the owner, the token holder, or an admin (ADMIN_EMAILS) for takedowns."""
    _check_origin(request)
    user = _current_user(request)
    await sites.delete(site_id, _bearer(authorization), user)
    log.info("site %s unpublished by %s", site_id, user["email"] if user else "token")
    return {"ok": True}


@app.post("/api/sites/{site_id}/token")
async def rotate_token(request: Request, site_id: str, authorization: str | None = Header(None)):
    _check_origin(request)
    token = await sites.rotate_token(site_id, _bearer(authorization), _current_user(request))
    return {"edit_token": token}


@app.post("/api/sites/{site_id}/claim")
async def claim_site(request: Request, site_id: str, authorization: str | None = Header(None)):
    """Attach a site shared before sign-in to the signed-in account, proven by its edit token."""
    _check_origin(request)
    user = _current_user(request)
    if not user:
        raise SiteError("sign in to claim a site", 401)
    meta = await sites.claim(site_id, _bearer(authorization), user)
    return {"site": meta}


@app.get("/api/sites/{site_id}")
async def get_site(site_id: str):
    return {"site": await sites.public_meta(site_id)}


@app.get("/api/cron/cleanup")
async def cron_cleanup(authorization: str | None = Header(None)):
    """Daily Vercel Cron job (vercel.json). Vercel sends "Authorization: Bearer $CRON_SECRET"."""
    secret = os.environ.get("CRON_SECRET")
    if not secret or not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
        raise SiteError("not allowed", 401)
    result = await sites.cleanup()
    log.info("cleanup: %s", result)
    return result


# ---- Serving shared sites ----


def _not_found() -> Response:
    return Response("<h1>404</h1><p>Not found in this site.</p>", 404, media_type="text/html")


def _is_html(path: str) -> bool:
    return content_type(path).startswith("text/html")


@app.api_route("/s/{ref}", methods=["GET", "HEAD"])
@app.api_route("/r/{ref}", methods=["GET", "HEAD"])
async def serve_root_redirect(ref: str, request: Request):
    q = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"{request.url.path}/{q}", status_code=308)


async def _locate(prefix: str, ref: str, path: str) -> Response | tuple[dict, str, str]:
    """Resolve a shared-site path to (version, file path, digest), or a redirect / 404 response."""
    site_id, _, vstr = ref.partition("@")
    pinned = bool(vstr)
    try:
        n = int(vstr) if pinned else None
    except ValueError:
        return _not_found()
    try:
        version = await sites.resolve(site_id, n)
    except SiteError:
        return _not_found()
    if version["via_slug"] and version["current_slug"] != version["via_slug"]:
        # an earlier link name of a renamed site: forward to the current one, keeping the version and path
        target = version["current_slug"] + (f"@{vstr}" if pinned else "")
        return RedirectResponse(f"{prefix}{target}/{path}", status_code=308)

    base = f"{prefix}{ref}/"
    if path == "":
        if "/" in version["entry"]:
            return RedirectResponse(base + version["entry"], status_code=307)
        path = version["entry"]
    elif path.endswith("/"):
        path += "index.html"

    digest = await sites.file_digest(version["site_id"], version["n"], path)
    if digest is None:
        if await sites.file_digest(version["site_id"], version["n"], f"{path}/index.html"):
            return RedirectResponse(f"{base}{path}/", status_code=308)
        return _not_found()
    return version, path, digest


async def _file_response(
    request: Request, ref: str, path: str, digest: str, transform=None, fixed: bool = True
) -> Response:
    # Files never change for a digest, so its hash is the ETag: a revisit gets a 304 instead of the file.
    # /r/ HTML also depends on the frame script, so its version is part of the tag.
    etag = f'"{digest}-{FRAME_VERSION}"' if transform else f'"{digest}"'
    headers = {
        "Content-Security-Policy": SANDBOX_CSP,
        "Access-Control-Allow-Origin": "*",
        "X-Content-Type-Options": "nosniff",
        # a pinned version's files never change; its start page (fixed=False) can be changed by the owner
        "Cache-Control": "public, max-age=3600" if "@" in ref and fixed else "no-cache",
        "ETag": etag,
    }
    if etag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    data = await sites.read_blob(digest)
    if data is None:
        return _not_found()
    return Response(transform(data) if transform else data, media_type=content_type(path), headers=headers)


@app.api_route("/s/{ref}/{path:path}", methods=["GET", "HEAD"])
async def serve(ref: str, path: str, request: Request):
    """HTML pages get the wrapper that saves the site's localStorage; other files are served as-is."""
    found = await _locate("/s/", ref, path)
    if isinstance(found, Response):
        return found
    version, file_path, digest = found
    if not _is_html(file_path):
        return await _file_response(request, ref, file_path, digest)
    html, nonce = wrapper_page(version["site_id"], ref, path, SANDBOX_FLAGS)
    return Response(
        html,
        media_type="text/html; charset=utf-8",
        headers={
            # our code only: the shared site itself runs in the sandboxed /r/ frame
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "frame-src 'self'; img-src 'self' https: data:; base-uri 'none'; form-action 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Cache-Control": "no-cache",
        },
    )


@app.api_route("/r/{ref}/{path:path}", methods=["GET", "HEAD"])
async def serve_frame(ref: str, path: str, request: Request):
    """What the /s/ wrapper frames: the site's files, sandboxed, with the storage script in HTML pages."""
    found = await _locate("/r/", ref, path)
    if isinstance(found, Response):
        return found
    _, file_path, digest = found
    transform = (lambda data: frame_page(data, ref)) if _is_html(file_path) else None
    return await _file_response(request, ref, file_path, digest, transform, fixed=path != "")

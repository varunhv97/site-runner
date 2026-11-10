"""FastAPI app: share API (/api/*) and shared-site serving (/s/<id>[@<version>]/...).

Runs as a Vercel Python function in prod; `uvicorn api.index:app --port 8000` in dev.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, File, Form, Header, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from _core import auth  # noqa: E402
from _core.sites import SiteError, Sites, content_type, public_meta  # noqa: E402
from _core.storage import StorageError, make_storage  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("site-runner")

app = FastAPI(title="Site Runner", docs_url=None, redoc_url=None, openapi_url=None)
sites = Sites(make_storage())

# Shared content runs with an opaque origin: it can't read this app's cookies/storage.
SANDBOX_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads allow-pointer-lock"


@app.exception_handler(SiteError)
async def site_error_handler(_: Request, exc: SiteError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


@app.exception_handler(StorageError)
async def storage_error_handler(_: Request, exc: StorageError):
    log.error("storage error: %s", exc)
    return JSONResponse({"error": "Storage is unavailable right now. Try again in a minute."}, status_code=502)


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


def _share_url(request: Request, site_id: str, version: int | None = None) -> str:
    return f"{_base_url(request)}/s/{site_id}{f'@{version}' if version else ''}/"


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
async def health():
    return {"ok": True, "storage": type(sites.storage).__name__}


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
    found = []
    for site_id in await sites.list_user_sites(user["sub"]):
        try:
            meta = await sites.load_meta(site_id)
        except SiteError:
            continue
        found.append(public_meta(meta) | {"url": _share_url(request, site_id)})
    return {"sites": found}


# ---- Sites ----


@app.post("/api/sites")
async def create_site(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(""),
    note: str = Form(""),
):
    _check_origin(request)
    user = _current_user(request)
    if auth.enabled() and not user:
        raise SiteError("sign in with Google to share a site", 401)
    if not auth.enabled() and os.environ.get("VERCEL_ENV") == "production":
        # no anonymous public hosting: production shares only once Google sign-in is configured
        raise SiteError("Sharing opens once Google sign-in is set up on this site", 503)
    meta, token = await sites.create(name, await file.read(), note, owner=user)
    return {
        "site": public_meta(meta),
        "edit_token": token,
        "url": _share_url(request, meta["id"]),
        "version_url": _share_url(request, meta["id"], meta["live"]),
    }


@app.post("/api/sites/{site_id}/versions")
async def add_version(
    request: Request,
    site_id: str,
    file: UploadFile = File(...),
    note: str = Form(""),
    authorization: str | None = Header(None),
):
    _check_origin(request)
    meta = await sites.add_version(
        site_id, _bearer(authorization), await file.read(), note, user=_current_user(request)
    )
    return {
        "site": public_meta(meta),
        "url": _share_url(request, site_id),
        "version_url": _share_url(request, site_id, meta["live"]),
    }


class LiveBody(BaseModel):
    version: int


@app.post("/api/sites/{site_id}/live")
async def set_live(request: Request, site_id: str, body: LiveBody, authorization: str | None = Header(None)):
    _check_origin(request)
    meta = await sites.set_live(site_id, _bearer(authorization), body.version, user=_current_user(request))
    return {"site": public_meta(meta)}


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
    return {"site": public_meta(meta)}


@app.get("/api/sites/{site_id}")
async def get_site(site_id: str):
    return {"site": public_meta(await sites.load_meta(site_id))}


# ---- Serving shared sites ----


def _not_found() -> Response:
    return Response("<h1>404</h1><p>Not found in this site.</p>", 404, media_type="text/html")


@app.api_route("/s/{ref}", methods=["GET", "HEAD"])
async def serve_root_redirect(ref: str, request: Request):
    q = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/s/{ref}/{q}", status_code=308)


@app.api_route("/s/{ref}/{path:path}", methods=["GET", "HEAD"])
async def serve(ref: str, path: str):
    site_id, _, vstr = ref.partition("@")
    meta = await sites.load_meta(site_id)
    pinned = bool(vstr)
    try:
        n = int(vstr) if pinned else meta["live"]
        if n < 1:
            raise IndexError
        version = meta["versions"][n - 1]
    except (ValueError, IndexError, TypeError):
        return _not_found()

    base = f"/s/{ref}/"
    manifest = version["manifest"]
    if path == "":
        if "/" in version["entry"]:
            return RedirectResponse(base + version["entry"], status_code=307)
        path = version["entry"]
    elif path.endswith("/"):
        path += "index.html"

    if path not in manifest:
        if f"{path}/index.html" in manifest:
            return RedirectResponse(f"{base}{path}/", status_code=308)
        return _not_found()

    data = await sites.read_file(version, path)
    if data is None:
        return _not_found()
    return Response(
        data,
        media_type=content_type(path),
        headers={
            "Content-Security-Policy": SANDBOX_CSP,
            "Access-Control-Allow-Origin": "*",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=3600" if pinned else "no-cache",
        },
    )

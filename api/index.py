"""FastAPI app: the share API (/api/*) on the app's host, and user content on the content hosts.

HostRouter sends requests for hosts under USERCONTENT_BASE_DOMAIN to the content app (_core/content.py);
every other host gets the app routes. /s/<ref>[@n]/<path> (and the old /r/) redirect to the content host.
Runs as a Vercel Python function in prod; `uvicorn api.index:app --port 8000` in dev.
"""

import hmac
import logging
import os
import re
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, File, Form, Header, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from _core import auth, hosts  # noqa: E402
from _core.content import CONTENT_HEADERS, make_content_app, not_found  # noqa: E402
from _core.db import make_engine  # noqa: E402
from _core.sites import SiteError, Sites  # noqa: E402
from _core.storage import StorageError, make_storage, oidc_token  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("site-runner")

app = FastAPI(title="Site Runner", docs_url=None, redoc_url=None, openapi_url=None)
sites = Sites(make_storage(), make_engine())
content_app = make_content_app(lambda: sites)


async def _plain(send, status: int, text: str) -> None:
    headers = {"content-type": "text/plain; charset=utf-8", **CONTENT_HEADERS}
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }
    )
    await send({"type": "http.response.body", "body": text.encode()})


class HostRouter:
    """The security boundary between the app and user content, by the Host header (which a browser can't forge).

    Content hosts (under USERCONTENT_BASE_DOMAIN) only reach the content app: no cookies in, none out, and
    the sandbox/isolation headers on every response. Other hosts reach the app routes, unless this deployment
    is content-only (CONTENT_ONLY=1). Also remembers the per-request OIDC token Blob storage authenticates with.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        oidc_token.set(headers.get("x-vercel-oidc-token"))
        label = hosts.content_label(headers.get("host"))  # None when unset or misconfigured: never content
        if label is None:
            if hosts.content_only():
                return await _plain(send, 404, "Not found.")
            return await self.inner(scope, receive, send)

        scope = dict(scope, headers=[(k, v) for k, v in scope["headers"] if k.lower() != b"cookie"])

        async def send_content(message):
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"set-cookie"]
                present = {k.lower() for k, _ in kept}
                kept += [(k.encode(), v.encode()) for k, v in CONTENT_HEADERS.items() if k.encode() not in present]
                message = dict(message, headers=kept)
            await send(message)

        await content_app(scope, receive, send_content)


app.add_middleware(HostRouter)


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
        if os.environ.get("VERCEL_ENV") == "production":
            # never build links, sign-in callbacks or the origin check from request headers in production
            raise SiteError("PUBLIC_BASE_URL must be set in production", 503)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        base = f"{proto}://{host}"
    return base.rstrip("/")


def _share_url(request: Request, ref: str, version: int | None = None) -> str:
    """ref: the site's link name when it has one, else its random id."""
    return f"{_base_url(request)}/s/{ref}{f'@{version}' if version else ''}/"


def _ref(meta: dict) -> str:
    return meta.get("slug") or meta["id"]


def _secure(request: Request) -> bool:
    return _base_url(request).startswith("https://")


def _cookie(request: Request, name: str) -> str:
    return auth.cookie_name(name, _secure(request))


def _current_user(request: Request) -> dict | None:
    if not auth.enabled():
        return None
    return auth.verify(request.cookies.get(_cookie(request, auth.SESSION_COOKIE)))


def _content_config() -> dict:
    domain = hosts.content_domain()
    return {"content_scheme": hosts.content_scheme(domain), "content_domain": domain}


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


@app.get("/api/config")
async def config():
    """Where user content lives, for /app to build preview origins (https://p-<local id>.<content domain>)."""
    try:
        return _content_config()
    except hosts.ConfigError as e:
        raise SiteError(str(e), 503) from e


# ---- Google sign-in ----


@app.get("/api/auth/login")
async def login(request: Request, next: str = "/app"):
    if not auth.enabled():
        return JSONResponse({"error": "Google sign-in is not set up on this server"}, status_code=503)
    state, cookie = auth.new_state(next)
    res = RedirectResponse(auth.login_url(state, f"{_base_url(request)}/api/auth/callback"), status_code=302)
    res.set_cookie(
        _cookie(request, auth.STATE_COOKIE),
        cookie,
        max_age=auth.STATE_TTL,
        httponly=True,
        secure=_secure(request),
        samesite="lax",
        path="/",  # __Host- cookies must use Path=/
    )
    return res


@app.get("/api/auth/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    saved = auth.verify(request.cookies.get(_cookie(request, auth.STATE_COOKIE)))
    if error or not code or not saved or not state or saved.get("state") != state:
        return RedirectResponse("/app?signin=failed", status_code=302)
    try:
        user = await auth.fetch_google_user(code, f"{_base_url(request)}/api/auth/callback")
    except auth.AuthError:
        return RedirectResponse("/app?signin=failed", status_code=302)
    await sites.touch_user(user)
    res = RedirectResponse(saved["next"], status_code=302)
    secure = _secure(request)
    res.delete_cookie(_cookie(request, auth.STATE_COOKIE), path="/", secure=secure, httponly=True)
    if secure:  # the cookies' names before the __Host- prefix
        res.delete_cookie(auth.SESSION_COOKIE, path="/")
        res.delete_cookie(auth.STATE_COOKIE, path="/api/auth")
    res.set_cookie(
        _cookie(request, auth.SESSION_COOKIE),
        auth.session_value(user),
        max_age=auth.SESSION_TTL,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return res


@app.post("/api/auth/logout")
async def logout(request: Request):
    _check_origin(request)
    res = JSONResponse({"ok": True})
    res.delete_cookie(_cookie(request, auth.SESSION_COOKIE), path="/", secure=_secure(request), httponly=True)
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


@app.get("/api/sites/{site_id}/versions/{n}/files")
async def version_files(request: Request, site_id: str, n: int, authorization: str | None = Header(None)):
    """A version's files and start page, so its owner can open the site in another browser."""
    return await sites.version_files(site_id, _bearer(authorization), n, _current_user(request))


@app.get("/api/sites/{site_id}/files/{digest}")
async def site_file(request: Request, site_id: str, digest: str, authorization: str | None = Header(None)):
    """One file's original bytes for the owner. Always a sandboxed download: the bytes are a shared site's
    (untrusted) content, so they must never render on this app's origin."""
    data = await sites.site_blob(site_id, _bearer(authorization), digest, _current_user(request))
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=31536000, immutable",  # a digest's bytes never change
        },
    )


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


# ---- Shared links: everything moved to the content hosts ----

_REF = re.compile(r"^([^@]+)(?:@(\d{1,9}))?$", re.ASCII)


@app.api_route("/s/{ref}", methods=["GET", "HEAD"])
@app.api_route("/r/{ref}", methods=["GET", "HEAD"])
async def to_content_root(request: Request, ref: str):
    return await to_content_host(request, ref, "")


@app.api_route("/s/{ref}/{path:path}", methods=["GET", "HEAD"])
@app.api_route("/r/{ref}/{path:path}", methods=["GET", "HEAD"])
async def to_content_host(request: Request, ref: str, path: str):
    """/s/<name or id>[@n]/<path> -> https://<id>.<content domain>/[@n/]<path>, keeping the query.
    /s/ links stay the canonical share links; this app's origin never serves a shared site's files.
    (/r/ was the sandboxed frame of the old localStorage bridge; its links redirect the same way.)"""
    m = _REF.match(ref)
    if not m:
        return not_found()
    pinned = m.group(2)
    try:
        version = await sites.resolve(m.group(1), int(pinned) if pinned else None)
        origin = hosts.site_origin(version["site_id"])
    except SiteError:
        return not_found()
    except hosts.ConfigError as e:
        raise SiteError(str(e), 503) from e
    q = f"?{request.url.query}" if request.url.query else ""
    prefix = f"@{pinned}/" if pinned else ""
    return RedirectResponse(f"{origin}/{prefix}{quote(path)}{q}", status_code=308)

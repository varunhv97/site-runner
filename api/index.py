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

from _core import abuse, auth, hosts  # noqa: E402
from _core.content import CONTENT_HEADERS, held, make_content_app, not_found  # noqa: E402
from _core.db import make_engine  # noqa: E402
from _core.sites import SiteError, Sites, is_admin  # noqa: E402
from _core.storage import StorageError, make_storage, oidc_token  # noqa: E402
from _core import holds  # noqa: E402

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
    """Browser writes must come from this app's exact origin. Shared sites and previews run on subdomains of the
    content domain (same-site with the app until it's on the Public Suffix List, so the browser does send the
    session cookie): their Origin is their own, so they are refused. API and MCP clients send no Origin."""
    origin = request.headers.get("origin")
    if origin is not None and origin != _base_url(request):
        raise SiteError("request from another origin", 403)


async def _can_share(user: dict | None, site_id: str | None = None) -> None:
    """Banned accounts can't share: refuses writes by a banned user, and writes (also with an edit token) to a
    banned owner's site. Every share write route calls this before doing anything."""
    await abuse.check_can_share(sites, user, site_id)


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
    await _can_share(user)
    meta, token = await sites.create(
        name, await file.read(), note, owner=user, slug=slug.strip().lower() or None, entry=entry or None
    )
    return {
        "site": meta,
        "edit_token": token,
        "url": _share_url(request, _ref(meta)),
        "version_url": _share_url(request, _ref(meta), meta["live"]),
    } | await holds.site_status(sites.engine, meta["id"])


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
    await _can_share(_current_user(request), site_id)
    meta = await sites.add_version(
        site_id, _bearer(authorization), await file.read(), note, user=_current_user(request), entry=entry or None
    )
    return {
        "site": meta,
        "url": _share_url(request, _ref(meta)),
        "version_url": _share_url(request, _ref(meta), meta["live"]),
    } | await holds.site_status(sites.engine, meta["id"])


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
    await _can_share(_current_user(request), site_id)
    meta = await sites.set_slug(site_id, _bearer(authorization), body.slug.strip().lower(), _current_user(request))
    return {"site": meta, "url": _share_url(request, _ref(meta))}


@app.post("/api/sites/{site_id}/live")
async def set_live(request: Request, site_id: str, body: LiveBody, authorization: str | None = Header(None)):
    _check_origin(request)
    await _can_share(_current_user(request), site_id)
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
    await _can_share(_current_user(request), site_id)
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
    if is_admin(user):
        await abuse.action_site_reports(sites, [site_id], user)  # a takedown settles the site's open reports
    log.info("site %s unpublished by %s", site_id, user["email"] if user else "token")
    return {"ok": True}


@app.post("/api/sites/{site_id}/token")
async def rotate_token(request: Request, site_id: str, authorization: str | None = Header(None)):
    _check_origin(request)
    await _can_share(_current_user(request), site_id)
    token = await sites.rotate_token(site_id, _bearer(authorization), _current_user(request))
    return {"edit_token": token}


@app.post("/api/sites/{site_id}/claim")
async def claim_site(request: Request, site_id: str, authorization: str | None = Header(None)):
    """Attach a site shared before sign-in to the signed-in account, proven by its edit token."""
    _check_origin(request)
    user = _current_user(request)
    if not user:
        raise SiteError("sign in to claim a site", 401)
    await _can_share(user, site_id)
    meta = await sites.claim(site_id, _bearer(authorization), user)
    return {"site": meta}


@app.get("/api/sites/{site_id}")
async def get_site(site_id: str):
    return {"site": await sites.public_meta(site_id)}


# ---- Abuse: reports, admin review, bans ----


class ReportBody(BaseModel):
    url: str
    reason: str
    details: str = ""
    contact: str = ""


class ReportStatusBody(BaseModel):
    status: str


class BanBody(BaseModel):
    email: str
    reason: str = ""


def _client_ip(request: Request) -> str:
    """The visitor's IP for rate limits. Vercel sets X-Real-IP / X-Forwarded-For itself (clients can't spoof
    them there); elsewhere these headers are only as trustworthy as the proxy in front."""
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
    return forwarded.strip() or (request.client.host if request.client else "unknown")


def _require_admin(request: Request) -> dict:
    user = _current_user(request)
    if not is_admin(user):
        raise SiteError("admins only", 403)
    return user


# ---- Safety holds (scan.py flags an upload; holds.py keeps the site private until an admin decides) ----


class HoldDecision(BaseModel):
    decision: str


def _admin_for_holds(request: Request) -> dict:
    if not _current_user(request):
        raise SiteError("sign in as an admin", 401)
    return _require_admin(request)


@app.post("/api/reports")
async def report_site(request: Request, body: ReportBody):
    """Report a shared site (anyone, signed in or not). Rate-limited per IP."""
    _check_origin(request)
    report = await abuse.create_report(
        sites,
        url=body.url,
        reason=body.reason,
        details=body.details,
        contact=body.contact,
        user=_current_user(request),
        ip=_client_ip(request),
        base_url=_base_url(request),
    )
    log.info("report %s about site %s (%s)", report["id"], report["site_id"], body.reason)
    return {"ok": True, "id": report["id"]}


@app.get("/api/admin/reports")
async def admin_reports(request: Request, status: str = "open"):
    _require_admin(request)
    rows = await abuse.list_reports(sites, status)
    return {"reports": [r | {"site_url": _share_url(request, r["slug"] or r["site_id"])} for r in rows]}


@app.post("/api/admin/reports/{report_id}")
async def admin_report_status(request: Request, report_id: int, body: ReportStatusBody):
    _check_origin(request)
    admin = _require_admin(request)
    return {"report": await abuse.set_report_status(sites, report_id, body.status, admin)}


@app.get("/api/admin/bans")
async def admin_bans(request: Request):
    _require_admin(request)
    return {"bans": await abuse.list_bans(sites)}


@app.post("/api/admin/bans")
async def admin_ban(request: Request, body: BanBody):
    """Ban an account by email: it can't share, and its shared sites are unpublished."""
    _check_origin(request)
    admin = _require_admin(request)
    result = await abuse.ban(sites, body.email, body.reason, admin)
    log.info("%s banned by %s; unpublished %s", result["ban"]["email"], admin["email"], result["sites_unpublished"])
    return result


@app.delete("/api/admin/bans/{email}")
async def admin_unban(request: Request, email: str):
    _check_origin(request)
    admin = _require_admin(request)
    await abuse.unban(sites, email)
    log.info("%s unbanned by %s", email, admin["email"])
    return {"ok": True}


@app.get("/api/sites/{site_id}/hold")
async def site_hold(request: Request, site_id: str, authorization: str | None = Header(None)):
    """{"held": bool, "reasons": [...]} for the owner, so /app can show why links don't work yet."""
    return await holds.owner_status(sites, site_id, _bearer(authorization), _current_user(request))


@app.get("/api/admin/holds")
async def admin_holds(request: Request, status: str = "held"):
    """Sites held for review (status=held|approved|rejected|all)."""
    _admin_for_holds(request)
    await sites._ready()
    return {"holds": await holds.list_holds(sites.engine, None if status == "all" else status)}


@app.post("/api/admin/holds/{site_id}")
async def admin_review_hold(request: Request, site_id: str, body: HoldDecision):
    """approve: the site's links work again. reject: the site is unpublished (an admin takedown)."""
    _check_origin(request)
    user = _admin_for_holds(request)
    if body.decision not in ("approve", "reject"):
        raise SiteError('decision must be "approve" or "reject"', 400)
    await sites._ready()
    if not await holds.review(sites.engine, site_id, body.decision == "approve", user["email"]):
        raise SiteError("this site has no open hold", 404)
    if body.decision == "reject":
        try:
            await sites.delete(site_id, None, user)
        except SiteError as e:
            if e.status != 404:  # already unpublished: nothing left to do
                raise
    log.info("hold on site %s: %s by %s", site_id, body.decision, user["email"])
    return {"ok": True, "site_id": site_id, "decision": body.decision}


@app.get("/api/cron/cleanup")
async def cron_cleanup(authorization: str | None = Header(None)):
    """Daily Vercel Cron job (vercel.json). Vercel sends "Authorization: Bearer $CRON_SECRET"."""
    secret = os.environ.get("CRON_SECRET")
    if not secret or not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
        raise SiteError("not allowed", 401)
    result = await sites.cleanup()
    result["rate_limits_pruned"] = await abuse.prune_rate_limits(sites)
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
    except SiteError as e:
        return held() if e.status == 451 else not_found()
    except hosts.ConfigError as e:
        raise SiteError(str(e), 503) from e
    q = f"?{request.url.query}" if request.url.query else ""
    prefix = f"@{pinned}/" if pinned else ""
    return RedirectResponse(f"{origin}/{prefix}{quote(path)}{q}", status_code=308)

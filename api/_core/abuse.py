"""Abuse handling: reports of shared sites, admin review, account bans and a simple rate limit.

Works on the same database as `Sites` (tables `reports`, `bans`, `rate_limits` in db.py). Every function takes
the `Sites` instance, so tests that swap `api.index.sites` get these on the same database.
"""

import os
import re
import time
from urllib.parse import unquote, urlsplit

from sqlalchemy import delete, func, or_, select, update

from .db import bans, rate_limits, reports, users
from .db import sites as sites_t
from . import hosts, mailer
from .sites import SiteError, is_admin

REASONS = ("phishing", "malware", "scam", "spam", "illegal", "other")
STATUSES = ("open", "actioned", "dismissed")
MAX_URL = 500
MAX_DETAILS = 2000
MAX_CONTACT = 320
MAX_BAN_REASON = 500
# Reports per client IP per hour (fixed window, counted in rate_limits). Anyone can report, so this keeps
# one source from flooding the queue. Requests that fail the cheap format checks aren't counted.
REPORTS_PER_HOUR = 10
# Report emails: one per site per hour (a flood about one site is one email) and REPORT_EMAILS_PER_DAY in all,
# under Resend's free 100 a day. Past either, reports are still stored for /admin; only the email is skipped.
REPORT_EMAILS_PER_DAY = 40
LIST_LIMIT = 200
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_CONTACT = "https://github.com/varunhv97/site-runner/issues"
REASON_LABELS = {
    "phishing": "Phishing",
    "malware": "Malware or a harmful download",
    "scam": "Scam or fraud",
    "spam": "Spam",
    "illegal": "Illegal content",
    "other": "Something else",
}


def contact() -> str:
    """Where people write about abuse and bans: ABUSE_CONTACT (an email or URL), else the issue tracker."""
    return os.environ.get("ABUSE_CONTACT") or DEFAULT_CONTACT


def banned_message() -> str:
    return f"This account can't share sites. Contact {contact()} if you think this is a mistake."


def _window() -> str:
    """The current hour (UTC): rate limits count in fixed one-hour windows."""
    return time.strftime("%Y-%m-%dT%H", time.gmtime())


# ---- reports ----


def site_ref_from_url(url: str, base_url: str) -> str:
    """The id or link name in a share link of this app (<base>/s/<ref>[@n]/...) or a user-content URL
    (https://<ref>.<USERCONTENT_BASE_DOMAIN>/...). 400 for anything else."""
    text = url.strip()
    if not text:
        raise SiteError("Paste the link to the site you're reporting.", 400)
    if len(text) > MAX_URL:
        raise SiteError(f"That link is too long (at most {MAX_URL} characters).", 400)
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as e:
        raise SiteError("That doesn't look like a link.", 400) from e
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or not parts.hostname:
        raise SiteError("That doesn't look like a link.", 400)
    host = parts.hostname.lower()
    base = urlsplit(base_url)
    try:
        domain = hosts.content_domain().lower()  # the same setting (and local default) content hosts use
    except hosts.ConfigError:
        domain = ""
    if host == (base.hostname or "").lower() and port == base.port:
        segments = parts.path.split("/")
        ref = unquote(segments[2]).partition("@")[0] if len(segments) > 2 and segments[1] == "s" else ""
        if not ref:
            raise SiteError(f"That isn't a shared site's link. Those look like {base_url}/s/name/.", 400)
    elif domain and (parts.netloc.lower() if ":" in domain else host).endswith("." + domain):
        ref = (parts.netloc.lower() if ":" in domain else host)[: -len(domain) - 1]
        if not ref or "." in ref:
            raise SiteError("That isn't a shared site's link.", 400)
    else:
        raise SiteError(f"Only sites shared on {base.hostname} can be reported here.", 400)
    return ref.lower()


async def _hit_rate_limit(sites, conn, key: str, limit: int) -> None:
    stmt = sites._upsert(rate_limits).values(key=key[:300], window=_window(), count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key", "window"], set_={"count": rate_limits.c.count + 1}
    ).returning(rate_limits.c.count)
    if (await conn.execute(stmt)).scalar_one() > limit:
        raise SiteError("Too many reports from your network. Try again in an hour.", 429)


async def _bump(sites, key: str, window: str) -> int:
    """Count one more in (key, window) and return the new count (never refuses)."""
    async with sites.engine.begin() as conn:
        stmt = sites._upsert(rate_limits).values(key=key[:300], window=window, count=1)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key", "window"], set_={"count": rate_limits.c.count + 1}
        ).returning(rate_limits.c.count)
        return (await conn.execute(stmt)).scalar_one()


async def prune_rate_limits(sites) -> int:
    """Drop counters of past windows (called by the daily cleanup cron)."""
    await sites._ready()
    async with sites.engine.begin() as conn:
        return (await conn.execute(delete(rate_limits).where(rate_limits.c.window < _window()))).rowcount


async def create_report(
    sites, *, url: str, reason: str, details: str, contact: str, user: dict | None, ip: str, base_url: str
) -> dict:
    """Store a report about a shared site. Anyone may report; REPORTS_PER_HOUR per IP."""
    if reason not in REASONS:
        raise SiteError(f"Pick a reason: {', '.join(REASONS)}.", 400)
    details, contact = details.strip(), contact.strip()
    if len(details) > MAX_DETAILS:
        raise SiteError(f"Details are too long (at most {MAX_DETAILS} characters).", 400)
    if reason == "other" and not details:
        raise SiteError("Tell us what's wrong with the site in the details.", 400)
    if contact and (len(contact) > MAX_CONTACT or not EMAIL_RE.match(contact)):
        raise SiteError("The contact email doesn't look right. Leave it empty if you prefer.", 400)
    ref = site_ref_from_url(url, base_url)
    await sites._ready()
    async with sites.engine.begin() as conn:
        await _hit_rate_limit(sites, conn, f"report:{ip}", REPORTS_PER_HOUR)
    # the counter is committed on its own, so failed lookups below still count toward the limit
    async with sites.engine.begin() as conn:
        try:
            site_id, _ = await sites._site_id_for(conn, ref)
        except SiteError as e:
            raise SiteError("We couldn't find a shared site at that link.", 404) from e
        deleted = (await conn.execute(select(sites_t.c.deleted_at).where(sites_t.c.id == site_id))).scalar()
        if deleted:
            raise SiteError("That site isn't shared any more.", 404)
        row = (
            await conn.execute(
                reports.insert()
                .values(
                    site_id=site_id,
                    url=url.strip(),
                    reason=reason,
                    details=details,
                    contact=contact,
                    reporter=user["sub"] if user else None,
                    created_at=int(time.time()),
                    status="open",
                )
                .returning(reports.c.id)
            )
        ).scalar_one()
        slug = await sites._current_slug(conn, site_id)
        owner = (
            await conn.execute(
                select(users.c.email)
                .select_from(sites_t.join(users, users.c.sub == sites_t.c.owner))
                .where(sites_t.c.id == site_id)
            )
        ).scalar()
    emailed = await _notify(sites, row, site_id, slug, owner, url.strip(), reason, details, contact, user, base_url)
    return {"id": row, "site_id": site_id, "emailed": emailed}


async def _notify(sites, report_id, site_id, slug, owner, url, reason, details, contact, user, base_url) -> bool:
    """Email REPORT_EMAIL about a new report (mailer.py; off without RESEND_API_KEY). The report is already
    stored, so a skipped or failed email only costs the instant notice: it's still in /admin."""
    to = os.environ.get("REPORT_EMAIL", "").strip()
    if not to or not mailer.enabled():
        return False
    if await _bump(sites, f"report-email-site:{site_id}", _window()) > 1:
        return False  # this site was already emailed about this hour
    sent_today = await _bump(sites, "report-emails", time.strftime("%Y-%m-%d", time.gmtime()))
    if sent_today > REPORT_EMAILS_PER_DAY:
        return False
    name = slug or site_id
    lines = [
        f"Reason:   {REASON_LABELS.get(reason, reason)}",
        f"Site:     {name} (id {site_id})",
        f"Owner:    {owner or 'no account (edit token only)'}",
        f"Link:     {url}",
        f"Reporter: {user['email'] if user else 'not signed in'}" + (f", contact {contact}" if contact else ""),
        "",
        "Details:",
        details or "(none given)",
        "",
        f"Review it, unpublish the site or ban the owner at {base_url}/admin (report #{report_id}).",
    ]
    if sent_today == REPORT_EMAILS_PER_DAY:
        lines += [
            "",
            f"This is the last report email today ({REPORT_EMAILS_PER_DAY}): later reports are only in /admin.",
        ]
    subject = f"[Site Runner] {REASON_LABELS.get(reason, reason)} report: {name}"
    return await mailer.send(to, subject, "\n".join(lines), reply_to=contact or None)


async def list_reports(sites, status: str = "open") -> list[dict]:
    """Newest first, with the site's link name, owner email, report count and state, for the admin page."""
    if status not in STATUSES + ("all",):
        raise SiteError(f"status must be one of {', '.join(STATUSES + ('all',))}", 400)
    await sites._ready()
    per_site = select(reports.c.site_id, func.count().label("n")).group_by(reports.c.site_id).subquery()
    q = (
        select(
            reports,
            per_site.c.n.label("site_reports"),
            sites_t.c.owner,
            sites_t.c.deleted_at,
            users.c.email.label("owner_email"),
        )
        .select_from(
            reports.join(per_site, per_site.c.site_id == reports.c.site_id)
            .outerjoin(sites_t, sites_t.c.id == reports.c.site_id)
            .outerjoin(users, users.c.sub == sites_t.c.owner)
        )
        .order_by(reports.c.created_at.desc(), reports.c.id.desc())
        .limit(LIST_LIMIT)
    )
    if status != "all":
        q = q.where(reports.c.status == status)
    async with sites.engine.connect() as conn:
        rows = (await conn.execute(q)).all()
        out = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "site_id": r.site_id,
                    "slug": await sites._current_slug(conn, r.site_id),
                    "owner_email": r.owner_email,
                    "owner_banned": await _is_banned(conn, r.owner, r.owner_email) if r.owner else False,
                    "site_unpublished": r.deleted_at is not None,
                    "url": r.url,
                    "reason": r.reason,
                    "details": r.details,
                    "contact": r.contact,
                    "created_at": r.created_at,
                    "status": r.status,
                    "site_reports": r.site_reports,
                }
            )
    return out


async def set_report_status(sites, report_id: int, status: str, admin: dict) -> dict:
    if status not in ("actioned", "dismissed"):
        raise SiteError("status must be actioned or dismissed", 400)
    await sites._ready()
    async with sites.engine.begin() as conn:
        res = await conn.execute(
            update(reports)
            .where(reports.c.id == report_id)
            .values(status=status, handled_by=admin["email"], handled_at=int(time.time()))
        )
        if res.rowcount == 0:
            raise SiteError("no such report", 404)
    return {"id": report_id, "status": status}


async def action_site_reports(sites, site_ids: list[str], admin: dict, conn=None) -> None:
    """Mark the open reports of these sites actioned (after a takedown or a ban)."""
    if not site_ids:
        return
    if conn is None:
        await sites._ready()
        async with sites.engine.begin() as c:
            return await action_site_reports(sites, site_ids, admin, c)
    await conn.execute(
        update(reports)
        .where(reports.c.site_id.in_(site_ids), reports.c.status == "open")
        .values(status="actioned", handled_by=admin["email"], handled_at=int(time.time()))
    )


# ---- bans ----


async def _is_banned(conn, sub: str | None, email: str | None) -> bool:
    conds = []
    if sub:
        conds.append(bans.c.sub == sub)
    if email:
        conds.append(bans.c.email == email.lower())
    if not conds:
        return False
    return (await conn.execute(select(bans.c.email).where(or_(*conds)).limit(1))).first() is not None


async def check_can_share(sites, user: dict | None, site_id: str | None = None) -> None:
    """403 when the signed-in account is banned, or (for writes to a site, also with its edit token) when the
    site's owner is. The one check every share write path calls."""
    await sites._ready()
    async with sites.engine.connect() as conn:
        if user and await _is_banned(conn, user.get("sub"), user.get("email")):
            raise SiteError(banned_message(), 403)
        if site_id:
            q = (
                select(sites_t.c.owner, users.c.email)
                .select_from(sites_t.outerjoin(users, users.c.sub == sites_t.c.owner))
                .where(sites_t.c.id == site_id)
            )
            row = (await conn.execute(q)).first()
            if row and row.owner and await _is_banned(conn, row.owner, row.email):
                raise SiteError(banned_message(), 403)


def _ban_dict(row) -> dict:
    return {"email": row.email, "reason": row.reason, "created_at": row.created_at, "by": row.by_admin}


async def ban(sites, email: str, reason: str, admin: dict) -> dict:
    """Ban an account by email: it can't share any more, and its shared sites are unpublished now (their open
    reports marked actioned). Unbanning doesn't bring those sites back."""
    email = email.strip().lower()
    if len(email) > MAX_CONTACT or not EMAIL_RE.match(email):
        raise SiteError("Enter the account's email address.", 400)
    if is_admin({"email": email}):
        raise SiteError("Admins can't be banned. Remove the address from ADMIN_EMAILS first.", 400)
    reason = reason.strip()[:MAX_BAN_REASON]
    now = int(time.time())
    await sites._ready()
    async with sites.engine.begin() as conn:
        subs = list((await conn.execute(select(users.c.sub).where(func.lower(users.c.email) == email))).scalars())
        values = dict(
            email=email, sub=subs[0] if subs else None, reason=reason, created_at=now, by_admin=admin["email"]
        )
        stmt = sites._upsert(bans).values(**values)
        await conn.execute(
            stmt.on_conflict_do_update(index_elements=["email"], set_={k: v for k, v in values.items() if k != "email"})
        )
        ids = []
        if subs:
            live = select(sites_t.c.id).where(sites_t.c.owner.in_(subs), sites_t.c.deleted_at.is_(None))
            ids = list((await conn.execute(live)).scalars())
        if ids:
            await conn.execute(
                update(sites_t).where(sites_t.c.id.in_(ids)).values(deleted_at=now, deleted_by_admin=True)
            )
            await action_site_reports(sites, ids, admin, conn)
        row = (await conn.execute(select(bans).where(bans.c.email == email))).first()
    return {"ban": _ban_dict(row), "sites_unpublished": ids}


async def unban(sites, email: str) -> None:
    await sites._ready()
    async with sites.engine.begin() as conn:
        res = await conn.execute(delete(bans).where(bans.c.email == email.strip().lower()))
        if res.rowcount == 0:
            raise SiteError("no ban for that email", 404)


async def list_bans(sites) -> list[dict]:
    await sites._ready()
    async with sites.engine.connect() as conn:
        rows = (await conn.execute(select(bans).order_by(bans.c.created_at.desc()).limit(LIST_LIMIT))).all()
    return [_ban_dict(r) for r in rows]

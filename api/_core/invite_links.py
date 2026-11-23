"""One-time invite links: the owner makes a link, anyone signed in who opens it within an hour can join once.

The link is `<PUBLIC_BASE_URL>/join#<code>`. The code sits in the fragment, so it never reaches a server log or a
Referer; /join sends it in a POST body. It's a bearer credential: only the owner makes one (browser session or API
token, never an edit token, and never through MCP), only its sha256 is stored, and the code is shown once.
Redeeming marks the link used with one guarded UPDATE in the same transaction as the membership, so two redeems of
one code can't both succeed. Lookups and redeems answer every kind of failure with the same 404, so a guesser learns
nothing about which codes existed. Outstanding links take a member seat (members.seats_used).
"""

import secrets
import time

from sqlalchemy import select, update

from . import abuse
from .abuse import _hit_rate_limit
from .db import invite_links, site_members, sites as sites_t, users
from .members import FULL_MESSAGE, MAX_MEMBERS, _account_email, _outstanding_links, _owned_site, seats_used
from .sites import SiteError, _hash_token, _new_id

PREFIX = "srj_"
TTL = 3600  # a link works for an hour
MAX_CODE = 100
# Links an owner makes per hour.
CREATES_PER_HOUR = 30
# Lookups plus redeems per hour, per account and per client IP: codes can't be guessed (256 bits), and this keeps
# anyone from trying.
TRIES_PER_HOUR = 20
IP_TRIES_PER_HOUR = 200  # higher: an office or carrier NAT shares one address, and one account mustn't lock it out
# How far back the owner's list shows links that were used.
RECENT_USED = 7 * 24 * 3600
INVALID = "This invite link is invalid, expired or already used."


def _now() -> int:
    return int(time.time())


def _redeemable(now: int):
    return (
        invite_links.c.used_at.is_(None),
        invite_links.c.revoked_at.is_(None),
        invite_links.c.expires_at > now,
    )


# ---- the owner's side ----


async def create(sites, site_id: str, user: dict | None) -> tuple[dict, str]:
    """A new link: (its listing, the code, shown once). 409 when the site has no seat left for it."""
    await sites._ready()
    code = PREFIX + secrets.token_urlsafe(32)
    now = _now()
    async with sites.engine.begin() as conn:
        await _owned_site(sites, conn, site_id, user, lock=True)  # serialises seat counting for this site
        if await seats_used(conn, site_id, now) >= MAX_MEMBERS:
            raise SiteError(FULL_MESSAGE, 409)
        await _hit_rate_limit(
            sites, conn, f"invite-link:{user['sub']}", CREATES_PER_HOUR, "Too many invite links this hour."
        )
        row = {"id": _new_id(), "created_at": now, "expires_at": now + TTL}
        await conn.execute(
            invite_links.insert().values(**row, site_id=site_id, token_hash=_hash_token(code), created_by=user["sub"])
        )
    return row, code


async def list_links(sites, site_id: str, user: dict | None) -> dict:
    """Outstanding links and links used in the last week (with who used them). Never a code."""
    await sites._ready()
    now = _now()
    async with sites.engine.connect() as conn:
        await _owned_site(sites, conn, site_id, user)
        q = select(invite_links.c.id, invite_links.c.created_at, invite_links.c.expires_at)
        outstanding = await conn.execute(q.where(*_outstanding_links(site_id, now)).order_by(invite_links.c.created_at))
        q = (
            select(invite_links.c.id, invite_links.c.created_at, invite_links.c.used_at, users.c.email)
            .select_from(invite_links.outerjoin(users, users.c.sub == invite_links.c.used_by))
            .where(invite_links.c.site_id == site_id, invite_links.c.used_at > now - RECENT_USED)
        )
        used = await conn.execute(q.order_by(invite_links.c.used_at.desc()))
        return {
            "links": [dict(r._mapping) for r in outstanding],
            "used": [
                {"id": r.id, "created_at": r.created_at, "used_at": r.used_at, "used_by_email": r.email} for r in used
            ],
        }


async def revoke(sites, site_id: str, link_id: str, user: dict | None) -> None:
    await sites._ready()
    async with sites.engine.begin() as conn:
        await _owned_site(sites, conn, site_id, user)
        result = await conn.execute(
            update(invite_links)
            .where(
                invite_links.c.id == link_id,
                invite_links.c.site_id == site_id,
                invite_links.c.used_at.is_(None),
                invite_links.c.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
    if result.rowcount != 1:
        raise SiteError("no such invite link (or it was already used)", 404)


# ---- the invitee's side ----


async def _count_try(sites, user: dict | None, ip: str) -> None:
    if not user:
        raise SiteError("sign in to use an invite link", 401)
    await sites._ready()
    async with sites.engine.begin() as conn:  # committed on its own, so failed tries count
        message = "Too many invite link tries this hour. Try again later."
        await _hit_rate_limit(sites, conn, f"join:{user['sub']}", TRIES_PER_HOUR, message)
        await _hit_rate_limit(sites, conn, f"join-ip:{ip}", IP_TRIES_PER_HOUR, message)


async def _find(conn, code: str, now: int):
    """The redeemable link for this code and its (not unpublished) site, else the generic 404."""
    if not isinstance(code, str) or not code.startswith(PREFIX) or len(code) > MAX_CODE:
        raise SiteError(INVALID, 404)
    q = (
        select(invite_links, sites_t.c.name, sites_t.c.owner)
        .select_from(invite_links.join(sites_t, sites_t.c.id == invite_links.c.site_id))
        .where(invite_links.c.token_hash == _hash_token(code), sites_t.c.deleted_at.is_(None), *_redeemable(now))
    )
    row = (await conn.execute(q)).first()
    if row is None:
        raise SiteError(INVALID, 404)
    return row


async def lookup(sites, code: str, user: dict | None, ip: str) -> dict:
    """What the link is for, so /join can ask before joining. Uses nothing up."""
    await _count_try(sites, user, ip)
    async with sites.engine.connect() as conn:
        row = await _find(conn, code, _now())
        owner = (await conn.execute(select(users.c.email).where(users.c.sub == row.owner))).scalar()
        return {"site_name": row.name, "owner_email": owner, "expires_at": row.expires_at}


async def redeem(sites, code: str, user: dict | None, ip: str) -> dict:
    """Join the link's site as a member and use the link up. Refused, leaving the link unused: the owner's own link
    (400), an account that's already a member (409), a banned account or owner (403), a full site (409)."""
    await _count_try(sites, user, ip)
    async with sites.engine.connect() as conn:
        site_id = (await _find(conn, code, _now())).site_id
    await abuse.check_can_share(sites, user, site_id)
    async with sites.engine.begin() as conn:
        site = await sites._site(conn, site_id, lock=True)  # serialises joins to one site (Postgres)
        now = _now()
        link = await _find(conn, code, now)  # again, inside the transaction
        if site.owner == user["sub"]:
            raise SiteError("This is your own site's invite link. Send it to the person you want to invite.", 400)
        email = await _account_email(conn, user)
        if not email:
            raise SiteError("Sign in with Google again, then open the link.", 400)
        mine = select(site_members).where(
            site_members.c.site_id == site_id, (site_members.c.email == email) | (site_members.c.sub == user["sub"])
        )
        rows = (await conn.execute(mine)).all()
        if any(r.accepted_at is not None for r in rows):
            raise SiteError("You're already a member of this site.", 409)
        pending = next((r for r in rows if r.email == email), None)
        # this link's own seat becomes the membership; a pending invite of this account's converts in place
        if await seats_used(conn, site_id, now) - 1 - (pending is not None) >= MAX_MEMBERS:
            raise SiteError(FULL_MESSAGE, 409)
        used = await conn.execute(
            update(invite_links)
            .where(invite_links.c.token_hash == link.token_hash, *_redeemable(now))
            .values(used_at=now, used_by=user["sub"])
        )
        if used.rowcount != 1:  # someone redeemed it first
            raise SiteError(INVALID, 404)
        if pending is not None:
            await conn.execute(
                update(site_members)
                .where(site_members.c.site_id == site_id, site_members.c.email == email)
                .values(sub=user["sub"], accepted_at=now)
            )
        else:
            await conn.execute(
                site_members.insert().values(
                    site_id=site_id,
                    email=email,
                    sub=user["sub"],
                    invited_by=link.created_by,
                    created_at=now,
                    accepted_at=now,
                )
            )
        return await sites.user_site(conn, site_id, "member")

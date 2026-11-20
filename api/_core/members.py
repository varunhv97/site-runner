"""Collaborators: a site's owner invites other Google accounts by email to publish to and pull from the site.

Table `site_members` (db.py). An invite is pending until an account whose verified Google email equals it is
known (signs in); its sub is filled in then. Sites.can_edit decides what a member may do. Only the owner (signed
in or with an API token, never an edit token) lists, invites and removes members; a member may remove itself.
"""

import time

from sqlalchemy import delete, func, select

from .abuse import EMAIL_RE, MAX_CONTACT, _hit_rate_limit
from .db import site_members, users
from .sites import SiteError

MAX_MEMBERS = 10
# Invites per owner per hour (fixed window in rate_limits), so an account can't churn through addresses.
INVITES_PER_HOUR = 30


def _member(row) -> dict:
    return {"email": row.email, "status": "accepted" if row.sub else "pending", "created_at": row.created_at}


async def _owner_email(conn, site) -> str | None:
    if not site.owner:
        return None
    return (await conn.execute(select(users.c.email).where(users.c.sub == site.owner))).scalar()


async def _owned_site(sites, conn, site_id: str, user: dict | None, lock: bool = False):
    if not user:
        raise SiteError("sign in to manage a site's members", 401)
    site = await sites._site(conn, site_id, lock=lock)
    if site.owner != user["sub"]:
        raise SiteError("only the site's owner can manage its members", 403)
    return site


async def _listing(conn, site) -> dict:
    q = select(site_members).where(site_members.c.site_id == site.id)
    rows = (await conn.execute(q.order_by(site_members.c.created_at, site_members.c.email))).all()
    return {
        "owner": {"email": await _owner_email(conn, site)},
        "members": [_member(r) for r in rows],
        "max_members": MAX_MEMBERS,
    }


async def list_members(sites, site_id: str, user: dict | None) -> dict:
    await sites._ready()
    async with sites.engine.connect() as conn:
        return await _listing(conn, await _owned_site(sites, conn, site_id, user))


async def invite(sites, site_id: str, email: str, user: dict | None) -> dict:
    """Add a member by Google email. 409 when it's already a member or the site has MAX_MEMBERS."""
    email = email.strip().lower()
    if len(email) > MAX_CONTACT or not EMAIL_RE.match(email):
        raise SiteError("Enter the Google account's email address.", 400)
    await sites._ready()
    async with sites.engine.begin() as conn:
        site = await _owned_site(sites, conn, site_id, user, lock=True)  # serialises invites to one site
        if email in ((await _owner_email(conn, site) or "").lower(), (user.get("email") or "").lower()):
            raise SiteError("That's the site's owner, who can already publish to it.", 400)
        exists = select(site_members.c.email).where(site_members.c.site_id == site_id, site_members.c.email == email)
        if (await conn.execute(exists)).first():
            raise SiteError(f"{email} is already a member of this site.", 409)
        count = select(func.count()).where(site_members.c.site_id == site_id)
        if (await conn.execute(count)).scalar_one() >= MAX_MEMBERS:
            raise SiteError(f"A site can have {MAX_MEMBERS} members. Remove one first.", 409)
        await _hit_rate_limit(
            sites, conn, f"invite:{user['sub']}", INVITES_PER_HOUR, "Too many invites this hour. Try again later."
        )
        # an account already known by this email is the member straight away; otherwise when it first signs in
        subs = list((await conn.execute(select(users.c.sub).where(func.lower(users.c.email) == email))).scalars())
        await conn.execute(
            site_members.insert().values(
                site_id=site_id,
                email=email,
                sub=subs[0] if len(subs) == 1 else None,
                invited_by=user["sub"],
                created_at=int(time.time()),
            )
        )
        return await _listing(conn, site)


async def remove(sites, site_id: str, email: str, user: dict | None) -> None:
    """The owner removes any member; a member removes itself (leaves the site)."""
    if not user:
        raise SiteError("sign in to manage a site's members", 401)
    email = email.strip().lower()
    await sites._ready()
    async with sites.engine.begin() as conn:
        site = await sites._site(conn, site_id, lock=True)
        q = select(site_members).where(site_members.c.site_id == site_id, site_members.c.email == email)
        row = (await conn.execute(q)).first()
        own_email = (user.get("email") or "").lower()
        is_self = row is not None and (row.sub == user["sub"] or (row.sub is None and row.email == own_email))
        if site.owner != user["sub"] and not is_self:
            raise SiteError("only the site's owner can remove other members", 403)
        if row is None:
            raise SiteError("no such member", 404)
        await conn.execute(delete(site_members).where(site_members.c.site_id == site_id, site_members.c.email == email))

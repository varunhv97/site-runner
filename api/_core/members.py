"""Collaborators: a site's owner invites other Google accounts by email to publish to and pull from the site.

Table `site_members` (db.py). An invite is pending, and grants nothing, until the invitee accepts it
(`/api/me/invites`): the account whose email in `users` (recorded at Google sign-in, verified) equals the invited
address. Accepting fills sub and accepted_at. Invite responses say "pending" until then, whether or not an account
with that email exists. Sites.can_edit decides what a member may do. Only the owner (signed in or with an API
token, never an edit token) lists, invites and removes members; a member may leave; an invitee may decline.
"""

import time

from sqlalchemy import delete, func, select, update

from .abuse import EMAIL_RE, MAX_CONTACT, _hit_rate_limit
from .db import invite_links, site_members, sites as sites_t, users
from .sites import SiteError

MAX_MEMBERS = 10
# Invites per owner per hour (fixed window in rate_limits), so an account can't churn through addresses.
INVITES_PER_HOUR = 30
# Accepts and declines per account per hour.
ANSWERS_PER_HOUR = 60
FULL_MESSAGE = (
    f"A site can have {MAX_MEMBERS} members, counting pending invites and unused invite links. Remove one first."
)


def _member(row) -> dict:
    status = "accepted" if row.accepted_at is not None else "pending"
    return {"email": row.email, "status": status, "created_at": row.created_at}


def _outstanding_links(site_id: str, now: int):
    """A site's invite links that can still be redeemed: unused, unrevoked, unexpired."""
    return (
        invite_links.c.site_id == site_id,
        invite_links.c.used_at.is_(None),
        invite_links.c.revoked_at.is_(None),
        invite_links.c.expires_at > now,
    )


async def seats_used(conn, site_id: str, now: int | None = None) -> int:
    """Members (accepted and pending) plus outstanding invite links: together at most MAX_MEMBERS."""
    now = int(time.time()) if now is None else now
    taken = (await conn.execute(select(func.count()).where(site_members.c.site_id == site_id))).scalar_one()
    links = (await conn.execute(select(func.count()).where(*_outstanding_links(site_id, now)))).scalar_one()
    return taken + links


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
        "invite_links": await seats_used(conn, site.id) - len(rows),  # outstanding ones, which take seats too
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
        if await seats_used(conn, site_id) >= MAX_MEMBERS:
            raise SiteError(FULL_MESSAGE, 409)
        await _hit_rate_limit(
            sites, conn, f"invite:{user['sub']}", INVITES_PER_HOUR, "Too many invites this hour. Try again later."
        )
        # pending until the invitee accepts; never looks up whether an account with this email exists
        await conn.execute(
            site_members.insert().values(
                site_id=site_id, email=email, sub=None, invited_by=user["sub"], created_at=int(time.time())
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
        is_self = row is not None and row.accepted_at is not None and row.sub == user["sub"]
        if site.owner != user["sub"] and not is_self:
            raise SiteError("only the site's owner can remove other members", 403)
        if row is None:
            raise SiteError("no such member", 404)
        await conn.execute(delete(site_members).where(site_members.c.site_id == site_id, site_members.c.email == email))


# ---- the invitee's side: list, accept, decline ----


async def _account_email(conn, user: dict) -> str:
    """The email stored for this account at its latest Google sign-in (verified), not one carried in a session
    cookie or token, so an address the account no longer has can't be used to accept."""
    q = select(users.c.email).where(users.c.sub == user["sub"])
    return ((await conn.execute(q)).scalar() or "").strip().lower()


def _not_owner(user: dict):
    return sites_t.c.owner.is_(None) | (sites_t.c.owner != user["sub"])


def _pending_for(email: str):
    return (
        select(site_members, sites_t.c.name, sites_t.c.owner)
        .select_from(site_members.join(sites_t, sites_t.c.id == site_members.c.site_id))
        .where(site_members.c.email == email, site_members.c.accepted_at.is_(None), sites_t.c.deleted_at.is_(None))
    )


async def my_invites(sites, user: dict | None) -> list[dict]:
    """Invites waiting for this account's answer, oldest first."""
    if not user:
        raise SiteError("sign in to see your invitations", 401)
    await sites._ready()
    async with sites.engine.connect() as conn:
        email = await _account_email(conn, user)
        if not email:
            return []
        q = _pending_for(email).where(_not_owner(user)).order_by(site_members.c.created_at)
        out = []
        for r in (await conn.execute(q)).all():
            out.append(
                {
                    "site_id": r.site_id,
                    "site_name": r.name,
                    "slug": await sites._current_slug(conn, r.site_id),
                    "owner_email": await _owner_email(conn, r),
                    "created_at": r.created_at,
                }
            )
        return out


async def _answer_row(sites, conn, site_id: str, user: dict | None):
    if not user:
        raise SiteError("sign in to answer an invitation", 401)
    await _hit_rate_limit(
        sites, conn, f"invite-answer:{user['sub']}", ANSWERS_PER_HOUR, "Too many answers this hour. Try again later."
    )
    email = await _account_email(conn, user)
    row = None
    if email:
        q = _pending_for(email).where(site_members.c.site_id == site_id, _not_owner(user))
        row = (await conn.execute(q.with_for_update(of=site_members))).first()
    if row is None:
        raise SiteError("no invitation to this site for your account", 404)
    return row


async def accept(sites, site_id: str, user: dict | None) -> dict:
    """The invitee agrees: the invite becomes membership. Returns the site as /api/me/sites lists it."""
    await sites._ready()
    async with sites.engine.begin() as conn:
        row = await _answer_row(sites, conn, site_id, user)
        await conn.execute(
            update(site_members)
            .where(site_members.c.site_id == site_id, site_members.c.email == row.email)
            .values(sub=user["sub"], accepted_at=int(time.time()))
        )
        return await sites.user_site(conn, site_id, "member")


async def decline(sites, site_id: str, user: dict | None) -> None:
    await sites._ready()
    async with sites.engine.begin() as conn:
        row = await _answer_row(sites, conn, site_id, user)
        await conn.execute(
            delete(site_members).where(site_members.c.site_id == site_id, site_members.c.email == row.email)
        )

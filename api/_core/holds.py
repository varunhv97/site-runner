"""Safety holds: a site whose upload looked like phishing, a seed-phrase grab or a miner (scan.py) keeps its
files, but its links answer 451 until an admin approves it. Rejecting unpublishes the site.

Its own table (create_all adds tables, never columns). A site is held while any of its rows is "held".
"""

import json
import time

from sqlalchemy import Column, ForeignKeyConstraint, Index, Integer, String, Table, Text, select, update

from .db import metadata, sites, users

HELD, APPROVED, REJECTED = "held", "approved", "rejected"
HELD_MESSAGE = "This site is waiting for a safety review."

holds = Table(
    "holds",
    metadata,
    Column("site_id", String(16), primary_key=True),
    Column("n", Integer, primary_key=True),  # the version whose upload was flagged
    Column("reasons", Text, nullable=False),  # JSON list of short reasons
    Column("created_at", Integer, nullable=False),
    Column("status", String(10), nullable=False, default=HELD),
    Column("reviewed_by", String(320), nullable=True),  # the admin's email
    Column("reviewed_at", Integer, nullable=True),
    ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
    Index("ix_holds_status", "status"),
)


async def add(conn, site_id: str, n: int, reasons: list[str]) -> None:
    await conn.execute(
        holds.insert().values(
            site_id=site_id, n=n, reasons=json.dumps(reasons), created_at=int(time.time()), status=HELD
        )
    )


async def reasons(conn, site_id: str) -> list[str]:
    """Why the site is held (empty when it isn't)."""
    q = select(holds.c.reasons).where(holds.c.site_id == site_id, holds.c.status == HELD).order_by(holds.c.n)
    out: list[str] = []
    for raw in (await conn.execute(q)).scalars():
        out += [r for r in json.loads(raw) if r not in out]
    return out


async def is_held(conn, site_id: str) -> bool:
    q = select(holds.c.n).where(holds.c.site_id == site_id, holds.c.status == HELD).limit(1)
    return (await conn.execute(q)).first() is not None


def status(found: list[str]) -> dict:
    """The part of a share response that says whether the site is held."""
    return {"held": True, "reasons": found} if found else {"held": False}


async def site_status(engine, site_id: str) -> dict:
    async with engine.connect() as conn:
        return status(await reasons(conn, site_id))


async def list_holds(engine, state: str | None = HELD) -> list[dict]:
    q = (
        select(holds, sites.c.name, sites.c.owner, users.c.email.label("owner_email"))
        .select_from(holds.join(sites, sites.c.id == holds.c.site_id).outerjoin(users, users.c.sub == sites.c.owner))
        .order_by(holds.c.created_at.desc())
    )
    if state:
        q = q.where(holds.c.status == state)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
    return [
        {
            "site_id": r.site_id,
            "n": r.n,
            "name": r.name,
            "owner_email": r.owner_email,
            "reasons": json.loads(r.reasons),
            "created_at": r.created_at,
            "status": r.status,
            "reviewed_by": r.reviewed_by,
            "reviewed_at": r.reviewed_at,
        }
        for r in rows
    ]


async def review(engine, site_id: str, approve: bool, admin_email: str) -> int:
    """Close the site's open holds. Returns how many there were."""
    async with engine.begin() as conn:
        result = await conn.execute(
            update(holds)
            .where(holds.c.site_id == site_id, holds.c.status == HELD)
            .values(status=APPROVED if approve else REJECTED, reviewed_by=admin_email, reviewed_at=int(time.time()))
        )
        return result.rowcount


async def owner_status(store, site_id: str, token: str | None, user: dict | None) -> dict:
    """Whether a site is held, for its owner or token holder (store: the Sites instance)."""
    await store._ready()
    async with store.engine.connect() as conn:
        store.check_access(await store._site(conn, site_id), token, user)
        return status(await reasons(conn, site_id))

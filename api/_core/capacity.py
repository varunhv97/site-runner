"""Site-wide storage cap: per-account quotas can be dodged with many accounts, so the total is capped too.

The total is the size of every stored file (the `blobs` table; files no version uses any more are dropped by the
daily cleanup). An upload whose new files would take the total past GLOBAL_STORAGE_LIMIT_MB is refused, and
once the total passes ALERT_FRACTION of the limit, REPORT_EMAIL gets one email a day (mailer.py).
"""

import logging
import os
import time

from sqlalchemy import func, select

from . import mailer
from .db import blobs, rate_limits
from .sites import MB, SiteError, _mb

log = logging.getLogger("site-runner")

DEFAULT_LIMIT_MB = 900  # under Vercel Blob's included storage on the Hobby plan; set GLOBAL_STORAGE_LIMIT_MB to change
ALERT_FRACTION = 0.8
FULL_MESSAGE = "Site Runner's storage is full right now, so new uploads are paused. Please try again later."


def limit_bytes() -> int:
    try:
        mb = float(os.environ.get("GLOBAL_STORAGE_LIMIT_MB") or DEFAULT_LIMIT_MB)
    except ValueError:
        mb = DEFAULT_LIMIT_MB
    return int(mb * MB)


async def check(sites, sizes: dict[str, int]) -> None:
    """Refuse an upload (digest -> size) whose files not stored yet would take the total past the limit."""
    await sites._ready()
    async with sites.engine.connect() as conn:
        total = (await conn.execute(select(func.coalesce(func.sum(blobs.c.size), 0)))).scalar_one()
        known = set(
            (await conn.execute(select(blobs.c.digest).where(blobs.c.digest.in_(list(sizes))))).scalars()
            if sizes
            else []
        )
    after = total + sum(size for digest, size in sizes.items() if digest not in known)
    limit = limit_bytes()
    if after > limit:
        log.error("storage full: %s of %s; upload refused", _mb(after), _mb(limit))
        raise SiteError(FULL_MESSAGE, 507)
    if after > limit * ALERT_FRACTION:
        await _alert(sites, after, limit)


async def _alert(sites, used: int, limit: int) -> None:
    """At most one email a day while storage is above ALERT_FRACTION of the limit."""
    to = os.environ.get("REPORT_EMAIL", "").strip()
    if not to or not mailer.enabled():
        return
    day = time.strftime("%Y-%m-%d", time.gmtime())
    async with sites.engine.begin() as conn:
        stmt = sites._upsert(rate_limits).values(key="storage-alert", window=day, count=1)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key", "window"], set_={"count": rate_limits.c.count + 1}
        ).returning(rate_limits.c.count)
        if (await conn.execute(stmt)).scalar_one() > 1:
            return
    await mailer.send(
        to,
        f"[Site Runner] Storage at {used * 100 // limit}%",
        f"Stored files use {_mb(used)} of the {_mb(limit)} limit (GLOBAL_STORAGE_LIMIT_MB).\n"
        "At the limit, new uploads are refused. Unpublish abandoned or abusive sites in /admin, or raise the limit\n"
        "if your Blob plan allows it.",
    )

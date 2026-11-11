"""Personal API tokens: a signed-in account creates them in /app to act as itself from a CLI or MCP client.

Sent as "Authorization: Bearer srk_...". The prefix tells them apart from a site's edit token (which is sent the
same way but only unlocks that one site). Only a token's sha256 is stored. A token user can do what the account
can do with its sites, but never admin work, and can't create or list tokens (a leaked token can't mint more).
"""

import secrets
import time

from sqlalchemy import delete, func, select, update

from .db import api_tokens, users
from .sites import SiteError, _hash_token, _new_id

PREFIX = "srk_"
MAX_TOKENS = 20
MAX_NAME = 60
# last_used_at is written at most this often per token, so a busy CLI doesn't turn every request into a write
TOUCH_EVERY = 3600


def is_api_token(value: str | None) -> bool:
    return bool(value and value.startswith(PREFIX))


def _meta(row: dict) -> dict:
    return {k: row[k] for k in ("id", "name", "hint", "created_at", "last_used_at")}


async def create(sites, user: dict, name: str) -> tuple[dict, str]:
    """A new token for the account: (its listing, the token itself, shown once)."""
    name = name.strip()[:MAX_NAME] or "API token"
    await sites._ready()
    token = PREFIX + secrets.token_urlsafe(32)
    row = {
        "id": _new_id(),
        "sub": user["sub"],
        "name": name,
        "token_hash": _hash_token(token),
        "hint": token[-4:],
        "created_at": int(time.time()),
        "last_used_at": None,
    }
    async with sites.engine.begin() as conn:
        count = (await conn.execute(select(func.count()).where(api_tokens.c.sub == user["sub"]))).scalar_one()
        if count >= MAX_TOKENS:
            raise SiteError(f"An account can have {MAX_TOKENS} API tokens. Revoke one you don't use first.", 409)
        await conn.execute(api_tokens.insert().values(**row))
    return _meta(row), token


async def list_tokens(sites, sub: str) -> list[dict]:
    await sites._ready()
    async with sites.engine.connect() as conn:
        rows = await conn.execute(
            select(api_tokens).where(api_tokens.c.sub == sub).order_by(api_tokens.c.created_at.desc())
        )
        return [_meta(r._mapping) for r in rows]


async def revoke(sites, sub: str, token_id: str) -> None:
    await sites._ready()
    async with sites.engine.begin() as conn:
        result = await conn.execute(delete(api_tokens).where(api_tokens.c.id == token_id, api_tokens.c.sub == sub))
    if not result.rowcount:
        raise SiteError("no such API token", 404)


async def user_for(sites, token: str) -> dict | None:
    """The account a token acts as ({sub, email, name, via}), or None when it's unknown or revoked."""
    await sites._ready()
    async with sites.engine.connect() as conn:
        q = (
            select(api_tokens.c.id, api_tokens.c.sub, api_tokens.c.last_used_at, users.c.email)
            .select_from(api_tokens.outerjoin(users, users.c.sub == api_tokens.c.sub))
            .where(api_tokens.c.token_hash == _hash_token(token))
        )
        row = (await conn.execute(q)).first()
    if not row:
        return None
    now = int(time.time())
    if not row.last_used_at or now - row.last_used_at >= TOUCH_EVERY:
        async with sites.engine.begin() as conn:
            await conn.execute(update(api_tokens).where(api_tokens.c.id == row.id).values(last_used_at=now))
    return {"sub": row.sub, "email": row.email or "", "name": "", "via": "api_token"}

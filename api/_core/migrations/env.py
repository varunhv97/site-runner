"""Alembic environment. At runtime db.migrate() hands in an open connection (inside its transaction, holding the
migration lock); the alembic CLI (alembic.ini at the project root) connects to DATABASE_URL, else local SQLite."""

import asyncio

from alembic import context

from _core import holds  # noqa: F401  (registers the holds table on the metadata)
from _core.db import make_engine, metadata


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_cli() -> None:
    engine = make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(run)
    await engine.dispose()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    asyncio.run(run_cli())

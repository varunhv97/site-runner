"""Alembic migrations (api/_core/migrations), applied by db.migrate() on first use.

Runs on SQLite; set TEST_POSTGRES_URL (an empty scratch database: tables are dropped) to run them on Postgres too.
"""

import asyncio
import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from _core import db
from _core.db import make_engine, metadata, migrate

HEAD = ScriptDirectory(db.MIGRATIONS).get_current_head()
URLS = ["sqlite"] + (["postgres"] if os.environ.get("TEST_POSTGRES_URL") else [])


@pytest.fixture(params=URLS)
def engine(request, tmp_path):
    if request.param == "sqlite":
        yield make_engine(f"sqlite:///{tmp_path}/m.db")
        return
    eng = make_engine(os.environ["TEST_POSTGRES_URL"])

    async def drop_all():
        async with eng.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

    asyncio.run(drop_all())
    yield eng
    asyncio.run(drop_all())


def run(coro):
    return asyncio.run(coro)


async def state(engine):
    """(current revision, differences between the database and db.metadata)."""

    def read(conn):
        return MigrationContext.configure(conn).get_current_revision(), compare_metadata(
            MigrationContext.configure(conn), metadata
        )

    async with engine.connect() as conn:
        return await conn.run_sync(read)


async def legacy_database(engine):
    """A database as create_all left it before migrations: the baseline tables and no alembic_version."""

    def build(conn):
        config = db.Config()
        config.set_main_option("script_location", db.MIGRATIONS)
        config.attributes["connection"] = conn
        command.upgrade(config, db.BASELINE)
        conn.execute(text("DROP TABLE alembic_version"))

    async with engine.begin() as conn:
        await conn.run_sync(build)
        await conn.execute(
            text(
                "INSERT INTO sites (id, name, token_hash, owner, live, created_at, deleted_by_admin) "
                "VALUES ('abc123', 'Old site', 'h', '111', 1, 1, false)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO versions (site_id, n, created_at, note, entry, size, files) "
                "VALUES ('abc123', 1, 1, '', 'index.html', 2, 1)"
            )
        )


def test_fresh_database_ends_at_head(engine):
    run(migrate(engine))
    revision, diff = run(state(engine))
    assert revision == HEAD and diff == []


def test_existing_database_without_alembic_version_is_stamped_and_upgraded(engine):
    run(legacy_database(engine))
    run(migrate(engine))
    revision, diff = run(state(engine))
    assert revision == HEAD and diff == []

    async def rows():
        async with engine.connect() as conn:
            q = "SELECT s.id, s.name, v.n, v.published_by FROM sites s JOIN versions v ON v.site_id = s.id"
            return (await conn.execute(text(q))).all(), (await conn.execute(text("SELECT * FROM site_members"))).all()

    old, members = run(rows())
    assert [tuple(r) for r in old] == [("abc123", "Old site", 1, None)] and members == []  # data kept


def test_migrate_twice_is_a_no_op(engine):
    run(migrate(engine))
    run(migrate(engine))
    assert run(state(engine))[0] == HEAD


def test_concurrent_cold_starts(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("the advisory lock is Postgres only")

    async def many():
        await asyncio.gather(*(migrate(engine) for _ in range(5)))

    run(many())
    revision, diff = run(state(engine))
    assert revision == HEAD and diff == []

    async def tables():
        async with engine.connect() as conn:
            return await conn.run_sync(lambda c: inspect(c).get_table_names())

    assert "alembic_version" in run(tables())


def test_concurrent_first_requests_migrate_once(tmp_path):
    """A cold process serving parallel requests must not run Alembic twice at once (its context is global)."""
    from _core.sites import Sites
    from _core.storage import LocalStorage

    sites = Sites(LocalStorage(str(tmp_path / "files")), make_engine(f"sqlite:///{tmp_path}/c.db"))

    async def go():
        await asyncio.gather(*(sites._ready() for _ in range(8)))

    run(go())
    assert sites._schema_ready

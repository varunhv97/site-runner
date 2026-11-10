"""Metadata database: Postgres (Neon) in production, SQLite locally and in tests.

Files live in Blob storage under their sha256; this database records which files make up each version.
"""

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("sub", String(255), primary_key=True),  # Google account id
    Column("email", String(320), nullable=False, default=""),
    Column("created_at", Integer, nullable=False),
)

sites = Table(
    "sites",
    metadata,
    Column("id", String(16), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("token_hash", String(64), nullable=False),
    Column("owner", String(255), nullable=True),
    Column("live", Integer, nullable=True),
    Column("created_at", Integer, nullable=False),
    Column("deleted_at", Integer, nullable=True),
    Column("deleted_by_admin", Boolean, nullable=False, default=False),
    Index("ix_sites_owner", "owner"),
)

versions = Table(
    "versions",
    metadata,
    Column("site_id", String(16), primary_key=True),
    Column("n", Integer, primary_key=True),
    Column("created_at", Integer, nullable=False),
    Column("note", String(200), nullable=False, default=""),
    Column("entry", Text, nullable=False),
    Column("size", Integer, nullable=False),
    Column("files", Integer, nullable=False),
    ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
)

version_files = Table(
    "version_files",
    metadata,
    Column("site_id", String(16), primary_key=True),
    Column("n", Integer, primary_key=True),
    Column("path", Text, primary_key=True),
    Column("digest", String(64), nullable=False),
    ForeignKeyConstraint(["site_id", "n"], ["versions.site_id", "versions.n"], ondelete="CASCADE"),
    Index("ix_version_files_digest", "digest"),
)

uploads = Table(
    "uploads",
    metadata,
    Column("sub", String(255), primary_key=True),
    Column("day", String(10), primary_key=True),  # YYYY-MM-DD, UTC
    Column("count", Integer, nullable=False),
)


def _async_url(url: str) -> tuple[str, dict]:
    """Turn a provider URL (postgres://...?sslmode=require) into an async SQLAlchemy URL + connect args."""
    if url.startswith("sqlite"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1) if "+aiosqlite" not in url else url, {}
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    ssl = query.pop("sslmode", None) not in (None, "disable")
    query.pop("channel_binding", None)  # libpq-only option Neon adds; asyncpg rejects it
    scheme = "postgresql+asyncpg"
    clean = urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), ""))
    # Neon's pooled endpoint runs PgBouncer in transaction mode: no server-side prepared statements.
    args = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
    if ssl:
        args["ssl"] = "require"
    return clean, args


def make_engine(url: str | None = None) -> AsyncEngine:
    url = url or os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if not url:
        if os.environ.get("VERCEL"):
            raise RuntimeError("DATABASE_URL is not set: connect a Postgres database to the project")
        os.makedirs(os.environ.get("LOCAL_DATA_DIR", ".data"), exist_ok=True)
        url = f"sqlite:///{os.environ.get('LOCAL_DATA_DIR', '.data')}/site-runner.db"
    async_url, connect_args = _async_url(url)
    # NullPool: serverless functions freeze between requests, so never keep idle connections around.
    return create_async_engine(async_url, poolclass=NullPool, connect_args=connect_args)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

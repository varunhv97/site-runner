"""Baseline: the schema as create_all made it before migrations. Databases from then are stamped at this
revision (db.migrate), so this only runs on new databases.

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("sub", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("hint", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_api_tokens_sub", "api_tokens", ["sub"])
    op.create_table(
        "bans",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("sub", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("by_admin", sa.String(length=320), nullable=False),
        sa.PrimaryKeyConstraint("email"),
    )
    op.create_index("ix_bans_sub", "bans", ["sub"])
    op.create_table(
        "blobs",
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("digest"),
    )
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("window", sa.String(length=16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "window"),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.String(length=16), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(length=320), nullable=False),
        sa.Column("reporter", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("handled_by", sa.String(length=320), nullable=True),
        sa.Column("handled_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_site", "reports", ["site_id"])
    op.create_index("ix_reports_status", "reports", ["status"])
    op.create_table(
        "sites",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("live", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.Integer(), nullable=True),
        sa.Column("deleted_by_admin", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sites_owner", "sites", ["owner"])
    op.create_table(
        "uploads",
        sa.Column("sub", sa.String(length=255), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("sub", "day"),
    )
    op.create_table(
        "users",
        sa.Column("sub", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("sub"),
    )
    op.create_table(
        "deleted_versions",
        sa.Column("site_id", sa.String(length=16), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id", "n"),
    )
    op.create_table(
        "holds",
        sa.Column("site_id", sa.String(length=16), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("reasons", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("reviewed_by", sa.String(length=320), nullable=True),
        sa.Column("reviewed_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id", "n"),
    )
    op.create_index("ix_holds_status", "holds", ["status"])
    op.create_table(
        "slugs",
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("slug"),
    )
    op.create_index("ix_slugs_site", "slugs", ["site_id"])
    op.create_table(
        "versions",
        sa.Column("site_id", sa.String(length=16), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=False),
        sa.Column("entry", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("files", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id", "n"),
    )
    op.create_table(
        "version_files",
        sa.Column("site_id", sa.String(length=16), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["site_id", "n"], ["versions.site_id", "versions.n"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id", "n", "path"),
    )
    op.create_index("ix_version_files_digest", "version_files", ["digest"])


def downgrade() -> None:
    for table in [
        "version_files",
        "versions",
        "slugs",
        "holds",
        "deleted_versions",
        "users",
        "uploads",
        "sites",
        "reports",
        "rate_limits",
        "blobs",
        "bans",
        "api_tokens",
    ]:
        op.drop_table(table)

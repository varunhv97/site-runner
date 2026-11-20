"""One-time invite links: invite_links.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invite_links",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("site_id", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("used_at", sa.Integer(), nullable=True),
        sa.Column("used_by", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_invite_links_site", "invite_links", ["site_id"])


def downgrade() -> None:
    op.drop_table("invite_links")

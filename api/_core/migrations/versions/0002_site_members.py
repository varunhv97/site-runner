"""Collaborators: site_members, and who published each version.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_members",
        sa.Column("site_id", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("sub", sa.String(length=255), nullable=True),
        sa.Column("invited_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id", "email"),
    )
    op.create_index("ix_site_members_email", "site_members", ["email"])
    op.create_index("ix_site_members_sub", "site_members", ["sub"])
    op.add_column("versions", sa.Column("published_by", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("versions") as batch:
        batch.drop_column("published_by")
    op.drop_table("site_members")

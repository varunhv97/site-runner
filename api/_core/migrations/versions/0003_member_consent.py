"""Invites need the invitee's consent: site_members.accepted_at. Existing rows go back to pending.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("site_members", sa.Column("accepted_at", sa.Integer(), nullable=True))
    # rows made before consent existed were active without the invitee agreeing: they must accept again
    op.execute("UPDATE site_members SET sub = NULL")


def downgrade() -> None:
    with op.batch_alter_table("site_members") as batch:
        batch.drop_column("accepted_at")

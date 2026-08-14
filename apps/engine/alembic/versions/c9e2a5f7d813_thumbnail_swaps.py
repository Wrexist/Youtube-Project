"""thumbnail swaps

Backs FIX-TASKS E2's thumbnail A/B swapping: the 14-day-between-swaps guardrail
needs "was there a swap on this video recently" as a query, and Phase 8's
attribution needs a swap date to segment a video's CTR history against — neither
is answerable from `PerformanceRecord.payload` alone, which holds only the
video's current, single `thumbnail_concept`.

Revision ID: c9e2a5f7d813
Revises: b6a4f8d1c72e
Create Date: 2026-08-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e2a5f7d813"
down_revision: str | Sequence[str] | None = "b6a4f8d1c72e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "thumbnail_swaps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("video_id", sa.String(length=64), nullable=False),
        sa.Column("from_concept", sa.String(length=64), nullable=False),
        sa.Column("to_concept", sa.String(length=64), nullable=False),
        sa.Column("variant_key", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_thumbnail_swaps_video_id"), "thumbnail_swaps", ["video_id"], unique=False
    )
    op.create_index(op.f("ix_thumbnail_swaps_at"), "thumbnail_swaps", ["at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_thumbnail_swaps_at"), table_name="thumbnail_swaps")
    op.drop_index(op.f("ix_thumbnail_swaps_video_id"), table_name="thumbnail_swaps")
    op.drop_table("thumbnail_swaps")

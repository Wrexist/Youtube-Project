"""review snapshots

Backs the weekly review's diff. Each row is what the system believed on one run —
without it the job can only recompute the current state, which the insights
endpoint already returns on demand, and "what changed this week" is unanswerable.

Revision ID: a3c81f27b904
Revises: de1f58e5536c
Create Date: 2026-08-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c81f27b904"
down_revision: str | Sequence[str] | None = "de1f58e5536c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "review_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("video_count", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_review_snapshots_generated_at"),
        "review_snapshots",
        ["generated_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_review_snapshots_generated_at"), table_name="review_snapshots")
    op.drop_table("review_snapshots")

"""keep the readable review, not only the diff snapshot

`review_snapshots.payload` holds the trimmed comparison state — four strings and a
verdict per finding — which is everything next week's diff needs and nothing a
person would want to read. The readable report went into arq's result store, where
`keep_result = 3600` drops it after an hour: a review generated at Monday 06:00 was
unreadable by 07:01, and the only other way to see one was to run a fresh one and
consume the baseline the real weekly diff was going to compare against.

Nullable, because every existing row predates this and there is nothing to
backfill. A missing report is honestly "that run's report is gone", which is true.

Revision ID: c7d2b1a4e903
Revises: a3c81f27b904
Create Date: 2026-08-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d2b1a4e903"
down_revision: str | Sequence[str] | None = "a3c81f27b904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("review_snapshots", sa.Column("report", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("review_snapshots", "report")

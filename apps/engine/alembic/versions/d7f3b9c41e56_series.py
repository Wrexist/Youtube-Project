"""series

Backs the Series screen and the run planner: a standing series config —
cadence, budget, auto-publish — that survives a restart. Until this table
existed, `automation.Series` was an in-memory dataclass only tests
constructed, and the Series screen shipped with its primary action disabled.

Revision ID: d7f3b9c41e56
Revises: c9e2a5f7d813
Create Date: 2026-08-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f3b9c41e56"
down_revision: str | Sequence[str] | None = "c9e2a5f7d813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "series",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("niche", sa.Text(), nullable=False),
        sa.Column("monthly_budget_usd", sa.Float(), nullable=False),
        sa.Column("shorts_per_week", sa.Integer(), nullable=False),
        sa.Column("long_per_week", sa.Integer(), nullable=False),
        sa.Column("auto_publish", sa.Boolean(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("series")

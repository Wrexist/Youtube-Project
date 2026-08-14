"""keyword snapshots

Backs trend monitoring's rising-autocomplete signal (FIX-TASKS E3). One row per
seed, holding the autocomplete terms seen the last time `engine.trending` polled
it — the diff against today's terms is what "newly moving" means, and without a
durable "last time" the freshness component collapses to comparing today against
nothing, which is not a trend, it is just today.

Revision ID: b6a4f8d1c72e
Revises: f1a72e9c3d48
Create Date: 2026-08-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6a4f8d1c72e"
down_revision: str | Sequence[str] | None = "f1a72e9c3d48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "keyword_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("seed", sa.String(length=300), nullable=False),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_keyword_snapshots_seed"), "keyword_snapshots", ["seed"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_keyword_snapshots_seed"), table_name="keyword_snapshots")
    op.drop_table("keyword_snapshots")

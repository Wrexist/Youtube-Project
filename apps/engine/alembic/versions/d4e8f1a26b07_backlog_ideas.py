"""backlog ideas

`api/ideas.py` scored ideas and kept them in a process-local dict for thirty
minutes, so the same channel was shown the same suggestions over and over and there
was no way to say "not that one" that outlived a reload.

`topic` is unique: the generator proposes by adjacency to what the channel has
already published, so without the constraint the backlog fills with near-duplicates.

Revision ID: d4e8f1a26b07
Revises: c7d2b1a4e903
Create Date: 2026-08-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e8f1a26b07"
down_revision: str | Sequence[str] | None = "c7d2b1a4e903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "backlog_ideas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("topic", sa.String(length=300), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("demand", sa.Float(), nullable=False),
        sa.Column("competition", sa.Float(), nullable=False),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_backlog_ideas_topic"), "backlog_ideas", ["topic"], unique=True)
    op.create_index(op.f("ix_backlog_ideas_status"), "backlog_ideas", ["status"], unique=False)
    op.create_index(
        op.f("ix_backlog_ideas_created_at"), "backlog_ideas", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_backlog_ideas_created_at"), table_name="backlog_ideas")
    op.drop_index(op.f("ix_backlog_ideas_status"), table_name="backlog_ideas")
    op.drop_index(op.f("ix_backlog_ideas_topic"), table_name="backlog_ideas")
    op.drop_table("backlog_ideas")

"""genre watchlist tables

Backs genre intelligence (Phase 9's competitor half): channels a human decided
to watch, and the videos sweeps have seen from them. `first_seen_views` on
`watched_videos` is written once per row so view velocity is computable from
two columns instead of a snapshot-history table that would grow forever for
data only ever read as "latest" and "how fast it got here".

Revision ID: e8f3a9c4b217
Revises: c9e2a5f7d813
Create Date: 2026-08-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f3a9c4b217"
down_revision: str | Sequence[str] | None = "c9e2a5f7d813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "watched_channels",
        sa.Column("youtube_channel_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("youtube_channel_id"),
    )
    op.create_table(
        "watched_videos",
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column(
            "watched_channel_id",
            sa.String(length=64),
            sa.ForeignKey(
                "watched_channels.youtube_channel_id", name="fk_watched_videos_channel",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("likes", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_views", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("video_id"),
    )
    op.create_index(
        op.f("ix_watched_videos_watched_channel_id"),
        "watched_videos",
        ["watched_channel_id"],
    )
    op.create_index(
        op.f("ix_watched_videos_published_at"), "watched_videos", ["published_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_watched_videos_published_at"), table_name="watched_videos")
    op.drop_index(
        op.f("ix_watched_videos_watched_channel_id"), table_name="watched_videos"
    )
    op.drop_table("watched_videos")
    op.drop_table("watched_channels")

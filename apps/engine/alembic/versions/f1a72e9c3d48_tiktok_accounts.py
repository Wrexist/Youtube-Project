"""tiktok accounts

Lane A needs a connection that survives a restart *and* refreshes itself. TikTok
access tokens last 24 hours, so without stored tokens and a refresh path the
integration works on the day it is set up and is dead by the next sweep — which
reads as "the feature broke" rather than "a token expired".

Its own table rather than a row in `channels`: that one is shaped around a YouTube
channel, and widening it with a `platform` column would make every YouTube query
filter on something that is only ever one value.

The refresh token is encrypted, like YouTube's, and there is no column for a
plaintext one so it cannot be written by accident.

Revision ID: f1a72e9c3d48
Revises: e5b93c17d24a
Create Date: 2026-08-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a72e9c3d48"
down_revision: str | Sequence[str] | None = "e5b93c17d24a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tiktok_accounts",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("open_id", sa.String(length=128), nullable=False),
        sa.Column("handle", sa.String(length=64), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("tiktok_accounts")

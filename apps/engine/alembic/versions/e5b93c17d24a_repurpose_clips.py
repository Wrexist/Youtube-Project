"""repurpose: clip sources, grants, assets, projects

The rights model made physical. `clip_sources` holds public metadata about a public
post, which is fine to keep indefinitely. `clip_assets` holds the media, and a row
there only exists because a `clip_grants` row said it could — the acquire stage
checks the grant before a byte moves, so an asset without a grant is a bug rather
than merely bad practice.

`clip_grants` is a table and not columns on the source because grants have a
lifetime. A campaign ends, a licence runs its term, a creator withdraws permission,
and the video published under the old grant is still live. Flattening it would
overwrite the history that answers "were we allowed to publish that, at the time we
published it".

`(platform, external_id)` is unique: discovery re-runs constantly and would
otherwise re-propose the same twenty TikToks every sweep.

Revision ID: e5b93c17d24a
Revises: d4e8f1a26b07
Create Date: 2026-08-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b93c17d24a"
down_revision: str | Sequence[str] | None = "d4e8f1a26b07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "clip_sources",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("creator_handle", sa.String(length=64), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("hashtags", sa.JSON(), nullable=False),
        sa.Column("sound_id", sa.String(length=64), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("region", sa.String(length=8), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.Column("fit_score", sa.Float(), nullable=False),
        sa.Column("fit_reasons", sa.JSON(), nullable=False),
        sa.Column("channel_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clip_sources_platform"), "clip_sources", ["platform"], unique=False)
    op.create_index(
        op.f("ix_clip_sources_external_id"), "clip_sources", ["external_id"], unique=False
    )
    op.create_index(
        op.f("ix_clip_sources_creator_handle"), "clip_sources", ["creator_handle"], unique=False
    )
    op.create_index(op.f("ix_clip_sources_fit_score"), "clip_sources", ["fit_score"], unique=False)
    op.create_index(
        op.f("ix_clip_sources_channel_key"), "clip_sources", ["channel_key"], unique=False
    )
    op.create_index(op.f("ix_clip_sources_status"), "clip_sources", ["status"], unique=False)
    op.create_index(
        op.f("ix_clip_sources_discovered_at"), "clip_sources", ["discovered_at"], unique=False
    )
    op.create_index(
        "ix_clip_sources_platform_external",
        "clip_sources",
        ["platform", "external_id"],
        unique=True,
    )

    op.create_table(
        "clip_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("lane", sa.String(length=16), nullable=False),
        sa.Column("grantor", sa.String(length=128), nullable=False),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("platforms", sa.JSON(), nullable=False),
        sa.Column("rules", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["clip_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clip_grants_source_id"), "clip_grants", ["source_id"], unique=False)
    op.create_index(op.f("ix_clip_grants_expires_at"), "clip_grants", ["expires_at"], unique=False)

    op.create_table(
        "clip_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("has_watermark", sa.Boolean(), nullable=False),
        sa.Column("watermark_regions", sa.JSON(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["clip_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clip_assets_source_id"), "clip_assets", ["source_id"], unique=False)
    op.create_index(op.f("ix_clip_assets_sha256"), "clip_assets", ["sha256"], unique=False)

    op.create_table(
        "repurpose_projects",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("channel_key", sa.String(length=64), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=False),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_repurpose_projects_channel_key"),
        "repurpose_projects",
        ["channel_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_repurpose_projects_created_at"),
        "repurpose_projects",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_repurpose_projects_created_at"), table_name="repurpose_projects")
    op.drop_index(op.f("ix_repurpose_projects_channel_key"), table_name="repurpose_projects")
    op.drop_table("repurpose_projects")

    op.drop_index(op.f("ix_clip_assets_sha256"), table_name="clip_assets")
    op.drop_index(op.f("ix_clip_assets_source_id"), table_name="clip_assets")
    op.drop_table("clip_assets")

    op.drop_index(op.f("ix_clip_grants_expires_at"), table_name="clip_grants")
    op.drop_index(op.f("ix_clip_grants_source_id"), table_name="clip_grants")
    op.drop_table("clip_grants")

    op.drop_index("ix_clip_sources_platform_external", table_name="clip_sources")
    op.drop_index(op.f("ix_clip_sources_discovered_at"), table_name="clip_sources")
    op.drop_index(op.f("ix_clip_sources_status"), table_name="clip_sources")
    op.drop_index(op.f("ix_clip_sources_channel_key"), table_name="clip_sources")
    op.drop_index(op.f("ix_clip_sources_fit_score"), table_name="clip_sources")
    op.drop_index(op.f("ix_clip_sources_creator_handle"), table_name="clip_sources")
    op.drop_index(op.f("ix_clip_sources_external_id"), table_name="clip_sources")
    op.drop_index(op.f("ix_clip_sources_platform"), table_name="clip_sources")
    op.drop_table("clip_sources")

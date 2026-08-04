"""Database tables.

Deliberately a near-copy of the module-level dicts they replace — `JOBS`,
`CHANNELS`, `SCHEDULE`, `RECORDS`, `LAUNCHES` — because the shapes were designed
to make this swap contained. Where a dict held a nested structure it is stored as
JSON rather than normalised: these are documents, they are read whole, and a
schema migration for every new stage field would be a tax on the thing this
project changes most.

Two things are *not* JSON, on purpose:

  * **Quota entries** are rows. They are summed by day on every scheduling
    decision and every publish, and that has to be a query rather than a
    deserialise-and-loop.
  * **Publish times** are a column with an index. The calendar sorts by them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Job(Base):
    """One workflow run.

    `states` is the serialised stage map. It is rewritten after every stage, which
    is what makes a render resumable across a restart — the thing §5.1 was about.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workflow: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="running")
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    states: Mapped[dict] = mapped_column(JSON, default=dict)
    # The event log, appended to as the job runs. Replayed to a subscriber that
    # connects late or reloads mid-render.
    events: Mapped[list] = mapped_column(JSON, default=list)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    # Set when this job was started by the approval gate from a finished video job.
    source_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Channel(Base):
    """A connected YouTube channel.

    The refresh token is stored encrypted — non-negotiable #4 — and there is no
    column for a plaintext one, so it cannot be written by accident.
    """

    __tablename__ = "channels"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), default="")
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QuotaEntry(Base):
    """One metered YouTube API call.

    Rows, not JSON: `spent()` runs on every scheduling decision and before every
    publish, and it must be a sum over an index rather than a full deserialise.
    This is also the single most important table to persist — without it a restart
    forgets the day's spend and the next upload silently overruns the 10,000-unit
    ceiling.
    """

    __tablename__ = "quota_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation: Mapped[str] = mapped_column(String(64))
    cost: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")


# Quota is bucketed by Pacific day, so the common query is a range scan on `at`.
Index("ix_quota_entries_at_operation", QuotaEntry.at, QuotaEntry.operation)


class ScheduleSlot(Base):
    """A video booked for a publish time."""

    __tablename__ = "schedule"

    video_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    job_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChannelLaunch(Base):
    """A channel-launch design, kept because the manual steps take days."""

    __tablename__ = "channel_launches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    niche: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerformanceRecord(Base):
    """A published video's metrics, for the Phase 8 feedback loop."""

    __tablename__ = "performance_records"

    video_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ReviewSnapshot(Base):
    """What the weekly review believed, one row per run.

    Kept as history rather than a single overwritten row: the diff only needs the
    latest, but "when did this become confirmed" is the question anyone will ask
    the first time a finding changes the generator's behaviour, and it cannot be
    reconstructed after the fact.

    `payload` holds the trimmed snapshot from `review.snapshot()` — four strings
    and a verdict per finding — not the full report. See the note there.
    """

    __tablename__ = "review_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

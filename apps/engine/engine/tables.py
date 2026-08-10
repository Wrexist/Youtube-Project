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


class BacklogIdea(Base):
    """A researched video idea, kept until it is made or refused.

    `api/ideas.py` has always scored ideas well and remembered them for thirty
    minutes in a process-local dict, so the same channel got the same suggestions
    proposed, scored, shown and forgotten over and over — and there was no way to
    say "not that one" that survived a reload. A backlog is the difference between
    a suggestion box and a plan.

    `topic` is unique because the generator is adjacency-based and will happily
    re-propose something already on the list; without the constraint the backlog
    fills with near-duplicates of whatever the channel last published.
    """

    __tablename__ = "backlog_ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    score: Mapped[float] = mapped_column(default=0.0)
    demand: Mapped[float] = mapped_column(default=0.0)
    competition: Mapped[float] = mapped_column(default=0.0)
    why: Mapped[str] = mapped_column(Text, default="")
    #: What produced this idea. CLAUDE.md #2: every generated artifact records the
    #: prompt and the model, with no exception for throwaway ones — and an idea that
    #: shapes a whole video is not throwaway. Without these the backlog is a list of
    #: LLM output nobody can attribute, which is exactly what Phase 8's feedback
    #: loop cannot work with.
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    #: `open`, `used` or `dismissed`. Resolved rows are kept rather than deleted:
    #: "we already made this" and "I said no to this" are both reasons not to
    #: propose it again, and a delete forgets the difference.
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    #: Which job consumed it, when one did.
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    #: The readable report — `Review.as_dict()`, sentences and all.
    #:
    #: Separate from `payload` because they answer different questions and have
    #: different lifetimes: `payload` is the comparison state next week's diff
    #: reads, and this is what a person reads. It used to live only in arq's result
    #: store, where `keep_result = 3600` deleted it an hour after the cron produced
    #: it — so the weekly review was written every Monday and readable by nobody.
    #:
    #: Nullable: rows written before this column existed have no report, and
    #: "that run's report is gone" is the honest answer for them.
    #:
    #: `none_as_null` is load-bearing. SQLAlchemy's JSON type stores a Python
    #: `None` as the JSON value `null` by default, not as SQL NULL — so
    #: `report.is_not(None)` matched every report-less row, and `latest_review`
    #: happily returned the JSON null of the newest one. A snapshot written without
    #: a report would have hidden the last real review.
    report: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True), nullable=True, default=None
    )
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

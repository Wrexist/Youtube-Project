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


class Series(Base):
    """A standing series config: a repeatable format with its own cadence and budget.

    This is the persistence for `automation.Series`, which spent its whole life as
    an in-memory dataclass that only tests ever constructed — the Series screen
    shipped with its primary action disabled and the honest excuse "the series
    endpoint does not exist yet". Columns mirror the dataclass one-to-one so
    `repository` can move between the two with `asdict`-shaped code rather than a
    mapping layer.
    """

    __tablename__ = "series"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    niche: Mapped[str] = mapped_column(Text, default="")
    monthly_budget_usd: Mapped[float] = mapped_column(default=0.0)
    shorts_per_week: Mapped[int] = mapped_column(Integer, default=3)
    long_per_week: Mapped[int] = mapped_column(Integer, default=1)
    auto_publish: Mapped[bool] = mapped_column(default=False)
    paused: Mapped[bool] = mapped_column(default=False)
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


class KeywordSnapshot(Base):
    """The autocomplete terms seen for a seed, the last time trend monitoring polled it.

    `engine.trending.rising_autocomplete_terms` is what "a query is newly moving"
    means in this codebase: today's `research.keywords.suggest()` output for a seed,
    diffed against whatever this table held for that seed last time. One row per
    seed, overwritten on each poll — history is not kept because only "what did we
    see last time" is ever read; a row-per-poll table would grow forever for a
    source meant to run on every backlog build.

    `seed` is unique for the same reason `BacklogIdea.topic` is: two pollers racing
    on the same niche should update one row, not create a duplicate the next diff
    never looks at.
    """

    __tablename__ = "keyword_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seed: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    #: A JSON array of strings, not a JSON object — the whole point is "the exact
    #: list seen last time", and wrapping it would only add a key nothing reads.
    terms: Mapped[list] = mapped_column(JSON, default=list)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ThumbnailSwap(Base):
    """One thumbnail A/B swap (FIX-TASKS E2), for the timing guardrails and for
    Phase 8 attribution to segment a video's CTR history at the swap date.

    Rows, not JSON on `PerformanceRecord` — mirroring `QuotaEntry`'s reasoning
    (see the module docstring): "was there a swap on this video in the last 14
    days" is a query the decision logic runs on every sweep, not a document that
    gets read whole. `video_id` is indexed but deliberately not unique — a video
    can be swapped more than once over its lifetime, and the whole point is
    keeping each one.
    """

    __tablename__ = "thumbnail_swaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(64), index=True)
    from_concept: Mapped[str] = mapped_column(String(64), default="")
    to_concept: Mapped[str] = mapped_column(String(64), default="")
    #: The storage key of the image that was set, so the swap is reproducible /
    #: auditable without re-deriving which variant "to_concept" referred to.
    variant_key: Mapped[str] = mapped_column(Text, default="")
    #: Why the decision logic swapped it, in the same words `thumbnail_ab.should_swap`
    #: put on the idea card — e.g. "ctr 2.10% is well below the 5.40% channel median".
    reason: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class TikTokAccount(Base):
    """A connected TikTok account, for Lane A.

    Its own table rather than a row in `channels`: that one is shaped around a
    YouTube channel — `channel_id`, playlists, a quota ledger keyed on it — and
    widening it with a `platform` column would make every YouTube query filter on
    something that is only ever one value.

    The refresh token is encrypted, like YouTube's, and for the same reason: it is
    durable access to an account, and there is no column here for a plaintext one
    so it cannot be written by accident.

    `open_id` is TikTok's stable per-app user id. Kept because the handle can
    change and the id cannot, so it is what tells "the same account reconnected"
    apart from "a second account was added".
    """

    __tablename__ = "tiktok_accounts"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    open_id: Mapped[str] = mapped_column(String(128), default="")
    handle: Mapped[str] = mapped_column(String(64), default="")
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When the *refresh* token dies. Past this only a human re-authorising helps,
    #: and knowing it lets the screen warn before rather than after.
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class ClipSource(Base):
    """A short-form clip we found. **Metadata only — never the media.**

    The split between this table and `ClipAsset` is the rights model made physical.
    Public metadata about a public post is fine to hold indefinitely: a URL, a
    handle, a view count. The *file* is another matter, and it does not exist in
    this system until a `ClipGrant` says it may. A discovered clip with no grant
    stays a row here forever, which is the correct end state for most of them.

    `external_id` is unique per platform because discovery re-runs constantly and
    re-proposing the same TikTok every sweep would fill the workspace with the
    same twenty clips — the same problem `BacklogIdea.topic` solves upstream.
    """

    __tablename__ = "clip_sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), default="tiktok", index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    creator_handle: Mapped[str] = mapped_column(String(64), default="", index=True)
    #: The caption as posted. Untrusted — it reaches an LLM prompt and must go
    #: through `untrusted.fence()` at every call site that interpolates it.
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    sound_id: Mapped[str] = mapped_column(String(64), default="")
    #: Public counters at discovery time. A document, read whole, never queried.
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    region: Mapped[str] = mapped_column(String(8), default="")
    duration_s: Mapped[float] = mapped_column(default=0.0)
    fit_score: Mapped[float] = mapped_column(default=0.0, index=True)
    fit_reasons: Mapped[list] = mapped_column(JSON, default=list)
    #: Which channel this was scored for. Fit is not a property of a clip, it is a
    #: property of a clip *and a channel*, and the same TikTok can be an obvious
    #: yes for one and irrelevant to another.
    channel_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    #: `discovered`, `selected`, `dismissed`. Resolved rows are kept, not deleted —
    #: "I said no to this" is a reason not to surface it again next sweep.
    status: Mapped[str] = mapped_column(String(16), default="discovered", index=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


Index("ix_clip_sources_platform_external", ClipSource.platform, ClipSource.external_id, unique=True)


class ClipGrant(Base):
    """Authority to use one clip, and the evidence for it.

    A table rather than columns on `ClipSource` because grants have a *lifetime*.
    A campaign ends, a licence runs its term, a creator withdraws permission — and
    a video published under the old grant is still live. Flattening this into the
    source row would overwrite the history that answers "were we allowed to publish
    that, at the time we published it", which is the only question that matters
    when somebody eventually asks.

    Mirrors `engine.repurpose.rights.Grant` field for field; `repository` converts.
    """

    __tablename__ = "clip_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("clip_sources.id", ondelete="CASCADE"), index=True
    )
    lane: Mapped[str] = mapped_column(String(16))
    grantor: Mapped[str] = mapped_column(String(128), default="")
    evidence_kind: Mapped[str] = mapped_column(String(32), default="")
    #: A storage key or a URL. Deliberately not prose — "they said yes on stream"
    #: is not something anyone can check six months later, and six months later is
    #: exactly when it gets checked.
    evidence_ref: Mapped[str] = mapped_column(Text, default="")
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    #: Campaign content rules verbatim. Prose, because that is how campaign owners
    #: write them and a human has to read them — parsing them into flags would
    #: invent precision that is not in the source.
    rules: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClipAsset(Base):
    """Media on disk for a cleared clip.

    A row here is proof that a grant existed at fetch time — the acquire stage
    checks `Grant.permits_acquisition` before a byte moves, so an asset without a
    grant is not a policy violation, it is a bug.

    `sha256` is for deduplication, not for evading anything: the same clip reaches
    us through several discovery paths and there is no reason to store it twice.
    """

    __tablename__ = "clip_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("clip_sources.id", ondelete="CASCADE"), index=True
    )
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    duration_s: Mapped[float] = mapped_column(default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    #: Whether a third-party watermark was found, and where. Independently
    #: disqualifying for Shorts monetisation, so it is a stored fact rather than
    #: something re-derived at publish time.
    has_watermark: Mapped[bool] = mapped_column(default=False)
    watermark_regions: Mapped[list] = mapped_column(JSON, default=list)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RepurposeProject(Base):
    """An episode: which clips, cut where, and why they are together.

    `segments` is JSON because it is a document rewritten whole on every edit, and
    a schema migration per new segment field would tax the thing this feature
    changes most — the same reasoning as `Job.states`.
    """

    __tablename__ = "repurpose_projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    #: The editorial argument binding the clips. Without one this is a bag of
    #: clips, which is the shape the reused-content policy names as failing.
    thesis: Mapped[str] = mapped_column(Text, default="")
    segments: Mapped[list] = mapped_column(JSON, default=list)
    job_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    #: The last `gate.Report` this project produced, verbatim. Stored rather than
    #: recomputed because it records the threshold version that judged it, and
    #: "what did we check, and when" is the question a channel review asks.
    report: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


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

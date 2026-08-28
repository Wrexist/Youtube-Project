"""May we use this footage? A copyright question, and only that.

Nothing here has an opinion about whether the finished video is original enough to
monetise. That is `gate.py`, it is judged by a different system against different
rules, and the two are independent in both directions. See the package docstring.

**The rule this module exists to enforce:** no media is fetched without a grant.
Not "should not be" — `Grant.permits_acquisition` is checked by the acquire stage
before a byte moves, because the alternative is a storage directory full of other
people's video with no record of why we have any of it. A discovered clip with no
grant stays metadata forever: the URL and the public view count are fine to hold,
the file is not.

Grants expire, and that is why this is a stored record rather than a boolean on the
clip. A campaign ends, a licence runs its term, a creator withdraws permission — and
a video published last month is still live. `expired` and `revoked` are therefore
different states with different meanings: one lapsed on schedule, the other was
taken back, and only the second is a reason to reconsider what is already published.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Lane(StrEnum):
    """How a clip came to be usable.

    Ordered by how much friction stands between discovering a clip and building
    with it, which is also — not coincidentally — the order to build them in.
    """

    #: Your own account. No counterparty, no term, no expiry.
    OWN = "own"
    #: A funded clipping campaign or official clip programme. The rights holder
    #: *wants* the clips made and pays per verified view, so enrolment replaces
    #: negotiation entirely. Carries content rules that are part of the grant.
    CAMPAIGN = "campaign"
    #: A creator asked and agreed, one-to-one. Slow, but unrestricted by a
    #: campaign's rules.
    LICENSED = "licensed"
    #: A short excerpt quoted inside a video that is substantially ours. The
    #: excerpt is a citation, not the product.
    COMMENTARY = "commentary"
    #: Creative Commons, stock, or brand-supplied under a published licence.
    OPEN_LICENCE = "open_licence"


#: Lanes where somebody else's permission is the basis, so there is a counterparty
#: to name and evidence to keep. `OWN` has neither, and demanding a grantor for
#: your own footage would be paperwork theatre.
_NEEDS_GRANTOR = frozenset({Lane.CAMPAIGN, Lane.LICENSED, Lane.OPEN_LICENCE})

#: Lanes that must credit the source on screen *and* in the description. Not
#: `OWN` (crediting yourself is noise) and not `COMMENTARY`, where the citation is
#: the point and the framing already names what is being discussed — though in
#: practice commentary videos name their source anyway, and the gate does not
#: object if they do.
_NEEDS_ATTRIBUTION = frozenset({Lane.CAMPAIGN, Lane.LICENSED, Lane.OPEN_LICENCE})

#: Lanes whose grant can carry an expiry. A campaign ends and a licence runs a
#: term; ownership does not lapse, and neither does a published open licence
#: (a CC grant is irrevocable for copies already made under it).
_CAN_EXPIRE = frozenset({Lane.CAMPAIGN, Lane.LICENSED})


def _aware(moment: datetime | None) -> datetime | None:
    """Coerce a naive datetime to UTC.

    **SQLite does not store timezones.** `DateTime(timezone=True)` is honoured by
    Postgres and quietly ignored by SQLite, so a grant read back from the default
    development database has naive `expires_at` and `revoked_at` while `now` is
    aware — and comparing them raises `TypeError: can't compare offset-naive and
    offset-aware datetimes`.

    That crash lands in `permits_acquisition`, which is the check standing between
    a lapsed licence and fetching media under it. Worse, it is invisible in CI:
    CI runs Postgres, which returns aware datetimes and passes. The failure is
    reserved for whoever runs the default configuration, which is everyone on a
    fresh clone.

    Coerced here, at the comparison, rather than only in the repository's row
    loader — grants are also constructed by API handlers and tests, and a rule
    enforced at one of three entrances is not enforced.
    """
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class RightsProblem:
    """One reason a grant does not authorise what is being asked of it.

    `fatal` distinguishes "this cannot proceed" from "this looks wrong and a human
    should see it". Only fatal problems block; the rest surface in the report.
    """

    code: str
    message: str
    fatal: bool = True


@dataclass(frozen=True)
class Grant:
    """Authority to use one clip, and the evidence for it.

    `evidence_ref` is a storage key or a URL — a screenshot of the DM, the campaign
    enrolment page, the licence text. Deliberately not free prose: "they said yes on
    stream" is not evidence anyone can check six months later, and six months later
    is exactly when it gets checked.
    """

    lane: Lane
    #: Who granted it. Empty for `OWN`, required for the lanes in `_NEEDS_GRANTOR`.
    grantor: str = ""
    #: What kind of proof exists — "campaign_enrolment", "dm_screenshot",
    #: "email", "licence_url", "self".
    evidence_kind: str = ""
    #: Where that proof lives. A storage key or a URL.
    evidence_ref: str = ""
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    #: Platforms this grant covers — "youtube", "tiktok", "shorts". Empty means
    #: unrestricted, which is the honest reading of "yes, go for it" from a
    #: creator who was not asked to enumerate platforms.
    platforms: frozenset[str] = field(default_factory=frozenset)
    #: Campaign content rules, verbatim, when the lane is CAMPAIGN. Kept as text
    #: because they are written by the campaign owner in prose and a human has to
    #: read them; parsing them into flags would invent precision that is not there.
    rules: str = ""

    # ── state ────────────────────────────────────────────────────────────────

    def revoked(self, *, now: datetime | None = None) -> bool:
        now = _aware(now) or datetime.now(UTC)
        revoked_at = _aware(self.revoked_at)
        return revoked_at is not None and revoked_at <= now

    def expired(self, *, now: datetime | None = None) -> bool:
        now = _aware(now) or datetime.now(UTC)
        expires_at = _aware(self.expires_at)
        return expires_at is not None and expires_at <= now

    def covers(self, platform: str) -> bool:
        """Whether this grant extends to `platform`.

        An empty `platforms` covers everything — see the field note. This is the
        permissive reading, and it is the right one: the alternative is refusing to
        build with a clip whose creator plainly said yes because they did not
        recite a list of websites.
        """
        return not self.platforms or platform.lower() in self.platforms

    def permits_acquisition(self, *, now: datetime | None = None) -> bool:
        """The single check the acquire stage runs before fetching media.

        Deliberately narrow: it asks only whether we may hold the file at all, not
        whether we may publish it to a given platform. Those separate because a
        revoked grant means delete the file, while an out-of-scope platform just
        means build for a different one.
        """
        return not self.revoked(now=now) and not self.expired(now=now)

    # ── validation ───────────────────────────────────────────────────────────

    def problems(
        self, *, platform: str = "youtube", now: datetime | None = None
    ) -> list[RightsProblem]:
        """Everything wrong with this grant, most serious first.

        Empty means cleared. This is the whole of the rights gate — there is no
        scoring, because rights are not a matter of degree: either there is
        authority to use the footage or there is not.
        """
        now = now or datetime.now(UTC)
        out: list[RightsProblem] = []

        if self.revoked(now=now):
            out.append(
                RightsProblem(
                    "revoked",
                    f"{self.grantor or 'the rights holder'} withdrew permission on "
                    f"{self.revoked_at:%Y-%m-%d}. Anything already published using this "
                    "clip should be reviewed.",
                )
            )
        elif self.expired(now=now):
            out.append(
                RightsProblem(
                    "expired",
                    f"the grant ran out on {self.expires_at:%Y-%m-%d}"
                    + (f" ({self.lane.value} from {self.grantor})" if self.grantor else ""),
                )
            )

        if self.lane in _NEEDS_GRANTOR and not self.grantor.strip():
            out.append(
                RightsProblem("no_grantor", f"a {self.lane.value} grant must name who granted it")
            )

        # Evidence is required wherever there is a counterparty. Without it the
        # record is an assertion that we have permission, which is precisely the
        # thing that needs proving.
        if self.lane in _NEEDS_GRANTOR and not self.evidence_ref.strip():
            out.append(
                RightsProblem(
                    "no_evidence",
                    f"a {self.lane.value} grant needs evidence — a link, a screenshot, "
                    "or the licence text. An unevidenced grant is a claim, not a record.",
                )
            )

        if not self.covers(platform):
            out.append(
                RightsProblem(
                    "platform_not_covered",
                    f"this grant covers {', '.join(sorted(self.platforms))} — not {platform}",
                )
            )

        # Non-fatal: an unbounded campaign grant is plausible (many run until the
        # budget is spent) but it is worth a human glance, because the usual cause
        # is nobody having read the campaign's terms.
        if self.lane in _CAN_EXPIRE and self.expires_at is None:
            out.append(
                RightsProblem(
                    "no_term",
                    f"this {self.lane.value} grant records no end date — check the "
                    "campaign's terms and set one if it has a term",
                    fatal=False,
                )
            )

        return sorted(out, key=lambda p: not p.fatal)

    @property
    def needs_attribution(self) -> bool:
        return self.lane in _NEEDS_ATTRIBUTION

    def cleared(self, *, platform: str = "youtube", now: datetime | None = None) -> bool:
        return not any(p.fatal for p in self.problems(platform=platform, now=now))

    def as_dict(self) -> dict:
        """The grant as JSON, with every timestamp an unambiguous UTC instant.

        Through `_aware` rather than `.isoformat()` directly, for the same reason
        the comparisons above use it — and it was missing here, which made this
        the *serialising* half of the same SQLite bug. A grant held in memory
        carried an aware `revoked_at` and left as `…+00:00`; the identical grant
        read back from SQLite carried a naive one and left as `…` with no offset.

        That is not cosmetic. `new Date("2026-08-28T09:43:20")` is parsed as
        **local** time by every browser, while the `+00:00` form is parsed as UTC
        — so the same revocation rendered hours apart on the rights card
        depending only on whether it had been through the database yet. Postgres
        returns aware datetimes and hides it, which is the same reason `_aware`
        exists at all: the failure is reserved for the default configuration.
        """

        def moment(value: datetime | None) -> str | None:
            coerced = _aware(value)
            return coerced.isoformat() if coerced else None

        return {
            "lane": self.lane.value,
            "grantor": self.grantor,
            "evidence_kind": self.evidence_kind,
            "evidence_ref": self.evidence_ref,
            "granted_at": moment(self.granted_at),
            "expires_at": moment(self.expires_at),
            "revoked_at": moment(self.revoked_at),
            "platforms": sorted(self.platforms),
            "rules": self.rules,
            "needs_attribution": self.needs_attribution,
        }


def own() -> Grant:
    """The Lane A grant. No counterparty, so nothing to evidence."""
    return Grant(lane=Lane.OWN, evidence_kind="self", evidence_ref="self")

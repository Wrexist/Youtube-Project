"""Grants, and the states they pass through.

The interesting cases are all about time: a grant that was valid when the video was
built and is not valid now. `expired` and `revoked` are deliberately different
answers, because only one of them is a reason to revisit what is already published.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engine.repurpose.rights import Grant, Lane, own

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def codes(grant, **kw):
    return {p.code for p in grant.problems(now=NOW, **kw)}


def fatal_codes(grant, **kw):
    return {p.code for p in grant.problems(now=NOW, **kw) if p.fatal}


def test_own_needs_no_paperwork():
    assert own().cleared(now=NOW)
    assert not own().needs_attribution


def test_campaign_grant_clears():
    grant = Grant(
        lane=Lane.CAMPAIGN,
        grantor="@streamer",
        evidence_kind="campaign_enrolment",
        evidence_ref="https://whop.example/c/1",
        granted_at=NOW - timedelta(days=7),
        expires_at=NOW + timedelta(days=30),
    )
    assert grant.cleared(now=NOW)
    assert grant.needs_attribution


def test_expired_grant_is_fatal():
    grant = Grant(
        lane=Lane.CAMPAIGN,
        grantor="@streamer",
        evidence_kind="campaign_enrolment",
        evidence_ref="https://whop.example/c/1",
        expires_at=NOW - timedelta(days=1),
    )
    assert "expired" in fatal_codes(grant)
    assert not grant.permits_acquisition(now=NOW)


def test_revoked_beats_expired_in_the_message():
    """Both true; the operator needs to hear the one that implies action."""
    grant = Grant(
        lane=Lane.LICENSED,
        grantor="@creator",
        evidence_kind="email",
        evidence_ref="storage://g/1",
        expires_at=NOW - timedelta(days=30),
        revoked_at=NOW - timedelta(days=1),
    )
    assert "revoked" in fatal_codes(grant)
    assert "expired" not in codes(grant)
    message = next(p.message for p in grant.problems(now=NOW) if p.code == "revoked")
    assert "already published" in message


def test_unevidenced_grant_is_refused():
    """An unevidenced grant is a claim that we have permission, not a record of it."""
    grant = Grant(lane=Lane.LICENSED, grantor="@creator", granted_at=NOW)
    assert "no_evidence" in fatal_codes(grant)


def test_grant_without_a_grantor_is_refused():
    grant = Grant(lane=Lane.CAMPAIGN, evidence_kind="x", evidence_ref="y")
    assert "no_grantor" in fatal_codes(grant)


def test_platform_scope_is_enforced():
    grant = Grant(
        lane=Lane.LICENSED,
        grantor="@creator",
        evidence_kind="dm_screenshot",
        evidence_ref="storage://g/2",
        platforms=frozenset({"tiktok"}),
    )
    assert "platform_not_covered" in fatal_codes(grant, platform="youtube")
    assert grant.cleared(platform="tiktok", now=NOW)


def test_empty_platform_scope_covers_everything():
    """ "Yes, go for it" from someone who was not asked to list websites."""
    grant = Grant(
        lane=Lane.LICENSED,
        grantor="@creator",
        evidence_kind="dm_screenshot",
        evidence_ref="storage://g/3",
    )
    assert grant.covers("youtube")
    assert grant.covers("tiktok")


def test_termless_campaign_warns_without_blocking():
    grant = Grant(
        lane=Lane.CAMPAIGN,
        grantor="@streamer",
        evidence_kind="campaign_enrolment",
        evidence_ref="https://whop.example/c/2",
    )
    assert "no_term" in codes(grant)
    assert "no_term" not in fatal_codes(grant)
    assert grant.cleared(now=NOW)


def test_acquisition_is_permitted_only_while_the_grant_lives():
    """The check the acquire stage runs before a byte moves."""
    live = Grant(lane=Lane.OWN)
    dead = Grant(lane=Lane.OWN, revoked_at=NOW - timedelta(seconds=1))
    assert live.permits_acquisition(now=NOW)
    assert not dead.permits_acquisition(now=NOW)


def test_naive_timestamps_from_sqlite_do_not_crash_the_check():
    """SQLite ignores `DateTime(timezone=True)` and hands back naive datetimes.

    Comparing one against an aware `now` raises TypeError, and it raises it inside
    `permits_acquisition` — the check standing between a lapsed licence and
    fetching media under it. CI runs Postgres and would never see this; every
    fresh clone runs SQLite and would.
    """
    naive_past = datetime(2026, 8, 9)  # noqa: DTZ001 — the whole point of the test
    grant = Grant(lane=Lane.CAMPAIGN, grantor="@s", evidence_kind="e", evidence_ref="r")

    assert Grant(**{**grant.__dict__, "expires_at": naive_past}).expired(now=NOW)
    assert Grant(**{**grant.__dict__, "revoked_at": naive_past}).revoked(now=NOW)
    assert not Grant(**{**grant.__dict__, "expires_at": naive_past}).permits_acquisition(now=NOW)


def test_a_naive_now_is_also_tolerated():
    """The caller is not always the one holding an aware clock either."""
    grant = Grant(lane=Lane.OWN, expires_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert grant.expired(now=datetime(2026, 8, 10))  # noqa: DTZ001


def test_as_dict_round_trips_the_fields_the_ui_reads():
    grant = Grant(
        lane=Lane.CAMPAIGN,
        grantor="@streamer",
        evidence_kind="campaign_enrolment",
        evidence_ref="https://whop.example/c/1",
        granted_at=NOW,
        expires_at=NOW + timedelta(days=30),
        platforms=frozenset({"youtube", "shorts"}),
        rules="credit in description; no gambling overlays",
    )
    payload = grant.as_dict()
    assert payload["lane"] == "campaign"
    assert payload["platforms"] == ["shorts", "youtube"]
    assert payload["needs_attribution"] is True
    assert payload["rules"].startswith("credit")

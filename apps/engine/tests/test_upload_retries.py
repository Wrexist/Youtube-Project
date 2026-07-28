"""The resumable upload's failure handling.

Two defects, both of which only show up when Google is having a bad day — which is
exactly when you least want to find them:

1. The 1,600 units for `videos.insert` were recorded only when the upload
   *succeeded*. Google charges them when the session opens, so every failed upload
   spent real quota the ledger never saw. Enough of those and the ledger waves
   through an upload there is no budget left for, which is the silent overrun the
   ledger exists to prevent.
2. Transient failures hit a bare `continue` inside `while offset < size`, with no
   attempt cap and no delay — a persistent 503 spun as fast as the network allowed,
   indefinitely, against an API that was asking us to back off.
"""

from __future__ import annotations

import pytest

from engine.providers import youtube

# ── backoff ─────────────────────────────────────────────────────────────────


def test_backoff_grows_and_is_capped():
    delays = [youtube._backoff(n, None) for n in range(1, 8)]
    assert delays == sorted(delays), "must not shrink"
    assert delays[0] == pytest.approx(2.0)
    assert max(delays) <= youtube.BACKOFF_MAX_S


def test_retry_after_wins_when_the_server_sends_one():
    """On a 429 that header is the server telling us the answer."""
    assert youtube._backoff(1, "17") == pytest.approx(17.0)


def test_an_absurd_retry_after_is_capped():
    """A Retry-After of an hour must not park a render for an hour."""
    assert youtube._backoff(1, "3600") == youtube.BACKOFF_MAX_S


def test_an_http_date_retry_after_falls_back_to_exponential():
    """Retry-After may be a date; it must not crash the upload."""
    assert youtube._backoff(2, "Wed, 21 Oct 2026 07:28:00 GMT") == pytest.approx(4.0)


def test_a_negative_retry_after_does_not_become_a_negative_sleep():
    assert youtube._backoff(1, "-5") >= 0.0


def test_the_cap_is_low_enough_to_notice_but_high_enough_to_survive_a_blip():
    assert 3 <= youtube.MAX_CHUNK_RETRIES <= 10


# ── resume offset ───────────────────────────────────────────────────────────


def test_the_servers_range_is_authoritative():
    """It can confirm less than we sent; believing ourselves would corrupt the file."""
    assert youtube._resume_offset("bytes=0-999", offset=0, chunk_len=8192) == 1000


def test_a_missing_range_assumes_the_chunk_landed():
    assert youtube._resume_offset(None, offset=4096, chunk_len=4096) == 8192


@pytest.mark.parametrize("header", ["nonsense", "bytes=", "bytes=abc-def", "0-1-2-3"])
def test_a_malformed_range_does_not_raise_mid_upload(header):
    """`int(rng.split("-")[1]) + 1` raised on anything unexpected — in the middle of
    an upload whose quota had already been spent."""
    assert youtube._resume_offset(header, offset=100, chunk_len=50) == 150


# ── quota is booked when the session opens ──────────────────────────────────


def test_quota_is_recorded_before_the_first_chunk_not_after_the_last():
    """The guard: `videos.insert` must not be recorded inside the success branch."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "engine/providers/youtube.py").read_text()
    upload = source[source.index("async def upload(") :]

    booking = upload.index('ledger.record("videos.insert"')
    loop = upload.index("while offset < size:")
    assert booking < loop, "quota must be booked before the upload loop, not on success"

    # And nothing re-records it on the way out.
    after_loop = upload[loop:]
    assert not re.search(r'ledger\.record\(\s*\n?\s*"videos\.insert"', after_loop)

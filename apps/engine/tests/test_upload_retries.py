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

import json
from datetime import UTC, datetime, timedelta

import httpx
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
    assert youtube._resume_offset("bytes=0-999", offset=0) == 1000


def test_a_missing_range_re_sends_rather_than_skipping():
    """Google's protocol: a 308 with no `Range` means nothing was persisted.

    The old fallback assumed the chunk landed and advanced past it, which leaves a
    hole in the middle of the uploaded file that nothing downstream detects.
    """
    assert youtube._resume_offset(None, offset=4096) == 4096


@pytest.mark.parametrize("header", ["nonsense", "bytes=", "bytes=abc-def", "0-1-2-3"])
def test_a_malformed_range_does_not_raise_mid_upload(header):
    """`int(rng.split("-")[1]) + 1` raised on anything unexpected — in the middle of
    an upload whose quota had already been spent."""
    assert youtube._resume_offset(header, offset=100) == 100


def test_a_308_that_never_advances_gives_up_instead_of_spinning():
    """Re-sending is right; re-sending forever is not."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "engine/providers/youtube.py").read_text(
        encoding="utf-8"
    )
    block = source[source.index("if resp.status_code == 308") :]
    block = block[: block.index("if resp.status_code in (500")]
    assert "attempts += 1" in block
    assert re.search(r"attempts > MAX_CHUNK_RETRIES", block)


# ── quota is booked when the session opens ──────────────────────────────────


#: How the upload books its 1,600 units. `record` first, then `reserve` once the
#: check and the write had to happen under one lock — the invariant this test is
#: about survived the rename, so the pattern names both rather than pinning one.
_BOOKS_THE_UPLOAD = r'ledger\.(record|reserve)\(\s*\n?\s*"videos\.insert"'


def test_quota_is_recorded_before_the_first_chunk_not_after_the_last():
    """The guard: `videos.insert` must not be booked inside the success branch.

    Google charges when the resumable session is created, whatever happens to the
    upload afterwards, so booking on 200 meant every failed upload spent real quota
    the ledger never saw — and enough of those wave through an upload there is no
    budget left for.
    """
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "engine/providers/youtube.py").read_text(
        encoding="utf-8"
    )
    upload = source[source.index("async def upload(") :]

    booking = re.search(_BOOKS_THE_UPLOAD, upload)
    assert booking, "the upload books no quota at all"
    loop = upload.index("while offset < size:")
    assert booking.start() < loop, "quota must be booked before the upload loop, not on success"

    # And nothing re-books it on the way out.
    assert not re.search(_BOOKS_THE_UPLOAD, upload[loop:])


# ── the request Google actually receives ────────────────────────────────────
#
# Everything above reads the source or a pure helper. The body construction at the
# top of `upload` — the snippet and status blocks, and the guard that refuses a
# publish time on a non-private video — was executed by nothing: every test that
# reaches a publish substitutes a fake client for the whole of this module.
#
# These drive the real `upload` against a mocked transport, so the thing under
# test is the JSON that would go to `videos.insert`. Getting `privacyStatus` or
# `publishAt` wrong there puts a video live on a real channel ahead of its date,
# and there is no undo for that.

SESSION_URL = "https://upload.example.test/session/1"


class _Recorded:
    """`httpx` with every `AsyncClient` answered locally, keeping the requests.

    `upload` builds its own clients, so there is no transport to inject; rebinding
    the module-level `httpx` name inside `engine.providers.youtube` is the narrowest
    substitute available.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __getattr__(self, name):
        return getattr(httpx, name)  # everything else stays the real module

    def AsyncClient(self, **kwargs):  # noqa: N802 — mirrors httpx's own name
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), **kwargs)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, headers={"Location": SESSION_URL})
        return httpx.Response(200, json={"id": "yt-abc"})

    @property
    def init_body(self) -> dict:
        return json.loads(next(r for r in self.requests if r.method == "POST").content)


@pytest.fixture
def uploader(monkeypatch, tmp_path):
    """A real `YouTube.upload` with no network and a ledger of its own."""
    from engine.quota import QuotaLedger

    recorded = _Recorded()
    monkeypatch.setattr(youtube, "httpx", recorded)
    monkeypatch.setattr(youtube, "ledger", QuotaLedger(persist=False))
    monkeypatch.setattr(youtube, "CHUNK", 1024)

    video = tmp_path / "render.mp4"
    video.write_bytes(b"m" * 512)

    creds = youtube.Credentials(
        refresh_token_encrypted="enc",
        access_token="tok",
        # Fresh, so `_headers` does not detour through `refresh()`.
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        channel_id="UC123",
    )
    return youtube.YouTube(creds), video, recorded


async def test_the_upload_body_carries_the_privacy_and_the_schedule(uploader):
    client, video, recorded = uploader
    when = datetime(2026, 9, 1, 14, 30, tzinfo=UTC)

    video_id = await client.upload(
        video,
        title="Why bridges collapse",
        description="A description.",
        tags=["bridges"],
        privacy="private",
        publish_at=when,
        made_for_kids=True,
    )

    assert video_id == "yt-abc"
    status = recorded.init_body["status"]
    assert status["privacyStatus"] == "private"
    assert status["publishAt"] == when.isoformat()
    assert status["selfDeclaredMadeForKids"] is True
    snippet = recorded.init_body["snippet"]
    assert snippet["title"] == "Why bridges collapse"
    assert snippet["tags"] == ["bridges"]


async def test_a_publish_time_on_a_public_video_is_refused_before_the_spend(uploader):
    """YouTube ignores `publishAt` on a public video — silently. So the alternative
    to raising here is a video that goes live immediately and cannot be recalled."""
    client, video, recorded = uploader

    with pytest.raises(ValueError, match="privacyStatus='private'"):
        await client.upload(
            video,
            title="t",
            description="d",
            tags=[],
            privacy="public",
            publish_at=datetime(2026, 9, 1, 14, 30, tzinfo=UTC),
        )

    assert recorded.requests == [], "the session was opened before the check"
    assert youtube.ledger.spent() == 0, "1,600 units booked for an upload that never ran"


async def test_an_unscheduled_upload_sends_no_publish_at(uploader):
    client, video, recorded = uploader
    await client.upload(video, title="t", description="d", tags=[], privacy="unlisted")

    status = recorded.init_body["status"]
    assert status["privacyStatus"] == "unlisted"
    assert "publishAt" not in status
    assert status["selfDeclaredMadeForKids"] is False


async def test_the_upload_books_its_units_once(uploader):
    """`reserve` runs for real here rather than being stubbed out — this is the only
    test in which the 1,600 units are booked by the code that spends them."""
    client, video, _recorded = uploader
    await client.upload(video, title="t", description="d", tags=[], privacy="private")

    assert youtube.ledger.spent() == 1600
    assert youtube.ledger.entries[0].channel_id == "UC123"

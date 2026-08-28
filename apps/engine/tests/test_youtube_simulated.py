"""The YouTube path driven against a simulated Google, not a substituted client.

KNOWN-ISSUES §1.1 has said since it was written that every Google call in this
repository is unexecuted code, and that the resumable chunk loop and the
`308 Resume Incomplete` handling are "the parts most likely to be subtly wrong,
because they are the parts that cannot be reasoned about without a real response".
We still have no credentials. But `providers/youtube.py` builds ordinary `httpx`
clients against module-level URL constants, so `respx` can stand in for
`oauth2.googleapis.com` and `www.googleapis.com` and answer the way the documented
protocol says Google answers.

The simulation is deliberately *unhelpful*. `ResumableUploadSim` keeps its own copy
of what it has persisted, refuses a chunk that does not start where its data ends —
which is what Google does, with a 400 — and hands back a `Range` header that is the
only truth about how much arrived. Code that trusts its own arithmetic over that
header fails here, which is the whole point: it would have failed at Google
identically, months from now, halfway through a 1,600-unit upload.

What this file proves is behaviour against the protocol as documented. It is not
proof that Google accepts these requests; §1.1 stays open until a real upload
happens. The honest list of what remains unproven is in the report that came with
this file and in §4.12.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from engine.providers import youtube
from engine.quota import QuotaLedger

#: Google hands back a session URI on the same host, carrying an opaque upload id.
#: Anything that assumes it is a different origin, or that it has no query string,
#: is assuming something Google does not promise.
SESSION_URI = f"{youtube.UPLOAD}?uploadType=resumable&upload_id=SIM-0001"

_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class ResumableUploadSim:
    """`upload/youtube/v3/videos`, behaving the way the protocol documents.

    Holds the bytes it has persisted, so "did every byte arrive, once, in order"
    is answerable by comparing `stored` with the file on disk rather than by
    trusting the requests we sent.

    * `faults` maps a PUT index to a status code returned instead of storing
      anything — Google's 500/503/429, which mean "try that chunk again".
    * `short_by` maps a PUT index to a number of bytes the server quietly drops
      from the end of the chunk. It still answers 308, and its `Range` says so.
      This is the case that cannot be reasoned about without a real response.
    """

    def __init__(
        self,
        video_id: str = "sim-video-1",
        *,
        faults: dict[int, int] | None = None,
        short_by: dict[int, int] | None = None,
        session_status: int = 200,
        session_headers: dict[str, str] | None = None,
    ) -> None:
        self.video_id = video_id
        self.faults = dict(faults or {})
        self.short_by = dict(short_by or {})
        self.session_status = session_status
        self.session_headers = (
            session_headers if session_headers is not None else {"Location": SESSION_URI}
        )
        self.stored = bytearray()
        self.sessions: list[httpx.Request] = []
        self.puts: list[tuple[str, bytes]] = []

    # ── routes ──────────────────────────────────────────────────────────────

    def open_session(self, request: httpx.Request) -> httpx.Response:
        self.sessions.append(request)
        return httpx.Response(self.session_status, headers=self.session_headers)

    def put_chunk(self, request: httpx.Request) -> httpx.Response:
        index = len(self.puts)
        content_range = request.headers.get("Content-Range", "")
        body = bytes(request.content)
        self.puts.append((content_range, body))

        match = _CONTENT_RANGE.fullmatch(content_range)
        assert match, f"malformed Content-Range {content_range!r}"
        first, last, total = (int(g) for g in match.groups())
        assert last - first + 1 == len(body), "Content-Range disagrees with the body length"

        if first != len(self.stored):
            # Google's answer to a chunk that does not continue what it holds. The
            # client cannot recover from it, and that is deliberate: a silently
            # accepted gap is a video with a hole in the middle.
            return httpx.Response(
                400,
                json={"error": {"code": 400, "message": f"expected byte {len(self.stored)}"}},
            )

        if index in self.faults:
            return httpx.Response(self.faults[index], json={"error": {"code": self.faults[index]}})

        kept = body[: len(body) - self.short_by.get(index, 0)]
        self.stored.extend(kept)

        if len(self.stored) == total:
            return httpx.Response(200, json={"kind": "youtube#video", "id": self.video_id})
        return httpx.Response(308, headers={"Range": f"bytes=0-{len(self.stored) - 1}"})

    # ── what a test asks it ─────────────────────────────────────────────────

    @property
    def session_body(self) -> dict:
        return json.loads(self.sessions[0].content)

    @property
    def content_ranges(self) -> list[str]:
        return [cr for cr, _ in self.puts]


def mount(sim: ResumableUploadSim) -> None:
    """Point respx at the simulator. Call inside an `@respx.mock` test."""
    respx.route(method="POST", url__startswith=youtube.UPLOAD).mock(side_effect=sim.open_session)
    respx.route(method="PUT", url__startswith=youtube.UPLOAD).mock(side_effect=sim.put_chunk)


async def _noop_emit(_event: dict) -> None:
    """A workflow event sink for tests that assert on state, not on the stream."""
    return None


class Events(list):
    """An awaitable event sink. `Workflow.run` awaits whatever it is handed."""

    async def __call__(self, event: dict) -> None:
        self.append(event)

    @property
    def types(self) -> list[str]:
        return [e["type"] for e in self]


def token_response(**overrides) -> httpx.Response:
    """What `oauth2.googleapis.com/token` returns for a refresh grant."""
    payload = {"access_token": "ya29.fresh", "expires_in": 3599, "token_type": "Bearer"}
    payload.update(overrides)
    return httpx.Response(200, json=payload)


@pytest.fixture(autouse=True)
def google(monkeypatch, tmp_path):
    """A ledger of this test's own, a real secret key, and no real backoff.

    `BACKOFF_BASE_S = 0` rather than patching `asyncio.sleep`: the arithmetic in
    `_backoff` is already covered in `test_upload_retries.py`, and a test that waits
    two real seconds per transient failure gets deleted rather than fixed.
    """
    from engine import crypto
    from engine.settings import get_settings

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "sim-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sim-client-secret")
    monkeypatch.setenv("STUDIO_SECRET_KEY", "k" * 48)
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    crypto.reset_cache()

    monkeypatch.setattr(youtube, "ledger", QuotaLedger(persist=False))
    monkeypatch.setattr(youtube, "BACKOFF_BASE_S", 0.0)

    yield

    get_settings.cache_clear()
    crypto.reset_cache()


def credentials(*, expired: bool = False) -> youtube.Credentials:
    from engine.crypto import encrypt

    return youtube.Credentials(
        refresh_token_encrypted=encrypt("1//refresh-token"),
        access_token="ya29.stale" if expired else "ya29.current",
        expires_at=datetime.now(UTC) + timedelta(seconds=-30 if expired else 3600),
        channel_id="UCsimulated",
    )


@pytest.fixture
def video(tmp_path):
    """Three-and-a-bit chunks of recognisable, position-dependent bytes.

    Position-dependent so a reassembly that is the right *length* but the wrong
    *order* still fails — `b"m" * n` would not catch a swapped chunk.
    """
    path = tmp_path / "render.mp4"
    path.write_bytes(bytes((i * 7 + 11) % 251 for i in range(3400)))
    return path


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(youtube, "CHUNK", 1024)
    return youtube.YouTube(credentials())


# ── OAuth ───────────────────────────────────────────────────────────────────


@respx.mock
async def test_an_expired_access_token_is_refreshed_exactly_once_for_one_upload(video, monkeypatch):
    """Four HTTP round trips to Google, one refresh.

    `_headers()` is called once per upload and the chunk PUTs reuse the session
    URI, so a second refresh would mean the token is being re-fetched inside the
    loop — 8,000 spurious token requests on a 60GB upload, and Google rate-limits
    the token endpoint.
    """
    monkeypatch.setattr(youtube, "CHUNK", 1024)
    token = respx.post(youtube.TOKEN_URL).mock(return_value=token_response())
    sim = ResumableUploadSim()
    mount(sim)

    creds = credentials(expired=True)
    await youtube.YouTube(creds).upload(video, title="t", description="d", tags=[])

    assert token.call_count == 1
    assert creds.access_token == "ya29.fresh"
    assert creds.is_fresh


@respx.mock
async def test_the_refresh_grant_sends_the_form_google_documents():
    """`grant_type=refresh_token` with the client pair, form-encoded, not JSON."""
    token = respx.post(youtube.TOKEN_URL).mock(return_value=token_response())

    await youtube.refresh(credentials(expired=True))

    request = token.calls[0].request
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert dict(httpx.QueryParams(request.content.decode())) == {
        "refresh_token": "1//refresh-token",
        "client_id": "sim-client-id",
        "client_secret": "sim-client-secret",
        "grant_type": "refresh_token",
    }


@respx.mock
async def test_a_fresh_token_is_not_refreshed_at_all():
    token = respx.post(youtube.TOKEN_URL).mock(return_value=token_response())
    respx.route(method="GET", url__startswith=f"{youtube.API}/channels").mock(
        return_value=httpx.Response(
            200, json={"items": [{"statistics": {"subscriberCount": "12"}}]}
        )
    )

    assert await youtube.YouTube(credentials()).subscriber_count() == 12
    assert token.call_count == 0


@respx.mock
async def test_revoked_consent_surfaces_as_the_disconnect_the_api_maps_to_409():
    """Google answers a revoked grant with 400 `invalid_grant`, which no retry fixes.

    `main.py` turns `ChannelDisconnected` into a 409 naming `/v1/auth/google`; a
    plain `YouTubeError` here would reach the operator as a 500 with no way out.
    """
    respx.post(youtube.TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            },
        )
    )

    with pytest.raises(youtube.ChannelDisconnected, match="revoked"):
        await youtube.refresh(credentials(expired=True))


@respx.mock
async def test_a_dead_refresh_token_does_not_book_the_uploads_1600_units(video, client):
    """The reservation is made before the token is refreshed, so it has to come back.

    `reserve()` runs first — correctly, since Google charges when the session
    opens — but the refresh sits between it and the session POST, outside the
    `try` that refunds. A channel whose consent was revoked therefore burned 1,600
    units per attempted publish against an API it never reached, and six attempts
    exhausted the day's quota for uploads that could not have happened.
    """
    respx.post(youtube.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    sim = ResumableUploadSim()
    mount(sim)
    client.creds.expires_at = datetime.now(UTC) - timedelta(seconds=30)

    with pytest.raises(youtube.ChannelDisconnected):
        await client.upload(video, title="t", description="d", tags=[])

    assert sim.sessions == [], "the session was opened without a usable token"
    assert youtube.ledger.spent() == 0, "1,600 units booked for a request Google never saw"


# ── the resumable upload ────────────────────────────────────────────────────


@respx.mock
async def test_the_session_opens_with_the_headers_that_make_it_resumable(video, client):
    """`uploadType=resumable` plus the two `X-Upload-Content-*` hints.

    Without `X-Upload-Content-Length` Google cannot report progress and will not
    fail early on an over-size file; without `part` the insert is rejected.
    """
    sim = ResumableUploadSim()
    mount(sim)

    await client.upload(video, title="t", description="d", tags=[])

    request = sim.sessions[0]
    assert dict(request.url.params) == {"uploadType": "resumable", "part": "snippet,status"}
    assert request.headers["X-Upload-Content-Length"] == str(video.stat().st_size)
    assert request.headers["X-Upload-Content-Type"] == "video/mp4"
    assert request.headers["Authorization"] == "Bearer ya29.current"
    assert request.headers["content-type"].startswith("application/json")


@respx.mock
async def test_every_byte_arrives_once_and_in_order(video, client):
    sim = ResumableUploadSim()
    mount(sim)

    await client.upload(video, title="t", description="d", tags=[])

    assert bytes(sim.stored) == video.read_bytes()
    # And on the wire, not merely in the reassembly: a clean run re-sends nothing.
    assert b"".join(body for _, body in sim.puts) == video.read_bytes()


@respx.mock
async def test_each_content_range_names_the_bytes_and_the_total(video, client):
    """`bytes <first>-<last>/<total>`. A wrong total makes Google wait forever for
    bytes that were already sent; a wrong first byte is a hole."""
    sim = ResumableUploadSim()
    mount(sim)

    await client.upload(video, title="t", description="d", tags=[])

    assert sim.content_ranges == [
        "bytes 0-1023/3400",
        "bytes 1024-2047/3400",
        "bytes 2048-3071/3400",
        "bytes 3072-3399/3400",
    ]


@respx.mock
async def test_the_video_id_google_returns_is_the_one_upload_returns(video, client):
    sim = ResumableUploadSim(video_id="dQw4w9WgXcQ")
    mount(sim)

    assert await client.upload(video, title="t", description="d", tags=[]) == "dQw4w9WgXcQ"


@respx.mock
async def test_progress_is_monotonic_and_finishes_at_one(video, client):
    """The Create screen's upload bar is this callback and nothing else.

    It used to stop at the second-to-last chunk — the final PUT answers 200 and
    returns before reporting — so a four-chunk upload left the row at 90% and then
    jumped to done. Harmless on a 3KB file; on a 40-minute upload it reads as a
    stall.
    """
    sim = ResumableUploadSim()
    mount(sim)
    seen: list[float] = []

    async def on_progress(fraction: float, message: str) -> None:
        seen.append(fraction)

    await client.upload(video, title="t", description="d", tags=[], on_progress=on_progress)

    assert seen == sorted(seen)
    assert all(0.0 <= f <= 1.0 for f in seen)
    assert seen[-1] == 1.0


# ── the server is the authority on how much arrived ─────────────────────────


@respx.mock
async def test_a_308_confirming_fewer_bytes_resends_from_the_servers_offset(video, client):
    """The case §1.1 says cannot be reasoned about without a real response.

    The server takes 300 of the first chunk's 1024 bytes and says so. Believing our
    own arithmetic instead would resume at 1024, leave bytes 300-1023 missing, and
    produce a video that uploaded "successfully" with a hole in it — which nothing
    downstream checks. The simulator refuses the non-contiguous chunk, so a skip is
    a test failure rather than a corrupt file.
    """
    sim = ResumableUploadSim(short_by={0: 724})
    mount(sim)

    await client.upload(video, title="t", description="d", tags=[])

    assert bytes(sim.stored) == video.read_bytes()
    assert sim.content_ranges[1] == "bytes 300-1323/3400", "resumed from our count, not theirs"


@respx.mock
async def test_a_308_that_confirms_less_than_we_already_had_rewinds(video, client):
    """A `Range` can go backwards, and the header is still the only truth.

    `_resume_offset` documents the server's `Range` as authoritative; the loop then
    only acted on it when it moved *forward*, and re-sent from its own offset
    otherwise. Google answers a chunk that starts past its data with a 400, so a
    server that dropped a buffered chunk turned into a hard failure of an upload
    that was recoverable — with the 1,600 units already spent.
    """
    puts: list[str] = []
    sim = ResumableUploadSim()
    mount(sim)

    rewound = False
    real = sim.put_chunk

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal rewound
        puts.append(request.headers["Content-Range"])
        if len(puts) == 2 and not rewound:
            # The second chunk lands, and then the server forgets half of the first.
            rewound = True
            del sim.stored[512:]
            return httpx.Response(308, headers={"Range": f"bytes=0-{len(sim.stored) - 1}"})
        return real(request)

    respx.route(method="PUT", url__startswith=youtube.UPLOAD).mock(side_effect=flaky)

    await client.upload(video, title="t", description="d", tags=[])

    assert puts[2] == "bytes 512-1535/3400", "did not go back to what the server actually held"
    assert bytes(sim.stored) == video.read_bytes()


@respx.mock
async def test_a_308_with_no_range_resends_the_chunk_rather_than_skipping_it(video, client):
    """No `Range` means nothing was persisted — the one reading that is safe."""
    sim = ResumableUploadSim()
    real = sim.put_chunk
    calls = {"n": 0}

    def stingy(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(308)  # no Range at all
        return real(request)

    respx.route(method="POST", url__startswith=youtube.UPLOAD).mock(side_effect=sim.open_session)
    respx.route(method="PUT", url__startswith=youtube.UPLOAD).mock(side_effect=stingy)

    await client.upload(video, title="t", description="d", tags=[])

    assert bytes(sim.stored) == video.read_bytes()


@respx.mock
async def test_a_server_that_never_confirms_anything_gives_up(video, client):
    respx.route(method="POST", url__startswith=youtube.UPLOAD).mock(
        return_value=httpx.Response(200, headers={"Location": SESSION_URI})
    )
    stuck = respx.route(method="PUT", url__startswith=youtube.UPLOAD).mock(
        return_value=httpx.Response(308, headers={"Range": "bytes=0-99"})
    )

    with pytest.raises(youtube.YouTubeError, match="confirmed no bytes"):
        await client.upload(video, title="t", description="d", tags=[])

    # One that made progress, then MAX_CHUNK_RETRIES + 1 that did not.
    assert stuck.call_count == youtube.MAX_CHUNK_RETRIES + 2


# ── transient failures ──────────────────────────────────────────────────────


@respx.mock
async def test_a_500_and_a_503_mid_upload_retry_and_then_succeed(video, client):
    sim = ResumableUploadSim(faults={1: 500, 2: 503})
    mount(sim)

    video_id = await client.upload(video, title="t", description="d", tags=[])

    assert video_id == "sim-video-1"
    assert bytes(sim.stored) == video.read_bytes()
    # Six PUTs for four chunks: the two rejected ones are re-sent from the same offset.
    assert sim.content_ranges == [
        "bytes 0-1023/3400",
        "bytes 1024-2047/3400",
        "bytes 1024-2047/3400",
        "bytes 1024-2047/3400",
        "bytes 2048-3071/3400",
        "bytes 3072-3399/3400",
    ]


@respx.mock
async def test_a_retried_upload_opens_one_session_and_books_one_charge(video, client):
    """Retrying a chunk is not retrying the upload.

    A second `POST ?uploadType=resumable` would be a second video on the channel
    and a second 1,600 units, and the ledger is what stands between six uploads a
    day and Google refusing the seventh.
    """
    sim = ResumableUploadSim(faults={0: 503, 1: 500, 3: 429})
    mount(sim)

    await client.upload(video, title="Why bridges collapse", description="d", tags=[])

    assert len(sim.sessions) == 1
    assert youtube.ledger.spent() == 1600
    assert [e.operation for e in youtube.ledger.entries] == ["videos.insert"]
    assert youtube.ledger.entries[0].channel_id == "UCsimulated"
    assert youtube.ledger.entries[0].note == "Why bridges collapse"


@respx.mock
async def test_giving_up_on_a_chunk_keeps_the_charge_because_google_kept_it(video, client):
    """Google charges when the session opens, whatever happens to the upload.

    Refunding here would hand back budget that is really gone, and the next upload
    would sail past a ceiling Google is already enforcing.
    """
    respx.route(method="POST", url__startswith=youtube.UPLOAD).mock(
        return_value=httpx.Response(200, headers={"Location": SESSION_URI})
    )
    respx.route(method="PUT", url__startswith=youtube.UPLOAD).mock(return_value=httpx.Response(503))

    with pytest.raises(youtube.YouTubeError, match="gave up after"):
        await client.upload(video, title="t", description="d", tags=[])

    assert youtube.ledger.spent() == 1600


@respx.mock
async def test_a_session_google_refuses_to_open_is_refunded(video, client):
    """The one window where a refund is safe: there is no session to be charged for."""
    mount(ResumableUploadSim(session_status=403, session_headers={}))

    with pytest.raises(youtube.YouTubeError, match="could not open upload session"):
        await client.upload(video, title="t", description="d", tags=[])

    assert youtube.ledger.spent() == 0


@respx.mock
async def test_a_400_on_a_chunk_stops_immediately(video, client):
    """4xx is deterministic. Retrying it spends time and proves nothing — and a 400
    from the chunk endpoint means the ranges have already gone wrong."""
    respx.route(method="POST", url__startswith=youtube.UPLOAD).mock(
        return_value=httpx.Response(200, headers={"Location": SESSION_URI})
    )
    refused = respx.route(method="PUT", url__startswith=youtube.UPLOAD).mock(
        return_value=httpx.Response(400, json={"error": {"message": "Bad Request"}})
    )

    with pytest.raises(youtube.YouTubeError, match=r"upload failed \(400\)"):
        await client.upload(video, title="t", description="d", tags=[])

    assert refused.call_count == 1


# ── the other publish-time calls ────────────────────────────────────────────

THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CAPTIONS_URL = "https://www.googleapis.com/upload/youtube/v3/captions"


@respx.mock
async def test_setting_a_thumbnail_posts_the_bytes_as_a_media_upload(tmp_path, client):
    """`thumbnails.set` is an upload endpoint, so it needs `uploadType`.

    Every `/upload/` URI in the Data API requires it — `videos.insert` passes
    `resumable` — and Google rejects the request without one. This call omitted it
    entirely, so the 50 units were spent on a 400 and the published video kept the
    auto-generated frame.
    """
    image = tmp_path / "thumb.jpg"
    image.write_bytes(b"\xff\xd8\xff" + b"j" * 4096)
    route = respx.post(THUMBNAIL_URL).mock(
        return_value=httpx.Response(200, json={"items": [{"default": {"url": "https://i/1.jpg"}}]})
    )

    await client.set_thumbnail("dQw4w9WgXcQ", image)

    request = route.calls[0].request
    assert dict(request.url.params) == {"videoId": "dQw4w9WgXcQ", "uploadType": "media"}
    assert request.headers["content-type"] == "image/jpeg"
    assert request.content == image.read_bytes()
    assert youtube.ledger.spent() == 50


async def test_an_oversized_thumbnail_is_refused_before_the_request(tmp_path, client):
    """2MB is YouTube's limit and the failure is a 400 after the units are gone."""
    image = tmp_path / "huge.jpg"
    image.write_bytes(b"j" * (2 * 1024 * 1024 + 1))

    with pytest.raises(ValueError, match="2MB"):
        await client.set_thumbnail("dQw4w9WgXcQ", image)

    assert youtube.ledger.spent() == 0


@respx.mock
async def test_captions_go_up_as_a_multipart_related_upload(tmp_path, client):
    """`multipart/related`, not `multipart/form-data`.

    Google's media-upload protocol accepts exactly one multipart flavour and says
    so in the refusal: "Media type 'multipart/form-data' is not supported. Valid
    media types: [multipart/related]". `httpx`'s `files=` produces form-data, so
    every caption upload this repository could have made would have cost 400 units
    and uploaded nothing.
    """
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,500\nThe bridge collapsed.\n", encoding="utf-8")
    route = respx.post(CAPTIONS_URL).mock(
        return_value=httpx.Response(200, json={"kind": "youtube#caption", "id": "cap-1"})
    )

    await client.upload_captions("dQw4w9WgXcQ", srt, language="en", name="English")

    request = route.calls[0].request
    assert dict(request.url.params) == {"part": "snippet", "uploadType": "multipart"}
    assert request.headers["content-type"].startswith("multipart/related; boundary=")

    body = request.content.decode("utf-8", errors="replace")
    assert "Content-Type: application/json" in body
    metadata = json.loads(body[body.index("{") : body.index("}}") + 2])
    assert metadata["snippet"] == {
        "videoId": "dQw4w9WgXcQ",
        "language": "en",
        "name": "English",
        "isDraft": False,
    }
    assert "The bridge collapsed." in body
    assert youtube.ledger.spent() == 400


@respx.mock
async def test_adding_to_a_playlist_sends_the_resource_id_google_expects(client):
    route = respx.post(f"{youtube.API}/playlistItems").mock(
        return_value=httpx.Response(200, json={"id": "PLI-1"})
    )

    await client.add_to_playlist("dQw4w9WgXcQ", "PL123")

    request = route.calls[0].request
    assert dict(request.url.params) == {"part": "snippet"}
    assert json.loads(request.content) == {
        "snippet": {
            "playlistId": "PL123",
            "resourceId": {"kind": "youtube#video", "videoId": "dQw4w9WgXcQ"},
        }
    }
    assert youtube.ledger.spent() == 50


@respx.mock
async def test_rescheduling_puts_a_private_status_with_a_utc_publish_at(client):
    """`videos.update` is a full replace of the parts named, and `publishAt` is
    ignored on anything but a private video — so both fields go every time."""
    route = respx.put(f"{youtube.API}/videos").mock(
        return_value=httpx.Response(200, json={"id": "dQw4w9WgXcQ"})
    )

    await client.reschedule(
        "dQw4w9WgXcQ", datetime(2026, 9, 1, 9, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    )

    request = route.calls[0].request
    assert dict(request.url.params) == {"part": "status"}
    assert json.loads(request.content) == {
        "id": "dQw4w9WgXcQ",
        "status": {"privacyStatus": "private", "publishAt": "2026-09-01T16:30:00+00:00"},
    }
    assert youtube.ledger.spent() == 50


@respx.mock
async def test_processing_status_reads_the_processing_details_part(client):
    route = respx.get(f"{youtube.API}/videos").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"processingDetails": {"processingStatus": "processing"}}]},
        )
    )

    assert await client.processing_status("dQw4w9WgXcQ") == "processing"
    assert dict(route.calls[0].request.url.params) == {
        "part": "processingDetails",
        "id": "dQw4w9WgXcQ",
    }


@respx.mock
async def test_a_video_google_has_never_heard_of_is_unknown_not_a_crash(client):
    """A deleted or still-propagating id comes back as `items: []`, not a 404."""
    respx.get(f"{youtube.API}/videos").mock(return_value=httpx.Response(200, json={"items": []}))

    assert await client.processing_status("gone") == "unknown"


@respx.mock
async def test_the_duration_comes_back_as_seconds_from_the_iso_8601_string(client):
    respx.get(f"{youtube.API}/videos").mock(
        return_value=httpx.Response(
            200, json={"items": [{"contentDetails": {"duration": "PT12M34S"}}]}
        )
    )

    assert await client.duration_seconds("dQw4w9WgXcQ") == 754.0


@respx.mock
async def test_trending_asks_the_mostpopular_chart_and_returns_titles(client):
    route = respx.get(f"{youtube.API}/videos").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"snippet": {"title": "Why the Baltimore bridge collapsed"}},
                    {"snippet": {"title": "Salt roads of the Sahara"}},
                ]
            },
        )
    )

    got = await client.trending(region_code="GB", category_id="27", limit=5)

    assert got == ["Why the Baltimore bridge collapsed", "Salt roads of the Sahara"]
    assert dict(route.calls[0].request.url.params) == {
        "part": "snippet",
        "chart": "mostPopular",
        "regionCode": "GB",
        "maxResults": "5",
        "videoCategoryId": "27",
    }


@respx.mock
async def test_search_asks_for_videos_only_and_returns_the_items(client):
    route = respx.get(f"{youtube.API}/search").mock(
        return_value=httpx.Response(200, json={"items": [{"id": {"videoId": "abc"}}]})
    )

    items = await client.search("bridge collapse", limit=5)

    assert items == [{"id": {"videoId": "abc"}}]
    assert dict(route.calls[0].request.url.params) == {
        "part": "snippet",
        "q": "bridge collapse",
        "type": "video",
        "maxResults": "5",
        "order": "relevance",
    }
    assert youtube.ledger.spent() == 100, "search.list is 100 units — the expensive read"


@respx.mock
async def test_a_hidden_subscriber_count_is_none_rather_than_an_error(client):
    """`hiddenSubscriberCount` is a setting an operator can turn on. A dashboard
    that 500s on a privacy preference is worse than one that says "unavailable"."""
    respx.get(f"{youtube.API}/channels").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"statistics": {"hiddenSubscriberCount": True, "subscriberCount": "0"}}]
            },
        )
    )

    assert await client.subscriber_count() is None


@respx.mock
async def test_a_google_error_carries_googles_own_words(client):
    """The message is what reaches the Queue screen; a bare status code sends
    people to check the wrong half of a Cloud console."""
    respx.get(f"{youtube.API}/channels").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "quotaExceeded"}], "message": "Quota exceeded."}},
        )
    )

    with pytest.raises(youtube.YouTubeError, match="quotaExceeded"):
        await client.subscriber_count()


# ── the composed publish half of PUBLISH_WORKFLOW ───────────────────────────


def _seeded_publish_states(chapters: list | None = None):
    """A finished video job's states, so only the four publish stages execute.

    This is what `POST /v1/jobs/{id}/publish` does: it seeds the publish workflow
    with the source job's outputs and lets `Workflow.run` replay them. Everything
    here stands in for a stage that has already cost real money.
    """
    from engine.workflows import video as video_wf
    from engine.workflows.base import Provenance, StageOutput, StageStatus
    from engine.workflows.seo import TitleVariant

    values: dict[str, object] = {
        "titles": [TitleVariant(text="Why bridges collapse", strategy="curiosity")],
        "description": "The Baltimore bridge went down in nine seconds.",
        "tags": ["bridges", "engineering"],
        "render": "renders/job-sim.mp4",
        "thumbnail": [{"key": "thumbs/job-sim-0.jpg"}],
        "subtitles": [
            {"start": 0.0, "end": 2.5, "text": "The bridge collapsed."},
            {"start": 2.5, "end": 5.25, "text": "Here is why."},
        ],
        "chapters": chapters if chapters is not None else [],
    }

    states = video_wf.PUBLISH_WORKFLOW.initial_states()
    for name, state in states.items():
        if name in ("upload", "thumbnail_set", "captions", "playlist"):
            continue
        state.status = StageStatus.DONE
        state.output = StageOutput(value=values.get(name, f"{name}-value"), provenance=Provenance())
    return states


@pytest.fixture
def publish_env(monkeypatch, tmp_path, video):
    """`PUBLISH_WORKFLOW` with a real store rooted in tmp_path and no real backoff."""
    from engine.settings import get_settings
    from engine.storage import ObjectStore
    from engine.workflows import publish as publish_mod

    root = tmp_path / "storage"
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(root))
    get_settings.cache_clear()
    store = ObjectStore()
    monkeypatch.setattr(publish_mod, "store", store)

    render = root / "renders" / "job-sim.mp4"
    render.parent.mkdir(parents=True, exist_ok=True)
    render.write_bytes(video.read_bytes())
    thumb = root / "thumbs" / "job-sim-0.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"\xff\xd8\xff" + b"j" * 1024)

    monkeypatch.setattr(youtube, "CHUNK", 1024)
    return root


def _mount_publish_routes(sim: ResumableUploadSim) -> dict[str, respx.Route]:
    mount(sim)
    return {
        "thumbnail": respx.post(THUMBNAIL_URL).mock(return_value=httpx.Response(200, json={})),
        "captions": respx.post(CAPTIONS_URL).mock(
            return_value=httpx.Response(200, json={"id": "cap-1"})
        ),
        "playlist": respx.post(f"{youtube.API}/playlistItems").mock(
            return_value=httpx.Response(200, json={"id": "PLI-1"})
        ),
    }


@respx.mock
async def test_the_uploaded_video_id_reaches_every_stage_downstream_of_it(publish_env):
    """The handoff the four stages exist to make: one id, three consumers.

    Each of thumbnail_set, captions and playlist reads `ctx.get("upload")`. A stage
    that reached for the job id, or for a value it computed itself, would set the
    thumbnail on somebody else's video — and there is no undo for that either.
    """
    from engine.workflows import video as video_wf

    sim = ResumableUploadSim(video_id="dQw4w9WgXcQ")
    routes = _mount_publish_routes(sim)
    events = Events()

    states = await video_wf.PUBLISH_WORKFLOW.run(
        "job-sim",
        {
            "youtube_client": youtube.YouTube(credentials()),
            "playlist_id": "PL123",
            "privacy": "private",
        },
        events,
        states=_seeded_publish_states(),
    )

    assert states["upload"].output.value == "dQw4w9WgXcQ"
    assert dict(routes["thumbnail"].calls[0].request.url.params)["videoId"] == "dQw4w9WgXcQ"
    captions_body = routes["captions"].calls[0].request.content.decode("utf-8", errors="replace")
    assert '"videoId": "dQw4w9WgXcQ"' in captions_body
    assert json.loads(routes["playlist"].calls[0].request.content)["snippet"]["resourceId"] == {
        "kind": "youtube#video",
        "videoId": "dQw4w9WgXcQ",
    }
    assert events.types[-1] == "workflow.completed"


@respx.mock
async def test_the_captions_the_workflow_sends_are_the_rendered_cues(publish_env):
    from engine.workflows import video as video_wf

    routes = _mount_publish_routes(ResumableUploadSim())

    await video_wf.PUBLISH_WORKFLOW.run(
        "job-sim",
        {"youtube_client": youtube.YouTube(credentials())},
        _noop_emit,
        states=_seeded_publish_states(),
    )

    body = routes["captions"].calls[0].request.content.decode("utf-8", errors="replace")
    assert "00:00:00,000 --> 00:00:02,500" in body
    assert "The bridge collapsed." in body


@respx.mock
async def test_failed_captions_leave_the_video_up_and_the_job_recoverable(publish_env, monkeypatch):
    """Captions are 400 units against an upload's 1,600, and the video is already
    live and correct without them. A failure here must not fail the job — the
    Queue screen's retry is per-stage precisely so this one is cheap to redo."""
    from engine.workflows import video as video_wf
    from engine.workflows.base import StageStatus

    sim = ResumableUploadSim(video_id="dQw4w9WgXcQ")
    mount(sim)
    respx.post(THUMBNAIL_URL).mock(return_value=httpx.Response(200, json={}))
    respx.post(CAPTIONS_URL).mock(
        return_value=httpx.Response(403, json={"error": {"message": "captions are disabled"}})
    )

    captions_stage = next(s for s in video_wf.PUBLISH_WORKFLOW.stages if s.name == "captions")
    monkeypatch.setattr(captions_stage, "max_attempts", 1)  # no need to wait out the backoff
    events = Events()

    states = await video_wf.PUBLISH_WORKFLOW.run(
        "job-sim",
        {"youtube_client": youtube.YouTube(credentials())},
        events,
        states=_seeded_publish_states(),
    )

    assert states["upload"].status is StageStatus.DONE
    assert states["upload"].output.value == "dQw4w9WgXcQ"
    assert states["captions"].status is StageStatus.SKIPPED
    assert states["thumbnail_set"].status is StageStatus.DONE
    assert events.types[-1] == "workflow.completed"
    # And the expensive half is not re-run on the retry.
    assert len(sim.sessions) == 1


@respx.mock
async def test_the_playlist_stage_is_skipped_without_an_id_and_the_job_still_completes(
    publish_env,
):
    from engine.workflows import video as video_wf
    from engine.workflows.base import StageStatus

    routes = _mount_publish_routes(ResumableUploadSim())

    states = await video_wf.PUBLISH_WORKFLOW.run(
        "job-sim",
        {"youtube_client": youtube.YouTube(credentials())},
        _noop_emit,
        states=_seeded_publish_states(),
    )

    assert states["playlist"].status is StageStatus.SKIPPED
    assert routes["playlist"].call_count == 0


# ── chapters reach the wire ─────────────────────────────────────────────────


@respx.mock
async def test_the_chapter_block_is_in_the_description_google_receives(publish_env):
    """YouTube has no chapters field — the timestamps in the description *are* the
    chapters. `append_chapters` was written, wired, and never once observed at the
    only place that matters, which is the body of `videos.insert`."""
    from engine.workflows import video as video_wf

    sim = ResumableUploadSim()
    _mount_publish_routes(sim)

    states = await video_wf.PUBLISH_WORKFLOW.run(
        "job-sim",
        {"youtube_client": youtube.YouTube(credentials())},
        _noop_emit,
        states=_seeded_publish_states(
            chapters=[("0:00", "The collapse"), ("1:20", "The design"), ("4:05", "What changed")]
        ),
    )

    description = sim.session_body["snippet"]["description"]
    assert description.endswith("Chapters:\n0:00 The collapse\n1:20 The design\n4:05 What changed")
    assert description.startswith("The Baltimore bridge went down in nine seconds.")
    assert states["upload"].output.provenance.params["chapters_appended"] is True


@respx.mock
async def test_a_video_with_no_chapters_sends_the_description_unchanged(publish_env):
    from engine.workflows import video as video_wf

    sim = ResumableUploadSim()
    _mount_publish_routes(sim)

    states = await video_wf.PUBLISH_WORKFLOW.run(
        "job-sim",
        {"youtube_client": youtube.YouTube(credentials())},
        _noop_emit,
        states=_seeded_publish_states(),
    )

    assert sim.session_body["snippet"]["description"] == (
        "The Baltimore bridge went down in nine seconds."
    )
    assert states["upload"].output.provenance.params["chapters_appended"] is False

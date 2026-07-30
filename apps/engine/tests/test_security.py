"""The things that would matter if this were reachable by anyone but you.

Written after a review found three that did matter: refresh tokens encrypted under a
key published in this repository, an artifact route that `ObjectStore.url()` pointed
at but which did not exist, and an unauthenticated endpoint whose in-memory set only
ever grew.
"""

from __future__ import annotations

import asyncio
import stat
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from engine.settings import PLACEHOLDER_SECRETS, get_settings


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """A storage root of our own, and no cached settings or cipher."""
    from engine import crypto

    get_settings.cache_clear()
    crypto.reset_cache()
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STUDIO_PERSIST", "false")
    monkeypatch.delenv("STUDIO_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()
    crypto.reset_cache()


# ── the encryption key ──────────────────────────────────────────────────────


@pytest.mark.parametrize("placeholder", sorted(PLACEHOLDER_SECRETS))
def test_no_published_placeholder_is_ever_used_as_a_key(sandbox, monkeypatch, placeholder):
    """They are in this repository. Anything encrypted under them is public.

    `.env.example` set STUDIO_SECRET_KEY to one of these and `scripts/setup.sh`
    copies that file to `.env`, so every install encrypted its YouTube refresh
    tokens — permanent access to the channel — under a key readable on GitHub. It
    was 47 characters, so the length check that was the only guard passed happily.
    """
    from engine import crypto

    monkeypatch.setenv("STUDIO_SECRET_KEY", placeholder)
    get_settings.cache_clear()
    assert crypto._resolve_secret() not in PLACEHOLDER_SECRETS


def test_env_example_does_not_ship_a_usable_looking_key():
    """The regression that made the default unreachable in the first place."""
    from pathlib import Path

    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text()
    for line in example.splitlines():
        stripped = line.strip()
        if stripped.startswith("STUDIO_SECRET_KEY="):
            value = stripped.split("=", 1)[1].strip()
            assert not value, f".env.example ships a key: {value!r}"


def test_a_key_is_generated_and_reused(sandbox):
    from engine import crypto

    first = crypto._resolve_secret()
    second = crypto._resolve_secret()
    assert first == second, "a new key each call would orphan every stored token"
    assert (sandbox / crypto.KEY_FILE).read_text().strip() == first
    assert len(first) >= 32


def test_the_generated_key_is_not_readable_by_other_users(sandbox):
    from engine import crypto

    crypto._resolve_secret()
    mode = (sandbox / crypto.KEY_FILE).stat().st_mode
    assert not mode & stat.S_IRGRP and not mode & stat.S_IROTH, oct(mode)


def test_two_installs_do_not_share_a_key(sandbox, tmp_path_factory, monkeypatch):
    from engine import crypto

    first = crypto._resolve_secret()

    other = tmp_path_factory.mktemp("other")
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(other))
    get_settings.cache_clear()
    assert crypto._resolve_secret() != first


def test_an_explicit_key_wins(sandbox, monkeypatch):
    from engine import crypto

    monkeypatch.setenv("STUDIO_SECRET_KEY", "x" * 40)
    get_settings.cache_clear()
    assert crypto._resolve_secret() == "x" * 40
    assert not (sandbox / crypto.KEY_FILE).exists(), "should not generate one it does not need"


def test_a_too_short_explicit_key_is_refused(sandbox, monkeypatch):
    """Failing loudly beats silently protecting a refresh token with 8 characters."""
    from engine import crypto

    monkeypatch.setenv("STUDIO_SECRET_KEY", "tooshort")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="32 characters"):
        crypto._resolve_secret()


def test_a_token_round_trips_and_the_ciphertext_is_not_the_plaintext(sandbox):
    from engine import crypto

    sealed = crypto.encrypt("1//refresh-token")
    assert "refresh-token" not in sealed
    assert crypto.decrypt(sealed) == "1//refresh-token"


def test_a_token_from_another_install_will_not_decrypt(sandbox, tmp_path_factory, monkeypatch):
    from engine import crypto

    sealed = crypto.encrypt("1//refresh-token")

    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path_factory.mktemp("elsewhere")))
    get_settings.cache_clear()
    crypto.reset_cache()
    with pytest.raises(crypto.DecryptionFailed):
        crypto.decrypt(sealed)


# ── the artifact route ──────────────────────────────────────────────────────


@pytest.fixture
def client(sandbox, monkeypatch):
    from engine import main
    from engine.storage import ObjectStore

    monkeypatch.setattr(main, "store", ObjectStore())
    with TestClient(main.app) as running:
        yield running


def _write(root, relative: str, data: bytes = b"x") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_a_thumbnail_is_served(client, sandbox):
    """`ObjectStore.url()` returned /v1/files/... and the route did not exist, so
    every thumbnail and every finished render was a dead link."""
    _write(sandbox, "thumbnails/job-0.jpg", b"\xff\xd8\xff-jpeg")
    response = client.get("/v1/files/thumbnails/job-0.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8\xff-jpeg"


def test_a_render_is_seekable(client, sandbox):
    """Without a range header the browser buffers the whole video before playing."""
    _write(sandbox, "renders/job.mp4", b"fake-mp4")
    response = client.get("/v1/files/renders/job.mp4")
    assert response.status_code == 200
    assert response.headers.get("accept-ranges") == "bytes"


def test_a_missing_file_is_a_404_not_a_500(client):
    assert client.get("/v1/files/thumbnails/nope.jpg").status_code == 404


@pytest.mark.parametrize(
    "attack",
    [
        "../../../etc/passwd",
        "thumbnails/../../../etc/passwd",
        "thumbnails/../.secret_key",
        "/etc/passwd",
        "thumbnails/....//....//etc/passwd",
    ],
)
def test_traversal_is_refused(client, attack):
    assert client.get(f"/v1/files/{attack}").status_code == 404


def test_the_encryption_key_is_not_downloadable(client, sandbox):
    """It lives in the storage root, which is what this route reads from."""
    from engine import crypto

    crypto._resolve_secret()
    for attempt in (crypto.KEY_FILE, f"thumbnails/../{crypto.KEY_FILE}"):
        assert client.get(f"/v1/files/{attempt}").status_code == 404


def test_the_database_is_not_downloadable(client, sandbox):
    _write(sandbox, "studio.db", b"SQLite format 3\x00")
    assert client.get("/v1/files/studio.db").status_code == 404


def test_only_directories_we_publish_are_readable(client, sandbox):
    """Downloaded footage is not ours to redistribute, whatever its extension."""
    _write(sandbox, "materials/pexels-1.mp4")
    _write(sandbox, "tmp/scratch.mp4")
    assert client.get("/v1/files/materials/pexels-1.mp4").status_code == 404
    assert client.get("/v1/files/tmp/scratch.mp4").status_code == 404


@pytest.mark.parametrize(
    ("key", "servable"),
    [
        ("thumbnails/j-0.jpg", True),
        ("renders/j.mp4", True),
        ("captions/j.srt", True),
        ("voiceover/j.mp3", True),
        ("materials/pexels-1.mp4", False),
    ],
)
def test_every_written_prefix_is_either_servable_or_deliberately_not(
    client, sandbox, key, servable
):
    """Guessed prefixes 404 the real output, and a 404 is a dead link nobody debugs.

    The allowlist first read "subtitles/" and "audio/" — neither of which anything
    writes — so captions and voiceovers were unreachable while looking fine.
    """
    _write(sandbox, key, b"payload")
    expected = 200 if servable else 404
    assert client.get(f"/v1/files/{key}").status_code == expected


def test_the_allowlist_matches_what_the_code_writes():
    """Fails when a new artifact prefix is written inline and never decided about.

    Only catches keys built as a literal at the call site — `thumbnails/` and
    `materials/` are assembled into a variable first and do not show up here. So
    this is a backstop for the common shape, not proof of completeness; the
    parametrised test above is what actually pins current behaviour.
    """
    import re
    from pathlib import Path

    from engine.main import _SERVABLE_ROOTS

    source = "\n".join(
        p.read_text() for p in (Path(__file__).resolve().parents[1] / "engine").rglob("*.py")
    )
    written = {f"{m}/" for m in re.findall(r'store\.put_\w+\([^,]+,\s*f?"([a-z_]+)/', source)}

    # `materials/` is written and deliberately not served: third-party stock footage.
    undecided = written - set(_SERVABLE_ROOTS) - {"materials/"}
    assert not undecided, f"new artifact prefix, neither served nor excluded: {sorted(undecided)}"


def test_an_unexpected_extension_is_refused(client, sandbox):
    _write(sandbox, "thumbnails/notes.txt")
    _write(sandbox, "thumbnails/script.py")
    assert client.get("/v1/files/thumbnails/notes.txt").status_code == 404
    assert client.get("/v1/files/thumbnails/script.py").status_code == 404


# ── the OAuth state set ─────────────────────────────────────────────────────


def test_a_state_is_single_use():
    """A replayed callback must not bind a second channel."""
    from engine.api import publishing

    publishing._STATES.clear()
    publishing._remember_state("abc")
    assert publishing._claim_state("abc") is True
    assert publishing._claim_state("abc") is False


def test_an_unknown_state_is_refused():
    from engine.api import publishing

    publishing._STATES.clear()
    assert publishing._claim_state("never-issued") is False


def test_a_stale_state_is_refused(monkeypatch):
    from engine.api import publishing

    publishing._STATES.clear()
    publishing._remember_state("abc")
    # Captured before patching, or the replacement calls itself.
    later = time.monotonic() + publishing._STATE_TTL_S + 1
    monkeypatch.setattr(publishing.time, "monotonic", lambda: later)
    assert publishing._claim_state("abc") is False


def test_the_state_set_cannot_grow_without_bound():
    """`GET /v1/auth/google` needs no credentials; the set only ever grew."""
    from engine.api import publishing

    publishing._STATES.clear()
    for i in range(publishing._MAX_STATES * 5):
        publishing._remember_state(f"state-{i}")
    assert len(publishing._STATES) <= publishing._MAX_STATES


def test_a_pending_auth_still_works_under_pressure():
    """Evicting must not lock out the person standing at the consent screen."""
    from engine.api import publishing

    publishing._STATES.clear()
    for i in range(publishing._MAX_STATES * 2):
        publishing._remember_state(f"old-{i}")
    publishing._remember_state("mine")
    assert publishing._claim_state("mine") is True


# ── and the endpoint actually asks ──────────────────────────────────────────
#
# Everything above tests `_claim_state` directly. `finish_auth` — the callback that
# binds a channel to this install — was executed by nothing, so the check could be
# deleted from it and the whole suite stayed green while the endpoint happily
# accepted an authorisation code from anywhere.


@pytest.fixture
def callback(sandbox, monkeypatch):
    """The callback endpoint, with the token exchange counted rather than made."""
    from engine.api import publishing
    from engine.providers import youtube

    calls: list[str] = []

    async def exchange(code: str):
        calls.append(code)
        return youtube.Credentials(refresh_token_encrypted="enc", access_token="tok")

    monkeypatch.setattr(youtube, "exchange_code", exchange)
    publishing._STATES.clear()
    publishing.CHANNELS.clear()

    from engine import main

    with TestClient(main.app) as running:
        yield running, calls
    publishing._STATES.clear()
    publishing.CHANNELS.clear()


def test_a_forged_state_never_reaches_the_token_exchange(callback):
    """Not merely a 400: the code must not be presented to Google at all."""
    client, exchanges = callback
    response = client.get(
        "/v1/auth/google/callback",
        params={"code": "4/attacker-code", "state": "never-issued"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert exchanges == [], "an unrecognised state still spent the code"


def test_a_replayed_callback_is_refused_the_second_time(callback):
    """A state is single-use, so a resent callback must not bind a second channel."""
    from engine.api import publishing

    client, exchanges = callback
    publishing._remember_state("issued")

    first = client.get(
        "/v1/auth/google/callback",
        params={"code": "4/real-code", "state": "issued"},
        follow_redirects=False,
    )
    assert first.status_code in (302, 307), first.text
    assert publishing.CHANNELS["default"].access_token == "tok"

    second = client.get(
        "/v1/auth/google/callback",
        params={"code": "4/real-code", "state": "issued"},
        follow_redirects=False,
    )
    assert second.status_code == 400
    assert exchanges == ["4/real-code"], "the replay was exchanged a second time"


def test_the_channel_list_carries_no_tokens(callback):
    """Deliberate and permanent, per the comment on the endpoint — so pinned.

    The refresh token is permanent access to someone's channel and the access token
    is an hour of it. Neither has any business in a payload the browser reads.
    """
    from engine.api import publishing

    client, _ = callback
    publishing.CHANNELS["default"] = publishing.youtube.Credentials(
        refresh_token_encrypted="ENCRYPTED-REFRESH", access_token="ya29.SECRET", channel_id="UC123"
    )

    body = client.get("/v1/channels").text
    assert "UC123" in body, "the endpoint should still say which channel is connected"
    assert "refresh_token" not in body
    assert "access_token" not in body
    assert "ENCRYPTED-REFRESH" not in body and "ya29.SECRET" not in body


# ── bounded downloads ───────────────────────────────────────────────────────


async def test_an_oversized_clip_is_refused_by_its_header():
    from engine.services import stock

    too_big = str(stock.MAX_CLIP_BYTES + 1)
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, headers={"content-length": too_big}, content=b"")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="refusing"):
            await stock._fetch_bounded(client, "https://example.com/huge.mp4")


async def test_a_lying_content_length_is_still_caught(monkeypatch):
    """The header is set by the sender, so the limit is enforced on real bytes."""
    from engine.services import stock

    monkeypatch.setattr(stock, "MAX_CLIP_BYTES", 1024)
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, headers={"content-length": "10"}, content=b"x" * 5000)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="exceeded"):
            await stock._fetch_bounded(client, "https://example.com/liar.mp4")


async def test_a_normal_clip_downloads_intact():
    from engine.services import stock

    payload = b"video-bytes" * 100
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, content=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await stock._fetch_bounded(client, "https://example.com/ok.mp4") == payload


async def test_an_oversized_download_does_not_kill_the_render(monkeypatch, sandbox):
    """One bad URL costs that beat its footage, not the whole video."""
    from engine.services import stock

    monkeypatch.setattr(stock, "MAX_CLIP_BYTES", 16)
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, content=b"x" * 4096))
    async with httpx.AsyncClient(transport=transport) as client:
        clip = {"id": "pexels-1", "url": "https://example.com/huge.mp4"}
        await stock._download(client, clip)
    assert "path" not in clip


# ── the quota ceiling across processes ──────────────────────────────────────
#
# The ledger caches the day's entries in memory and hydrates once at startup. That
# was correct while one process did everything. The render worker is a separate
# process and it is the one that uploads, so the API and the worker each accumulate
# spend the other cannot see — and both can approve the same last 1,600 units.


async def test_a_second_process_sees_spend_it_did_not_make(database):
    """The reproduction: two ledgers over one database, as API and worker."""
    from engine.quota import QuotaLedger

    worker = QuotaLedger(limit=10_000)
    api = QuotaLedger(limit=10_000)
    await worker.load()
    await api.load()

    for _ in range(5):
        await worker.record("videos.insert")  # 8,000 units spent by the worker

    # The stale read is what the API believed, and why it would have said yes.
    assert api.spent() == 0
    assert api.can_afford("videos.insert") is True

    await api.check_fresh("videos.insert")  # 8,000 + 1,600 still fits
    assert api.spent() == 8_000

    await worker.record("videos.insert")  # 9,600
    with pytest.raises(Exception, match="only"):
        await api.check_fresh("videos.insert")


async def test_a_refresh_failure_does_not_block_an_upload(database, monkeypatch):
    """A ledger that cannot be re-read is worse than one that is slightly stale."""
    from engine import quota
    from engine.quota import QuotaLedger

    ledger = QuotaLedger(limit=10_000)

    async def boom(*_a, **_kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(QuotaLedger, "load", boom)
    await ledger.check_fresh("videos.insert")  # must not raise
    assert quota.COSTS["videos.insert"] == 1600


async def test_check_fresh_still_refuses_when_the_day_is_spent(database):
    from engine.quota import QuotaExceeded, QuotaLedger

    ledger = QuotaLedger(limit=3_000)
    await ledger.load()
    await ledger.record("videos.insert")
    with pytest.raises(QuotaExceeded):
        await ledger.check_fresh("videos.insert")


# ── every YouTube call is metered ───────────────────────────────────────────
#
# CLAUDE.md #5: every provider call goes through the metering wrapper. For the
# YouTube client that wrapper is `YouTube._call`, and every test that touches a
# publish substitutes a fake client for the real one — so `_call` itself was
# executed by nothing. Both of its two lines could be deleted with the suite still
# fully green: `ledger.check` (refuse before spending) and `ledger.record` (book
# what was spent). Losing either is the silent overrun the ledger exists to
# prevent, so they are exercised here against a mocked transport.


class _OneTransport:
    """`httpx`, with every `AsyncClient` wired to a single handler.

    `_call` builds its own client per request, so there is no seam to pass a
    transport through. Rebinding the module-level `httpx` name inside
    `engine.providers.youtube` is the narrowest substitute available — it is scoped
    to that one module, not to httpx.
    """

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __getattr__(self, name):
        return getattr(httpx, name)  # everything else stays the real module

    def AsyncClient(self, **kwargs):  # noqa: N802 — this mirrors httpx's own name
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(self._seen), **kwargs)

    def _seen(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


@pytest.fixture
def metered(monkeypatch, tmp_path):
    """A YouTube client with a ledger of its own and no network."""
    from engine.providers import youtube
    from engine.quota import QuotaLedger

    def build(*, limit: int = 10_000, handler=None):
        led = QuotaLedger(limit=limit, persist=False)
        transport = _OneTransport(handler or (lambda _r: httpx.Response(200, json={})))
        monkeypatch.setattr(youtube, "ledger", led)
        monkeypatch.setattr(youtube, "httpx", transport)
        creds = youtube.Credentials(
            refresh_token_encrypted="enc",
            access_token="tok",
            # Fresh on purpose: a stale token sends `_headers` into `refresh()`,
            # which is a different code path than the one under test.
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            channel_id="UC123",
        )
        return youtube.YouTube(creds), led, transport

    return build


@pytest.fixture
def thumbnail(tmp_path):
    path = tmp_path / "thumb.jpg"
    path.write_bytes(b"\xff\xd8\xff" + b"x" * 512)
    return path


async def test_a_call_books_its_documented_cost(metered, thumbnail):
    from engine.quota import COSTS

    client, led, transport = metered()
    await client.set_thumbnail("yt-1", thumbnail)

    assert led.spent() == COSTS["thumbnails.set"]
    assert len(transport.requests) == 1
    # The charge is attributed to the channel, or the per-channel breakdown the
    # Calendar reads is empty for every call but the upload.
    assert led.entries[-1].channel_id == "UC123"


async def test_a_call_that_cannot_afford_itself_never_leaves_the_process(metered, thumbnail):
    """Refuse before the request, not after: the units are gone once it lands."""
    from engine.quota import QuotaExceeded

    client, _led, transport = metered(limit=10)  # a thumbnail set costs 50
    with pytest.raises(QuotaExceeded):
        await client.set_thumbnail("yt-1", thumbnail)

    assert transport.requests == [], "the request went out before the ledger was consulted"


async def test_a_failing_call_is_still_billed(metered, thumbnail):
    """Google charges for a request it rejects. Booking only on 200 is how the
    ledger drifts below the real spend and waves through an upload with no room."""
    from engine.providers.youtube import YouTubeError
    from engine.quota import COSTS

    client, led, _transport = metered(handler=lambda _r: httpx.Response(403, text="forbidden"))
    with pytest.raises(YouTubeError):
        await client.set_thumbnail("yt-1", thumbnail)

    assert led.spent() == COSTS["thumbnails.set"]


# ── reserve and refund: the 1,600-unit booking ──────────────────────────────
#
# `reserve()` and `refund()` are how the upload books its units, and both were
# 0% executed — the upload path is stubbed in every test that reaches it. Emptying
# either body left the suite green, which is precisely the pair of mutations that
# reintroduces the double-booking `reserve` was written to close.


async def test_reserve_refuses_without_booking_anything(database):
    from engine.quota import QuotaExceeded, QuotaLedger

    led = QuotaLedger(limit=1_000)  # an upload is 1,600
    with pytest.raises(QuotaExceeded):
        await led.reserve("videos.insert")
    assert led.spent() == 0, "a refused reservation must not leave a charge behind"


async def test_two_simultaneous_reservations_admit_exactly_one(database):
    """The reproduction `reserve` exists for.

    As two separate awaits — check, then record — both callers read the same spend,
    both passed, and both booked: 3,200 units against a 1,600-unit ceiling, found
    only when Google refused the second upload it had already charged for.
    """
    from engine.quota import QuotaExceeded, QuotaLedger

    led = QuotaLedger(limit=1_600)  # room for one upload and not two
    results = await asyncio.gather(
        led.reserve("videos.insert"),
        led.reserve("videos.insert"),
        return_exceptions=True,
    )

    refused = [r for r in results if isinstance(r, QuotaExceeded)]
    assert len(refused) == 1, f"both reservations were admitted: {results}"
    assert led.spent() == 1_600


async def test_a_refund_gives_the_units_back_and_deletes_the_row(database):
    """Only for a session that demonstrably never opened. It has to be durable —
    an in-memory-only refund comes back on the next `load()`."""
    from sqlalchemy import func, select

    from engine.db import session
    from engine.quota import QuotaLedger
    from engine.tables import QuotaEntry

    led = QuotaLedger()
    entry = await led.reserve("videos.insert")
    assert led.spent() == 1_600
    assert entry.row_id is not None

    await led.refund(entry)

    assert led.spent() == 0
    async with session() as s:
        assert (await s.execute(select(func.count()).select_from(QuotaEntry))).scalar() == 0


async def test_a_refund_still_lands_after_the_ledger_was_reloaded(database):
    """`load()` replaces every entry with a fresh object read back from its row, so
    the one the caller is holding is no longer the one in the list.

    A refund that quietly does nothing is the expensive direction: it eats a day's
    budget that Google never charged for.
    """
    from engine.quota import QuotaLedger

    led = QuotaLedger()
    entry = await led.reserve("videos.insert")
    await led.load()
    assert all(e is not entry for e in led.entries), "the premise: load() swapped the object out"

    await led.refund(entry)
    assert led.spent() == 0

    reloaded = QuotaLedger()
    await reloaded.load()
    assert reloaded.spent() == 0, "the row survived the refund"


async def test_a_refund_falls_back_to_the_row_id_when_the_reloaded_entry_differs(database):
    """The other half of the same problem, and the reason `refund` matches on
    `row_id` at all.

    SQLite hands a timestamp back exactly as it was given, so the reloaded `Entry`
    compares equal and `list.remove` finds it. A store with a coarser column does
    not, and then equality misses — which without the fallback is a refund that
    logs a warning and gives nothing back.
    """
    from engine.quota import QuotaLedger

    led = QuotaLedger()
    entry = await led.reserve("videos.insert")
    await led.load()
    led.entries[0].at = led.entries[0].at.replace(microsecond=0)  # a second-resolution column

    await led.refund(entry)
    assert led.spent() == 0


# ── what a connected channel leaves on disk ─────────────────────────────────
#
# The row above is the one place a token could sit at rest. Non-negotiable #4 says
# refresh tokens are encrypted there; it says nothing about access tokens because
# there was never a reason to keep one. `save_channel` kept them anyway — an hour
# of full upload access to someone's channel, in plaintext, in a column, surviving
# every restart, and readable by anything that can open the database file.
#
# Dropping it costs one refresh on the first publish after a restart, which is a
# path that has to work regardless: an access token that outlives the process is
# an expired access token far more often than it is a useful one.


async def test_an_access_token_is_never_written_to_the_database(database):
    """Encrypted refresh token in, no plaintext access token out."""
    from engine import repository
    from engine.db import session
    from engine.providers.youtube import Credentials
    from engine.tables import Channel

    await repository.save_channel(
        "default",
        Credentials(
            refresh_token_encrypted="ENCRYPTED-REFRESH",
            access_token="ya29.SECRET",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            channel_id="UC123",
        ),
    )

    async with session() as s:
        row = await s.get(Channel, "default")

    assert row is not None, "the channel was not saved at all"
    assert row.refresh_token_encrypted == "ENCRYPTED-REFRESH", "the durable half must persist"
    assert row.channel_id == "UC123"
    assert not row.access_token, f"an access token was stored: {row.access_token!r}"
    assert "ya29" not in (row.access_token or "")


async def test_a_restored_channel_must_refresh_before_it_can_publish(database):
    """The consequence, and the thing that makes dropping the token safe.

    `is_fresh` is what the client checks before every call; a restored credential
    has to answer False, or the publish path skips the refresh and sends a header
    with nothing behind it. Asserted on the *behaviour* rather than on the column,
    because that is what the upload actually consults.
    """
    from engine import repository
    from engine.providers.youtube import Credentials

    await repository.save_channel(
        "default",
        Credentials(
            refresh_token_encrypted="ENCRYPTED-REFRESH",
            access_token="ya29.SECRET",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            channel_id="UC123",
        ),
    )

    restored = (await repository.load_channels())["default"]

    assert restored.refresh_token_encrypted == "ENCRYPTED-REFRESH"
    assert restored.access_token == "", "the plaintext token came back out of the row"
    assert restored.is_fresh is False, "a restored credential must be refreshed before use"


# ── which environment variables an API caller may name ──────────────────────
#
# `ModelSpec.api_key_env` is operator-supplied and reaches `named_credential`,
# which reads it out of the process environment. Registering a model with a
# `base_url` the caller also chooses turns "which variable may be named" into
# "which secrets can be sent to an arbitrary host", so this predicate is the whole
# boundary. It had no test until now — which is how the round-3 version of it
# shipped admitting the app's own provider keys.


class TestCredentialEnvNames:
    def test_a_third_party_gateway_key_may_be_named(self):
        """The case the field exists for: Groq, OpenRouter, Together, DeepSeek."""
        from engine.settings import is_credential_env_name

        for name in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY"):
            assert is_credential_env_name(name), name

    def test_this_apps_own_provider_keys_may_not(self):
        """The hole the suffix allowlist left open on its own.

        `ANTHROPIC_API_KEY` ends in `_API_KEY` exactly like a gateway's, so the
        suffix rule admitted it — and `api_key_env: ANTHROPIC_API_KEY` with a
        perfectly public `base_url` then handed the operator's real key to that
        endpoint. A spec that wants the provider's own key leaves the field empty;
        naming one is only ever a way to route it somewhere else.
        """
        from engine.settings import is_credential_env_name

        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "PEXELS_API_KEY"):
            assert not is_credential_env_name(name), f"{name} is reachable by name"

    def test_secrets_that_are_not_shaped_like_a_provider_key_may_not(self):
        from engine.settings import is_credential_env_name

        for name in ("STUDIO_SECRET_KEY", "GOOGLE_CLIENT_SECRET", "AWS_SECRET_ACCESS_KEY", "PATH"):
            assert not is_credential_env_name(name), f"{name} is reachable by name"

    def test_every_declared_setting_is_covered_whatever_its_alias_shape(self):
        """The deny-list must not fail open on an alias spelling it cannot read.

        Walks `Settings` itself rather than a hardcoded list, so a field added later
        is covered without anyone remembering this test exists — and asserts the
        `AliasChoices` case explicitly, because `isinstance(alias, str)` silently
        skips it and a skipped name becomes a nameable one.
        """
        from pydantic import AliasChoices

        from engine.settings import Settings, _alias_spellings, is_credential_env_name

        for field_name, field in Settings.model_fields.items():
            alias = getattr(field, "validation_alias", None)
            for spelling in _alias_spellings(alias):
                assert not is_credential_env_name(spelling), (
                    f"{field_name} is reachable as {spelling}"
                )

        assert _alias_spellings(AliasChoices("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")) == {
            "ANTHROPIC_API_KEY",
            "CLAUDE_API_KEY",
        }
        assert _alias_spellings(None) == set()

"""The things that would matter if this were reachable by anyone but you.

Written after a review found three that did matter: refresh tokens encrypted under a
key published in this repository, an artifact route that `ObjectStore.url()` pointed
at but which did not exist, and an unauthenticated endpoint whose in-memory set only
ever grew.
"""

from __future__ import annotations

import stat
import time

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

"""The look-and-sound endpoint.

Six settings the renderer has always honoured and nothing could reach. The tests
that matter are the ones about *reaching* them: that a save actually takes effect in
this process rather than only on disk, and that a value which would render as
nothing is refused rather than silently ignored.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.api import style
from engine.settings import get_settings


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A live app whose `.env` and storage are throwaway.

    `env_path` is redirected because these tests write credentials-adjacent state,
    and a suite that edits the developer's real `.env` is one that breaks their
    install (the same trap `conftest` already guards for Settings).
    """
    from engine.api import setup
    from engine.main import app

    env = tmp_path / ".env"
    env.write_text("# scratch\n", encoding="utf-8")
    monkeypatch.setattr(setup, "env_path", lambda: env)
    monkeypatch.setattr(style, "env_path", lambda: env)
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path / "storage"))

    # The catalogue is a network call, cached per process. Stubbed so the tests do
    # not depend on Microsoft being reachable, and so the "offline" path below is
    # the only one that exercises the fallback.
    stub = style.Voice(
        id="en-US-AvaNeural",
        name="AvaNeural",
        locale="en-US",
        gender="Female",
        traits=["Friendly"],
    )
    monkeypatch.setattr(style, "_voices_cache", [stub])
    monkeypatch.setattr(style, "_voices_live", True)

    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c, env
    get_settings.cache_clear()


def test_it_reports_what_the_renderer_will_actually_use(client):
    c, _ = client
    body = c.get("/v1/style").json()

    assert body["voice"] == "en-US-AvaNeural"
    assert body["bgm_enabled"] is False
    assert body["ken_burns"] == "alternate"
    # Hard cuts by default — the renderer's comment says fast-cut faceless video
    # does not dissolve, and the screen must not imply otherwise.
    assert body["transition_fade_s"] == 0.0


def test_a_saved_voice_takes_effect_in_this_process(client):
    c, env = client

    body = c.put("/v1/style", json={"voice": "en-US-AndrewNeural"}).json()

    # Both halves, because either alone is the bug. On disk only, and the change
    # does nothing until a restart; in the environment only, and it is lost on one.
    assert body["voice"] == "en-US-AndrewNeural"
    assert get_settings().tts_voice == "en-US-AndrewNeural"
    assert "STUDIO_TTS_VOICE=en-US-AndrewNeural" in env.read_text(encoding="utf-8")


def test_an_exported_variable_does_not_outrank_the_save(client, monkeypatch):
    """`os.environ` beats the dotenv in pydantic-settings, so writing the file
    alone reports success and changes nothing — the exact failure `save_keys`
    documents."""
    c, _ = client
    monkeypatch.setenv("STUDIO_TTS_VOICE", "en-GB-RyanNeural")
    get_settings.cache_clear()
    assert get_settings().tts_voice == "en-GB-RyanNeural"

    c.put("/v1/style", json={"voice": "en-US-EmmaNeural"})

    assert get_settings().tts_voice == "en-US-EmmaNeural"


def test_a_nonsense_voice_is_refused_rather_than_written(client):
    c, env = client

    response = c.put("/v1/style", json={"voice": "not a voice"})

    assert response.status_code == 422
    assert "STUDIO_TTS_VOICE" not in env.read_text(encoding="utf-8")


def test_a_newline_cannot_smuggle_a_second_assignment(client):
    """A value reaches a dotenv, where a line break ends the assignment and starts
    another one. The id pattern is what closes that, so it is worth its own test."""
    c, env = client

    response = c.put("/v1/style", json={"voice": "en-US-AvaNeural\nSTUDIO_PERSIST=false"})

    assert response.status_code == 422
    assert "STUDIO_PERSIST" not in env.read_text(encoding="utf-8")


def test_a_font_that_is_not_installed_is_refused(client):
    """Accepting it would render in the fallback face and read as a setting that
    was ignored."""
    c, _ = client

    response = c.put("/v1/style", json={"subtitle_font": "Helvetica.ttf"})

    assert response.status_code == 422
    assert "fonts directory" in response.json()["detail"]


def test_a_track_that_is_not_there_is_refused(client):
    c, _ = client

    response = c.put("/v1/style", json={"bgm_track": "bangers.mp3"})

    assert response.status_code == 422


def test_a_track_that_is_there_is_accepted_and_listed(client, tmp_path):
    c, _ = client
    music = tmp_path / "storage" / "bgm"
    music.mkdir(parents=True, exist_ok=True)
    (music / "quiet-bed.mp3").write_bytes(b"not really an mp3")

    body = c.put("/v1/style", json={"bgm_enabled": True, "bgm_track": "quiet-bed.mp3"}).json()

    assert body["bgm_track"] == "quiet-bed.mp3"
    assert body["options"]["tracks"] == ["quiet-bed.mp3"]
    assert get_settings().bgm_enabled is True


def test_the_volume_bounds_come_from_settings(client):
    """`bgm_volume` is `gt=0, le=1` on the model. Zero is not "silent", it is a
    value the renderer treats as "do not mix", and the screen has a switch for that."""
    c, _ = client

    assert c.put("/v1/style", json={"bgm_volume": 0}).status_code == 422
    assert c.put("/v1/style", json={"bgm_volume": 1.5}).status_code == 422
    assert c.put("/v1/style", json={"bgm_volume": 0.2}).status_code == 200


def test_an_empty_body_changes_nothing(client):
    c, env = client
    before = env.read_text(encoding="utf-8")

    response = c.put("/v1/style", json={})

    assert response.status_code == 200
    assert env.read_text(encoding="utf-8") == before


def test_the_screen_still_opens_when_the_catalogue_is_unreachable(client, monkeypatch):
    """A settings screen that will not load because Microsoft is down would be a
    worse trade than a short list."""
    c, _ = client
    monkeypatch.setattr(style, "_voices_cache", None)
    monkeypatch.setattr(style, "_voices_live", False)

    import sys

    async def _fail():
        raise OSError("no route to host")

    unreachable = type("m", (), {"list_voices": staticmethod(_fail)})
    monkeypatch.setitem(sys.modules, "edge_tts", unreachable)

    body = c.get("/v1/style").json()

    assert body["options"]["voices_live"] is False
    assert [v["id"] for v in body["options"]["voices"]] == ["en-US-AvaNeural"]

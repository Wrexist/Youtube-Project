"""The Setup screen's API.

Two things are being protected here.

**`.env` is the operator's file and holds every credential on the machine.** The
write path must not lose their comments, must not reorder what it did not touch,
and must not be capable of leaving a truncated file behind. Those are the bulk of
these tests, because the failure mode is silent and expensive: nobody notices a
dropped comment, and nobody can recover a truncated credentials file.

**Values go in and never come out.** `GET /v1/setup` is the one endpoint that
knows what every key is and it must report only that they exist.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.api import setup as setup_api
from engine.main import app

client = TestClient(app)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """A dotenv the endpoint will write to, isolated from the real one."""
    path = tmp_path / ".env"
    monkeypatch.setattr(setup_api, "env_path", lambda: path)
    return path


# ── the write path ──────────────────────────────────────────────────────────


def test_an_existing_value_is_replaced_in_place(env_file):
    env_file.write_text("PEXELS_API_KEY=old\nOTHER=untouched\n", encoding="utf-8")
    setup_api.write_env(env_file, {"PEXELS_API_KEY": "new"})
    assert env_file.read_text(encoding="utf-8") == "PEXELS_API_KEY=new\nOTHER=untouched\n"


def test_a_new_value_is_appended(env_file):
    env_file.write_text("EXISTING=1\n", encoding="utf-8")
    setup_api.write_env(env_file, {"PEXELS_API_KEY": "abc"})
    text = env_file.read_text(encoding="utf-8")
    assert "EXISTING=1" in text
    assert "PEXELS_API_KEY=abc" in text


def test_comments_and_ordering_survive(env_file):
    """The whole reason this merges rather than regenerating from a template."""
    original = (
        "# My notes about this install\n"
        "\n"
        "# The LLM. Billing alert set at $20.\n"
        "ANTHROPIC_API_KEY=sk-old\n"
        "\n"
        "# Footage\n"
        "PEXELS_API_KEY=px-old\n"
    )
    env_file.write_text(original, encoding="utf-8")
    setup_api.write_env(env_file, {"ANTHROPIC_API_KEY": "sk-new"})

    text = env_file.read_text(encoding="utf-8")
    assert "# My notes about this install" in text
    assert "# The LLM. Billing alert set at $20." in text
    assert "# Footage" in text
    assert "ANTHROPIC_API_KEY=sk-new" in text
    assert "PEXELS_API_KEY=px-old" in text
    # Order preserved: the LLM line still comes before the footage line.
    assert text.index("ANTHROPIC_API_KEY") < text.index("PEXELS_API_KEY")


def test_a_commented_out_assignment_stays_commented(env_file):
    """Someone who commented a key out disabled it deliberately.

    Reactivating it under a new value would be a change they did not ask for, and
    on a shared machine it could re-enable spending they had switched off.
    """
    env_file.write_text("# OPENAI_API_KEY=disabled-on-purpose\n", encoding="utf-8")
    setup_api.write_env(env_file, {"OPENAI_API_KEY": "new"})

    text = env_file.read_text(encoding="utf-8")
    assert "# OPENAI_API_KEY=disabled-on-purpose" in text
    # The new value is appended as its own live line rather than rewriting theirs.
    assert "\nOPENAI_API_KEY=new" in text


def test_clearing_removes_the_line_rather_than_writing_an_empty_one(env_file):
    """`KEY=` is exported as an empty string, which is not the same as unset.

    An empty assignment shadows anything the surrounding environment would have
    supplied, so writing one turns "I removed this" into "I overrode this with
    nothing" — which presents as a key that cannot be set by any other means.
    """
    env_file.write_text("OPENAI_API_KEY=abc\nPEXELS_API_KEY=xyz\n", encoding="utf-8")
    setup_api.write_env(env_file, {"OPENAI_API_KEY": ""})

    text = env_file.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in text
    assert "PEXELS_API_KEY=xyz" in text


def test_writing_to_a_file_that_does_not_exist_yet(env_file):
    """The first run. There is no `.env` until something creates one."""
    assert not env_file.exists()
    setup_api.write_env(env_file, {"PEXELS_API_KEY": "abc"})
    assert "PEXELS_API_KEY=abc" in env_file.read_text(encoding="utf-8")


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "Windows synthesises st_mode - every writable file reports 0o666 whoever "
        "can actually open it - and os.chmod there only toggles the read-only "
        "attribute. The write path's chmod(0o600) is therefore neither honoured "
        "nor measurable, and asserting on the result tests Python's emulation "
        "rather than the file. What guards .env on Windows is the NTFS ACL "
        "inherited from its directory. See KNOWN-ISSUES.md 5.9."
    ),
)
def test_the_file_is_not_world_readable(env_file):
    import stat

    setup_api.write_env(env_file, {"ANTHROPIC_API_KEY": "sk-secret"})
    mode = stat.S_IMODE(env_file.stat().st_mode)
    assert mode == 0o600, f"credentials file is {oct(mode)}"


def test_a_failed_write_leaves_the_original_intact(env_file, monkeypatch):
    """The reason this goes through a temp file and a rename.

    A truncated `.env` is every credential on the machine, gone, with nothing to
    restore from — worse than a save that fails loudly.
    """
    env_file.write_text("ANTHROPIC_API_KEY=sk-original\n", encoding="utf-8")

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.replace", boom)
    with pytest.raises(OSError):
        setup_api.write_env(env_file, {"ANTHROPIC_API_KEY": "sk-new"})

    assert env_file.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=sk-original\n"


def test_a_failed_write_leaves_no_temp_file_behind(env_file, monkeypatch):
    """Otherwise every failure drops another copy of the keys next to the real one."""
    env_file.write_text("ANTHROPIC_API_KEY=sk-original\n", encoding="utf-8")
    monkeypatch.setattr(
        "pathlib.Path.replace", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("nope"))
    )
    with pytest.raises(OSError):
        setup_api.write_env(env_file, {"ANTHROPIC_API_KEY": "sk-new"})

    assert list(env_file.parent.glob(".env.*")) == []


def test_untouched_names_are_left_alone(env_file):
    """Absent from the request means unchanged — the form only sends what was typed."""
    env_file.write_text(
        "ANTHROPIC_API_KEY=sk-a\nPEXELS_API_KEY=px-b\nOPENAI_API_KEY=oa-c\n", encoding="utf-8"
    )
    setup_api.write_env(env_file, {"PEXELS_API_KEY": "px-new"})

    text = env_file.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-a" in text
    assert "OPENAI_API_KEY=oa-c" in text
    assert "PEXELS_API_KEY=px-new" in text


# ── the endpoints ───────────────────────────────────────────────────────────


def test_the_status_never_returns_a_key(monkeypatch):
    """The load-bearing assertion of the whole module."""
    from engine.settings import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-supersecretvalue")
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-alsosecret123")
    get_settings.cache_clear()
    try:
        body = client.get("/v1/setup").json()
        raw = str(body)
        assert "sk-ant-supersecretvalue" not in raw
        assert "pexels-alsosecret123" not in raw

        by_env = {c["env"]: c for c in body["credentials"]}
        assert by_env["ANTHROPIC_API_KEY"]["configured"] is True
        # Only the tail, which identifies a key without being one.
        assert by_env["ANTHROPIC_API_KEY"]["tail"] == "alue"
    finally:
        get_settings.cache_clear()


def test_a_short_value_gets_no_tail_at_all(monkeypatch):
    """Four of six characters is most of the key. Below the threshold, show nothing."""
    from engine.settings import get_settings

    monkeypatch.setenv("PEXELS_API_KEY", "abc123")
    get_settings.cache_clear()
    try:
        body = client.get("/v1/setup").json()
        entry = next(c for c in body["credentials"] if c["env"] == "PEXELS_API_KEY")
        assert entry["configured"] is True
        assert entry["tail"] == ""
    finally:
        get_settings.cache_clear()


def test_every_credential_says_what_it_unlocks_and_where_to_get_it():
    """A settings screen listing variable names is one you need the docs beside."""
    body = client.get("/v1/setup").json()
    assert body["credentials"], "the screen would be empty"
    for c in body["credentials"]:
        assert c["unlocks"].strip(), f"{c['env']} does not say what it is for"
        assert c["without_it"].strip(), f"{c['env']} does not say what breaks"
        assert c["url"].startswith("https://"), f"{c['env']} has nowhere to get one"
        assert c["effort"].strip(), f"{c['env']} does not say how long it takes"


def test_an_unknown_variable_is_refused(env_file):
    """The allowlist. Without it this writes arbitrary variables into the process
    environment of the thing holding every credential on the machine."""
    response = client.put("/v1/setup/keys", json={"values": {"PATH": "/tmp/evil"}})
    assert response.status_code == 400
    assert "PATH" in response.json()["detail"]
    assert not env_file.exists(), "a refused request still wrote to disk"


def test_a_value_containing_a_newline_is_refused(env_file):
    """A newline ends the assignment and starts a new one — the injection the
    name allowlist above does not cover."""
    response = client.put(
        "/v1/setup/keys",
        json={"values": {"PEXELS_API_KEY": "abc\nGOOGLE_CLIENT_SECRET=stolen"}},
    )
    assert response.status_code == 400
    assert not env_file.exists()


def test_saving_writes_the_file_and_takes_effect_immediately(env_file, monkeypatch):
    """A save that needs a restart to matter is a save that reported success and
    did nothing. `os.environ` wins over the dotenv, so both have to be updated."""
    import os

    from engine.settings import get_settings

    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        response = client.put(
            "/v1/setup/keys", json={"values": {"PEXELS_API_KEY": "px-live-value"}}
        )
        assert response.status_code == 200
        assert "PEXELS_API_KEY=px-live-value" in env_file.read_text(encoding="utf-8")
        assert get_settings().pexels_api_key == "px-live-value"

        # And the response reflects reality rather than acknowledging the request.
        entry = next(c for c in response.json()["credentials"] if c["env"] == "PEXELS_API_KEY")
        assert entry["configured"] is True
    finally:
        os.environ.pop("PEXELS_API_KEY", None)
        get_settings.cache_clear()


def test_an_empty_request_is_a_no_op_not_a_wipe(env_file):
    """Saving a form nobody typed into must not clear every key in it."""
    env_file.write_text("ANTHROPIC_API_KEY=sk-keep\n", encoding="utf-8")
    response = client.put("/v1/setup/keys", json={"values": {}})
    assert response.status_code == 200
    assert env_file.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=sk-keep\n"


def test_readiness_needs_an_llm_and_footage_not_a_named_key(monkeypatch):
    """`can_render` is what the screen's headline asserts, so it has to be true.

    An LLM key can come from any of three providers; requiring the Anthropic one
    specifically would tell someone running on OpenAI that they are not set up
    when they are.
    """
    from engine.settings import get_settings

    for name in ("ANTHROPIC_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "pb-key")
    get_settings.cache_clear()
    try:
        assert client.get("/v1/setup").json()["can_render"] is True
    finally:
        get_settings.cache_clear()


def test_the_worker_probe_does_not_block_the_event_loop():
    """It uses a *synchronous* Redis client, and nothing answering is the norm.

    Most installs run no worker at all, so the probe times out on every load of
    the Setup screen. Called inline from the async handler, that stalls this
    process for the whole timeout — including the SSE streams carrying render
    progress to anyone watching a job.
    """
    import inspect

    assert inspect.iscoroutinefunction(setup_api._worker_running)
    assert "to_thread" in inspect.getsource(setup_api._worker_running)
    # And the handler must await the async one, not call the sync one directly.
    handler = inspect.getsource(setup_api.status)
    assert "await _worker_running()" in handler
    assert "_worker_running_sync(" not in handler


def test_not_ready_when_there_is_no_footage_source(monkeypatch):
    from engine.settings import get_settings

    for name in ("PEXELS_API_KEY", "PIXABAY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")
    get_settings.cache_clear()
    try:
        body = client.get("/v1/setup").json()
        assert body["can_render"] is False
        assert "PEXELS_API_KEY" in body["missing_required"]
    finally:
        get_settings.cache_clear()


# ── where `.env` is ─────────────────────────────────────────────────────────
#
# `env_path()` used to end in `Path(__file__).resolve().parents[4]`, which is a
# fixed depth: it assumes this module sits five levels below the repository root.
# True for a checkout (`<root>/apps/engine/engine/api/setup.py`), false in the
# Docker image, where the engine is copied to `/app` and there are four parents in
# total — so the index raised IndexError and took `GET /v1/setup` and
# `PUT /v1/setup/keys` down with a 500 on every `--profile full` container. The
# Setup screen is the first thing a new install opens, so the first thing that
# install saw was a broken one.


def _at_depth(monkeypatch, path):
    """Run `env_path()` as if this module lived at `path`."""
    monkeypatch.setattr(setup_api, "__file__", str(path))


def test_env_path_survives_a_shallow_container_layout(monkeypatch, tmp_path):
    """The regression: `/app/engine/api/setup.py`, four parents, no marker above it.

    The assertion is only that a Path comes back. Which path is a judgement call
    that depends on what the image looks like; *raising* is not a judgement call,
    it is a 500 on the screen someone is using to enter their first API key.
    """
    _at_depth(monkeypatch, "/app/engine/api/setup.py")
    monkeypatch.chdir(tmp_path)  # no .env here, so the walk is actually taken

    result = setup_api.env_path()

    assert isinstance(result, Path)
    assert result.name == ".env"


def test_env_path_puts_a_new_file_at_the_repository_root(monkeypatch, tmp_path):
    """The checkout case, found by marker rather than by counting directories.

    The root is where the engine's *second* env_file candidate (`../../.env`)
    points, and therefore the only location both documented start-up directories
    agree on — writing anywhere else is a save that reports success and changes
    nothing.
    """
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    module = root / "apps" / "engine" / "engine" / "api" / "setup.py"
    module.parent.mkdir(parents=True)
    _at_depth(monkeypatch, module)

    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert setup_api.env_path() == root / ".env"


def test_env_path_still_prefers_an_existing_dotenv_in_the_working_directory(monkeypatch, tmp_path):
    """The walk is the fallback, never the first answer.

    `Settings.model_config` reads `./.env` before anything else, so a file that is
    already there is the one the engine loads — and Save has to write to the file
    the engine reads, whatever a marker higher up would suggest.
    """
    (tmp_path / ".git").mkdir()  # a marker that must not be preferred
    (tmp_path / "sub").mkdir()
    existing = tmp_path / "sub" / ".env"
    existing.write_text("PEXELS_API_KEY=px\n", encoding="utf-8")

    _at_depth(monkeypatch, tmp_path / "engine" / "api" / "setup.py")
    monkeypatch.chdir(tmp_path / "sub")

    assert setup_api.env_path() == existing.resolve()

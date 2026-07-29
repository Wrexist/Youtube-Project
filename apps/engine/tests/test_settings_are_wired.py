"""Every setting must do something.

The audit found seven settings read by nothing: `storage_backend` and the four
`s3_*` (so "s3" silently wrote to local disk), `youtube_daily_quota` (the ceiling
was hardcoded), `max_concurrent_renders` (a stated guardrail, unenforced),
`llm_provider`/`llm_fast_model` (model choice comes from the routing table), and
`elevenlabs_api_key` (no such provider).

A setting that does nothing is worse than a missing one: it reads as configured and
fails silently, in production, where it matters. The sweep at the bottom is the
guard — it fails when a new field is added and never read.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from engine.settings import Settings, get_settings

ENGINE = Path(__file__).resolve().parents[1] / "engine"

# Settings the sweep cannot see, with the reason each is legitimate.
_SWEEP_EXEMPT = {
    # Consumed by pydantic-settings itself, before any of our code runs.
    "env",
    # Does its work as a validation guard, not as a value: Literal["local"] means
    # STUDIO_STORAGE_BACKEND=s3 fails at startup instead of silently writing to
    # local disk. Nothing needs to *read* it while there is one backend.
    "storage_backend",
}


def _read_engine_source(*, skip: set[str] = frozenset()) -> str:
    return "\n".join(
        p.read_text()
        for p in ENGINE.rglob("*.py")
        if p.name != "settings.py" and p.name not in skip
    )


# ── storage ─────────────────────────────────────────────────────────────────


def test_s3_is_rejected_rather_than_silently_writing_locally(monkeypatch):
    """There is no S3 backend. Accepting "s3" loses every render on recycle."""
    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_STORAGE_BACKEND", "s3")
    try:
        with pytest.raises(Exception, match="storage_backend"):
            Settings()
    finally:
        get_settings.cache_clear()


def test_the_s3_settings_are_gone():
    """They configured nothing. Leaving them implied a backend that never existed."""
    for name in ("s3_bucket", "s3_endpoint", "s3_access_key", "s3_secret_key"):
        assert name not in Settings.model_fields, name


# ── quota ───────────────────────────────────────────────────────────────────


def test_the_daily_quota_ceiling_is_configurable(monkeypatch):
    """A granted quota extension has to be settable — see KNOWN-ISSUES §3.2."""
    from engine.quota import QuotaLedger

    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_YOUTUBE_DAILY_QUOTA", "50000")
    try:
        assert QuotaLedger().limit == 50_000
    finally:
        get_settings.cache_clear()


def test_the_default_ceiling_is_googles_grant():
    from engine.quota import QuotaLedger

    get_settings.cache_clear()
    assert QuotaLedger().limit == 10_000


def test_nothing_compares_spend_against_the_hardcoded_constant():
    """Call sites must read `ledger.limit`, or a raised ceiling only half-applies.

    Three of them did — /v1/quota and two in scheduling.py — so a configured
    extension would have been honoured by the ledger and ignored by the scheduler.
    quota.py is skipped: it is where the default constant is legitimately defined.
    """
    source = _read_engine_source(skip={"quota.py"})
    assert "DAILY_LIMIT" not in source, "found a hardcoded ceiling; use ledger.limit"


# ── render concurrency ──────────────────────────────────────────────────────


def test_concurrent_renders_are_capped(monkeypatch):
    from engine.workflows import media

    get_settings.cache_clear()
    media._render_slots.cache_clear()
    monkeypatch.setenv("STUDIO_MAX_CONCURRENT_RENDERS", "3")
    try:
        assert media._render_slots()._value == 3
    finally:
        get_settings.cache_clear()
        media._render_slots.cache_clear()


async def test_the_cap_actually_blocks_the_third_render(monkeypatch):
    from engine.workflows import media

    get_settings.cache_clear()
    media._render_slots.cache_clear()
    monkeypatch.setenv("STUDIO_MAX_CONCURRENT_RENDERS", "2")
    try:
        slots = media._render_slots()
        await slots.acquire()
        await slots.acquire()
        assert slots.locked(), "a third render must wait, not start"
    finally:
        get_settings.cache_clear()
        media._render_slots.cache_clear()


# ── providers that do not exist ─────────────────────────────────────────────


def test_tts_provider_only_offers_what_is_implemented():
    """Only Edge is wired. The other three recorded a false provider in provenance."""
    get_settings.cache_clear()
    with pytest.raises(Exception, match="tts_provider"):
        Settings(tts_provider="azure")


def test_removed_llm_settings_are_gone():
    """Model choice comes from the routing table, not from Settings."""
    for name in ("llm_provider", "llm_model", "llm_fast_model"):
        assert name not in Settings.model_fields, name


def test_elevenlabs_key_is_gone():
    assert "elevenlabs_api_key" not in Settings.model_fields


# ── the guard ───────────────────────────────────────────────────────────────


def test_every_setting_is_read_somewhere():
    """Fails when a field is added and never wired up.

    This is the check that would have caught all seven at the time they were
    introduced rather than in an audit months later.
    """
    source = _read_engine_source()
    unread = [
        name
        for name in Settings.model_fields
        if name not in _SWEEP_EXEMPT and not re.search(rf"\.{name}\b", source)
    ]
    assert not unread, f"settings read by nothing: {unread}"


def test_the_exemption_list_stays_honest():
    """An exempted setting that *is* now read should leave the list."""
    source = _read_engine_source()
    for name in _SWEEP_EXEMPT - {"env"}:
        assert not re.search(rf"\.{name}\b", source), (
            f"'{name}' is now read — remove it from _SWEEP_EXEMPT"
        )


def test_env_example_documents_only_real_settings():
    """.env.example drifting from Settings is how GOOGLE_REDIRECT_URI got lost."""
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text()
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", example, re.MULTILINE))

    expected = set()
    for name, field in Settings.model_fields.items():
        alias = getattr(field, "validation_alias", None)
        expected.add(alias if isinstance(alias, str) else f"STUDIO_{name.upper()}")

    web_only = {"NEXT_PUBLIC_ENGINE_URL"}
    assert documented - web_only <= expected, (
        f"documented but not a setting: {sorted(documented - web_only - expected)}"
    )


def test_settings_module_has_no_config_toml_lookup():
    """The upstream MoneyPrinterTurbo pattern CLAUDE.md bans."""
    source = _read_engine_source()
    assert "config.app.get(" not in source


#: Writing to `os.environ` — assignment, `pop`, `setdefault`. Stripped before the
#: read check below, because the rule is about where configuration is *read* from.
_ENV_WRITE = re.compile(
    r"os\.environ\[[^\]]+\]\s*=|os\.environ\.(pop|setdefault|update)\(",
)

#: The one module allowed to write. `api/setup.py` saves credentials to `.env`, and
#: `os.environ` takes precedence over the dotenv in pydantic-settings — so without
#: also updating the process environment, a variable that was already exported keeps
#: its old value and Save reports success while changing nothing until a full
#: restart. That is the opposite of drift: it is what makes Settings correct.
_MAY_WRITE_ENV = {"api/setup.py"}


#: Shared with the other source-shape guards — see the note in conftest.py. Without
#: it this fires on the prose that documents it: the comment in `api/setup.py`
#: explaining *why* `os.environ` has to be written is itself a match for
#: `os.environ`.
from conftest import code_only as _code_only  # noqa: E402


def test_nothing_reads_os_environ_directly():
    """Settings is the single source; a stray getenv is how drift starts.

    Reads only. A module that *writes* the environment is not competing with
    Settings for the answer, it is updating the thing Settings reads — which is
    exactly what saving a credential has to do to take effect in this process.
    """
    offenders = []
    for path in ENGINE.rglob("*.py"):
        if path.name == "settings.py":
            continue
        source = _code_only(path.read_text())
        rel = path.relative_to(ENGINE).as_posix()
        if rel in _MAY_WRITE_ENV:
            source = _ENV_WRITE.sub("", source)
        if re.search(r"os\.environ|os\.getenv", source):
            offenders.append(rel)
    assert not offenders, f"reads the environment directly: {offenders}"


def test_only_the_setup_endpoint_writes_the_environment():
    """The exemption above must stay one module wide.

    Anything else mutating `os.environ` is reconfiguring the process behind
    Settings' back, which is the drift the rule exists to stop — and it would be
    invisible, because the read check would keep passing.
    """
    writers = [
        p.relative_to(ENGINE).as_posix()
        for p in ENGINE.rglob("*.py")
        if p.name != "settings.py" and _ENV_WRITE.search(_code_only(p.read_text()))
    ]
    assert set(writers) <= _MAY_WRITE_ENV, f"unexpected environment writer: {writers}"


def test_no_secret_is_logged():
    """A key in a log file is a leaked key. Cheap to assert, expensive to miss."""
    result = subprocess.run(
        ["grep", "-rnE", r"logger\.[a-z]+\(.*(api_key|secret|token)", str(ENGINE)],
        capture_output=True,
        text=True,
    )
    interesting = [
        line
        for line in result.stdout.splitlines()
        # Naming a *variable* is fine; interpolating its value is not.
        if re.search(r"\{[^}]*(api_key|secret|token)[^}]*\}|\+\s*\w*(api_key|secret|token)", line)
    ]
    assert not interesting, f"possible secret in a log line: {interesting}"


# ── worker dispatch ─────────────────────────────────────────────────────────
#
# Codex review, P1: `enqueue` returned True whenever Redis accepted the job, so
# with Redis up but no arq worker consuming — exactly what `docker compose up -d`
# gives you, since the worker is a separate command — a Generate sat in `running`
# forever with no stage events and nothing in any log.


async def test_enqueue_refuses_when_no_worker_is_consuming(monkeypatch):
    """Redis being reachable is not the same question as a worker existing."""
    from engine import worker

    class DeadQueue:
        async def exists(self, _key):
            return 0  # the health key has expired, or was never set

        async def enqueue_job(self, *_a, **_kw):
            raise AssertionError("must not enqueue into a queue nobody is reading")

        async def aclose(self):
            pass

    monkeypatch.setattr(worker, "create_pool", lambda *_a, **_kw: _resolve(DeadQueue()))
    assert await worker.enqueue("job-1") is False


async def test_enqueue_uses_the_worker_when_one_is_alive(monkeypatch):
    from engine import worker

    enqueued = []

    class LiveQueue:
        async def exists(self, key):
            assert key == worker.HEALTH_KEY
            return 1

        async def enqueue_job(self, fn, *args):
            enqueued.append((fn, args))

        async def aclose(self):
            pass

    monkeypatch.setattr(worker, "create_pool", lambda *_a, **_kw: _resolve(LiveQueue()))
    assert await worker.enqueue("job-1") is True
    assert enqueued == [("run_job_task", ("job-1", None))]


async def test_enqueue_gives_up_on_a_redis_that_answers_nothing(monkeypatch):
    """Reachable-but-catatonic, which is not the same as unreachable.

    arq passes `conn_timeout` as `socket_connect_timeout` and never sets
    `socket_timeout`, so once a socket is open every command waits forever. A
    Redis that accepts connections and then stops answering — paused container,
    failing over, swapping — therefore hung `enqueue`, which runs inside
    `POST /v1/jobs`. Measured against a socket that accepts and never replies:
    still hanging at 20 seconds.
    """
    import asyncio

    from engine import worker

    class Catatonic:
        async def exists(self, _key):
            await asyncio.sleep(3600)  # answers, eventually, in the heat death

        async def enqueue_job(self, *_a, **_kw):
            raise AssertionError("should never get this far")

        async def aclose(self):
            pass

    monkeypatch.setattr(worker, "create_pool", lambda *_a, **_kw: _resolve(Catatonic()))
    monkeypatch.setattr(worker, "PROBE_BUDGET_S", 0.2)

    started = asyncio.get_running_loop().time()
    assert await worker.enqueue("job-1") is False
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 2.0, f"took {elapsed:.1f}s — the deadline is not bounding anything"


def test_the_probe_budget_covers_the_whole_handover():
    """Per-call timeouts would let a merely-slow Redis spend the budget repeatedly.

    Connect, health-key lookup and enqueue are three round trips; bounding each
    separately means the worst case is three times what the constant says.
    """
    import inspect

    from conftest import code_only

    from engine import worker

    body = code_only(inspect.getsource(worker.enqueue))
    assert body.count("wait_for") == 1, "the deadline should wrap the sequence, not each call"
    assert "PROBE_BUDGET_S" in body


async def test_enqueue_falls_back_when_redis_is_absent(monkeypatch):
    from engine import worker

    async def refuse(*_a, **_kw):
        raise ConnectionError("no redis")

    monkeypatch.setattr(worker, "create_pool", refuse)
    assert await worker.enqueue("job-1") is False


def test_the_health_check_interval_makes_the_key_a_liveness_signal():
    """arq's default is an hour, which would keep a dead worker looking alive."""
    from engine.worker import WorkerSettings

    assert WorkerSettings.health_check_interval <= 60


async def _resolve(value):
    return value


def test_the_enqueue_probe_does_not_retry_five_times():
    """`enqueue` runs inside POST /v1/jobs, so its Redis timeout is user-facing.

    With no Redis — the documented zero-config setup, where renders run in-process
    anyway — arq's defaults of five retries at one second each meant every Generate
    sat for five seconds before falling back to the path it was always going to
    take. Measured at 5.02s before this, 0.01s after.
    """
    from engine.worker import build_redis_settings, probe_redis_settings

    probe = probe_redis_settings()
    assert probe.conn_retries <= 1
    assert probe.conn_retry_delay == 0
    assert probe.conn_timeout <= 2

    # The worker keeps the generous defaults: it is long-running and should ride
    # out a Redis restart rather than dying on one refused connection.
    assert build_redis_settings().conn_retries > probe.conn_retries


async def test_the_fallback_is_fast_when_redis_is_absent(monkeypatch):
    import time

    from engine import worker

    async def refuse(*_a, **_kw):
        raise ConnectionError("no redis")

    monkeypatch.setattr(worker, "create_pool", refuse)
    started = time.monotonic()
    assert await worker.enqueue("job-1") is False
    assert time.monotonic() - started < 1.0

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


def test_nothing_reads_os_environ_directly():
    """Settings is the single source; a stray getenv is how drift starts."""
    offenders = [
        p.relative_to(ENGINE).as_posix()
        for p in ENGINE.rglob("*.py")
        if p.name != "settings.py" and re.search(r"os\.environ|os\.getenv", p.read_text())
    ]
    assert not offenders, f"reads the environment directly: {offenders}"


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

"""The health checks, which the Setup screen and the CLI both render.

The point of moving these out of `scripts/doctor.py` was that two callers now
need them and must not be able to disagree. These tests pin the properties that
made that move safe.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine import diagnostics
from engine.main import app

client = TestClient(app)


async def test_running_twice_does_not_accumulate():
    """The old version appended to a module-level list.

    Fine for a process that exits immediately afterwards, wrong for a server that
    answers this endpoint repeatedly — the second call would have reported the
    first call's results as well, growing without bound.
    """
    first = await diagnostics.run(include_network=False)
    second = await diagnostics.run(include_network=False)
    assert len(first.checks) == len(second.checks)
    assert [c.key for c in first.checks] == [c.key for c in second.checks]


async def test_every_failing_check_says_what_to_do():
    """A check that just says FAIL has made the situation worse."""
    report = await diagnostics.run(include_network=False)
    for check in report.checks:
        if check.level in ("fail", "warn"):
            assert check.fix or check.command or check.href, (
                f"{check.key} reports a problem and offers no way to resolve it"
            )


async def test_keys_are_never_in_the_report():
    """This runs over a live `Settings` holding every credential on the machine."""
    import os

    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-diagnostics-secret"
    from engine.settings import get_settings

    get_settings.cache_clear()
    try:
        report = await diagnostics.run(include_network=False)
        blob = " ".join(f"{c.detail} {c.fix} {c.command}" for c in report.checks)
        assert "sk-ant-diagnostics-secret" not in blob
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        get_settings.cache_clear()


async def test_a_check_that_raises_becomes_a_finding_not_a_crash(monkeypatch):
    """A diagnostic tool that itself fails opaquely is the worst version of this."""

    def explode(_report):
        raise RuntimeError("the probe caught fire")

    monkeypatch.setattr(diagnostics, "_check_ffmpeg", explode)
    report = await diagnostics.run(include_network=False)

    # The other checks still ran...
    assert len(report.checks) > 1
    # ...and the failure is reported rather than swallowed or raised.
    broken = [c for c in report.checks if "caught fire" in c.detail]
    assert broken and broken[0].level == "fail"


async def test_the_network_probe_is_skippable():
    """It waits up to six seconds on YouTube; a page render must not."""
    quick = await diagnostics.run(include_network=False)
    assert "grounding" not in {c.key for c in quick.checks}


def test_ready_means_no_blockers_not_no_warnings():
    """`ready` gates the headline, so what it means has to be exact."""
    report = diagnostics.Report()
    report.add(diagnostics.Check(key="a", name="A", level="ok"))
    report.add(diagnostics.Check(key="b", name="B", level="warn", detail="x", fix="y"))
    assert report.ready is True

    report.add(diagnostics.Check(key="c", name="C", level="fail", detail="x", fix="y"))
    assert report.ready is False


# ── the endpoint ────────────────────────────────────────────────────────────


def test_the_endpoint_reports_the_same_checks():
    response = client.get("/v1/setup/diagnostics?network=false")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"], "an empty report is not a report"
    assert body["blockers"] == sum(1 for c in body["checks"] if c["level"] == "fail")
    assert body["warnings"] == sum(1 for c in body["checks"] if c["level"] == "warn")
    assert body["ready"] is (body["blockers"] == 0)


def test_the_endpoint_does_not_dispose_the_shared_connection_pool():
    """The CLI version called `db.dispose()` after its database check.

    Correct there — nothing else in that process uses the pool. Inside the live
    API it would drop connections out from under running jobs, every time someone
    opened the Setup screen.
    """
    import inspect

    from conftest import code_only

    # Comments stripped: the comment in `_check_database` explaining why there is
    # no dispose() here otherwise matches the pattern this looks for.
    assert "dispose" not in code_only(inspect.getsource(diagnostics._check_database))


@pytest.mark.parametrize("network", ["true", "false"])
def test_both_modes_answer(network):
    assert client.get(f"/v1/setup/diagnostics?network={network}").status_code == 200

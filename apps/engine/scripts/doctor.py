"""Check everything, and say exactly what to do about whatever is missing.

    apps/engine/.venv/bin/python apps/engine/scripts/doctor.py

The checks themselves live in `engine/diagnostics.py`, because the Setup screen
shows them too and the terminal and the screen must not be able to disagree. This
file is the printing.

Exit code is 0 unless something **required** is broken, which makes it usable as a
pre-flight in a script or a container healthcheck.

You do not need this. `npm start`, then the Setup screen, runs the same checks
with a button. It stays for CI, for containers, and for the case where the engine
will not start at all — which is exactly when a screen served by that engine is no
use.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

# Never write while diagnosing. The library is read-only by construction, but this
# process is short-lived and standalone, so the belt as well as the braces costs
# nothing here. (It is set *here* rather than in the library precisely because the
# library is imported by the running server, where this would be destructive.)
os.environ.setdefault("STUDIO_PERSIST", "false")

# The checks deliberately provoke failures, and each one logs. That noise is the
# opposite of what this command is for — the report below says it better.
try:
    from loguru import logger

    logger.remove()
except ImportError:
    pass

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    GREEN = YELLOW = RED = DIM = RESET = ""

MARKS = {"ok": f"{GREEN}✓{RESET}", "warn": f"{YELLOW}!{RESET}", "fail": f"{RED}✗{RESET}"}


async def main() -> int:
    try:
        from engine import diagnostics
    except ImportError as exc:
        print(f"\n {RED}✗{RESET} engine package: {exc}")
        print(f'   {DIM}cd apps/engine && .venv/bin/pip install -e ".[dev]"{RESET}\n')
        return 1

    report = await diagnostics.run()

    width = max(len(c.name) for c in report.checks) + 2
    print()
    for check in report.checks:
        print(f" {MARKS[check.level]} {check.name:<{width}} {DIM}{check.detail}{RESET}")

    if report.blockers:
        print(f"\n{RED}{len(report.blockers)} thing(s) must be fixed before anything runs:{RESET}")
        for check in report.blockers:
            _advise(check)

    if report.warnings:
        print(f"\n{YELLOW}{len(report.warnings)} optional:{RESET}")
        for check in report.warnings:
            if check.fix or check.command:
                _advise(check)

    if report.ready:
        print(f"\n{GREEN}Ready.{RESET} Start it with:")
        print(f"   {DIM}npm start{RESET}")

    print()
    # Disposed here, not in the library: the checks run inside the live API too,
    # where tearing down the shared connection pool would drop connections out
    # from under running jobs. In this process nothing else is using it.
    try:
        from engine import db

        await db.dispose()
    except Exception:  # noqa: BLE001 — teardown must not change the exit code
        pass

    return 1 if report.blockers else 0


def _advise(check) -> None:
    """One finding, with whatever will actually resolve it."""
    print(f"   {check.name}: {check.fix or check.detail}")
    if check.command:
        print(f"      {DIM}{check.command}{RESET}")
    if check.href:
        print(f"      {DIM}or fix it in the app: http://localhost:3000{check.href}{RESET}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

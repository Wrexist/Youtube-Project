"""Keep the engine's errors somewhere they can be read after the fact.

Until this existed, loguru's default stderr sink was the only one. That is fine
while you are watching the terminal and useless the moment you are not, which is
the normal case: Studio is started by double-clicking a shortcut, its console
scrolls, and the traceback for a stage that failed twenty minutes ago is gone.
What reached the operator was the single line the UI shows on the failed row —
`RuntimeError: no keyword evidence for '...'` — with no way to get at the rest.

So: the same records, additionally written to a file that survives the window
closing, and a report endpoint that turns the tail of it into something
paste-able.

`diagnose=False` is not the default and is load-bearing. Loguru's "diagnose"
mode annotates each frame of a traceback with the *values* of the local
variables, which through `providers/llm.py` or `providers/youtube.py` means API
keys and refresh tokens written to a file in plain text. CLAUDE.md #4 says
secrets are never logged. `backtrace=True` keeps the full call chain, which is
the part that was actually missing.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

#: Under the storage root rather than next to the code: it is operator data, it
#: is already the directory the engine owns and creates, and `.gitignore` covers
#: it. Named for the process because the API and the worker both write, and
#: interleaved tracebacks from two processes are worse than two files.
LOG_DIR = "logs"

_installed: set[str] = set()


def path_for(storage_root: str | Path, process: str) -> Path:
    return Path(storage_root) / LOG_DIR / f"{process}.log"


def install(storage_root: str | Path, process: str = "engine") -> Path | None:
    """Add a rotating file sink. Safe to call twice; returns the path, or None.

    Never raises. A read-only storage root, a full disk or a locked file are all
    real states, and none of them is a reason to refuse to start the engine —
    stderr still has everything.
    """
    if process in _installed:
        return path_for(storage_root, process)

    target = path_for(storage_root, process)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            target,
            level="INFO",
            rotation="5 MB",
            retention=5,
            encoding="utf-8",
            enqueue=True,  # the render work is threaded; without this lines interleave
            backtrace=True,
            diagnose=False,  # see the module docstring - this one matters
        )
    except OSError as exc:
        logger.warning("could not open {} for logging: {}", target, exc)
        return None

    _installed.add(process)
    logger.info("logging to {}", target)
    return target


def tail(storage_root: str | Path, process: str = "engine", lines: int = 200) -> list[str]:
    """The last `lines` of the log, oldest first. Empty when there is no file yet."""
    target = path_for(storage_root, process)
    try:
        # Whole-file read: rotation caps this at 5 MB, and the alternative -
        # seeking backwards over a UTF-8 file - is a great deal of code to avoid
        # reading a few megabytes on a hand-pressed diagnostics button.
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]

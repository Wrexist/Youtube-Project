"""Subtitle font resolution.

MoviePy 2 hands `TextClip(font=...)` straight to Pillow, so a family name will
not do — it needs a path to a file that exists, and it raises at construction if
that path is wrong. Getting this wrong fails the render, which is the most
expensive stage, so it is checked up front instead.

Upstream ships fonts in `resource/fonts/` and picks by name. We do not
redistribute them (no licence files ship alongside them), so resolution is: an
explicit setting, then a drop-in directory under the storage root, then the
system fonts the engine image installs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from loguru import logger

from engine.settings import get_settings

# Bold first at every step: subtitles sit over moving footage, and a regular
# weight loses its stroke against a bright frame.
_SYSTEM_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)

_EXTENSIONS = (".ttf", ".otf", ".ttc")


def fonts_dir() -> Path:
    """Drop-in directory for fonts you have the right to use.

    Under the storage root rather than the source tree, so a mounted volume
    survives a container rebuild and nothing lands in the Git working copy.
    """
    return Path(get_settings().storage_root) / "fonts"


def available_fonts() -> list[Path]:
    """Every font the renderer could use, drop-ins first."""
    found: list[Path] = []
    directory = fonts_dir()
    if directory.is_dir():
        found.extend(
            sorted(
                (p for p in directory.iterdir() if p.suffix.lower() in _EXTENSIONS),
                key=lambda p: p.name.lower(),
            )
        )
    found.extend(Path(c) for c in _SYSTEM_CANDIDATES if Path(c).is_file())
    return found


def resolve(name_or_path: str = "") -> Path:
    """A concrete font file for `TextClip`.

    `name_or_path` may be an absolute path, or a bare filename to look up in the
    drop-in directory. Empty falls back to the configured font, then to whatever
    the system provides.

    Raises with the list of places searched rather than returning a default —
    a silently substituted font changes every frame of every video, and that is
    not something to discover after the fact.
    """
    requested = name_or_path or get_settings().subtitle_font

    if requested:
        candidate = Path(requested)
        if candidate.is_file():
            return candidate
        # A bare filename is the common case: "Inter-Bold.ttf" in the drop-in dir.
        scoped = _within(fonts_dir(), candidate.name)
        if scoped is not None and scoped.is_file():
            return scoped
        logger.warning("subtitle font {!r} not found; falling back to a system font", requested)

    for path in available_fonts():
        return path

    raise RuntimeError(
        "no subtitle font available. Set STUDIO_SUBTITLE_FONT to a .ttf path, drop "
        f"one into {fonts_dir()}, or install fonts-dejavu-core. Searched: "
        + ", ".join(_SYSTEM_CANDIDATES)
    )


def _within(directory: Path, name: str) -> Path | None:
    """Resolve `name` inside `directory`, refusing anything that escapes it.

    Font names can arrive from a series config, so a `../../etc/passwd` has to
    fail closed even though the worst case is only a confusing render error.
    """
    try:
        root = directory.resolve()
        path = (root / name).resolve()
    except OSError:
        return None
    return path if path.is_relative_to(root) else None


@lru_cache(maxsize=8)
def cached_resolve(name_or_path: str = "") -> str:
    """`resolve()` as a string, memoised for the render loop.

    A long-form render builds one `TextClip` per cue — hundreds of them — and
    each would otherwise stat the same font paths again.
    """
    return str(resolve(name_or_path))

"""Background music: selection, path safety, and mixing under the narration.

Ported from `vendor/moneyprinterturbo/app/services/bgm.py`. Upstream's Streamlit
upload pipeline (staging, chunked writes, atomic replace, FFmpeg probing) is not
here — we have no upload endpoint, and adding 200 lines of upload hardening for a
feature nobody can reach would be worse than not having it. What is kept is the
part that matters at render time: resolving a caller-supplied name to a file
inside an allow-listed directory, and the mix itself.

**Nothing ships with music.** Upstream bundles `resource/songs/` with unclear
provenance and it is deliberately not carried over — see `KNOWN-ISSUES.md` §3.3.
Point `STUDIO_BGM_DIR` at tracks you have the right to publish, or leave BGM off.
"""

from __future__ import annotations

import random
from pathlib import Path

from loguru import logger

from engine.settings import get_settings

# MoviePy decodes through FFmpeg, so this is not an MP3-only list. It stays
# narrow and audio-only so a stray .mp4 in the directory is not picked up as a
# music bed and silently dropped into the mix as a second video's audio.
SUPPORTED_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus")

# Fade the bed out under the last few seconds rather than cutting it. Upstream
# uses 3s and it is the right call — a hard stop reads as a mistake.
FADE_OUT_S = 3.0


def bgm_dir() -> Path:
    """Where music lives. Runtime data, so under the storage root."""
    settings = get_settings()
    return Path(settings.bgm_dir) if settings.bgm_dir else Path(settings.storage_root) / "bgm"


def should_mix(volume: float | None) -> bool:
    """One place that decides whether any BGM work happens at all.

    Kept separate from selection so that a zero or nonsense volume skips the
    directory scan, the file resolution and the mix, rather than each of those
    re-deciding. Upstream grew a copy of this check per provider.
    """
    if not get_settings().bgm_enabled:
        return False
    try:
        value = float(volume if volume is not None else get_settings().bgm_volume)
    except (TypeError, ValueError):
        return False
    return 0.0 < value <= 1.0


def list_tracks() -> list[Path]:
    """Usable music files, sorted for a stable order."""
    directory = bgm_dir()
    if not directory.is_dir():
        return []
    tracks: list[Path] = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        resolved = _within(directory, entry.name)
        # A symlink pointing out of the directory would otherwise hand an
        # arbitrary file to FFmpeg by way of the "random" picker.
        if resolved is None or not resolved.is_file():
            logger.warning("skipping unsafe background music entry: {}", entry.name)
            continue
        tracks.append(resolved)
    return tracks


def resolve(name: str = "") -> Path | None:
    """Pick a track. Empty or "random" chooses one; a name is looked up.

    Returns None rather than raising when there is no music: a missing bed is
    a video without music, not a failed render.
    """
    tracks = list_tracks()
    if not tracks:
        logger.info("no background music in {}; rendering without a bed", bgm_dir())
        return None

    if not name or name == "random":
        return random.choice(tracks)

    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        logger.warning("unsupported background music format: {!r}", name)
        return None

    # Match on the basename only — the caller supplies a track name, never a
    # path, and accepting a path is how this turns into an arbitrary-read.
    target = _within(bgm_dir(), Path(name).name)
    if target is None or not target.is_file():
        logger.warning("background music {!r} not found in {}", name, bgm_dir())
        return None
    return target


def mix(narration, *, duration: float, track: Path | None, volume: float):
    """Lay `track` under `narration`, looped and faded, at `volume`.

    Returns `narration` untouched when there is nothing to mix, and — this is
    the important part — also when the mix *fails*. Music is a nice-to-have;
    losing a whole long-form render because a music file was malformed is not a
    trade worth making, so the failure is logged loudly and the narration ships.
    """
    if track is None or not should_mix(volume):
        return narration

    try:
        from moviepy import AudioFileClip, CompositeAudioClip, afx

        bed = AudioFileClip(str(track)).with_effects(
            [
                afx.MultiplyVolume(volume),
                afx.AudioLoop(duration=duration),
                afx.AudioFadeOut(min(FADE_OUT_S, duration / 2)),
            ]
        )
    except Exception as exc:  # noqa: BLE001 — never lose a render over music
        logger.error("failed to mix background music {}: {}", track, exc)
        return narration

    logger.info("mixed background music: {} at {:.2f}", track.name, volume)
    return CompositeAudioClip([narration, bed])


def _within(directory: Path, name: str) -> Path | None:
    """Resolve `name` inside `directory`, refusing anything that escapes it."""
    try:
        root = directory.resolve()
        path = (root / name).resolve()
    except OSError:
        return None
    return path if path.is_relative_to(root) else None

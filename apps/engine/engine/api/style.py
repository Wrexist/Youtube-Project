"""How every video sounds and looks.

Six `Settings` fields decide the character of the output — the narrator, whether
there is music and which, the caption typeface, how stills move, and whether shots
cut or dissolve. The render engine has honoured all six since it was written. No
screen could reach any of them, so every video this project has ever produced is
narrated by `en-US-AvaNeural`, silent, in the default font, with alternating Ken
Burns and hard cuts, and the only way to change that was to edit `.env` by hand.

That is a quality ceiling and a policy risk at once: CLAUDE.md records that YouTube's
inauthentic-content rules target mass-produced templated content, and a channel that
shares its voice and typography with every other install's defaults is templated by
construction.

**Scope.** Six fields, not twenty-eight. CLAUDE.md says expose the three things that
actually vary and keep opinionated defaults for the rest, so the storage backend, the
concurrency limit and the quota ceiling stay where they are. Motion is included
because it is cheap and visible, and the screen puts it behind a disclosure.

Persistence goes through `setup.write_env`, the same atomic dotenv merge the
credentials screen uses, and through `os.environ` for the same reason it does: an
already-exported variable outranks the file in pydantic-settings, so writing the file
alone would report success and change nothing until a restart.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from engine.api.setup import env_path, write_env
from engine.services import bgm, fonts
from engine.settings import get_settings

router = APIRouter(prefix="/v1/style", tags=["style"])

#: Voice ids look like `en-US-AvaNeural` or `en-AU-WilliamMultilingualNeural`.
#: Checked before anything is written because the value lands in a dotenv, and a
#: newline in it would end the assignment and begin another one.
_VOICE_ID = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,8}-[A-Za-z]+$")

#: What to fall back to when the voice catalogue cannot be fetched. Deliberately
#: just the default: an offline install should still be able to open the screen and
#: see what it is set to, and pretending to offer a curated list that was never
#: verified against the service is worse than offering none.
_FALLBACK_VOICES = ("en-US-AvaNeural",)

#: The one register to drop. Everything else ships.
#:
#: This was an allowlist of `News`, `Novel` and `Conversation` — the documentary,
#: long-read and explainer registers — which sounds right and is not: only `en-US`
#: and `zh-CN` voices carry content tags at all, so an allowlist quietly deleted
#: every British, Australian and Irish narrator (Microsoft labels them `General`)
#: while keeping `en-US-AnaNeural`, whose tags are `Cartoon` and whose personality
#: is "Cute". Filtering on a field most rows leave blank is filtering on presence
#: of metadata, not on suitability.
_EXCLUDED_CATEGORIES = frozenset({"Cartoon"})


class Voice(BaseModel):
    """One narrator, described in the service's own words rather than ours."""

    id: str
    #: `AvaNeural` out of `en-US-AvaNeural` — the locale is shown separately.
    name: str
    locale: str
    gender: str
    #: Microsoft's `VoicePersonalities`, e.g. ["Warm", "Confident", "Authentic"].
    #: Passed through rather than paraphrased: these are the only honest
    #: description available without listening to all 322 of them.
    traits: list[str] = Field(default_factory=list)


class Options(BaseModel):
    """What this install can actually choose between, right now."""

    voices: list[Voice]
    #: Whether the catalogue above came from the service or is the offline stub.
    voices_live: bool
    #: Font files found under the fonts directory. Empty is normal — the renderer
    #: falls back to a bundled default.
    fonts: list[str]
    #: Music files found under `bgm_dir`. Empty is the shipped state: nothing comes
    #: with music, because publishing over an unlicensed bed is a copyright strike.
    tracks: list[str]
    tracks_dir: str


class Style(BaseModel):
    voice: str
    subtitle_font: str
    ken_burns: Literal["none", "in", "out", "alternate"]
    transition_fade_s: float
    bgm_enabled: bool
    bgm_volume: float
    bgm_track: str
    options: Options


class StyleUpdate(BaseModel):
    """Every field optional — the screen sends what changed, not the whole form."""

    voice: str | None = None
    subtitle_font: str | None = None
    ken_burns: Literal["none", "in", "out", "alternate"] | None = None
    transition_fade_s: float | None = Field(default=None, ge=0.0, le=2.0)
    bgm_enabled: bool | None = None
    bgm_volume: float | None = Field(default=None, gt=0.0, le=1.0)
    bgm_track: str | None = None


_voices_cache: list[Voice] | None = None
_voices_live = False
_voices_lock = asyncio.Lock()


async def _catalogue() -> tuple[list[Voice], bool]:
    """The voice list, fetched once per process.

    A network call, so it is cached and it is allowed to fail. The screen is still
    useful without it — the current voice is shown either way and the field accepts
    a typed id — and a settings screen that will not open because Microsoft is
    unreachable would be a worse trade than a short list.
    """
    global _voices_cache, _voices_live
    if _voices_cache is not None:
        return _voices_cache, _voices_live

    async with _voices_lock:
        if _voices_cache is not None:  # won by another request while we waited
            return _voices_cache, _voices_live

        try:
            import edge_tts

            raw = await asyncio.wait_for(edge_tts.list_voices(), timeout=10)
        except Exception as exc:  # noqa: BLE001 - any failure means "use the stub"
            logger.warning("could not fetch the voice catalogue ({}); using the default", exc)
            _voices_cache = [
                Voice(id=v, name=v.rsplit("-", 1)[-1], locale="-".join(v.split("-")[:2]), gender="")
                for v in _FALLBACK_VOICES
            ]
            _voices_live = False
            return _voices_cache, _voices_live

        voices: list[Voice] = []
        for entry in raw:
            tag = entry.get("VoiceTag") or {}
            categories = set(tag.get("ContentCategories") or [])
            if categories & _EXCLUDED_CATEGORIES:
                continue
            short = entry.get("ShortName", "")
            if not _VOICE_ID.fullmatch(short):
                continue
            voices.append(
                Voice(
                    id=short,
                    name=short.rsplit("-", 1)[-1],
                    locale="-".join(short.split("-")[:2]),
                    gender=entry.get("Gender", ""),
                    traits=list(tag.get("VoicePersonalities") or []),
                )
            )

        voices.sort(key=lambda v: (v.locale, v.name))
        _voices_cache = voices
        _voices_live = True
        return _voices_cache, _voices_live


async def _options() -> Options:
    voices, live = await _catalogue()
    # Both scan a directory, which is a blocking call on a slow or network volume.
    listing = await asyncio.gather(
        asyncio.to_thread(fonts.available_fonts),
        asyncio.to_thread(bgm.list_tracks),
        asyncio.to_thread(bgm.bgm_dir),
    )
    font_files, tracks, directory = listing
    return Options(
        voices=voices,
        voices_live=live,
        fonts=[p.name for p in font_files],
        tracks=[p.name for p in tracks],
        tracks_dir=str(directory),
    )


async def _current() -> Style:
    settings = get_settings()
    return Style(
        voice=settings.tts_voice,
        subtitle_font=settings.subtitle_font,
        ken_burns=settings.ken_burns,
        transition_fade_s=settings.transition_fade_s,
        bgm_enabled=settings.bgm_enabled,
        bgm_volume=settings.bgm_volume,
        bgm_track=settings.bgm_track,
        options=await _options(),
    )


@router.get("")
async def read() -> Style:
    return await _current()


@router.put("")
async def update(body: StyleUpdate) -> Style:
    """Write the changed fields to `.env` and make them live in this process.

    Returns the new state rather than an acknowledgement, so the screen shows what
    is actually in force instead of what it hoped it had set — the same contract as
    `PUT /v1/setup/keys`.
    """
    updates: dict[str, str] = {}

    if body.voice is not None:
        voice = body.voice.strip()
        if not _VOICE_ID.fullmatch(voice):
            raise HTTPException(422, f"{voice!r} is not a voice id, e.g. en-US-AndrewNeural")
        updates["STUDIO_TTS_VOICE"] = voice

    if body.subtitle_font is not None:
        font = body.subtitle_font.strip()
        # Validated against what is on disk rather than by pattern. `fonts.resolve`
        # confines lookups to the fonts directory already, so this is not the only
        # guard — but accepting a name that resolves to nothing would silently
        # render in the fallback face and look like the setting was ignored.
        if font and font not in {p.name for p in await asyncio.to_thread(fonts.available_fonts)}:
            raise HTTPException(422, f"no font named {font!r} in the fonts directory")
        updates["STUDIO_SUBTITLE_FONT"] = font

    if body.ken_burns is not None:
        updates["STUDIO_KEN_BURNS"] = body.ken_burns

    if body.transition_fade_s is not None:
        updates["STUDIO_TRANSITION_FADE_S"] = f"{body.transition_fade_s:g}"

    if body.bgm_enabled is not None:
        updates["STUDIO_BGM_ENABLED"] = "true" if body.bgm_enabled else "false"

    if body.bgm_volume is not None:
        updates["STUDIO_BGM_VOLUME"] = f"{body.bgm_volume:g}"

    if body.bgm_track is not None:
        track = body.bgm_track.strip()
        if track and track not in {p.name for p in await asyncio.to_thread(bgm.list_tracks)}:
            raise HTTPException(422, f"no track named {track!r} in {bgm.bgm_dir()}")
        updates["STUDIO_BGM_TRACK"] = track

    if not updates:
        return await _current()

    path = env_path()
    try:
        await asyncio.to_thread(write_env, path, updates)
    except OSError as exc:
        raise HTTPException(500, f"could not write {path}: {exc.strerror or exc}") from exc

    # `os.environ` outranks the dotenv in pydantic-settings, so a variable already
    # exported in this shell would keep its old value however the file is rewritten
    # — Save would report success and change nothing until a full restart.
    for name, value in updates.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)

    get_settings.cache_clear()
    # Fonts are resolved through an lru_cache keyed on the requested name, so a
    # changed default would otherwise keep rendering in the previous face for the
    # life of the process.
    fonts.cached_resolve.cache_clear()

    logger.info("style: set {}", ", ".join(f"{k}={v}" for k, v in sorted(updates.items())))
    return await _current()

"""Metered image generation.

Same contract as `providers.llm`: nothing calls an image API directly, because cost
tracking and provenance both depend on going through here. Images are priced per
image rather than per token, so `GeneratedImage` carries a flat `cost_usd` instead
of a token count — but it records the model and the prompt exactly as `Completion`
does, which is what CLAUDE.md's provenance rule requires.

**No new API key.** The two transports reuse `OPENAI_API_KEY` and `GEMINI_API_KEY`,
which Settings already had for the LLM providers. If you have either one, thumbnails
start generating with no extra setup; if you have neither, `generate()` returns None
and the caller keeps its placeholder. That is the whole reason `image_provider`
defaults to "auto" rather than naming a provider.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal

import httpx
from loguru import logger

from engine.settings import get_settings

ImageProvider = Literal["openai", "gemini"]


class ImageUnavailable(RuntimeError):
    """The provider could not be reached, or returned something unusable."""


@dataclass
class ImageSpec:
    """One image model.

    `cost_per_image` is a flat approximation. Image APIs price by size and quality
    tier rather than by output volume, so there is no exact arithmetic to do the way
    there is for tokens — this is close enough to keep the per-video budget honest
    and is deliberately rounded up rather than down.
    """

    provider: ImageProvider
    model: str
    label: str
    cost_per_image: float
    #: What we ask the provider for. Neither offers 1280x720, so the caller
    #: centre-crops; requesting the widest supported size loses the least.
    request_size: str

    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class GeneratedImage:
    data: bytes
    model: str
    prompt: str
    cost_usd: float


CATALOGUE: dict[ImageProvider, ImageSpec] = {
    "openai": ImageSpec(
        "openai", "gpt-image-1", "GPT Image 1", cost_per_image=0.19, request_size="1536x1024"
    ),
    "gemini": ImageSpec(
        "gemini", "imagen-4.0-generate-001", "Imagen 4", cost_per_image=0.04, request_size="16:9"
    ),
}


def selected() -> ImageSpec | None:
    """Which image model to use, or None to fall back to a composed placeholder.

    "auto" resolves against whichever key is actually present rather than asking the
    user to name a provider they may not have. GPT Image is preferred when both are
    set — it is the better thumbnail model, which is the thing that decides whether
    the video gets clicked. It costs more and returns 3:2 rather than 16:9, so the
    caller crops; both are accepted trade-offs for the quality.

    An explicitly named provider whose key is missing is a misconfiguration, not a
    reason to silently degrade — it warns, because a user who typed
    STUDIO_IMAGE_PROVIDER=gemini wants to know why nothing generated.
    """
    settings = get_settings()
    choice = settings.image_provider

    if choice == "none":
        return None

    # Order is the preference order for "auto".
    keys: dict[ImageProvider, str] = {
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
    }

    if choice == "auto":
        for provider, key in keys.items():
            if key:
                return CATALOGUE[provider]
        return None

    if not keys.get(choice):
        logger.warning(
            "image_provider is '{}' but its API key is not set; thumbnails will use the "
            "placeholder background",
            choice,
        )
        return None
    return CATALOGUE[choice]


async def generate(prompt: str) -> GeneratedImage | None:
    """One image for a thumbnail background, or None if generation is not configured.

    None is a supported outcome, not a failure: a first run with no keys still
    produces a thumbnail, just a flat one. A provider that is configured but *fails*
    is different — that raises, because silently returning the placeholder would hide
    a broken key behind a thumbnail that looks merely unstyled.
    """
    spec = selected()
    if spec is None:
        return None

    if spec.provider == "openai":
        data = await _openai(spec, prompt)
    else:
        data = await _gemini(spec, prompt)

    return GeneratedImage(
        data=data,
        model=spec.key(),
        prompt=prompt,
        cost_usd=spec.cost_per_image,
    )


async def _openai(spec: ImageSpec, prompt: str) -> bytes:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": spec.model,
                "prompt": prompt,
                "size": spec.request_size,
                "n": 1,
            },
        )
    if resp.status_code >= 400:
        raise ImageUnavailable(f"OpenAI images returned {resp.status_code}: {resp.text[:200]}")

    payload = resp.json()
    try:
        entry = payload["data"][0]
    except (KeyError, IndexError) as exc:
        raise ImageUnavailable(f"OpenAI images returned no image: {str(payload)[:200]}") from exc

    # gpt-image-1 always returns base64; the older DALL·E models default to a URL
    # unless asked otherwise. Handling both means switching model is a config change.
    if "b64_json" in entry:
        return base64.b64decode(entry["b64_json"])
    if "url" in entry:
        return await _fetch(entry["url"])
    raise ImageUnavailable(f"OpenAI images returned neither b64_json nor url: {str(entry)[:200]}")


async def _gemini(spec: ImageSpec, prompt: str) -> bytes:
    settings = get_settings()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{spec.model}:predict"
        f"?key={settings.gemini_api_key}"
    )
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            url,
            json={
                "instances": [{"prompt": prompt}],
                "parameters": {"sampleCount": 1, "aspectRatio": spec.request_size},
            },
        )
    if resp.status_code >= 400:
        # The key is in the query string, so the URL must never reach a log line.
        raise ImageUnavailable(f"Imagen returned {resp.status_code}: {resp.text[:200]}")

    payload = resp.json()
    try:
        return base64.b64decode(payload["predictions"][0]["bytesBase64Encoded"])
    except (KeyError, IndexError) as exc:
        raise ImageUnavailable(f"Imagen returned no image: {str(payload)[:200]}") from exc


async def _fetch(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise ImageUnavailable(f"could not download generated image: {resp.status_code}")
    return resp.content

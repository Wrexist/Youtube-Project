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
from dataclasses import dataclass, replace
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
    #: Which Gemini API this model answers on. The two are not interchangeable:
    #: the Imagen family is served by `:predict` with an `instances`/`parameters`
    #: body, and the Gemini image models by `:generateContent` with `contents`,
    #: returning the image as an inline part rather than a prediction. Asking one
    #: on the other's endpoint is a 404.
    endpoint: Literal["predict", "generateContent"] = "predict"

    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class GeneratedImage:
    data: bytes
    model: str
    prompt: str
    cost_usd: float


CATALOGUE: dict[ImageProvider, ImageSpec] = {
    # Gemini 3 Pro Image, which Google also ships under the name Nano Banana Pro.
    # The best thumbnail model available here and, at $0.134 against GPT Image 1's
    # $0.19, the cheaper of the two as well — so the quality/price trade-off the
    # old preference order was making no longer exists to be made.
    #
    # Prices are per generated image from Google's published table (1K/2K
    # standard tier), read rather than remembered: `cost_per_image` feeds the
    # per-video budget ceiling, and a guessed number there silently moves the
    # ceiling for every render.
    "gemini": ImageSpec(
        "gemini",
        "gemini-3-pro-image",
        "Gemini 3 Pro Image",
        cost_per_image=0.134,
        request_size="16:9",
        endpoint="generateContent",
    ),
    "openai": ImageSpec(
        "openai", "gpt-image-1", "GPT Image 1", cost_per_image=0.19, request_size="1536x1024"
    ),
}


def selected() -> ImageSpec | None:
    """Which image model to use, or None to fall back to a composed placeholder.

    "auto" resolves against whichever key is actually present rather than asking the
    user to name a provider they may not have. Gemini 3 Pro Image is preferred when
    both are set: it is the stronger thumbnail model — which is the thing that
    decides whether the video gets clicked — *and* it is cheaper, $0.134 against
    $0.19, and it returns 16:9 natively where GPT Image returns 3:2 and has to be
    cropped. It used to be the other way round, on a catalogue where the OpenAI
    entry was the better model and the trade-off was real.

    An explicitly named provider whose key is missing is a misconfiguration, not a
    reason to silently degrade — it warns, because a user who typed
    STUDIO_IMAGE_PROVIDER=gemini wants to know why nothing generated.
    """
    settings = get_settings()
    choice = settings.image_provider

    if choice == "none":
        return None

    # Keyed off CATALOGUE so its order *is* the preference order for "auto".
    # These used to be two separate literals, and reordering the catalogue to put
    # the better model first therefore changed nothing at all — "auto" kept
    # choosing the one this dict happened to list first.
    available: dict[ImageProvider, str] = {
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
    }
    keys: dict[ImageProvider, str] = {p: available.get(p, "") for p in CATALOGUE}

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


#: What to ask each provider for, per output shape. Thumbnails are always 16:9,
#: but generated b-roll has to match the video it is cut into — a 16:9 still in a
#: 9:16 timeline is centre-cropped to its middle third, which throws away the
#: composition the prompt asked for.
_SIZES: dict[str, dict[str, str]] = {
    "16:9": {"gemini": "16:9", "openai": "1536x1024"},
    "9:16": {"gemini": "9:16", "openai": "1024x1536"},
    "1:1": {"gemini": "1:1", "openai": "1024x1024"},
}


async def generate(prompt: str, *, aspect: str = "16:9") -> GeneratedImage | None:
    """One generated image, or None if generation is not configured.

    None is a supported outcome, not a failure: a first run with no keys still
    produces a thumbnail, just a flat one. A provider that is configured but *fails*
    is different — that raises, because silently returning the placeholder would hide
    a broken key behind a thumbnail that looks merely unstyled.

    `aspect` defaults to 16:9, which is every thumbnail. B-roll passes the video's
    own shape.
    """
    spec = selected()
    if spec is None:
        return None

    size = _SIZES.get(aspect, _SIZES["16:9"])[spec.provider]
    spec = replace(spec, request_size=size)

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
    # The key goes in a header, not `?key=`. Query strings are the one part of an
    # HTTPS request that leaks by default — httpx's own logging, any proxy access
    # log, an exception whose `request.url` gets formatted. CLAUDE.md's rule is that
    # secrets are never logged, and a URL-embedded key makes that a property of
    # every caller instead of a property of this function.
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{spec.model}:{spec.endpoint}"

    if spec.endpoint == "generateContent":
        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                # Both required. Without `responseModalities` the model answers
                # with a description of the picture instead of the picture, and
                # without the aspect ratio it returns square, which a 16:9
                # thumbnail then has to crop most of the subject out of.
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": spec.request_size},
            },
        }
    else:
        body = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1, "aspectRatio": spec.request_size},
        }

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            url, headers={"x-goog-api-key": settings.gemini_api_key or ""}, json=body
        )
    if resp.status_code >= 400:
        raise ImageUnavailable(f"{spec.label} returned {resp.status_code}: {resp.text[:200]}")

    payload = resp.json()
    if spec.endpoint == "predict":
        try:
            return base64.b64decode(payload["predictions"][0]["bytesBase64Encoded"])
        except (KeyError, IndexError) as exc:
            raise ImageUnavailable(f"{spec.label} returned no image: {str(payload)[:200]}") from exc

    # generateContent returns parts, of which the image is one — a refusal or a
    # safety block comes back as a text part and a 200, so "no inline data" is a
    # normal response to handle rather than a malformed one.
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            data = part.get("inlineData") or part.get("inline_data")
            if data and data.get("data"):
                return base64.b64decode(data["data"])
    raise ImageUnavailable(f"{spec.label} returned no image: {str(payload)[:300]}")


async def _fetch(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise ImageUnavailable(f"could not download generated image: {resp.status_code}")
    return resp.content

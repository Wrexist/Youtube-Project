"""TikTok, within what its APIs actually permit.

**Read this before extending the module.** TikTok's official APIs do not hand you
other people's videos, and no amount of engineering changes that:

  * **Display API** returns only the *authenticated user's own* content. That is
    Lane A, and it is the whole of what this module can fetch.
  * **Research API** is restricted to approved academic researchers.
  * Neither serves raw media for arbitrary creators.

So there is no `search_all_of_tiktok()` here and there must never be one. What
exists instead:

  * `own_videos()` — Lane A. The authenticated user's posts, with a real media URL.
  * `trends()` — public trend signal (hashtags, keywords) for *discovery*, which is
    a different problem from acquisition. Knowing that a topic is moving requires
    no video files and infringes nothing.

Lane B (campaign clipping) does not come through here at all. A campaign supplies
its own source material and its own content rules; the rights basis is enrolment,
recorded through `repurpose/rights.py`, and the media arrives by whatever route the
campaign specifies.

**Unverified against the live API.** This is reviewed code, not proven code — the
same status `PLAN.md` records for the YouTube publishing path, and for the same
reason: it needs credentials nobody has yet. Everything degrades to an empty list
when unconfigured rather than raising, so a keyless install still renders the
screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from engine.settings import get_settings

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"

#: Only what Lane A needs. `video.list` is the read scope for the user's own posts;
#: `user.info.basic` is what makes the handle available to attribute a clip to.
#: Deliberately minimal — a scope granted is a scope that can be misused later.
SCOPES = ("user.info.basic", "video.list")

#: The fields worth asking for. `download_addr` is absent on purpose: it is not
#: offered by the Display API, and a field list containing it fails the whole
#: request rather than degrading.
VIDEO_FIELDS = (
    "id",
    "title",
    "video_description",
    "duration",
    "cover_image_url",
    "share_url",
    "embed_link",
    "like_count",
    "comment_count",
    "share_count",
    "view_count",
    "create_time",
)

TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


class TikTokUnavailable(Exception):
    """No credentials, or TikTok refused. Never fatal to a screen render."""


@dataclass
class Clip:
    """One TikTok, as discovery records it.

    `caption` is untrusted — it reaches an LLM prompt and must go through
    `untrusted.fence()` at every interpolation site. Held raw here so the fencing
    happens where the prompt is built rather than being applied twice.
    """

    external_id: str
    url: str
    caption: str = ""
    creator_handle: str = ""
    duration_s: float = 0.0
    cover_url: str = ""
    hashtags: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    #: Where the media can be fetched, when the lane permits it at all. Empty for
    #: anything but the authenticated user's own posts.
    media_url: str = ""

    def as_dict(self) -> dict:
        return {
            "platform": "tiktok",
            "external_id": self.external_id,
            "url": self.url,
            "caption": self.caption,
            "creator_handle": self.creator_handle,
            "duration_s": self.duration_s,
            "hashtags": self.hashtags,
            "stats": self.stats,
        }


def configured() -> bool:
    settings = get_settings()
    return bool(settings.tiktok_client_key and settings.tiktok_client_secret)


def authorize_url(redirect_uri: str, state: str) -> str:
    """Where the browser goes to grant Lane A access."""
    if not configured():
        raise TikTokUnavailable("TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET are not set")
    from urllib.parse import urlencode

    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_key": get_settings().tiktok_client_key,
                "scope": ",".join(SCOPES),
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
    )


async def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    """Trade an authorisation code for tokens.

    The refresh token goes to `crypto.encrypt` before storage, like YouTube's —
    it is durable access to an account and there is no column for a plaintext one.
    """
    if not configured():
        raise TikTokUnavailable("TikTok credentials are not configured")

    settings = get_settings()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        # The body carries TikTok's own error description, which is the only thing
        # that distinguishes a bad code from a mismatched redirect URI.
        raise TikTokUnavailable(f"token exchange failed ({response.status_code}): {response.text}")
    return response.json()


async def own_videos(access_token: str, *, limit: int = 20) -> list[Clip]:
    """Lane A: the authenticated user's own posts.

    The only path in this module that yields media. Returns an empty list rather
    than raising when TikTok is unreachable — discovery failing must not take the
    screen with it.
    """
    if not access_token:
        return []

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{API}/video/list/",
                params={"fields": ",".join(VIDEO_FIELDS)},
                json={"max_count": min(limit, 20)},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 — any failure here is "no clips today"
        logger.warning("TikTok video.list failed: {}", exc)
        return []

    videos = (payload.get("data") or {}).get("videos") or []
    return [_clip(v) for v in videos]


def _clip(raw: dict) -> Clip:
    """One API row as a `Clip`.

    Defensive throughout: TikTok omits fields rather than nulling them, and a
    KeyError here would lose the whole sweep over one video missing a view count.
    """
    caption = str(raw.get("video_description") or raw.get("title") or "")
    return Clip(
        external_id=str(raw.get("id") or ""),
        url=str(raw.get("share_url") or ""),
        caption=caption,
        duration_s=float(raw.get("duration") or 0),
        cover_url=str(raw.get("cover_image_url") or ""),
        hashtags=_hashtags(caption),
        stats={
            "views": int(raw.get("view_count") or 0),
            "likes": int(raw.get("like_count") or 0),
            "comments": int(raw.get("comment_count") or 0),
            "shares": int(raw.get("share_count") or 0),
        },
        # `embed_link` is not a media file, but it is the only addressable handle
        # the Display API gives for the user's own post. The acquire stage decides
        # what to do with it; this module does not pretend it is an MP4.
        media_url=str(raw.get("embed_link") or ""),
    )


def _hashtags(caption: str) -> list[str]:
    import re

    return re.findall(r"#\w+", caption)


async def trends(region: str = "US", *, limit: int = 20) -> list[str]:
    """Trending terms for discovery. No video files involved.

    Public trend data, which is a genuinely different thing from acquisition:
    knowing a topic is moving infringes nothing and needs no rights basis.

    Returns an empty list when unconfigured or unreachable. That is honest — it
    means the freshness component scores zero rather than being invented, the same
    contract `ideas.score_idea` already has for `trending_terms`.
    """
    settings = get_settings()
    if not settings.tiktok_trends_url:
        return []

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                settings.tiktok_trends_url,
                params={"region": region, "limit": limit},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("TikTok trends unavailable: {}", exc)
        return []

    # Shape-tolerant: this endpoint is configured rather than fixed, so a caller
    # may point it at Creative Center, a proxy, or a cached export. Anything that
    # yields strings is accepted; anything else is dropped rather than crashing.
    items = payload.get("terms") or payload.get("data") or payload
    out: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                term = item.get("term") or item.get("hashtag_name") or item.get("name")
                if term:
                    out.append(str(term))
    return out[:limit]

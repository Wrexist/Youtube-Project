"""YouTube Data API v3.

Every call goes through the quota ledger before it is made. Uploads are resumable,
because a 500MB upload that fails at 90% and restarts from zero has still spent its
1,600 units.

Refresh tokens are encrypted at rest and never logged, never returned from an
endpoint, and never included in an error payload.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from engine.crypto import decrypt, encrypt
from engine.quota import ledger
from engine.settings import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"

SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # required for captions
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)

CHUNK = 8 * 1024 * 1024


class YouTubeError(Exception):
    pass


class ChannelDisconnected(YouTubeError):
    """The refresh token is dead. Re-auth is required; retrying will not help."""


@dataclass
class Credentials:
    refresh_token_encrypted: str
    access_token: str = ""
    expires_at: datetime | None = None
    channel_id: str = ""

    @property
    def is_fresh(self) -> bool:
        # 60s of headroom so a token doesn't expire mid-upload.
        return (
            bool(self.access_token)
            and bool(self.expires_at)
            and (self.expires_at > datetime.now(UTC) + timedelta(seconds=60))
        )


# ── OAuth ───────────────────────────────────────────────────────────────────


def authorize_url(state: str) -> str:
    s = get_settings()
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # without this there is no refresh token
        "prompt": "consent",  # forces one to be issued on re-auth
        "state": state,
    }
    return f"{AUTH_URL}?{httpx.QueryParams(params)}"


async def exchange_code(code: str) -> Credentials:
    s = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri": s.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise YouTubeError(f"token exchange failed: {resp.status_code}")

    payload = resp.json()
    if "refresh_token" not in payload:
        raise YouTubeError(
            "Google returned no refresh token — the user has authorised before. "
            "Revoke access and retry, or re-request with prompt=consent."
        )

    return Credentials(
        refresh_token_encrypted=encrypt(payload["refresh_token"]),
        access_token=payload["access_token"],
        expires_at=datetime.now(UTC) + timedelta(seconds=payload["expires_in"]),
    )


async def refresh(creds: Credentials) -> Credentials:
    """Refresh on demand. Never scheduled — an access token lasts an hour and
    scheduling refreshes just adds a way to be wrong."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "refresh_token": decrypt(creds.refresh_token_encrypted),
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "grant_type": "refresh_token",
            },
        )

    if resp.status_code == 400 and "invalid_grant" in resp.text:
        # Password change or revoked access. Retrying is pointless.
        raise ChannelDisconnected(
            "the channel's authorisation was revoked; the user must reconnect"
        )
    if resp.status_code != 200:
        raise YouTubeError(f"token refresh failed: {resp.status_code}")

    payload = resp.json()
    creds.access_token = payload["access_token"]
    creds.expires_at = datetime.now(UTC) + timedelta(seconds=payload["expires_in"])
    return creds


# ── client ──────────────────────────────────────────────────────────────────


class YouTube:
    def __init__(self, creds: Credentials) -> None:
        self.creds = creds

    async def _headers(self) -> dict[str, str]:
        if not self.creds.is_fresh:
            await refresh(self.creds)
        return {"Authorization": f"Bearer {self.creds.access_token}"}

    async def _call(self, method: str, url: str, operation: str, **kwargs: Any) -> httpx.Response:
        ledger.check(operation)  # refuse before spending, not after
        headers = {**(await self._headers()), **kwargs.pop("headers", {})}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
        ledger.record(operation, channel_id=self.creds.channel_id)

        if resp.status_code >= 400:
            raise YouTubeError(f"{operation} failed ({resp.status_code}): {resp.text[:300]}")
        return resp

    async def search(self, query: str, limit: int = 20) -> list[dict]:
        """Competitor mining. 100 units — the caller must cache (7 days standing policy)."""
        resp = await self._call(
            "GET",
            f"{API}/search",
            "search.list",
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": limit,
                "order": "relevance",
            },
        )
        return resp.json().get("items", [])

    async def upload(
        self,
        video_path: Path,
        *,
        title: str,
        description: str,
        tags: list[str],
        category_id: str = "27",
        privacy: str = "private",
        publish_at: datetime | None = None,
        made_for_kids: bool = False,
        language: str = "en",
        on_progress=None,
    ) -> str:
        """Resumable upload. Returns the video id.

        `publish_at` requires `privacy="private"` — setting a publish time on an
        already-public video does nothing at all, silently.
        """
        if publish_at and privacy != "private":
            raise ValueError(
                "scheduled publishing requires privacyStatus='private'; "
                "publishAt is ignored on public videos"
            )

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags,
                "categoryId": category_id,
                "defaultLanguage": language,
                "defaultAudioLanguage": language,
            },
            "status": {
                "privacyStatus": privacy,
                # Omitting this is a common cause of silently rejected uploads.
                "selfDeclaredMadeForKids": made_for_kids,
                "embeddable": True,
                "license": "youtube",
            },
        }
        if publish_at:
            body["status"]["publishAt"] = publish_at.astimezone(UTC).isoformat()

        size = (await asyncio.to_thread(video_path.stat)).st_size
        ledger.check("videos.insert")

        # 1. Open the resumable session.
        headers = {
            **(await self._headers()),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            init = await client.post(
                UPLOAD,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers=headers,
                content=json.dumps(body),
            )
        if init.status_code not in (200, 201):
            raise YouTubeError(f"could not open upload session: {init.text[:300]}")

        session_url = init.headers["Location"]

        # 2. Push chunks, resuming from the last byte the server confirmed.
        offset = 0
        async with httpx.AsyncClient(timeout=None) as client:
            with video_path.open("rb") as fh:
                while offset < size:
                    # Read off the event loop. An 8MB read from disk is tens of
                    # milliseconds, and stalling the loop mid-upload also stalls
                    # every other job's progress stream.
                    chunk = await asyncio.to_thread(_read_at, fh, offset, CHUNK)
                    end = offset + len(chunk) - 1
                    resp = await client.put(
                        session_url,
                        content=chunk,
                        headers={
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                        },
                    )

                    if resp.status_code in (200, 201):
                        ledger.record(
                            "videos.insert", channel_id=self.creds.channel_id, note=title[:60]
                        )
                        return resp.json()["id"]

                    if resp.status_code == 308:  # incomplete; continue
                        rng = resp.headers.get("Range")
                        offset = int(rng.split("-")[1]) + 1 if rng else offset + len(chunk)
                        if on_progress:
                            await on_progress(offset / size, "uploading")
                        continue

                    if resp.status_code in (500, 502, 503, 504, 429):
                        logger.warning(
                            "transient {} during upload; retrying chunk", resp.status_code
                        )
                        continue

                    # 4xx is deterministic — retrying burns quota for nothing.
                    raise YouTubeError(f"upload failed ({resp.status_code}): {resp.text[:300]}")

        raise YouTubeError("upload ended without the API returning a video id")

    async def set_thumbnail(self, video_id: str, image: Path) -> None:
        if image.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("thumbnail exceeds the 2MB limit")
        await self._call(
            "POST",
            "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
            "thumbnails.set",
            params={"videoId": video_id},
            content=image.read_bytes(),
            headers={"Content-Type": "image/jpeg"},
        )

    async def upload_captions(
        self, video_id: str, srt: Path, *, language: str = "en", name: str = ""
    ) -> None:
        """A real caption track. Burned-in subtitles do nothing for search; this does."""
        meta = {
            "snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}
        }
        files = {
            "metadata": (None, json.dumps(meta), "application/json"),
            "file": ("captions.srt", srt.read_bytes(), "application/octet-stream"),
        }
        await self._call(
            "POST",
            "https://www.googleapis.com/upload/youtube/v3/captions",
            "captions.insert",
            params={"part": "snippet"},
            files=files,
        )

    async def add_to_playlist(self, video_id: str, playlist_id: str) -> None:
        await self._call(
            "POST",
            f"{API}/playlistItems",
            "playlistItems.insert",
            params={"part": "snippet"},
            json={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        )

    async def reschedule(self, video_id: str, publish_at: datetime) -> None:
        """50 units — cheap enough that drag-to-reschedule can be used freely."""
        await self._call(
            "PUT",
            f"{API}/videos",
            "videos.update",
            params={"part": "status"},
            json={
                "id": video_id,
                "status": {
                    "privacyStatus": "private",
                    "publishAt": publish_at.astimezone(UTC).isoformat(),
                },
            },
        )

    async def processing_status(self, video_id: str) -> str:
        resp = await self._call(
            "GET",
            f"{API}/videos",
            "videos.list",
            params={"part": "processingDetails", "id": video_id},
        )
        items = resp.json().get("items", [])
        return items[0]["processingDetails"]["processingStatus"] if items else "unknown"


def _read_at(fh, offset: int, size: int) -> bytes:
    """Positioned read, run in a thread by the uploader."""
    fh.seek(offset)
    return fh.read(size)

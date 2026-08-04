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
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from engine.crypto import DecryptionFailed, decrypt, encrypt
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

#: YouTube's own field limits, enforced once more at the wire. `workflows/seo.py`
#: shapes a generated package to fit these, but it is not the only way a value
#: reaches here: a hand edit, a job restored from before a limit changed, or a
#: caller that never ran the SEO chain all arrive unchecked, and the API rejects the
#: whole insert — after the resumable session has already booked its 1,600 units.
#:
#: Deliberately not imported from `workflows.seo`: a provider that imports a
#: workflow inverts the layering every other provider here respects.
TITLE_MAX = 100
#: Bytes, not characters. The API measures the description in bytes and this one is
#: assembled with em dashes, so a 5,000-*character* description can be over.
DESCRIPTION_MAX_BYTES = 5000
TAGS_TOTAL_MAX = 500


def _tag_cost(tag: str) -> int:
    """A tag's share of the 500-character budget. A spaced tag is serialised
    quoted — `foo,"bar baz"` — so it costs two more than its own length plus the
    comma. Mirrors `workflows.seo.tag_cost`; see the note above on why it is not
    imported."""
    return len(tag) + (3 if " " in tag else 1)


def _clamp_tags(tags: list[str]) -> list[str]:
    """Drop tags once the budget is spent, rather than letting the API refuse them all.

    Greedy from the front: the SEO chain has already ordered them by value, and a
    caller that did not is no worse off than it was.
    """
    out: list[str] = []
    used = 0
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue  # one empty string rejects the entire field
        cost = _tag_cost(tag)
        if used + cost > TAGS_TOTAL_MAX:
            continue
        out.append(tag)
        used += cost
    if len(out) != len(tags):
        logger.warning(
            "trimmed {} tag(s) to fit YouTube's 500-character budget", len(tags) - len(out)
        )
    return out


def _clamp_utf8(text: str, limit: int) -> str:
    """Cut to `limit` UTF-8 bytes without splitting a character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    logger.warning("description is {} bytes; truncating to {}", len(encoded), limit)
    return encoded[:limit].decode("utf-8", errors="ignore")


#: Consecutive transient failures on the same chunk before giving up. The loop was
#: a bare `continue` with no cap and no delay, so a persistent 503 spun as fast as
#: the network allowed, forever, against an API that was asking us to slow down.
MAX_CHUNK_RETRIES = 6
#: Doubling from 2s: 2, 4, 8, 16, 32, 60. Capped so a Retry-After of an hour does
#: not park a render for an hour.
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 60.0


def _backoff(attempt: int, retry_after: str | None) -> float:
    """How long to wait before re-sending a chunk.

    Honours `Retry-After` when the server sends one — on a 429 that header is the
    server telling us the answer, and ignoring it earns a longer ban.
    """
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), BACKOFF_MAX_S)
        except ValueError:
            pass  # it may be an HTTP-date; fall through to the exponential
    return min(BACKOFF_BASE_S * (2 ** (attempt - 1)), BACKOFF_MAX_S)


def _resume_offset(header: str | None, offset: int) -> int:
    """Where to resume from after a 308.

    The server's `Range` is authoritative and may confirm less than we sent, so it
    is the only thing worth believing. Everything else resumes from where we were.

    That fallback used to be `offset + chunk_len` — assume the whole chunk landed.
    It is wrong in exactly the case it fires: Google's resumable-upload protocol
    says a 308 with *no* `Range` means zero bytes have been persisted, so assuming
    the chunk landed skips it, and the finished video is missing a hole in the
    middle. Re-sending a range the server already has is free — the protocol is
    idempotent per byte range — while skipping one is unrecoverable.
    """
    if header:
        # Strictly `bytes=<first>-<last>`. Splitting on "-" and taking [1] happens to
        # produce a *number* for plenty of malformed headers too ("0-1-2-3" yields
        # 2), and a plausible-but-wrong offset silently corrupts the uploaded file —
        # worse than not parsing it at all.
        match = re.fullmatch(r"\s*bytes\s*=\s*(\d+)\s*-\s*(\d+)\s*", header)
        if match:
            return int(match.group(2)) + 1
        logger.warning("unparseable Range header {!r}; re-sending from byte {}", header, offset)
    return offset


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
        if not self.access_token or not self.expires_at:
            return False
        # A naive `expires_at` is coerced rather than compared. It reaches here from
        # any store with no timezone type — SQLite is the one in the box — and
        # comparing naive with aware raises TypeError, which is not "stale" and does
        # not reach `refresh()`. Coercing means a naive row is simply judged on its
        # merits and, when it loses, self-heals through the refresh path.
        expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=UTC)
        return expires > datetime.now(UTC) + timedelta(seconds=60)


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
    try:
        refresh_token = decrypt(creds.refresh_token_encrypted)
    except DecryptionFailed as exc:
        # The key changed, or the row was restored without storage/.secret_key
        # beside it. Indistinguishable from a revoked grant from here, and the fix
        # is the same one — so say that, rather than surfacing a 500 nobody can act on.
        raise ChannelDisconnected(
            "this channel's stored credential cannot be decrypted with the current "
            "key — reconnect the channel to store a fresh one"
        ) from exc

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
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
        await ledger.record(operation, channel_id=self.creds.channel_id)

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

    async def subscriber_count(self) -> int | None:
        """The channel's subscriber total. One quota unit.

        Analytics reports `subscribersGained`, which is a *delta* — summing it over
        a window gives you the change, not the count, and it ignores both losses and
        every subscriber the channel had before the window opened. The Partner
        Programme threshold is a total, so it has to come from the Data API.

        Returns None rather than raising when the channel has hidden its subscriber
        count: `hiddenSubscriberCount` is a setting an operator can turn on, and a
        dashboard that 500s because of a privacy preference is worse than one that
        says the number is unavailable.
        """
        resp = await self._call(
            "GET",
            f"{API}/channels",
            "channels.list",
            params={"part": "statistics", "mine": "true"},
        )
        items = resp.json().get("items", [])
        if not items:
            return None
        stats = items[0].get("statistics", {})
        if stats.get("hiddenSubscriberCount"):
            return None
        raw = stats.get("subscriberCount")
        return int(raw) if raw is not None else None

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
                "title": title[:TITLE_MAX],
                "description": _clamp_utf8(description, DESCRIPTION_MAX_BYTES),
                "tags": _clamp_tags(tags),
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

        # 1. Reserve the units, then open the resumable session.
        #
        # Reserved, not merely checked. This used to be `check_fresh()` here and
        # `record()` after the session opened — two separate awaits with a network
        # round trip between them. Two publishes starting together both re-read the
        # same "8,400 of 10,000 spent", both passed, and both then booked 1,600, so
        # the ledger sailed past its own ceiling and Google refused the second
        # upload after it had already been charged for. `reserve()` re-reads, checks
        # and books under one lock, so the second caller sees the first one's spend.
        #
        # Booked *before* the session rather than after it because Google charges
        # when the session is created, whatever happens to the upload afterwards —
        # recording only on success meant every failed upload spent real quota the
        # ledger never saw.
        entry = await ledger.reserve(
            "videos.insert", channel_id=self.creds.channel_id, note=title[:60]
        )

        headers = {
            **(await self._headers()),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                init = await client.post(
                    UPLOAD,
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    headers=headers,
                    content=json.dumps(body),
                )
        except Exception:
            # No session, so nothing to charge for. Refunding is the *only* safe
            # window: past this point the session exists and its units are spent
            # whether the upload finishes or not.
            await ledger.refund(entry)
            raise
        if init.status_code not in (200, 201):
            await ledger.refund(entry)
            raise YouTubeError(f"could not open upload session: {init.text[:300]}")

        session_url = init.headers["Location"]

        # 2. Push chunks, resuming from the last byte the server confirmed.
        offset = 0
        # Transient failures are retried, but not forever and not immediately. This
        # was a bare `continue` in a `while True`: a persistent 503 spun as fast as
        # the network allowed, indefinitely, hammering the API that was already
        # asking us to back off.
        attempts = 0
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
                        return resp.json()["id"]

                    if resp.status_code == 308:  # incomplete; continue
                        moved = _resume_offset(resp.headers.get("Range"), offset)
                        if moved > offset:
                            offset = moved
                            attempts = 0  # progress was made
                            if on_progress:
                                await on_progress(offset / size, "uploading")
                            continue

                        # A 308 that confirmed nothing. Re-sending is correct, but
                        # a server that keeps confirming nothing has to hit the same
                        # ceiling as a 503 — otherwise the loop spins on one chunk
                        # for as long as the API keeps answering.
                        attempts += 1
                        if attempts > MAX_CHUNK_RETRIES:
                            raise YouTubeError(
                                f"upload stalled at byte {offset} of {size}: "
                                f"{MAX_CHUNK_RETRIES} consecutive 308s confirmed no bytes"
                            )
                        await asyncio.sleep(_backoff(attempts, resp.headers.get("Retry-After")))
                        continue

                    if resp.status_code in (500, 502, 503, 504, 429):
                        attempts += 1
                        if attempts > MAX_CHUNK_RETRIES:
                            raise YouTubeError(
                                f"upload gave up after {MAX_CHUNK_RETRIES} consecutive "
                                f"{resp.status_code}s at byte {offset} of {size}"
                            )
                        delay = _backoff(attempts, resp.headers.get("Retry-After"))
                        logger.warning(
                            "transient {} during upload; retrying chunk in {:.1f}s (attempt {}/{})",
                            resp.status_code,
                            delay,
                            attempts,
                            MAX_CHUNK_RETRIES,
                        )
                        await asyncio.sleep(delay)
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

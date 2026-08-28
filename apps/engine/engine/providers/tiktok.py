"""TikTok, within what its APIs actually permit.

**Read this before extending the module.** TikTok's official APIs do not hand you
other people's videos, and no amount of engineering changes that:

  * **Display API** returns only the *authenticated user's own* content. That is
    Lane A, and it is the whole of what this module can fetch.
  * **Research API** is restricted to approved academic researchers.
  * Neither serves raw media for arbitrary creators.

So there is no `search_all_of_tiktok()` here and there must never be one. This is
an API client, not a scraper: every request is authenticated as the account whose
content it returns.

Lane B (campaign clipping) does not come through here at all. A campaign supplies
its own source material and its own content rules; the rights basis is enrolment,
recorded through `repurpose/rights.py`.

## Reliability

Five things this has to get right, and each one is a way the integration silently
stops working rather than failing loudly:

1. **TikTok answers 200 with an error body**, in either of *two* shapes.
   `{"error": {"code": "access_token_invalid", ...}}` from the Display API and
   `{"error": "invalid_grant", "error_description": ...}` from the OAuth token
   endpoint both arrive with HTTP 200, so `raise_for_status()` sees nothing wrong.
   Every response goes through `_unwrap`, which is the only place that decides
   whether a call succeeded, and `_error_in`, which is the only place that knows
   there are two shapes.
2. **Access tokens last 24 hours.** Without refresh the connection works on the
   day you set it up and is dead by the next sweep — the failure mode that looks
   like "the feature stopped working" a day after anyone tested it.
3. **`video.list` is paginated.** It returns at most 20 rows plus a cursor. A
   sweep that ignores `has_more` sees the newest 20 posts and nothing else, which
   is indistinguishable from a working sweep on a small account.
4. **An expired token and an empty account look identical** if every failure
   returns `[]`. Auth failures raise, so the screen can say *reconnect*; only
   genuinely transient trouble degrades to an empty list.
5. **Refreshing twice at once destroys the connection.** The refresh token is
   rotated, so two callers spending the same stored one leave the second holding
   a token TikTok has retired — and its failure overwrites the first one's good
   token. Serialising that is `repository.tiktok_access_token`'s job, not this
   module's; `refresh()` here is deliberately a plain call with no state of its
   own, and must stay that way or the lock will be guarding the wrong thing.

**Unverified against the live API.** Reviewed code, not proven code — the same
status `PLAN.md` records for the YouTube publish path, and for the same reason: it
needs credentials nobody has yet. The error-code strings in `_AUTH_ERRORS` are the
likeliest thing to be wrong, so `_unwrap` also treats any 401 as an auth failure
regardless of the code it carries.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

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

USER_FIELDS = ("open_id", "display_name", "username")

TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

#: TikTok's own page size for `video.list`. Asking for more is rejected, so a
#: bigger sweep is more pages rather than a bigger page.
PAGE_SIZE = 20

#: Stop paginating here however much `has_more` insists. A runaway cursor — which
#: a malformed response can produce — would otherwise sweep forever.
MAX_PAGES = 25

#: Attempts per request, and the base for exponential backoff.
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0

#: Refresh a token this long before it actually expires. A sweep that starts with
#: 30 seconds left on the clock finishes with an invalid one.
REFRESH_MARGIN = timedelta(minutes=5)

#: Error codes that mean "the human must re-authorise". Raised rather than
#: swallowed, so the screen can say *reconnect* instead of showing an empty grid.
#: Not exhaustive — `_unwrap` also treats any 401 as an auth failure, which is the
#: backstop for a code not on this list.
#:
#: The first group is the Display API's vocabulary; the last two are the OAuth
#: token endpoint's, which speaks plain OAuth 2.0 error names instead.
#: `invalid_grant` is what a dead refresh token comes back as, and it is the single
#: most likely error this integration will ever see in production. `invalid_client`
#: is deliberately *not* here: wrong credentials in `.env` are a configuration fault
#: and reconnecting would not fix them.
_AUTH_ERRORS = frozenset(
    {
        "access_token_invalid",
        "access_token_expired",
        "refresh_token_invalid",
        "refresh_token_expired",
        "scope_not_authorized",
        "scope_permission_missed",
        "invalid_grant",
        "access_denied",
    }
)


class TikTokUnavailable(Exception):
    """TikTok cannot be reached or refused. Transient, or a configuration fault."""


class TikTokAuthExpired(TikTokUnavailable):
    """The connection is dead and only a human re-authorising fixes it.

    Its own type because the remedy is different: everything else is worth a
    retry, and this is worth a button that says *Reconnect*.
    """


@dataclass
class Tokens:
    """What an OAuth exchange or refresh returns.

    `refresh_token` is encrypted before storage by the caller — it is durable
    access to an account and there is no column for a plaintext one, the same rule
    `tables.Channel` states for YouTube.
    """

    access_token: str = ""
    refresh_token: str = ""
    open_id: str = ""
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    scope: str = ""

    @property
    def expired(self) -> bool:
        """True inside `REFRESH_MARGIN` of expiry, not just after it."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at - REFRESH_MARGIN


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


#: Values that are present, non-empty, and still not a key. `.env.example` ships
#: with prompts in these shapes, and `configured()` — which only asks whether the
#: string is non-empty — waves every one of them through to TikTok, which answers
#: `client_key` on a page that cannot say which of the two credentials it meant.
_PLACEHOLDERS = frozenset(
    {
        "your_client_key_here",
        "your_client_secret_here",
        "your-client-key",
        "your-client-secret",
        "changeme",
        "change-me",
        "xxx",
        "todo",
    }
)


def credential_problem() -> str | None:
    """Why TikTok will refuse these credentials, before the browser is sent there.

    TikTok's authorize page reports a bad key as `client_key` in small print on
    an otherwise blank error screen, with no indication of *how* it is bad — and
    by then the operator has left the app, so the app cannot tell them anything
    either. Everything catchable is worth catching on this side of that trip.

    Deliberately not a format check on the key itself. TikTok's keys currently
    start `aw` and run about twenty characters, but that is an observation about
    today's keys rather than a documented contract, and a validator that refuses
    a *valid* future key is worse than the error page it replaces. Only the three
    faults that are unambiguous are named here.
    """
    settings = get_settings()
    key = settings.tiktok_client_key
    secret = settings.tiktok_client_secret

    if not key or not secret:
        return (
            "TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET are not both set in .env. "
            "Both come from the same app on developers.tiktok.com."
        )
    if key.lower() in _PLACEHOLDERS or secret.lower() in _PLACEHOLDERS:
        return (
            "TIKTOK_CLIENT_KEY or TIKTOK_CLIENT_SECRET is still the placeholder "
            "from .env.example. Replace both with the values from your app on "
            "developers.tiktok.com."
        )
    if key == secret:
        return (
            "TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET are the same value — one "
            "of them has been pasted over the other. They are two different "
            "strings on the app's credentials page."
        )
    return None


def credential_hint() -> str:
    """The configured client key, in full, so it can be compared with TikTok's page.

    Shown rather than masked, which was the wrong call the first time. A client
    key is an OAuth *client identifier*: it is public by definition, it travels
    in the query string of the URL the browser is sent to, and TikTok displays it
    on the app's own page. The **secret** is the one that must never be rendered,
    and it never is.

    Masking it made the one thing this exists for impossible — comparing what the
    engine holds against what TikTok shows, character by character, which is how
    a transposed field or a key from a different app is actually found.
    """
    return get_settings().tiktok_client_key or "not set"


#: PKCE verifier alphabet and length, from RFC 7636 §4.1 — the unreserved
#: characters, 43 to 128 of them. `token_urlsafe` emits exactly this alphabet
#: (it adds `-` and `_`), so no filtering is needed; 64 bytes of entropy lands
#: comfortably inside the range at 86 characters.
_VERIFIER_BYTES = 64


def code_verifier() -> str:
    """A fresh PKCE verifier. One per sign-in attempt, never reused."""
    return secrets.token_urlsafe(_VERIFIER_BYTES)


def code_challenge(verifier: str) -> str:
    """The challenge TikTok expects for a verifier — **hex**, not base64url.

    This is where TikTok departs from RFC 7636, and it departs silently: the RFC
    says `BASE64URL(SHA256(verifier))` and every other provider we talk to means
    that, so the obvious implementation is accepted at the authorize step and then
    fails at the token exchange with a flat `invalid_grant` that names nothing.
    TikTok's own Login Kit documentation is explicit — "You must use hex encoding
    of SHA256 to generate the code challenge from the code verifier" — and their
    example is `CryptoJS.SHA256(code_verifier).toString(CryptoJS.enc.Hex)`.

    `code_challenge_method` is still sent as `S256`, which is the *hash* name and
    is the only value TikTok accepts. The encoding is not part of that name, which
    is exactly why this is easy to get wrong.
    """
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def authorize_url(redirect_uri: str, state: str, verifier: str) -> str:
    """Where the browser goes to grant Lane A access.

    `verifier` is the PKCE code verifier for this attempt; the caller keeps it
    and hands it back to `exchange_code`. It is not optional: TikTok refuses the
    authorize request outright without a `code_challenge`, on a page that says
    "Something went wrong" and lists the missing parameter in small print. That
    was the state of this integration until someone tried it against the real
    API — the simulated tests could not have found it, because a fixture answers
    whatever it is asked.
    """
    if not configured():
        raise TikTokUnavailable("TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET are not set")

    url = (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_key": get_settings().tiktok_client_key,
                "scope": ",".join(SCOPES),
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
    )
    # Logged in full, deliberately. Every value in it is public — the client key
    # is an OAuth client identifier, and the rest is a scope list, a redirect and
    # a hash. The secret and the verifier are not here and never will be.
    #
    # It is logged because TikTok's refusals name a parameter and nothing else
    # ("client_key"), on a page the operator reaches after leaving the app. The
    # request that produced it is the only other evidence there is, and without
    # this line it existed nowhere.
    logger.info("tiktok authorize url: {}", url)
    return url


# ── the transport ───────────────────────────────────────────────────────────


def _error_in(payload: dict) -> tuple[str, str]:
    """The error code and message, from either shape TikTok uses.

    **There are two, and they are not interchangeable.** The Display API nests the
    error in an object:

        {"data": {...}, "error": {"code": "access_token_invalid", "message": "..."}}

    The OAuth token endpoint returns plain OAuth 2.0 instead, where `error` is a
    *string* and the human-readable part lives in a sibling field:

        {"error": "invalid_grant", "error_description": "Refresh token is invalid"}

    Reading the second shape as if it were the first is `"invalid_grant".get(...)`,
    an `AttributeError` — which is neither of this module's exception types, so
    every caller's `except TikTokUnavailable` misses it. A dead refresh token, the
    most ordinary failure this integration has, therefore surfaced as a 500 in the
    browser tab instead of a "reconnect the account" the operator could act on.
    """
    raw = payload.get("error")
    if isinstance(raw, dict):
        return str(raw.get("code") or ""), str(raw.get("message") or "")
    if isinstance(raw, str):
        return raw, str(payload.get("error_description") or "")
    return "", ""


def _unwrap(response: httpx.Response) -> dict:
    """The only place that decides whether a TikTok call succeeded.

    **TikTok answers 200 with an error body.** `raise_for_status()` is therefore
    not enough on its own and never was: an invalid access token arrives as a
    perfectly ordinary 200 carrying `{"error": {"code": "access_token_invalid"}}`.
    A client that trusts the status code treats that as a successful empty sweep.
    """
    try:
        payload = response.json()
    except ValueError as exc:
        raise TikTokUnavailable(
            f"TikTok returned {response.status_code} with a body that is not JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise TikTokUnavailable(
            f"TikTok returned {response.status_code} with a body that is not an object"
        )

    code, message = _error_in(payload)
    # "ok" is success; the field is absent on some endpoints, which is also success.
    failed = bool(code) and code != "ok"

    if response.status_code == 401 or (failed and code in _AUTH_ERRORS):
        raise TikTokAuthExpired(
            f"TikTok rejected the credentials ({code or response.status_code}). "
            "Reconnect the account to continue."
        )
    if failed or response.status_code >= 400:
        raise TikTokUnavailable(
            f"TikTok error {code or response.status_code}: {message or response.text[:200]}"
        )
    return payload


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict:
    """One call, retried on the failures that are worth retrying.

    Retries 429 and 5xx; never retries an auth failure, which will fail
    identically three times and only delay the "reconnect" the operator needs to
    see. `Retry-After` is honoured when TikTok sends one, because guessing a
    backoff shorter than the one it asked for is how a rate limit becomes a ban.
    """
    last: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last = TikTokUnavailable(f"could not reach TikTok: {exc}")
        else:
            if response.status_code == 429 or response.status_code >= 500:
                last = TikTokUnavailable(
                    f"TikTok returned {response.status_code}"
                    + (" (rate limited)" if response.status_code == 429 else "")
                )
                retry_after = _retry_after(response)
            else:
                return _unwrap(response)  # raises TikTokAuthExpired without retrying
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(retry_after or BACKOFF_BASE_S * 2 ** (attempt - 1))
                continue

        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BACKOFF_BASE_S * 2 ** (attempt - 1))

    raise last or TikTokUnavailable("TikTok request failed")


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        # Capped: a header asking us to wait an hour should surface as a failure
        # the operator sees, not as a request that hangs for an hour.
        return min(float(raw), 30.0)
    except ValueError:
        return None


# ── tokens ──────────────────────────────────────────────────────────────────


def _tokens_from(payload: dict) -> Tokens:
    """A token response as `Tokens`.

    v2 returns the fields at the top level; some older documentation shows them
    nested under `data`. Both are accepted because getting this wrong produces an
    empty token that fails later and further away.
    """
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    body = body or {}
    now = datetime.now(UTC)

    def seconds(name: str) -> datetime | None:
        raw = body.get(name)
        try:
            return now + timedelta(seconds=int(raw)) if raw else None
        except (TypeError, ValueError):
            return None

    return Tokens(
        access_token=str(body.get("access_token") or ""),
        refresh_token=str(body.get("refresh_token") or ""),
        open_id=str(body.get("open_id") or ""),
        expires_at=seconds("expires_in"),
        refresh_expires_at=seconds("refresh_expires_in"),
        scope=str(body.get("scope") or ""),
    )


async def exchange_code(code: str, redirect_uri: str, verifier: str) -> Tokens:
    """Trade an authorisation code for tokens.

    `verifier` must be the same one whose challenge went out with the authorize
    request. TikTok recomputes the hash and refuses the exchange if it disagrees
    — which is the whole point of PKCE, and also means a mismatch here fails with
    a bare `invalid_grant` rather than anything that names the cause.
    """
    if not configured():
        raise TikTokUnavailable("TikTok credentials are not configured")

    settings = get_settings()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        payload = await _request(
            client,
            "POST",
            TOKEN_URL,
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    tokens = _tokens_from(payload)
    if not tokens.access_token:
        raise TikTokUnavailable("TikTok returned no access token for that code")
    return tokens


async def refresh(refresh_token: str) -> Tokens:
    """Trade a refresh token for a new access token.

    Access tokens last 24 hours. Without this the integration works on the day it
    is set up and is dead by the next sweep — which reads as "the feature broke"
    rather than as "a token expired", and is the single likeliest way this stops
    working unattended.
    """
    if not configured():
        raise TikTokUnavailable("TikTok credentials are not configured")
    if not refresh_token:
        raise TikTokAuthExpired("no refresh token stored — reconnect the account")

    settings = get_settings()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        payload = await _request(
            client,
            "POST",
            TOKEN_URL,
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    tokens = _tokens_from(payload)
    if not tokens.access_token:
        raise TikTokAuthExpired("TikTok refused the refresh token — reconnect the account")
    # TikTok rotates the refresh token on some grants and omits it on others.
    # Carrying the old one forward when none comes back keeps a working connection
    # working; overwriting it with "" would end the connection at the next expiry.
    if not tokens.refresh_token:
        tokens.refresh_token = refresh_token
    return tokens


# ── reads ───────────────────────────────────────────────────────────────────


async def creator_handle(access_token: str) -> str:
    """The authenticated account's @handle.

    Fetched once per sweep rather than per clip. Without it `creator_handle` is
    empty on every row, which quietly disables two things that read it: the
    on-screen credit, and `clip_source` in the feedback loop — so the most
    actionable attribution dimension would group every video under "".
    """
    if not access_token:
        return ""

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        payload = await _request(
            client,
            "GET",
            f"{API}/user/info/",
            params={"fields": ",".join(USER_FIELDS)},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    user = (payload.get("data") or {}).get("user") or {}
    handle = str(user.get("username") or user.get("display_name") or "")
    return f"@{handle}" if handle and not handle.startswith("@") else handle


async def own_videos(access_token: str, *, limit: int = 20) -> list[Clip]:
    """Lane A: the authenticated user's own posts, across as many pages as needed.

    Two rules about failure, and they are in tension on purpose:

      * **A sweep that collected nothing raises.** Returning `[]` for an outage is
        the confusion this module exists to avoid — an empty account and a broken
        connection would look identical to every caller.
      * **A sweep that collected something returns it.** One bad page late in a
        sweep must not discard the pages that worked, and the operator is better
        served by 40 clips and a warning than by an exception and none.
    """
    if not access_token:
        return []

    collected: list[Clip] = []
    cursor: int | None = None
    handle = ""
    failure: TikTokUnavailable | None = None

    try:
        handle = await creator_handle(access_token)
    except TikTokAuthExpired:
        raise
    except TikTokUnavailable as exc:
        # A missing handle costs a credit line, not the sweep.
        logger.warning("could not read the TikTok handle: {}", exc)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for page in range(MAX_PAGES):
            body: dict[str, Any] = {"max_count": min(PAGE_SIZE, max(1, limit - len(collected)))}
            if cursor is not None:
                body["cursor"] = cursor

            try:
                payload = await _request(
                    client,
                    "POST",
                    f"{API}/video/list/",
                    params={"fields": ",".join(VIDEO_FIELDS)},
                    json=body,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )
            except TikTokAuthExpired:
                raise
            except TikTokUnavailable as exc:
                logger.warning("TikTok video.list failed on page {}: {}", page + 1, exc)
                failure = exc
                break

            data = payload.get("data") or {}
            videos = data.get("videos") or []
            collected.extend(_clip(v, creator=handle) for v in videos)

            if len(collected) >= limit or not data.get("has_more") or not videos:
                break

            next_cursor = data.get("cursor")
            # A cursor that does not advance is how a malformed response turns a
            # sweep into an infinite loop that re-reads page one forever.
            if next_cursor is None or next_cursor == cursor:
                break
            cursor = next_cursor

    if not collected and failure is not None:
        raise failure
    return collected[:limit]


def _clip(raw: dict, *, creator: str = "") -> Clip:
    """One API row as a `Clip`.

    Defensive throughout: TikTok omits fields rather than nulling them, and a
    KeyError here would lose the whole sweep over one video missing a view count.
    """
    caption = str(raw.get("video_description") or raw.get("title") or "")
    return Clip(
        external_id=str(raw.get("id") or ""),
        url=str(raw.get("share_url") or ""),
        caption=caption,
        creator_handle=creator,
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

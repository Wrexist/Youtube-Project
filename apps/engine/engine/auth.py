"""The bearer-token gate for the engine's HTTP surface.

KNOWN-ISSUES.md §6 named this the largest open gap: the engine was completely
unauthenticated, and CORS names `http://localhost:3000` as a trusted origin —
which means *any* dev server a developer happens to run on that port, not
specifically Studio's web app, was trusted too. `STUDIO_API_TOKEN` closes that:
set it, and every route except `/health` refuses a request that does not carry
it. Unset — the default, so every install that predates this and every test in
this suite keeps working untouched — the gate is a no-op and the engine is
exactly as open as it always was.

**Why `fastapi.security` and not a bare `Header()`/`Query()` dependency.** A
`Header()`/`Query()` parameter shows up in the OpenAPI document as an ordinary
parameter on every gated route — a hundred-odd near-identical entries that
`packages/contracts` would then generate a type for on every operation, which is
noise nothing reads (the credential is attached by `lib/engine.ts` directly,
never through a generated per-call parameter). `HTTPBearer`/`APIKeyQuery` are
FastAPI's actual vocabulary for this: they render once as a named
`securityScheme` and each gated route references it by name, which is both the
correct OpenAPI shape for "this route needs a credential" and a vastly smaller
diff in `packages/contracts/openapi.json`.

**Why a dependency and not middleware.** `CORSMiddleware` has to run outermost so
a 401 still carries CORS headers back to a browser that is allowed to see it, and
so a CORS preflight (`OPTIONS`, no `Authorization` header, no body) is answered by
`CORSMiddleware` itself before anything of ours runs. A `Depends` sits inside all
of that for free; hand-rolled ASGI middleware would have to reimplement the
ordering to get the same thing.

**Why some routes accept a query parameter.** Three routes are reached by the
browser in ways that cannot carry a header at all: `EventSource` (job progress),
and plain `<img>`/`<video>`/`<a href>` (rendered files). `require_token_flexible`
accepts the token as `?token=` for exactly those, because refusing them outright
would just move the outage from "unauthenticated" to "the UI cannot show its own
renders" — see `apps/web/lib/engine.ts` for what carries it. Every other route —
every write, and everything that reaches a stored credential — takes the header
only, and only a page running on the server (never the client bundle) can supply
that one. A token that can ride in a URL is weaker than one that cannot: it can
end up in a server log or a browser history entry. That is a real, accepted
trade for a single-user, localhost-only tool (CLAUDE.md: "do not expose the
engine") whose alternative was routing every render and every progress stream
through a hand-rolled streaming proxy — a second render pipeline's worth of code
to keep a value out of a URL that only this machine's own browser ever sees.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyQuery, HTTPAuthorizationCredentials, HTTPBearer

from engine.settings import get_settings

#: `auto_error=False` on both: a missing credential is not an error by itself when
#: the token is unset, so `_matches` — not FastAPI's default 401/403 — decides that.
_bearer_scheme = HTTPBearer(auto_error=False)
_query_scheme = APIKeyQuery(name="token", auto_error=False)


def _matches(supplied: str) -> bool:
    token = get_settings().api_token
    if not token:
        return True
    # Constant-time: a token compared with `==` leaks, one byte at a time, how much
    # of a guess was right through how long the comparison took to fail.
    return bool(supplied) and hmac.compare_digest(supplied, token)


async def require_token(
    # noqa'd rather than restructured: ruff's B008 hardcodes an exemption list for
    # fastapi.Depends/Query/Header/Path/Body/Cookie/Form/File and has not caught up
    # to fastapi.Security, which is otherwise exactly the same pattern — a default
    # evaluated once at route registration, not per request.
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),  # noqa: B008
) -> None:
    """The default gate: header only. Applied to every route except `/health` and
    the handful that also accept `require_token_flexible` instead."""
    if not _matches(credentials.credentials if credentials else ""):
        raise HTTPException(401, "missing or invalid bearer token")


async def require_token_flexible(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),  # noqa: B008
    token: str | None = Security(_query_scheme),  # noqa: B008
) -> None:
    """The gate for routes a browser reaches without a header: header or `?token=`."""
    supplied = credentials.credentials if credentials else (token or "")
    if not _matches(supplied):
        raise HTTPException(401, "missing or invalid bearer token")

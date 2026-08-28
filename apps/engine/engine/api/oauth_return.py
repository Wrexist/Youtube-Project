"""Where a provider's callback sends the browser once it is done.

Both OAuth round trips — Google's and TikTok's — end in a redirect issued by
this engine to a page in the web app. That page used to be the screen the
operator started from, carrying the outcome in its query string. It worked, but
only when the consent page had replaced the app's own window.

Consent is opened in a popup now, the way every large integration does it, and
a popup that ends up rendering a second full copy of Studio is a worse outcome
than the redirect it replaced: the window the person is actually looking at —
the one behind the popup, with the button they pressed — never hears anything.

So the redirect lands on `/connected`, a page with one job: hand the outcome to
the window that opened it and get out of the way. When there is no such window
(the popup was blocked and consent loaded in place, or the browser's
Cross-Origin-Opener-Policy severed the link) it forwards to the screen and the
query string this function used to point at directly. Nothing downstream of
that had to change.

The parameters are deliberately flat and provider-agnostic: `/connected` is not
the place to know what a TikTok failure looks like as opposed to a Google one.
"""

from urllib.parse import quote

from fastapi.responses import RedirectResponse

#: Screens a sign-in may return to. An allowlist rather than "any path", because
#: this value arrives from a query string and lands in a `Location` header —
#: reflecting it unchecked is an open redirect, which is worth more to a phisher
#: than the account being connected.
RETURN_TO = frozenset({"setup", "repurpose"})


def consent_return(
    provider: str,
    *,
    ok: bool,
    reason: str = "",
    source: str = "",
    return_to: str = "setup",
) -> RedirectResponse:
    """Redirect the consent window to the handoff page.

    `source` distinguishes our own failure from the provider's. The Setup screen
    renders the two completely differently and must not have to guess: it used
    to infer it from whether the text contained a space, which mislabels exactly
    the case that matters — a bare `ConnectError` is one word.
    """
    from engine.settings import get_settings

    screen = return_to if return_to in RETURN_TO else "setup"
    query = [
        f"provider={quote(provider)}",
        f"status={'ok' if ok else 'error'}",
        f"return_to={screen}",
    ]
    if reason:
        query.append(f"reason={quote(reason)}")
    if source:
        query.append(f"source={quote(source)}")

    web = get_settings().web_url.rstrip("/")
    # 303 rather than 302: the browser arrived here by GET and leaves by GET, and
    # saying so explicitly keeps a re-issued callback from being re-POSTed.
    return RedirectResponse(f"{web}/connected?{'&'.join(query)}", status_code=303)

"""Where an OAuth callback sends the browser.

One function, and every property of the URL it builds matters to something:
the path decides whether the popup can report back at all, `return_to` is an
open-redirect surface, and `source` is what stops the Setup screen rendering our
own exception under "that is Google's own error code".
"""

from urllib.parse import parse_qs, urlparse

import pytest

from engine.api.oauth_return import consent_return
from engine.settings import get_settings


@pytest.fixture(autouse=True)
def _web(monkeypatch):
    monkeypatch.setenv("STUDIO_WEB_URL", "http://studio.local:3000/")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def parts(response):
    url = urlparse(response.headers["location"])
    return url, parse_qs(url.query)


def test_it_lands_on_the_handoff_page_not_a_screen():
    """The popup must not become a second full copy of Studio.

    Consent opens in a 520-pixel window; redirecting that window to `/setup`
    renders the whole app inside it while the window the operator is actually
    looking at hears nothing at all.
    """
    url, _ = parts(consent_return("youtube", ok=True))
    assert url.path == "/connected"


def test_it_honours_the_configured_web_address():
    """Hardcoding localhost:3000 was wrong for every install but the developer's."""
    url, _ = parts(consent_return("tiktok", ok=True))
    assert url.scheme == "http"
    assert url.netloc == "studio.local:3000"
    # One slash, not two: `web_url` may or may not carry a trailing one.
    assert url.path == "/connected"


def test_the_outcome_is_a_word_not_a_provider_specific_parameter():
    """`/connected` has no business knowing what a TikTok failure looks like."""
    _, ok = parts(consent_return("tiktok", ok=True))
    _, bad = parts(consent_return("tiktok", ok=False, reason="nope"))
    assert ok["status"] == ["ok"]
    assert bad["status"] == ["error"]
    assert ok["provider"] == ["tiktok"]


def test_a_reason_is_escaped_rather_than_pasted_in():
    reason = "that sign-in link has expired — try again"
    _, query = parts(consent_return("tiktok", ok=False, reason=reason))
    assert query["reason"] == [reason]


def test_an_empty_reason_is_left_out_entirely():
    """So the page can tell "no reason given" from "the reason was blank"."""
    _, query = parts(consent_return("youtube", ok=True))
    assert "reason" not in query
    assert "source" not in query


def test_our_own_failure_is_marked_as_ours():
    """The Setup screen renders these two completely differently.

    It used to infer whose failure it was from whether the text contained a
    space, which is wrong for exactly the case that matters: `str(exc)` is empty
    for a bare `ConnectError`, so the reason becomes the one-word class name and
    was rendered as one of Google's own error codes.
    """
    _, query = parts(consent_return("youtube", ok=False, reason="ConnectError", source="engine"))
    assert query["source"] == ["engine"]


@pytest.mark.parametrize("screen", ["setup", "repurpose"])
def test_a_known_screen_is_carried_through(screen):
    _, query = parts(consent_return("tiktok", ok=True, return_to=screen))
    assert query["return_to"] == [screen]


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.test",
        "//evil.test",
        "../../etc/passwd",
        "setup?x=1",
        "",
    ],
)
def test_an_unknown_screen_falls_back_rather_than_being_reflected(hostile):
    """This value arrives from a query string and ends up in a `Location` header.

    Reflecting it unchecked is an open redirect, which is worth more to a phisher
    than the account being connected: a link that walks someone through a real
    Google consent screen and lands them on a page of the attacker's choosing.
    """
    _, query = parts(consent_return("tiktok", ok=False, return_to=hostile))
    assert query["return_to"] == ["setup"]


def test_it_redirects_by_get():
    """303, not 302: the browser arrived by GET and must leave by GET."""
    assert consent_return("youtube", ok=True).status_code == 303

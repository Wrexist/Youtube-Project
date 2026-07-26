"""Channel identity: validation, limits, and handle rules.

**YouTube has no API for creating a channel.** There is no `channels.insert`. A
channel comes into existence only through the YouTube UI, attached to a Google
account or a Brand Account, and no amount of engineering changes that.

So this module does the part that *can* be automated — designing the whole identity
and validating it against YouTube's real constraints — and the launch workflow hands
back a short manual checklist for the four or five clicks only a human can do.
Everything after those clicks is applied over the API.

The limits below are enforced in code for the same reason the SEO limits are: a name
that fails at `channels.update` time has already wasted the user's attention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# YouTube's actual limits.
NAME_MAX = 100
HANDLE_MIN = 3
HANDLE_MAX = 30
DESCRIPTION_MAX = 1000
KEYWORDS_MAX = 500  # total characters, quoted phrases included

# Banner renders at wildly different crops per device. Only the centre survives TV,
# desktop, and mobile alike, so the logo and any text must live inside it.
BANNER_SIZE = (2048, 1152)
BANNER_SAFE_AREA = (1235, 338)
BANNER_MAX_BYTES = 6 * 1024 * 1024
AVATAR_SIZE = (800, 800)

HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,30}$")

# Handles that read as impersonation or invite a trademark complaint. Not
# exhaustive — it catches the obvious own-goals before someone builds a brand on one.
RISKY_HANDLE_TERMS = frozenset(
    """official youtube google netflix disney bbc cnn nasa apple amazon tesla
    verified news""".split()
)


@dataclass
class Problem:
    field: str
    message: str
    fatal: bool = True


@dataclass
class ChannelIdentity:
    name: str = ""
    handle: str = ""
    tagline: str = ""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    niche: str = ""
    audience: str = ""
    country: str = "US"
    language: str = "en"

    # Design direction, consumed by the asset generator.
    avatar_concept: str = ""
    banner_concept: str = ""
    palette: list[str] = field(default_factory=list)

    def keywords_string(self) -> str:
        """YouTube stores channel keywords as one space-separated string, with
        multi-word phrases quoted."""
        return " ".join(f'"{k}"' if " " in k else k for k in self.keywords)

    def summary(self) -> str:
        return f"@{self.handle} · {self.name}"


def normalize_handle(raw: str) -> str:
    """Turn a proposed name into something YouTube will accept as a handle."""
    handle = re.sub(r"[^A-Za-z0-9._-]", "", raw.replace(" ", ""))
    handle = handle.strip("._-")
    if len(handle) < HANDLE_MIN:
        handle = (handle + "channel")[:HANDLE_MAX]
    return handle[:HANDLE_MAX]


def validate(identity: ChannelIdentity) -> list[Problem]:
    """Every way this identity would be rejected or would age badly.

    Fatal problems block the launch. Non-fatal ones are shown and can be accepted —
    they are judgement calls, not API constraints.
    """
    problems: list[Problem] = []

    if not identity.name:
        problems.append(Problem("name", "the channel needs a name"))
    elif len(identity.name) > NAME_MAX:
        problems.append(
            Problem("name", f"name is {len(identity.name)} characters; the limit is {NAME_MAX}")
        )

    if not HANDLE_PATTERN.match(identity.handle or ""):
        problems.append(
            Problem(
                "handle",
                f"handle must be {HANDLE_MIN}-{HANDLE_MAX} characters of letters, "
                f"numbers, periods, underscores or hyphens",
            )
        )
    else:
        lowered = identity.handle.lower()
        hit = next((t for t in RISKY_HANDLE_TERMS if t in lowered), None)
        if hit:
            problems.append(
                Problem(
                    "handle",
                    f"“{hit}” in a handle reads as impersonation and invites a "
                    f"trademark complaint once the channel has any reach",
                    fatal=False,
                )
            )

    if len(identity.description) > DESCRIPTION_MAX:
        problems.append(
            Problem(
                "description",
                f"description is {len(identity.description)} characters; "
                f"the limit is {DESCRIPTION_MAX}",
            )
        )
    elif len(identity.description) < 100:
        problems.append(
            Problem(
                "description",
                "the About text is very short — it is a ranking signal and the first "
                "thing a potential subscriber reads",
                fatal=False,
            )
        )

    keyword_chars = len(identity.keywords_string())
    if keyword_chars > KEYWORDS_MAX:
        problems.append(
            Problem(
                "keywords",
                f"keywords total {keyword_chars} characters; the limit is {KEYWORDS_MAX}",
            )
        )
    if len(identity.keywords) < 5:
        problems.append(Problem("keywords", "fewer than 5 channel keywords", fatal=False))

    if not identity.niche:
        problems.append(Problem("niche", "no niche defined — the whole system keys off it"))

    return problems


def trim_keywords(
    keywords: list[str], *, suggestions: list[str] | None = None
) -> list[str]:
    """Drop keywords past the 500-character budget, keeping the highest-value ones.

    When autocomplete suggestions are provided the list is re-ranked by position
    in that list before trimming — lower index means YouTube surfaced the query
    first, which is the closest free proxy for search volume.  Keywords that do
    not appear in the autocomplete data are sorted to the end.

    The budget is filled greedily: a keyword that doesn't fit is skipped rather
    than stopping the loop, so shorter high-value terms aren't lost because one
    long term came first.
    """
    if suggestions:
        suggestion_rank: dict[str, int] = {s.lower(): i for i, s in enumerate(suggestions)}

        def _keyword_rank(kw: str) -> int:
            lower = kw.lower()
            if lower in suggestion_rank:
                return suggestion_rank[lower]
            # Word-level subset match: all words of the keyword appear in a
            # suggestion phrase or vice versa.  Substring matching is intentionally
            # avoided — "known" is a substring of "unknown", which would give
            # unrelated terms a false high rank.
            kw_words = set(lower.split())
            for phrase, rank in suggestion_rank.items():
                phrase_words = set(phrase.split())
                if kw_words <= phrase_words or phrase_words <= kw_words:
                    return rank
            return len(suggestions)  # not in autocomplete — lowest priority

        keywords = sorted(keywords, key=_keyword_rank)

    out: list[str] = []
    used = 0
    for keyword in keywords:
        cost = len(keyword) + (3 if " " in keyword else 1)  # mirrors keywords_string()
        if used + cost <= KEYWORDS_MAX:
            out.append(keyword)
            used += cost
    return out


def branding_payload(identity: ChannelIdentity) -> dict:
    """The `channels.update` body.

    Note what is *not* here: the channel name and handle. Neither is settable through
    the Data API — both are changed in YouTube Studio by hand. Only the description,
    keywords, and country can be pushed.
    """
    return {
        "brandingSettings": {
            "channel": {
                "description": identity.description[:DESCRIPTION_MAX],
                "keywords": identity.keywords_string()[:KEYWORDS_MAX],
                "country": identity.country,
                "defaultLanguage": identity.language,
            }
        }
    }


# The steps no API can perform. Kept in code rather than in a document so the UI
# renders exactly what the system could not do, and never silently implies otherwise.
MANUAL_STEPS = [
    {
        "id": "create",
        "title": "Create the channel",
        "detail": "youtube.com → your avatar → Create a channel. Use a Brand Account "
        "so ownership can be transferred later without moving the Google account.",
        "url": "https://www.youtube.com/create_channel",
    },
    {
        "id": "handle",
        "title": "Claim the handle",
        "detail": "YouTube Studio → Customisation → Basic info. Handles are "
        "first-come; claim it before doing anything else.",
        "url": "https://studio.youtube.com",
    },
    {
        "id": "name",
        "title": "Set the channel name",
        "detail": "Same screen. The Data API cannot set a channel's name — this one "
        "is unavoidable.",
        "url": "https://studio.youtube.com",
    },
    {
        "id": "verify",
        "title": "Verify by phone",
        "detail": "Unlocks custom thumbnails and videos longer than 15 minutes. "
        "Without it, half this system's output cannot be uploaded as intended.",
        "url": "https://www.youtube.com/verify",
    },
    {
        "id": "oauth",
        "title": "Connect the channel to Studio",
        "detail": "Grants upload and analytics access. Everything after this point is automated.",
        "url": None,
    },
]

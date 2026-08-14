"""One episode, packaged natively for each platform it goes to.

Practitioners posting the same clip to several places change three things per
platform: **the hook text, the caption, and the first frame**. That is not
detection evasion and it is worth being clear about the difference — each
platform's audience, aspect and first-frame treatment genuinely differ, and a
YouTube title read aloud on TikTok reads as a cross-post because it *is* one.

**What this does not do.** It does not re-encode. The picture is the same file;
what changes is the text wrapped around it and which moment the thumbnail or
cover frame is taken from. Re-encoding three variants to change a caption would
triple the render cost for something the platform renders itself.

**The honest limit.** Only YouTube has a publish path in this repository
(`workflows/publish.py`). Every other package here is *export* — text for a human
to paste, or for a cross-poster that does not exist yet. Saying so in the type
(`publishable`) keeps a future caller from assuming a package can be posted just
because it can be produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: YouTube's real limits, restated here rather than imported so this module reads
#: as one table. `channel.py` and `workflows/seo.py` own enforcement for the
#: publish path; these are the truncation points for the *other* platforms, where
#: nothing else checks.
LIMITS = {
    "youtube_shorts": {"title": 100, "caption": 5_000, "tags": 500},
    "tiktok": {"title": 0, "caption": 2_200, "tags": 0},
    "reels": {"title": 0, "caption": 2_200, "tags": 0},
}

#: How many hashtags read as native rather than as spam, per platform. YouTube
#: puts them under the title and three is the visible maximum; the short-form
#: apps treat them as the topic model's input and tolerate more.
HASHTAGS = {"youtube_shorts": 3, "tiktok": 5, "reels": 5}

#: Aim well under the hard title limit — past roughly 60 characters YouTube
#: truncates in most surfaces, so the tail is written for nobody.
TITLE_TARGET = 60


@dataclass
class Package:
    """One platform's packaging of the same video."""

    platform: str
    title: str
    caption: str
    hashtags: list[str] = field(default_factory=list)
    #: Seconds into the video for the cover frame. Taken from the teased hook when
    #: there is one — the moment chosen precisely because it earns attention.
    cover_at_s: float = 0.0
    #: Whether this repository can actually post it. False everywhere but YouTube,
    #: and stated rather than implied.
    publishable: bool = False

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "title": self.title,
            "caption": self.caption,
            "hashtags": self.hashtags,
            "cover_at_s": round(self.cover_at_s, 2),
            "publishable": self.publishable,
        }


def package(
    *,
    titles: list[str],
    description: str,
    tags: list[str],
    thesis: str = "",
    hook: dict | None = None,
    platforms: tuple[str, ...] = ("youtube_shorts", "tiktok", "reels"),
) -> list[Package]:
    """Build one package per platform from a finished episode's metadata.

    `titles` is the scored variant list, best first. Each platform takes a
    *different* one where there are enough to go round — the same headline in
    three feeds is the cross-post smell, and the variants already exist because
    the SEO chain generates several by design.
    """
    if not titles:
        titles = [thesis[:TITLE_TARGET]] if thesis else ["Untitled"]

    cover_at = float((hook or {}).get("at_s") or 0.0) if (hook or {}).get("teased") else 0.0
    hashtags = _hashtags(tags)

    out: list[Package] = []
    for index, platform in enumerate(platforms):
        limits = LIMITS.get(platform, LIMITS["tiktok"])
        # Round-robin rather than always the best: the top variant goes to the
        # platform that is actually published to, and the rest get the others.
        title = titles[index % len(titles)]

        if platform == "youtube_shorts":
            out.append(
                Package(
                    platform=platform,
                    title=_clamp(titles[0], limits["title"]),
                    caption=_clamp(description, limits["caption"]),
                    hashtags=hashtags[: HASHTAGS.get(platform, 3)],
                    cover_at_s=cover_at,
                    publishable=True,
                )
            )
            continue

        # The short-form apps have no title field at all: the first line of the
        # caption *is* the hook, so the title is promoted into it rather than
        # dropped. A package whose headline went nowhere is the commonest way a
        # cross-post lands with no hook.
        body = thesis or _first_sentence(description)
        caption = _clamp(f"{title}\n\n{body}".strip(), limits["caption"])
        out.append(
            Package(
                platform=platform,
                title="",
                caption=caption,
                hashtags=hashtags[: HASHTAGS.get(platform, 5)],
                cover_at_s=cover_at,
                publishable=False,
            )
        )

    return out


def _hashtags(tags: list[str]) -> list[str]:
    """Tags as hashtags, deduplicated, order preserved.

    Spaces are closed up rather than dropped, because "index funds" becoming
    "#index" loses the phrase that was the point of the tag.
    """
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        cleaned = "".join(ch for ch in str(tag) if ch.isalnum() or ch.isspace()).strip()
        if not cleaned:
            continue
        tag_text = "#" + "".join(word.capitalize() for word in cleaned.split())
        key = tag_text.lower()
        if key not in seen:
            seen.add(key)
            out.append(tag_text)
    return out


def _clamp(text: str, limit: int) -> str:
    """Trim to the limit at a word boundary, never mid-word.

    A caption cut mid-word reads as a broken app, which is the same reasoning
    `compose._wrap_caption` records for line breaks.
    """
    text = text.strip()
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    spaced = cut.rsplit(" ", 1)[0]
    return (spaced if len(spaced) > limit * 0.6 else cut).rstrip()


def _first_sentence(text: str) -> str:
    for stop in (". ", "! ", "? "):
        if stop in text:
            return text.split(stop, 1)[0] + stop.strip()
    return text.strip()

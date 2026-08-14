"""Per-platform packaging.

The distinction worth holding on to while reading these: varying the hook text,
caption and cover frame per platform is *native packaging*, not evasion. Each
platform's audience and first-frame treatment genuinely differ, and a YouTube
title pasted into TikTok reads as a cross-post because it is one.
"""

from __future__ import annotations

from engine.repurpose.packaging import HASHTAGS, LIMITS, package

TITLES = ["The first title", "A second angle", "A third framing"]
DESCRIPTION = "The opening line does the work. Everything after it is body copy."
TAGS = ["index funds", "investing", "personal finance", "index funds"]


def _by_platform(packages):
    return {p.platform: p for p in packages}


# ── what varies per platform ────────────────────────────────────────────────


def test_each_platform_gets_a_different_headline():
    """The same headline in three feeds is the cross-post smell, and the variants
    already exist because the SEO chain generates several by design."""
    packages = package(titles=TITLES, description=DESCRIPTION, tags=TAGS)

    headlines = [p.title or p.caption.splitlines()[0] for p in packages]
    assert len(set(headlines)) == len(headlines)


def test_youtube_gets_the_best_variant():
    """It is the one that is actually published to."""
    packages = _by_platform(package(titles=TITLES, description=DESCRIPTION, tags=TAGS))
    assert packages["youtube_shorts"].title == TITLES[0]


def test_the_short_form_apps_promote_the_title_into_the_caption():
    """They have no title field, so a headline left in one goes nowhere — the
    commonest way a cross-post lands with no hook."""
    packages = _by_platform(package(titles=TITLES, description=DESCRIPTION, tags=TAGS))

    tiktok = packages["tiktok"]
    assert tiktok.title == ""
    assert tiktok.caption.startswith(TITLES[1])


def test_the_thesis_carries_into_the_short_form_caption():
    packages = _by_platform(
        package(titles=TITLES, description=DESCRIPTION, tags=TAGS, thesis="one shared mistake")
    )
    assert "one shared mistake" in packages["reels"].caption


def test_the_cover_frame_comes_from_a_teased_hook():
    """The moment chosen precisely because it earns attention."""
    packages = package(
        titles=TITLES,
        description=DESCRIPTION,
        tags=TAGS,
        hook={"at_s": 12.5, "teased": True},
    )
    assert all(p.cover_at_s == 12.5 for p in packages)


def test_an_unteased_hook_leaves_the_cover_at_the_start():
    """Nothing to tease means the clip already opens on its best moment."""
    packages = package(
        titles=TITLES, description=DESCRIPTION, tags=TAGS, hook={"at_s": 9.0, "teased": False}
    )
    assert all(p.cover_at_s == 0.0 for p in packages)


# ── the honest limit ────────────────────────────────────────────────────────


def test_only_youtube_is_marked_publishable():
    """Only YouTube has a publish path here. Stating it in the type keeps a
    future caller from assuming a package can be posted because it exists."""
    packages = _by_platform(package(titles=TITLES, description=DESCRIPTION, tags=TAGS))

    assert packages["youtube_shorts"].publishable is True
    assert packages["tiktok"].publishable is False
    assert packages["reels"].publishable is False


# ── limits ──────────────────────────────────────────────────────────────────


def test_a_long_title_is_clamped_to_youtubes_limit():
    packages = _by_platform(package(titles=["x" * 500], description=DESCRIPTION, tags=TAGS))
    assert len(packages["youtube_shorts"].title) <= LIMITS["youtube_shorts"]["title"]


def test_a_long_caption_is_clamped_per_platform():
    packages = _by_platform(package(titles=TITLES, description="word " * 2000, tags=TAGS))

    assert len(packages["tiktok"].caption) <= LIMITS["tiktok"]["caption"]
    assert len(packages["youtube_shorts"].caption) <= LIMITS["youtube_shorts"]["caption"]


def test_clamping_never_cuts_mid_word():
    """A caption cut mid-word reads as a broken app — the same reasoning
    `compose._wrap_caption` records for line breaks."""
    packages = _by_platform(package(titles=TITLES, description="alpha " * 900, tags=TAGS))

    assert not packages["tiktok"].caption.endswith("alph")


# ── hashtags ────────────────────────────────────────────────────────────────


def test_tags_become_hashtags_without_losing_the_phrase():
    """ "index funds" becoming "#index" loses the phrase that was the point."""
    packages = _by_platform(package(titles=TITLES, description=DESCRIPTION, tags=TAGS))
    assert "#IndexFunds" in packages["tiktok"].hashtags


def test_duplicate_tags_appear_once():
    packages = _by_platform(package(titles=TITLES, description=DESCRIPTION, tags=TAGS))
    tags = packages["tiktok"].hashtags
    assert len(tags) == len({t.lower() for t in tags})


def test_hashtag_counts_are_per_platform():
    """Three is YouTube's visible maximum; the short-form apps feed them to their
    topic model and tolerate more."""
    many = [f"tag{i}" for i in range(20)]
    packages = _by_platform(package(titles=TITLES, description=DESCRIPTION, tags=many))

    assert len(packages["youtube_shorts"].hashtags) == HASHTAGS["youtube_shorts"]
    assert len(packages["tiktok"].hashtags) == HASHTAGS["tiktok"]


def test_junk_tags_are_dropped_rather_than_emitted_as_a_bare_hash():
    packages = _by_platform(package(titles=TITLES, description=DESCRIPTION, tags=["", "  ", "!!!"]))
    assert packages["tiktok"].hashtags == []


# ── degenerate input ────────────────────────────────────────────────────────


def test_no_titles_falls_back_to_the_thesis():
    packages = _by_platform(
        package(titles=[], description=DESCRIPTION, tags=TAGS, thesis="the shared mistake")
    )
    assert "the shared mistake" in packages["youtube_shorts"].title


def test_no_titles_and_no_thesis_still_produces_something_postable():
    packages = package(titles=[], description="", tags=[])
    assert all(p.title or p.caption for p in packages)


def test_fewer_titles_than_platforms_wraps_around():
    packages = package(titles=["only one"], description=DESCRIPTION, tags=TAGS)
    assert len(packages) == 3


def test_as_dict_is_serialisable():
    payload = package(titles=TITLES, description=DESCRIPTION, tags=TAGS)[0].as_dict()
    assert set(payload) == {
        "platform",
        "title",
        "caption",
        "hashtags",
        "cover_at_s",
        "publishable",
    }

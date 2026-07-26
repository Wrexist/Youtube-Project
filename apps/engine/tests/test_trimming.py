"""Tests for value-aware keyword and tag trimming (D4).

Both functions must:
  - stay within the character budget
  - prefer autocomplete-ranked terms over arbitrary ordering
  - fill greedily (skip a term that doesn't fit rather than stopping)
  - keep the exact-title tag pinned at the front (validate_tags only)
"""

from __future__ import annotations

import pytest

from engine.channel import KEYWORDS_MAX, trim_keywords
from engine.workflows.seo import TAGS_TOTAL_MAX, validate_tags


# ── trim_keywords ────────────────────────────────────────────────────────────


def _kw_cost(kw: str) -> int:
    """Mirror the cost model used inside trim_keywords."""
    return len(kw) + (3 if " " in kw else 1)


class TestTrimKeywordsBasic:
    def test_empty_list_returns_empty(self):
        assert trim_keywords([]) == []

    def test_all_fit_unchanged_order_without_suggestions(self):
        kws = ["cats", "dogs", "birds"]
        result = trim_keywords(kws)
        assert result == kws

    def test_budget_respected(self):
        # Build a list that definitely exceeds 500 chars.
        big = [f"keyword number {i}" for i in range(100)]
        result = trim_keywords(big)
        total = sum(_kw_cost(k) for k in result)
        assert total <= KEYWORDS_MAX

    def test_greedy_fill_skips_long_term(self):
        """A very long keyword in the middle must not block shorter ones after it."""
        short = "cats"
        long = "x" * 490  # would consume almost the whole budget by itself
        after = "dogs"

        # Without suggestions: sort order is preserved → long comes second.
        result = trim_keywords([short, long, after])
        # short fits, long fits only if it doesn't overshoot; after must also fit
        total = sum(_kw_cost(k) for k in result)
        assert total <= KEYWORDS_MAX
        # Both short terms must survive if the long one would bust the budget.
        if _kw_cost(short) + _kw_cost(long) > KEYWORDS_MAX:
            assert short in result
            assert after in result
            assert long not in result


class TestTrimKeywordsWithSuggestions:
    def test_high_autocomplete_rank_survives_cut(self):
        # suggestions[0] is the most valuable term.
        suggestions = ["bridge engineering", "bridge design", "suspension bridge"]
        # Construct enough keywords that the budget must cut some.
        keywords = [f"filler keyword {i}" for i in range(60)] + ["suspension bridge"]
        result = trim_keywords(keywords, suggestions=suggestions)
        # "suspension bridge" (rank 2) should be preferred over arbitrary fillers.
        assert "suspension bridge" in result

    def test_top_ranked_term_beats_earlier_list_position(self):
        suggestions = ["best term", "second term"]
        # "best term" appears last in keywords list.
        keywords = ["second term", "best term"]
        result = trim_keywords(keywords, suggestions=suggestions)
        # After ranking, "best term" should come first.
        assert result[0] == "best term"

    def test_terms_not_in_suggestions_sorted_last(self):
        suggestions = ["known term"]
        keywords = ["unknown term one", "known term", "unknown term two"]
        result = trim_keywords(keywords, suggestions=suggestions)
        assert result[0] == "known term"

    def test_partial_match_considered(self):
        # "bridge" is a substring of "bridge engineering" in suggestions.
        suggestions = ["bridge engineering"]
        keywords = ["other", "bridge"]
        result = trim_keywords(keywords, suggestions=suggestions)
        assert result[0] == "bridge"

    def test_budget_still_respected_with_suggestions(self):
        suggestions = [f"term {i}" for i in range(50)]
        keywords = [f"term {i}" for i in range(100)]
        result = trim_keywords(keywords, suggestions=suggestions)
        total = sum(_kw_cost(k) for k in result)
        assert total <= KEYWORDS_MAX


# ── validate_tags ────────────────────────────────────────────────────────────


def _tag_cost(tag: str) -> int:
    return len(tag) + 1  # comma separator


class TestValidateTagsBasic:
    def test_empty_list_returns_empty(self):
        assert validate_tags([]) == []

    def test_all_fit_preserved(self):
        tags = ["cats", "dogs", "birds"]
        assert validate_tags(tags) == tags

    def test_budget_respected(self):
        big = [f"tag number {i}" for i in range(100)]
        result = validate_tags(big)
        total = sum(_tag_cost(t) for t in result)
        assert total <= TAGS_TOTAL_MAX

    def test_greedy_fill_skips_oversized_tag(self):
        short = "cats"
        long = "x" * 490
        after = "dogs"
        result = validate_tags([short, long, after])
        total = sum(_tag_cost(t) for t in result)
        assert total <= TAGS_TOTAL_MAX
        if _tag_cost(short) + _tag_cost(long) > TAGS_TOTAL_MAX:
            assert short in result
            assert after in result
            assert long not in result


class TestValidateTagsExactTitle:
    def test_exact_title_pinned_first(self):
        title = "Why Bridges Collapse"
        tags = ["filler a", "filler b", title, "filler c"]
        result = validate_tags(tags, exact_title=title)
        assert result[0] == title

    def test_exact_title_case_insensitive_match(self):
        title = "Why Bridges Collapse"
        # The model might return it in a different case.
        tags = ["why bridges collapse", "other tag"]
        result = validate_tags(tags, exact_title=title)
        assert result[0] == "why bridges collapse"

    def test_exact_title_survives_budget_pressure(self):
        """Even when many tags are ahead of the title, it must not be pushed out."""
        title = "My Exact Title"
        fillers = ["f" * 40 for _ in range(12)]  # enough to fill most of the budget
        tags = fillers + [title]
        result = validate_tags(tags, exact_title=title)
        # Title should be first and present.
        assert result[0] == title

    def test_no_title_provided_works_normally(self):
        tags = ["tag a", "tag b"]
        result = validate_tags(tags)
        assert result == tags


class TestValidateTagsWithSuggestions:
    def test_autocomplete_ranked_tag_preferred(self):
        suggestions = ["suspension bridge", "cable bridge", "beam bridge"]
        # Put high-value tag last in the input list.
        tags = ["filler one", "filler two", "suspension bridge"]
        result = validate_tags(tags, suggestions=suggestions)
        # suspension bridge (rank 0) should appear before fillers.
        assert result.index("suspension bridge") < result.index("filler one")

    def test_exact_title_still_first_with_suggestions(self):
        title = "Bridge Failures Explained"
        suggestions = ["other tag"]
        tags = ["other tag", title, "extra tag"]
        result = validate_tags(tags, exact_title=title, suggestions=suggestions)
        assert result[0] == title

    def test_budget_respected_with_suggestions(self):
        suggestions = [f"query {i}" for i in range(50)]
        tags = [f"query {i}" for i in range(100)]
        result = validate_tags(tags, suggestions=suggestions)
        total = sum(_tag_cost(t) for t in result)
        assert total <= TAGS_TOTAL_MAX

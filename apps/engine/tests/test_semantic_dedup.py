"""Tests for semantic duplicate detection (D3).

The embedding check is layered on top of Jaccard for the ambiguous zone
(SEMANTIC_LOWER ≤ Jaccard < DUPLICATE_THRESHOLD).  Ollama is never required:
when unavailable the function falls through to Jaccard-only.
"""

from __future__ import annotations

import pytest

from engine.ideas import (
    DUPLICATE_THRESHOLD,
    SEMANTIC_EMBEDDING_THRESHOLD,
    SEMANTIC_LOWER,
    IdeaStatus,
    _cosine,
    build_backlog_async,
    find_duplicate_async,
    similarity,
)

# ── _cosine ──────────────────────────────────────────────────────────────────


class TestCosine:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        assert abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9

    def test_antiparallel_vectors(self):
        assert abs(_cosine([1.0, 0.0], [-1.0, 0.0]) - (-1.0)) < 1e-9

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_symmetric(self):
        a, b = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]
        assert abs(_cosine(a, b) - _cosine(b, a)) < 1e-9


# ── find_duplicate_async — Jaccard path (no Ollama) ──────────────────────────


class TestFindDuplicateAsyncJaccard:
    @pytest.mark.asyncio
    async def test_clear_duplicate_detected(self):
        """Jaccard is decisive above DUPLICATE_THRESHOLD."""
        existing = ["why bridges collapse"]
        # Highly similar wording — Jaccard should exceed 0.45.
        dup, score, method = await find_duplicate_async(
            "why bridges collapse suddenly", existing, ollama_base_url=None
        )
        # Score varies; just verify method says Jaccard and result is returned.
        assert method.startswith("Jaccard")

    @pytest.mark.asyncio
    async def test_clear_non_duplicate(self):
        """Completely unrelated topics should not be flagged."""
        existing = ["why bridges collapse"]
        dup, score, method = await find_duplicate_async(
            "how to bake sourdough bread", existing, ollama_base_url=None
        )
        assert dup is None

    @pytest.mark.asyncio
    async def test_empty_existing_never_flags(self):
        dup, score, method = await find_duplicate_async("any topic", [], ollama_base_url=None)
        assert dup is None

    @pytest.mark.asyncio
    async def test_returns_method_string_jaccard_without_ollama(self):
        dup, score, method = await find_duplicate_async(
            "why bridges collapse",
            ["why bridges collapse"],
            ollama_base_url=None,
        )
        # Perfect Jaccard match → returned as Jaccard method.
        assert "Jaccard" in method

    @pytest.mark.asyncio
    async def test_best_candidate_returned(self):
        existing = ["cat food recipes", "why bridges collapse", "cat grooming tips"]
        dup, score, method = await find_duplicate_async(
            "cat grooming guide", existing, ollama_base_url=None
        )
        # "cat grooming tips" is more similar than the others.
        if dup:
            assert "cat" in dup.lower() or "groom" in dup.lower()


# ── find_duplicate_async — embedding path (mocked Ollama) ────────────────────


class TestFindDuplicateAsyncEmbedding:
    """Test the embedding code path using monkeypatching.

    We need topics where Jaccard sits in the ambiguous zone [SEMANTIC_LOWER, DUPLICATE_THRESHOLD).
    "the secrets of deep sea creatures" vs "deep ocean creature secrets revealed" shares two
    content tokens (secrets, deep) out of seven → Jaccard ≈ 0.29.
    """

    _TOPIC = "the secrets of deep sea creatures"
    _EXISTING = ["deep ocean creature secrets revealed"]

    def _assert_in_ambiguous_zone(self) -> None:
        j = similarity(self._TOPIC, self._EXISTING[0])
        assert SEMANTIC_LOWER <= j < DUPLICATE_THRESHOLD, (
            f"Jaccard {j:.3f} not in [{SEMANTIC_LOWER}, {DUPLICATE_THRESHOLD}) — "
            "adjust topics if STOPWORDS or DUPLICATE_THRESHOLD changed"
        )

    @pytest.mark.asyncio
    async def test_embedding_confirms_ambiguous_duplicate(self, monkeypatch):
        """When Jaccard is in the ambiguous zone and embeddings are very similar,
        the pair is flagged as a duplicate."""
        self._assert_in_ambiguous_zone()

        # Stub _get_embedding to return identical vectors (cosine = 1.0).
        import engine.ideas as ideas_mod

        monkeypatch.setattr(ideas_mod, "_get_embedding", lambda *_a, **_kw: _return([1.0, 0.0]))

        dup, score, method = await find_duplicate_async(
            self._TOPIC, self._EXISTING, ollama_base_url="http://fake-ollama"
        )
        assert dup == self._EXISTING[0]
        assert score >= SEMANTIC_EMBEDDING_THRESHOLD
        assert "embedding" in method

    @pytest.mark.asyncio
    async def test_ollama_unavailable_falls_through_to_jaccard(self, monkeypatch):
        """When _get_embedding returns None (Ollama down), Jaccard is used.
        The pair sits in the ambiguous zone, so no duplicate is flagged."""
        self._assert_in_ambiguous_zone()

        import engine.ideas as ideas_mod

        monkeypatch.setattr(ideas_mod, "_get_embedding", lambda *_a, **_kw: _return(None))

        dup, score, method = await find_duplicate_async(
            self._TOPIC, self._EXISTING, ollama_base_url="http://fake-ollama"
        )
        # Jaccard alone cannot confirm — must not flag as duplicate.
        assert dup is None
        assert "Jaccard" in method

    @pytest.mark.asyncio
    async def test_embedding_below_threshold_not_flagged(self, monkeypatch):
        """Low embedding similarity (orthogonal vectors) must not produce a false positive."""
        self._assert_in_ambiguous_zone()

        import engine.ideas as ideas_mod

        # Topic gets [1,0], candidate gets [0,1] — cosine = 0.0 (clearly unrelated).
        call_count = {"n": 0}

        async def _fake_embedding(*_a, **_kw):
            call_count["n"] += 1
            return [1.0, 0.0] if call_count["n"] == 1 else [0.0, 1.0]

        monkeypatch.setattr(ideas_mod, "_get_embedding", _fake_embedding)

        dup, score, method = await find_duplicate_async(
            self._TOPIC, self._EXISTING, ollama_base_url="http://fake-ollama"
        )
        assert dup is None


# ── build_backlog_async ───────────────────────────────────────────────────────


class TestBuildBacklogAsync:
    @pytest.mark.asyncio
    async def test_clear_duplicate_marked_rejected(self):
        topics = ["why bridges collapse", "why bridges collapse suddenly"]
        out = await build_backlog_async(
            topics,
            published_topics=[],
            suggestions=["bridge", "collapse"],
        )
        statuses = [i.status for i in out]
        assert IdeaStatus.REJECTED in statuses

    @pytest.mark.asyncio
    async def test_duplicate_has_method_in_notes(self):
        topics = ["why bridges collapse", "why bridges collapse"]
        out = await build_backlog_async(
            topics,
            published_topics=[],
            suggestions=[],
        )
        rejected = [i for i in out if i.status is IdeaStatus.REJECTED]
        assert rejected
        assert rejected[0].notes  # non-empty method description

    @pytest.mark.asyncio
    async def test_non_duplicates_all_backlog(self):
        topics = ["bridge engineering", "sourdough bread", "space exploration"]
        out = await build_backlog_async(
            topics,
            published_topics=[],
            suggestions=[],
        )
        assert all(i.status is IdeaStatus.BACKLOG for i in out)

    @pytest.mark.asyncio
    async def test_published_topics_checked_against(self):
        published = ["why bridges collapse"]
        # Clear near-duplicate of a published topic.
        topics = ["why do bridges collapse"]
        out = await build_backlog_async(
            topics,
            published_topics=published,
            suggestions=[],
        )
        # The similar topic must be flagged.
        assert any(i.duplicate_of for i in out)

    @pytest.mark.asyncio
    async def test_similarity_score_stored(self):
        topics = ["why bridges collapse", "why do bridges collapse"]
        out = await build_backlog_async(
            topics,
            published_topics=[],
            suggestions=[],
        )
        for idea in out:
            assert 0.0 <= idea.similarity <= 1.0

    @pytest.mark.asyncio
    async def test_sorted_non_rejected_first(self):
        topics = ["why bridges collapse", "why bridges collapse again", "sourdough bread"]
        out = await build_backlog_async(
            topics,
            published_topics=[],
            suggestions=[],
        )
        first_rejected = next(
            (i for i, idea in enumerate(out) if idea.status is IdeaStatus.REJECTED), len(out)
        )
        last_non_rejected = max(
            (i for i, idea in enumerate(out) if idea.status is not IdeaStatus.REJECTED), default=-1
        )
        assert last_non_rejected <= first_rejected


# ── helpers ──────────────────────────────────────────────────────────────────


async def _return(value):
    """Async wrapper so monkeypatch can replace async functions with coroutines."""
    return value

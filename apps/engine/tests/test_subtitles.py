"""Tests for subtitle punctuation restoration and cue grouping (D1).

edge-tts strips punctuation from WordBoundary events, so _restore_punctuation()
must re-attach it from the original text before _group_cues() sees the cues.
Without this the sentence-break check in _group_cues() never fires.
"""

from __future__ import annotations

import pytest

from engine.workflows.media import _group_cues, _restore_punctuation


def _cues(words: list[str], start: float = 0.0, dur: float = 0.5) -> list[dict]:
    """Build minimal word-cue dicts for test input."""
    result = []
    t = start
    for word in words:
        result.append({"start": t, "end": t + dur, "text": word})
        t += dur
    return result


# ── _restore_punctuation ─────────────────────────────────────────────────────


class TestRestorePunctuation:
    def test_period_restored_at_sentence_end(self):
        original = "The bridge collapsed. It fell into the river."
        # edge-tts strips the periods.
        cues = _cues(["The", "bridge", "collapsed", "It", "fell", "into", "the", "river"])
        result = _restore_punctuation(cues, original)
        texts = [c["text"] for c in result]
        assert "collapsed." in texts
        assert "river." in texts

    def test_exclamation_restored(self):
        original = "Watch out! The deck is falling!"
        cues = _cues(["Watch", "out", "The", "deck", "is", "falling"])
        result = _restore_punctuation(cues, original)
        texts = [c["text"] for c in result]
        assert "out!" in texts
        assert "falling!" in texts

    def test_question_mark_restored(self):
        original = "Why did it collapse? Nobody knows."
        cues = _cues(["Why", "did", "it", "collapse", "Nobody", "knows"])
        result = _restore_punctuation(cues, original)
        texts = [c["text"] for c in result]
        assert "collapse?" in texts

    def test_no_mutation_of_input(self):
        original = "Hello world."
        cues = _cues(["Hello", "world"])
        original_texts = [c["text"] for c in cues]
        _restore_punctuation(cues, original)
        assert [c["text"] for c in cues] == original_texts

    def test_mid_sentence_words_unchanged(self):
        original = "The quick brown fox. Jumps over."
        cues = _cues(["The", "quick", "brown", "fox", "Jumps", "over"])
        result = _restore_punctuation(cues, original)
        assert result[0]["text"] == "The"
        assert result[1]["text"] == "quick"
        assert result[2]["text"] == "brown"

    def test_returns_same_count(self):
        original = "One two three. Four five."
        cues = _cues(["One", "two", "three", "Four", "five"])
        result = _restore_punctuation(cues, original)
        assert len(result) == len(cues)

    def test_timings_preserved(self):
        original = "Hello world."
        cues = _cues(["Hello", "world"], start=1.0, dur=0.4)
        result = _restore_punctuation(cues, original)
        # strict=True: _restore_punctuation must return one cue per input cue,
        # so a length change is the bug this loop exists to catch.
        for orig, restored in zip(cues, result, strict=True):
            assert restored["start"] == orig["start"]
            assert restored["end"] == orig["end"]

    def test_no_original_punctuation_leaves_cues_unchanged(self):
        original = "plain words no punctuation"
        cues = _cues(["plain", "words", "no", "punctuation"])
        result = _restore_punctuation(cues, original)
        assert [c["text"] for c in result] == ["plain", "words", "no", "punctuation"]

    def test_leading_punctuation_ignored(self):
        original = '"Hello," she said.'
        cues = _cues(["Hello", "she", "said"])
        result = _restore_punctuation(cues, original)
        # Only trailing sentence-ending punctuation should transfer.
        assert "said." in [c["text"] for c in result]
        # Leading quote on "Hello" should not appear.
        assert result[0]["text"] == "Hello"


# ── _group_cues with restored punctuation ────────────────────────────────────


class TestGroupCuesWithRestoredPunctuation:
    def test_breaks_at_sentence_end(self):
        """After restoration the sentence-break check must fire."""
        original = "The bridge collapsed. It fell into the river."
        raw_cues = _cues(["The", "bridge", "collapsed", "It", "fell", "into", "the", "river"])
        restored = _restore_punctuation(raw_cues, original)
        grouped = _group_cues(restored)
        texts = [g["text"] for g in grouped]
        # "The bridge collapsed." must be one cue, not merged with the next sentence.
        assert any("collapsed" in t and "fell" not in t for t in texts)

    def test_char_limit_still_applies(self):
        original = "this is a very long sentence with many many many many words here"
        raw_cues = _cues(original.split())
        restored = _restore_punctuation(raw_cues, original)
        grouped = _group_cues(restored, max_chars=20)
        for g in grouped:
            # Each grouped cue should not be much longer than the limit
            # (the limit applies before flush, so the final word can push it just over).
            assert len(g["text"]) <= 35  # generous upper bound

    def test_sentence_break_before_char_limit(self):
        """A short sentence must not be merged with the next just to fill the budget."""
        original = "Short. Also short. Also short."
        raw_cues = _cues(["Short", "Also", "short", "Also", "short"])
        restored = _restore_punctuation(raw_cues, original)
        grouped = _group_cues(restored, max_chars=42)
        # With sentence breaks, we should get separate lines for each sentence.
        # (Depending on how "Also short." aligns, we may get 2-3 groups.)
        assert len(grouped) >= 2

    def test_timings_of_grouped_cues(self):
        original = "One two three. Four five."
        raw_cues = _cues(["One", "two", "three", "Four", "five"])
        restored = _restore_punctuation(raw_cues, original)
        grouped = _group_cues(restored)
        # Each group's start <= end
        for g in grouped:
            assert g["start"] <= g["end"]
        # Groups must be non-overlapping in time
        for i in range(len(grouped) - 1):
            assert grouped[i]["end"] <= grouped[i + 1]["start"] + 0.01  # float tolerance


# ── written forms ───────────────────────────────────────────────────────────
#
# edge-tts reports what it *said*, not what it read, so a script saying "$50,000"
# produced the caption "fifty thousand dollars". Rendered frames from a real job
# read "and fifty thousand / dollars." and "Lasagna on October / fifth twenty
# eighteen." — a transcript of a phone call rather than a caption track, and
# three times the width on the frame where width is scarcest.


class TestSpokenWordCount:
    """Only has to count, and only well enough to stop one number eating the next."""

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("$50,000", 3),  # fifty thousand dollars
            ("2018", 2),  # twenty eighteen - a year is read as two pairs
            ("300,000,000", 3),  # three hundred million
            ("75%", 3),  # seventy five percent
            ("12", 1),
            ("5", 1),
        ],
    )
    def test_counts_the_spoken_expansion(self, token, expected):
        from engine.workflows.media import _spoken_word_count

        assert _spoken_word_count(token) == expected

    def test_a_token_with_no_digits_is_one_word(self):
        from engine.workflows.media import _spoken_word_count

        assert _spoken_word_count("subscribers") == 1


class TestRestoreWrittenForms:
    def test_a_currency_amount_is_written_not_spoken(self):
        from engine.workflows.media import _restore_written_forms

        cues = _spoken("He put up fifty thousand dollars today")
        out = _restore_written_forms(cues, "He put up $50,000 today")
        assert [c["text"] for c in out] == ["He", "put", "up", "$50,000", "today"]

    def test_the_merged_cue_keeps_the_full_span(self):
        """Timing has to come from the words that were actually spoken, or the
        caption drifts away from the audio."""
        from engine.workflows.media import _restore_written_forms

        cues = _spoken("He put up fifty thousand dollars today")
        out = _restore_written_forms(cues, "He put up $50,000 today")
        amount = next(c for c in out if c["text"] == "$50,000")
        assert amount["start"] == cues[3]["start"]
        assert amount["end"] == cues[5]["end"]

    def test_one_number_does_not_swallow_the_next(self):
        """ "October 5, 2018" — the "5," took "fifth twenty eighteen" and "2018."
        then ate the word after it, losing "That" out of the script."""
        from engine.workflows.media import _restore_written_forms

        cues = _spoken("on October fifth twenty eighteen That was")
        out = _restore_written_forms(cues, "on October 5, 2018. That was")
        assert [c["text"] for c in out] == ["on", "October", "5,", "2018.", "That", "was"]

    def test_punctuation_comes_back_for_free(self):
        """The written token already carries it, which is why this replaced the
        separate punctuation pass rather than running alongside it."""
        from engine.workflows.media import _restore_written_forms

        out = _restore_written_forms(_spoken("the bridge fell"), "the bridge fell.")
        assert out[-1]["text"] == "fell."

    def test_badly_drifted_alignment_keeps_the_spoken_forms(self):
        """A caption track missing its numerals beats one out of sync with the audio."""
        from engine.workflows.media import _restore_written_forms

        cues = _spoken("completely different words here that match nothing at all")
        out = _restore_written_forms(cues, "the bridge fell.")
        assert [c["text"] for c in out] == [c["text"] for c in cues]

    def test_no_written_text_is_a_no_op(self):
        from engine.workflows.media import _restore_written_forms

        cues = _spoken("the bridge fell")
        assert _restore_written_forms(cues, "") is cues


def _spoken(sentence: str) -> list[dict]:
    return [
        {"start": i * 0.35, "end": i * 0.35 + 0.35, "text": w}
        for i, w in enumerate(sentence.split())
    ]

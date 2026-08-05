"""Tests for how an idea's trend signal ages.

Freshness used to be a pair of cliffs. A topic sharing one word with a trending
term scored 1.0 and one sharing none scored 0.0; and an idea counted for full
value on day 44 and vanished on day 46. Both are now curves, and the tests below
are mostly about the *shape* — a decay that never actually decays passes any test
that only checks the endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.ideas import FRESHNESS_HALF_LIFE_DAYS, Idea, IdeaStatus, next_up, score_idea

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def idea(*, freshness: float = 1.0, age_days: float = 0.0, **kw) -> Idea:
    return Idea(
        topic=kw.pop("topic", "bridges"),
        created_at=NOW - timedelta(days=age_days),
        freshness=freshness,
        **kw,
    )


class TestDecayShape:
    def test_a_brand_new_idea_keeps_all_of_its_trend_signal(self):
        assert idea(age_days=0).freshness_at(NOW) == pytest.approx(1.0)

    def test_one_half_life_halves_it(self):
        got = idea(age_days=FRESHNESS_HALF_LIFE_DAYS).freshness_at(NOW)
        assert got == pytest.approx(0.5, abs=1e-3)

    def test_two_half_lives_quarter_it(self):
        got = idea(age_days=2 * FRESHNESS_HALF_LIFE_DAYS).freshness_at(NOW)
        assert got == pytest.approx(0.25, abs=1e-3)

    def test_it_decays_every_day_rather_than_in_steps(self):
        """A step function passes an endpoints-only test while behaving like the
        cliff this replaced."""
        values = [idea(age_days=d).freshness_at(NOW) for d in range(0, 40)]
        assert all(b < a for a, b in zip(values, values[1:], strict=False))

    def test_an_idea_with_no_trend_signal_stays_at_zero(self):
        assert idea(freshness=0.0, age_days=5).freshness_at(NOW) == 0.0

    def test_a_clock_skewed_future_timestamp_does_not_amplify_the_signal(self):
        """Negative age through the exponential would return >1.0 and let a bad
        clock outrank every real idea."""
        assert idea(age_days=-30).freshness_at(NOW) == pytest.approx(1.0)

    def test_a_naive_now_is_accepted_too(self):
        """`created_at` was normalised and the *argument* was not, so an aware
        record against a naive clock raised TypeError — not "stale", and nowhere
        near any handler that expects one."""
        from datetime import datetime as dt

        assert idea(freshness=1.0).freshness_at(dt(2026, 8, 4, 12, 0)) == pytest.approx(1.0)

    def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing(self):
        stale = Idea(topic="x", created_at=NOW.replace(tzinfo=None), freshness=1.0)
        assert stale.freshness_at(NOW) == pytest.approx(1.0)


class TestScore:
    def test_the_score_falls_as_the_idea_ages(self):
        young = idea(age_days=0, demand=0.5)
        old = idea(age_days=60, demand=0.5)
        assert old.score_at(NOW) < young.score_at(NOW)

    def test_only_the_freshness_component_decays(self):
        """Demand, competition and fit are measurements of the world, not of when
        somebody happened to write the idea down."""
        young = idea(age_days=0, freshness=0.0, demand=0.8, fit=0.6, competition=0.2)
        old = idea(age_days=120, freshness=0.0, demand=0.8, fit=0.6, competition=0.2)
        assert old.score_at(NOW) == young.score_at(NOW)

        # And the absolute value, so decaying every component by the same constant
        # cannot pass by keeping the two sides equal to each other.
        assert young.score_at(NOW) == pytest.approx(0.40 * 0.8 + 0.25 * 0.8 + 0.20 * 0.6, abs=1e-3)

    def test_an_aged_idea_with_a_trend_signal_decays_only_that_term(self):
        """Pinned as an exact figure with freshness *non-zero*. Checked only against
        a freshness-free idea, a decay that leaks into demand hides behind the
        `if freshness` case that never fires."""
        aged = idea(
            age_days=FRESHNESS_HALF_LIFE_DAYS,
            freshness=1.0,
            demand=0.8,
            fit=0.6,
            competition=0.2,
        )
        assert aged.score_at(NOW) == pytest.approx(
            0.40 * 0.8 + 0.25 * 0.8 + 0.20 * 0.6 + 0.15 * 0.5, abs=1e-3
        )

    def test_freshness_is_worth_at_most_its_weight(self):
        bare = idea(age_days=0, freshness=0.0)
        full = idea(age_days=0, freshness=1.0)
        assert full.score_at(NOW) - bare.score_at(NOW) == pytest.approx(0.15, abs=1e-3)


class TestSummary:
    def test_a_decayed_trend_signal_is_shown_on_the_card(self):
        """It is the only component that moves without an edit. Hidden, an idea
        sliding down the backlog has no visible cause."""
        assert "trend" in idea(freshness=1.0, age_days=0).summary()

    def test_an_idea_with_no_trend_signal_does_not_mention_one(self):
        assert "trend" not in idea(freshness=0.0).summary()


class TestTrendMatching:
    def _scored(self, topic: str, trends: list[str]) -> Idea:
        return score_idea(
            topic,
            suggestions=[],
            competitor_count=5,
            trending_terms=trends,
        )

    def test_a_full_match_scores_higher_than_a_single_shared_word(self):
        """The old rule gave both exactly 1.0: one *is* the trend, the other happens
        to share a noun with it.

        The shared word has to be a literal token match — "bridges" and "bridge"
        are different tokens, so a topic worded that way overlaps nothing and would
        make this pass under the binary rule too.
        """
        exact = self._scored("bridge collapse baltimore", ["bridge collapse baltimore"])
        partial = self._scored("bridge safety", ["bridge collapse baltimore"])

        assert 0.0 < partial.freshness < exact.freshness
        assert partial.freshness == pytest.approx(1 / 3, abs=1e-3)

    def test_covering_the_whole_trend_scores_one(self):
        assert self._scored("bridge collapse", ["bridge collapse"]).freshness == 1.0

    def test_no_overlap_scores_zero(self):
        assert self._scored("baking sourdough", ["bridge collapse"]).freshness == 0.0

    def test_the_best_of_several_trends_wins(self):
        got = self._scored("bridge collapse", ["baking bread", "bridge collapse"])
        assert got.freshness == 1.0

    def test_a_long_topic_is_not_penalised_for_being_long(self):
        """Overlap is measured against the *trend's* words. Divide by the topic's
        instead and a thorough title scores worse than a vague one."""
        short = self._scored("bridge collapse", ["bridge collapse"])
        long = self._scored(
            "bridge collapse and the engineering failures behind it",
            ["bridge collapse"],
        )
        assert long.freshness == short.freshness

    def test_no_trending_terms_leaves_freshness_alone(self):
        assert self._scored("bridges", []).freshness == 0.0


class TestNextUp:
    def test_the_hard_cutoff_still_removes_genuinely_stale_ideas(self):
        old = Idea(topic="old", created_at=NOW - timedelta(days=60))
        fresh = Idea(topic="fresh", created_at=NOW)
        assert [i.topic for i in next_up([old, fresh], 5, now=NOW)] == ["fresh"]

    def test_ranking_degrades_before_the_cutoff_rather_than_at_it(self):
        """Two ideas identical but for age. The older one should already be ranked
        below well before day 45, which a pure cutoff cannot express."""
        recent = Idea(topic="recent", created_at=NOW - timedelta(days=1), freshness=1.0)
        older = Idea(topic="older", created_at=NOW - timedelta(days=30), freshness=1.0)
        assert [i.topic for i in next_up([older, recent], 2, now=NOW)] == [
            "recent",
            "older",
        ]

    def test_a_stronger_idea_still_beats_a_newer_weak_one(self):
        """Decay tilts the ranking; it does not take it over. Freshness is 15% of
        the score and must not behave like the whole of it."""
        strong = Idea(
            topic="strong",
            created_at=NOW - timedelta(days=30),
            demand=1.0,
            fit=1.0,
        )
        weak = Idea(topic="weak", created_at=NOW, freshness=1.0)
        assert next_up([weak, strong], 1, now=NOW)[0].topic == "strong"

    def test_a_future_dated_idea_is_not_offered(self):
        """Zero age means full trend weight, and every future timestamp clears the
        cutoff — so a record dated a year ahead outranked everything real for a
        year. Clock skew and hand-edited rows both produce one."""
        ahead = Idea(topic="ahead", created_at=NOW + timedelta(days=365), freshness=1.0)
        here = Idea(topic="here", created_at=NOW)
        assert [i.topic for i in next_up([ahead, here], 5, now=NOW)] == ["here"]

    def test_next_up_accepts_a_naive_clock(self):
        from datetime import datetime as dt

        assert next_up([Idea(topic="x", created_at=NOW)], 1, now=dt(2026, 8, 4, 12, 0))

    def test_only_backlog_ideas_are_offered(self):
        done = Idea(topic="done", created_at=NOW, status=IdeaStatus.PUBLISHED)
        todo = Idea(topic="todo", created_at=NOW)
        assert [i.topic for i in next_up([done, todo], 5, now=NOW)] == ["todo"]

    def test_an_explicit_clock_is_honoured(self):
        """Without it every test here would be a wall-clock test, and the decay
        would only be observable by waiting three weeks."""
        item = Idea(topic="x", created_at=NOW, freshness=1.0)
        later = NOW + timedelta(days=FRESHNESS_HALF_LIFE_DAYS)
        assert item.score_at(later) < item.score_at(NOW)

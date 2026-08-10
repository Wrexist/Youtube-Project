"""The idea backlog.

`api/ideas.py` proposed, scored, showed and forgot: a process-local cache with a
thirty-minute life and no memory of what the operator had already refused, so the
same channel saw the same suggestions over and over and "not that one" did not
survive a reload.

Everything below is about the two behaviours that make a backlog a plan rather than
a list — it *depletes* when you act on an idea, and it *refuses* to re-propose one
you have already resolved.

Called directly rather than through `TestClient`, for the reason written up in
`test_spend.py`: the app runs on its own event loop and asyncpg will not share a
pool across two.
"""

from __future__ import annotations

import pytest

from engine import repository


def _idea(topic: str, score: float = 0.5) -> dict:
    return {"topic": topic, "score": score, "demand": 0.4, "competition": 0.3, "why": "because"}


async def test_ideas_persist_and_come_back_best_first(database):
    await repository.add_backlog_ideas(
        [_idea("why bridges collapse", 0.4), _idea("how salt changed trade", 0.9)]
    )

    ideas = await repository.open_backlog_ideas()

    assert [i["topic"] for i in ideas] == ["how salt changed trade", "why bridges collapse"]


async def test_an_idea_already_on_the_list_is_not_added_twice(database):
    await repository.add_backlog_ideas([_idea("why bridges collapse")])
    added = await repository.add_backlog_ideas([_idea("why bridges collapse", 0.99)])

    assert added == 0
    ideas = await repository.open_backlog_ideas()
    assert len(ideas) == 1
    # And the original score stands. Re-scoring in place would let a re-proposal
    # float an idea back to the top of a list it is already on.
    assert ideas[0]["score"] == pytest.approx(0.5)


async def test_making_a_video_takes_its_idea_off_the_list(database):
    await repository.add_backlog_ideas([_idea("why bridges collapse")])

    used = await repository.resolve_backlog_idea(
        topic="why bridges collapse", status="used", job_id="job-1"
    )

    assert used is True
    assert await repository.open_backlog_ideas() == []


async def test_a_refused_idea_is_never_proposed_again(database):
    """Kept, not deleted. "I said no" is a reason not to re-propose, and the
    generator works by adjacency to published topics — it would re-derive the same
    idea from the same history next week."""
    await repository.add_backlog_ideas([_idea("why bridges collapse")])
    ideas = await repository.open_backlog_ideas()
    await repository.resolve_backlog_idea(idea_id=ideas[0]["id"], status="dismissed")

    assert await repository.open_backlog_ideas() == []
    re_added = await repository.add_backlog_ideas([_idea("why bridges collapse")])
    assert re_added == 0
    assert await repository.open_backlog_ideas() == []


async def test_resolving_something_not_on_the_list_says_so(database):
    assert await repository.resolve_backlog_idea(topic="never heard of it", status="used") is False


async def test_resolving_twice_only_counts_once(database):
    """The Create screen sends a topic on every generate. Re-running the same topic
    must not re-resolve an idea that a different job already consumed."""
    await repository.add_backlog_ideas([_idea("why bridges collapse")])

    first = await repository.resolve_backlog_idea(
        topic="why bridges collapse", status="used", job_id="job-1"
    )
    second = await repository.resolve_backlog_idea(
        topic="why bridges collapse", status="used", job_id="job-2"
    )

    assert first is True
    assert second is False

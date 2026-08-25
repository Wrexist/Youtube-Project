"""Channel launches survive a restart — KNOWN-ISSUES §5.8, finally closed.

`save_launch`/`load_launches` existed for a long time with no application
caller, and the loader returned a flattened dict that could not be assigned
into the mirror shape `api/channels.py` reads. These tests drive the wired
version: the API module's own `_save` and `restore`, round-tripping through the
real repository.
"""

from __future__ import annotations

from engine import repository
from engine.api import channels as api
from engine.workflows.base import Provenance, StageOutput, StageStatus


def _mirror(launch_id: str, *, status: str = "running") -> dict:
    states = api.LAUNCH_WORKFLOW.initial_states()
    # One finished stage with a JSON-shaped value, the way a real run leaves it.
    grounding = states["grounding"]
    grounding.status = StageStatus.DONE
    grounding.output = StageOutput(
        value={"suggestions": ["why bridges collapse"]},
        provenance=Provenance(model="m", prompt="p"),
        cost_usd=0.01,
    )
    return {
        "id": launch_id,
        "niche": "bridges",
        "states": states,
        "events": [{"type": "stage.completed", "stage": "grounding"}],
        "status": status,
        "inputs": {"niche": "bridges", "country": "US", "language": "en"},
    }


async def test_a_launch_round_trips_with_its_stage_outputs(database):
    api.LAUNCHES.clear()
    api.LAUNCHES["l1"] = _mirror("l1", status="completed")
    await api._save("l1")

    api.LAUNCHES.clear()
    await api.restore()

    record = api.LAUNCHES["l1"]
    assert record["status"] == "completed"
    assert record["inputs"]["country"] == "US"
    assert record["events"][0]["stage"] == "grounding"
    state = record["states"]["grounding"]
    assert state.status is StageStatus.DONE
    assert state.output.value == {"suggestions": ["why bridges collapse"]}
    api.LAUNCHES.clear()


async def test_a_launch_that_was_running_comes_back_interrupted(database):
    api.LAUNCHES.clear()
    api.LAUNCHES["l2"] = _mirror("l2", status="running")
    await api._save("l2")

    api.LAUNCHES.clear()
    await api.restore()

    # Nothing is executing it any more; `running` would promise progress that
    # will never arrive.
    assert api.LAUNCHES["l2"]["status"] == "interrupted"
    api.LAUNCHES.clear()


async def test_the_loader_returns_the_payload_unflattened(database):
    await repository.save_launch("l3", "completed", "bridges", {"states": {}, "events": []})
    loaded = await repository.load_launches()
    assert loaded["l3"]["payload"] == {"states": {}, "events": []}
    assert "states" not in loaded["l3"]  # the flattening was the §5.8 bug

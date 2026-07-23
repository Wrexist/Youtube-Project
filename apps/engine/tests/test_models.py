"""Model routing tests.

The routing table decides which model runs which stage, and a mistake here is
expensive in one of two directions: a frontier model on tag generation wastes money,
a 7B on the critique pass wastes views. Both are silent.
"""

from __future__ import annotations

import json

import pytest

from engine.models import CATALOGUE, DEFAULT_ROUTES, TASKS, ModelSpec, Routing


def local(name: str = "qwen2.5:14b", **kw) -> ModelSpec:
    return ModelSpec("ollama", name, f"{name} (local)", **kw)


def test_every_task_has_a_default_route():
    assert set(DEFAULT_ROUTES) == set(TASKS)
    for key in DEFAULT_ROUTES.values():
        assert key in CATALOGUE


def test_critical_tasks_default_to_the_strongest_model():
    """Hook, draft, critique and titles decide whether a video works."""
    for task, meta in TASKS.items():
        if meta["quality"] == "critical":
            assert DEFAULT_ROUTES[task] == "anthropic:claude-opus-4-8", task


def test_mechanical_tasks_default_to_a_cheap_model():
    assert "haiku" in DEFAULT_ROUTES["tags"]
    assert "haiku" in DEFAULT_ROUTES["chapters"]


def test_routing_a_single_task_leaves_the_rest_alone():
    r = Routing()
    r.set_route("tags", "ollama:qwen2.5:14b")
    assert r.spec_for("tags").is_local
    assert not r.spec_for("draft").is_local


def test_unknown_task_or_model_is_rejected():
    r = Routing()
    with pytest.raises(KeyError):
        r.set_route("not_a_task", "anthropic:claude-opus-4-8")
    with pytest.raises(KeyError):
        r.set_route("tags", "anthropic:does-not-exist")


def test_route_everything_local():
    """The 'run it all on my own machine' path."""
    r = Routing()
    r.set_all("ollama:qwen2.5:14b")
    assert all(r.spec_for(t).is_local for t in TASKS)
    assert r.estimated_cost_multiplier() == 0.0


def test_local_models_are_free_so_the_budget_ceiling_stays_honest():
    spec = local()
    assert spec.is_free
    assert spec.cost(1_000_000, 1_000_000) == 0.0


def test_paid_model_cost_is_computed_per_million_tokens():
    opus = CATALOGUE["anthropic:claude-opus-4-8"]
    # 1M in at $5 + 1M out at $25
    assert opus.cost(1_000_000, 1_000_000) == pytest.approx(30.0)


def test_a_missing_route_falls_back_rather_than_crashing():
    """A bad config should not take down a render mid-flight."""
    r = Routing()
    r.routes["draft"] = "nonexistent:model"
    assert r.spec_for("draft") is not None


def test_json_unreliable_model_on_a_json_task_is_flagged():
    r = Routing()
    r.add_model(local("mistral:7b", json_mode=False))
    r.set_route("beats", "ollama:mistral:7b")  # beats needs JSON
    problems = [p for p in r.problems() if p["task"] == "beats"]
    assert problems and "JSON" in problems[0]["message"]


def test_small_context_on_a_long_output_task_is_flagged():
    r = Routing()
    r.add_model(local("tiny:1b", context=4_000))
    r.set_route("draft", "ollama:tiny:1b")
    problems = [p for p in r.problems() if p["task"] == "draft"]
    assert problems
    assert any("truncated" in p["message"] for p in problems)


def test_a_small_local_model_on_a_critical_task_is_flagged():
    r = Routing()
    r.add_model(local("gemma2:9b", context=8_000))
    r.set_route("hook", "ollama:gemma2:9b")
    problems = [p for p in r.problems() if p["task"] == "hook"]
    assert problems
    assert any("costs views" in p["message"] for p in problems)


def test_default_routing_has_no_problems():
    assert Routing().problems() == []


def test_cost_multiplier_reflects_the_trade():
    r = Routing()
    assert r.estimated_cost_multiplier() == pytest.approx(1.0)
    r.set_all("anthropic:claude-haiku-4-5-20251001")
    assert r.estimated_cost_multiplier() < 1.0


def test_routing_survives_a_save_and_load(tmp_path):
    r = Routing()
    r.add_model(local())
    r.set_route("tags", "ollama:qwen2.5:14b")
    path = tmp_path / "routing.json"
    r.save(path)

    loaded = Routing.load(path)
    assert loaded.spec_for("tags").key() == "ollama:qwen2.5:14b"
    assert loaded.spec_for("draft").key() == DEFAULT_ROUTES["draft"]


def test_a_corrupt_config_falls_back_to_defaults_rather_than_bricking(tmp_path):
    path = tmp_path / "routing.json"
    path.write_text("{ not json at all", encoding="utf-8")
    assert Routing.load(path).spec_for("draft").key() == DEFAULT_ROUTES["draft"]


def test_missing_config_is_not_an_error(tmp_path):
    assert Routing.load(tmp_path / "absent.json").routes == DEFAULT_ROUTES


def test_custom_openai_compatible_endpoint_round_trips(tmp_path):
    """Covers Groq, DeepSeek, OpenRouter, LM Studio, vLLM — all one transport."""
    r = Routing()
    r.add_model(
        ModelSpec(
            "openai_compatible",
            "llama-3.3-70b",
            "Groq Llama 3.3",
            base_url="https://api.groq.com/openai/v1",
            input_per_m=0.59,
            output_per_m=0.79,
        )
    )
    r.set_route("draft", "openai_compatible:llama-3.3-70b")
    path = tmp_path / "r.json"
    r.save(path)

    spec = Routing.load(path).spec_for("draft")
    assert spec.base_url == "https://api.groq.com/openai/v1"
    assert not spec.is_free


def test_saved_config_is_readable_json(tmp_path):
    path = tmp_path / "routing.json"
    Routing().save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"routes", "catalogue"}

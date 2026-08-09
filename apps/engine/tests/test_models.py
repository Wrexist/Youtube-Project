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
            assert DEFAULT_ROUTES[task] == "anthropic:claude-opus-5", task


def test_mechanical_tasks_default_to_a_cheap_model():
    assert "haiku" in DEFAULT_ROUTES["tags"]
    assert "haiku" in DEFAULT_ROUTES["chapters"]


def test_the_previous_default_is_still_in_the_catalogue():
    """Routes naming it are saved on people's machines already.

    Dropping a catalogue entry does not raise anything — `spec_for` falls back — so a
    stage someone deliberately pinned would quietly move to a different model.
    """
    assert "anthropic:claude-opus-4-8" in CATALOGUE


def test_temperature_is_withheld_from_the_models_that_reject_it():
    """The routing table's half of a live 400.

    Opus 4.7 and up and Fable 5 removed the sampling parameters; sending
    `temperature` is a 400 on every call, which is what the default route for every
    critical task was doing.
    """
    for key in ("anthropic:claude-opus-5", "anthropic:claude-opus-4-8", "anthropic:claude-fable-5"):
        assert CATALOGUE[key].temperature_policy == "none", key
        assert CATALOGUE[key].thinks_by_default, key

    assert CATALOGUE["anthropic:claude-sonnet-5"].temperature_policy == "default-only"
    assert CATALOGUE["anthropic:claude-sonnet-5"].thinks_by_default

    haiku = CATALOGUE["anthropic:claude-haiku-4-5-20251001"]
    assert haiku.temperature_policy == "any"
    assert not haiku.thinks_by_default

    assert local().temperature_policy == "any"
    assert not local().thinks_by_default


def test_a_persisted_route_to_a_no_sampling_model_is_fixed_by_upgrading():
    """The capability is derived, not stored — old `routing.json` files predate it."""
    restored = Routing.from_dict(
        {
            "routes": {"draft": "anthropic:claude-opus-4-8"},
            "catalogue": {
                "anthropic:claude-opus-4-8": {
                    "provider": "anthropic",
                    "model": "claude-opus-4-8",
                    "label": "Claude Opus 4.8",
                }
            },
        }
    )
    assert restored.spec_for("draft").temperature_policy == "none"


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


# ── the recommended preset ──────────────────────────────────────────────────
#
# `DEFAULT_ROUTES` names Anthropic for every task, which is the right default and
# useless to someone holding only an OpenAI key: every stage fails at its first
# call, and the manual fix is eighteen dropdowns. These cover the button that
# replaces that.


@pytest.mark.parametrize(
    ("configured", "provider"),
    [
        ({"anthropic", "openai", "gemini"}, "anthropic"),
        ({"openai", "gemini"}, "openai"),
        ({"gemini"}, "gemini"),
    ],
)
def test_the_recommendation_uses_a_provider_that_is_configured(configured, provider):
    from engine.models import recommended_routes

    routes = recommended_routes(configured)
    assert routes, "every task must be routed"
    assert all(model.startswith(f"{provider}:") for model in routes.values()), routes


def test_the_recommendation_covers_every_task():
    from engine.models import recommended_routes

    assert set(recommended_routes({"openai"})) == set(TASKS)


def test_every_recommended_model_exists_in_the_catalogue():
    """A route naming a model with no catalogue entry silently falls back."""
    from engine.models import recommended_routes

    for configured in ({"anthropic"}, {"openai"}, {"gemini"}):
        for task, model in recommended_routes(configured).items():
            assert model in CATALOGUE, f"{configured} routes {task} to unknown {model}"


def test_the_recommendation_still_tiers_by_what_the_task_needs():
    """The point is the best model *per task*, not one model everywhere."""
    from engine.models import recommended_routes

    routes = recommended_routes({"openai"})
    assert routes["hook"] != routes["tags"], "a critical task and a mechanical one matched"


def test_nothing_configured_falls_back_to_the_defaults():
    """A fresh install shows the opinionated default, not an empty table."""
    from engine.models import recommended_routes

    assert recommended_routes(set()) == DEFAULT_ROUTES


# ── provider quirks that are 400s, not ignored parameters ───────────────────


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.6-luna", "o3", "o4-mini"])
def test_openai_reasoning_models_rename_the_output_ceiling(model):
    """Verified against the live API: `max_tokens` is a 400 on these, not a no-op."""
    spec = ModelSpec("openai", model, model)
    assert spec.max_tokens_field == "max_completion_tokens"
    assert spec.temperature_policy == "default-only"
    assert spec.thinks_by_default, "reasoning tokens come out of the same budget"


def test_openai_compatible_gateways_keep_the_old_spelling():
    """`base_url` points this transport at Groq, DeepSeek, OpenRouter and vLLM,
    none of which followed the rename — sending them the new one breaks all of them."""
    spec = ModelSpec(
        "openai_compatible",
        "llama-3.3-70b",
        "Groq Llama 3.3",
        base_url="https://api.groq.com/openai/v1",
    )
    assert spec.max_tokens_field == "max_tokens"
    assert spec.temperature_policy == "any"


def test_the_older_openai_models_are_unaffected():
    spec = CATALOGUE["openai:gpt-4o"]
    assert spec.max_tokens_field == "max_tokens"
    assert spec.temperature_policy == "any"
    assert not spec.thinks_by_default

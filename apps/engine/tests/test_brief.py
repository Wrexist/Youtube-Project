"""The Create screen's "Improve with AI" button.

One model call before the workflow starts, turning what someone typed into a topic
the pipeline can research. It matters more than its size suggests: keyword
grounding seeds autocomplete with this string, web research searches for it, and
the angle and hook stages are handed it as the premise. A vague topic does not
fail loudly — it produces a competent video about nothing in particular.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.api import brief as brief_api
from engine.main import app

client = TestClient(app)


class _Completion:
    model = "anthropic:claude-sonnet-5"
    cost_usd = 0.0033


class _Model:
    """Stands in for the routed LLM. Records what it was asked."""

    def __init__(self, payload):
        self.payload = payload
        self.prompt = ""

    async def json(self, prompt, **_kw):
        self.prompt = prompt
        return self.payload, _Completion()


@pytest.fixture
def model(monkeypatch):
    def install(payload):
        fake = _Model(payload)
        monkeypatch.setattr(brief_api.llm, "for_task", lambda _task: fake)
        return fake

    return install


def test_a_fragment_becomes_a_researchable_topic(model):
    model(
        {
            "topic": "how MrBeast makes money from his giveaway videos",
            "format": "long",
            "why": "Narrowed a channel name to one answerable question.",
        }
    )
    body = client.post("/v1/brief", json={"rough": "mrbeast", "format": "short"}).json()
    assert body["topic"] == "how MrBeast makes money from his giveaway videos"
    assert body["why"]


def test_the_model_may_change_the_format(model):
    """A growth story needs chronology; a Short cannot carry it. The button is
    allowed to disagree with the chip, and the UI says why."""
    model({"topic": "t", "format": "long", "why": "needs chronology"})
    body = client.post("/v1/brief", json={"rough": "mrbeast", "format": "short"}).json()
    assert body["format"] == "long"


def test_a_format_the_model_invented_is_ignored(model):
    """Anything but short/long would reach `JobRequest`, whose pattern rejects it —
    after the operator had pressed Generate."""
    model({"topic": "t", "format": "vertical-ish", "why": ""})
    body = client.post("/v1/brief", json={"rough": "mrbeast", "format": "short"}).json()
    assert body["format"] == "short"


def test_an_empty_topic_keeps_what_was_typed(model):
    """Never blank the field someone was about to press Generate on."""
    model({"topic": "   ", "format": "short", "why": ""})
    body = client.post("/v1/brief", json={"rough": "mrbeast", "format": "short"}).json()
    assert body["topic"] == "mrbeast"


def test_the_response_records_the_model_and_the_cost(model):
    """CLAUDE.md #2. This string seeds an entire video, so "where did it come
    from" has to be answerable afterwards."""
    model({"topic": "t", "format": "short", "why": ""})
    body = client.post("/v1/brief", json={"rough": "mrbeast", "format": "short"}).json()
    assert body["model"] == "anthropic:claude-sonnet-5"
    assert body["cost_usd"] == 0.0033


def test_the_prompt_carries_the_selected_format(model):
    """So the model can disagree with a reason rather than guessing in a vacuum."""
    fake = model({"topic": "t", "format": "short", "why": ""})
    client.post("/v1/brief", json={"rough": "mrbeast", "format": "long"})
    assert "long-form 16:9" in fake.prompt


def test_an_unreachable_provider_is_503_not_500(model, monkeypatch):
    """Not a bug in Studio, and the screen has something useful to say about it."""
    from engine.providers.llm import ProviderUnavailable

    class Dead:
        async def json(self, *_a, **_kw):
            raise ProviderUnavailable("no key configured")

    monkeypatch.setattr(brief_api.llm, "for_task", lambda _task: Dead())
    assert client.post("/v1/brief", json={"rough": "mrbeast"}).status_code == 503


@pytest.mark.parametrize("rough", ["", "   ", "x" * 501])
def test_input_is_bounded(rough):
    """Empty is nothing to work from; 500 characters is a pitch, and anything past
    it is a document being pushed through the model on someone else's key."""
    assert client.post("/v1/brief", json={"rough": rough}).status_code == 422


def test_brief_is_a_routable_task():
    """It appears on the Models screen like every other model call, rather than
    being a hidden one nobody can see or change."""
    from engine.models import DEFAULT_ROUTES, TASKS

    assert "brief" in TASKS
    assert "brief" in DEFAULT_ROUTES

"""Per-task model routing.

Not every stage needs the same model. Tag generation is mechanical and a local 8B
model handles it fine; the critique pass is the single largest quality lever in the
system and deserves the best model available. Forcing one choice across all of them
either wastes money or wastes quality.

So every task is routed independently, and the routing is data — editable at runtime
through the API and the Models screen, not baked into each stage.

Local models via Ollama are first-class here, not an afterthought. Two consequences
worth knowing:

  * **Local models cost $0**, so they do not consume the per-video budget. A pipeline
    routed entirely to Ollama runs the budget ceiling at zero and never trips it.
  * **Local models are worse at structured output.** Most stages here demand strict
    JSON. `json_mode` marks the models that can be trusted with it; the LLM wrapper
    retries with the parse error fed back either way, but a 3B model will still fail
    where a frontier model won't. The Models screen warns rather than silently
    producing worse videos.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from loguru import logger

Provider = Literal["anthropic", "openai", "gemini", "ollama", "openai_compatible"]

#: Anthropic models that removed the sampling parameters. Sending `temperature` to
#: one of these is a 400 on every call, not a value the provider quietly ignores.
_NO_SAMPLING = ("claude-fable-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7")

#: Models that keep `temperature` but reject any value other than the default.
_DEFAULT_SAMPLING_ONLY = ("claude-sonnet-5",)

#: OpenAI's reasoning line, which made the same two changes Anthropic did and one
#: more. Verified against the live API rather than inferred, because all three are
#: 400s rather than ignored parameters:
#:
#:     max_tokens: 64, temperature: 0.7  -> 400 'max_tokens' is not supported with
#:                                          this model. Use 'max_completion_tokens'
#:     max_completion_tokens, temperature: 0.7
#:                                       -> 400 'temperature' does not support 0.7
#:                                          with this model. Only the default (1)
#:     max_completion_tokens             -> 200
#:
#: `gpt-5-chat-latest` is non-reasoning and would tolerate a temperature, but it
#: is not in the catalogue and treating it as reasoning only costs it the default
#: sampling it would have used anyway.
_OPENAI_REASONING = ("gpt-5", "o1", "o3", "o4")

#: Models that reason before answering unless told otherwise. On these `max_tokens`
#: is the ceiling for thinking *and* answer together, which is why `providers/llm.py`
#: adds head-room rather than handing the caller's number straight to the API.
_THINKS_BY_DEFAULT = _NO_SAMPLING + _DEFAULT_SAMPLING_ONLY

# Every routable task, with what it actually demands. `quality` is the honest
# guidance shown next to the picker — some of these genuinely do not need a big model.
TASKS: dict[str, dict] = {
    # Script chain
    #
    # `brief` runs before the workflow does — it is the Create screen's "improve
    # this" button, turning a typed fragment into a topic the rest of the chain
    # can actually work with. Routed like everything else so it is visible and
    # changeable on the Models screen rather than being a hidden model call.
    "brief": {"group": "Script", "needs": "judgement, JSON", "quality": "high"},
    "research": {"group": "Script", "needs": "long context, JSON", "quality": "high"},
    "angle": {"group": "Script", "needs": "judgement", "quality": "high"},
    "hook": {"group": "Script", "needs": "judgement", "quality": "critical"},
    "beats": {"group": "Script", "needs": "structure, JSON", "quality": "high"},
    "draft": {"group": "Script", "needs": "long output", "quality": "critical"},
    "critique": {"group": "Script", "needs": "judgement", "quality": "critical"},
    "revision": {"group": "Script", "needs": "long output", "quality": "high"},
    # Repurpose
    #
    # Two tasks rather than one because they answer different questions and one is
    # far harder than the other. `thesis` decides what a set of clips is *about* —
    # the editorial argument that makes an edit more than a bag of clips, and the
    # thing the reused-content policy calls "editing that tells a story". That is
    # judgement, and it is the difference between a monetisable video and a
    # compilation. `commentary` then writes to fixed cut timings, which is closer
    # to constrained prose than to judgement.
    "thesis": {"group": "Repurpose", "needs": "judgement, JSON", "quality": "critical"},
    "commentary": {"group": "Repurpose", "needs": "timed prose, JSON", "quality": "high"},
    # SEO
    "titles": {"group": "SEO", "needs": "judgement, JSON", "quality": "critical"},
    "description": {"group": "SEO", "needs": "prose", "quality": "high"},
    "tags": {"group": "SEO", "needs": "mechanical", "quality": "low"},
    "chapters": {"group": "SEO", "needs": "mechanical, JSON", "quality": "low"},
    # Assets
    "thumbnail": {"group": "Assets", "needs": "judgement, JSON", "quality": "medium"},
    # Channel launch
    "positioning": {"group": "Channel", "needs": "judgement", "quality": "high"},
    "naming": {"group": "Channel", "needs": "judgement", "quality": "high"},
    "about": {"group": "Channel", "needs": "prose", "quality": "high"},
    "visuals": {"group": "Channel", "needs": "judgement", "quality": "medium"},
    "series": {"group": "Channel", "needs": "structure, JSON", "quality": "medium"},
    "backlog": {"group": "Channel", "needs": "volume, JSON", "quality": "medium"},
}


@dataclass
class ModelSpec:
    """One routable model."""

    provider: Provider
    model: str
    label: str = ""
    base_url: str = ""
    #: USD per million tokens. Both zero for local models — that is what keeps the
    #: budget ceiling honest rather than charging for electricity.
    input_per_m: float = 0.0
    output_per_m: float = 0.0
    #: Whether this model can be relied on to emit strict JSON.
    json_mode: bool = True
    context: int = 128_000
    temperature: float = 1.0
    #: Name of the environment variable holding *this* model's key, when it must
    #: not be the provider's default one.
    #:
    #: `base_url` makes every OpenAI-compatible gateway routable — Groq, DeepSeek,
    #: OpenRouter, Together, LM Studio, vLLM — and without this the bearer sent to
    #: all of them was `OPENAI_API_KEY`. That is a single credential with two
    #: incompatible jobs: point it at a gateway and thumbnail generation breaks
    #: (`providers/images.py` always calls api.openai.com), leave it a real OpenAI
    #: key and it is handed to a third party on every routed stage. Naming a
    #: separate variable per model is the way out; empty keeps the old behaviour.
    api_key_env: str = ""

    @property
    def is_local(self) -> bool:
        return self.provider == "ollama"

    @property
    def temperature_policy(self) -> str:
        """What this model will accept for `temperature`: any value, the default, or none.

        The frontier Anthropic models dropped the sampling parameters, and dropped
        them loudly: `temperature` comes back as a 400 rather than being ignored, so
        a route to one of them fails on its first call. Sonnet 5 is in between — the
        field is still accepted, but only at its default.

        Derived from the model id rather than stored as a field, so a `routing.json`
        written before this existed is fixed by upgrading rather than by re-saving it.
        Persisted catalogues are exactly where the stale entries live.
        """
        if self.provider == "openai" and self.model.startswith(_OPENAI_REASONING):
            return "default-only"
        if self.provider != "anthropic":
            return "any"
        if self.model.startswith(_NO_SAMPLING):
            return "none"
        if self.model.startswith(_DEFAULT_SAMPLING_ONLY):
            return "default-only"
        return "any"

    @property
    def max_tokens_field(self) -> str:
        """What this provider calls the output ceiling on /chat/completions.

        OpenAI's reasoning models renamed it and reject the old spelling outright.
        Decided per model rather than per provider because `base_url` points the
        same transport at Groq, DeepSeek, OpenRouter, Together, LM Studio and
        vLLM, none of which have followed the rename — sending them
        `max_completion_tokens` would break every one.
        """
        if self.provider == "openai" and self.model.startswith(_OPENAI_REASONING):
            return "max_completion_tokens"
        return "max_tokens"

    @property
    def thinks_by_default(self) -> bool:
        """Whether this model reasons before answering with no prompting to do so.

        Load-bearing for OpenAI's reasoning line as well as Anthropic's: reasoning
        tokens are drawn from the same `max_completion_tokens` budget as the
        answer, so without the reserve a stage that asks for a short answer gets
        its whole allowance spent on thinking and comes back empty.
        """
        if self.provider == "openai":
            return self.model.startswith(_OPENAI_REASONING)
        return self.provider == "anthropic" and self.model.startswith(_THINKS_BY_DEFAULT)

    @property
    def is_free(self) -> bool:
        return self.input_per_m == 0.0 and self.output_per_m == 0.0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_m + output_tokens * self.output_per_m) / 1_000_000

    def key(self) -> str:
        return f"{self.provider}:{self.model}"


# Models known to the system out of the box. Users can add any others; this is a
# starting point, not a whitelist.
CATALOGUE: dict[str, ModelSpec] = {
    spec.key(): spec
    for spec in [
        # Anthropic, best first. The three defaults are Opus 5 / Sonnet 5 / Haiku 4.5;
        # the other two are here because someone will want them, not because the
        # pipeline needs them.
        ModelSpec(
            "anthropic",
            "claude-opus-5",
            "Claude Opus 5",
            input_per_m=5,
            output_per_m=25,
            context=1_000_000,
        ),
        ModelSpec(
            "anthropic",
            "claude-sonnet-5",
            "Claude Sonnet 5",
            input_per_m=3,
            output_per_m=15,
            context=1_000_000,
        ),
        ModelSpec(
            "anthropic",
            "claude-haiku-4-5-20251001",
            "Claude Haiku 4.5",
            input_per_m=1,
            output_per_m=5,
            context=200_000,
        ),
        # Twice the price of Opus 5 for the hardest reasoning there is. Not a default:
        # a script pipeline is not where that money buys the most.
        ModelSpec(
            "anthropic",
            "claude-fable-5",
            "Claude Fable 5",
            input_per_m=10,
            output_per_m=50,
            context=1_000_000,
        ),
        # The previous default. Kept because routes that name it are already saved on
        # people's machines, and a missing catalogue entry silently re-routes a stage.
        ModelSpec(
            "anthropic",
            "claude-opus-4-8",
            "Claude Opus 4.8",
            input_per_m=5,
            output_per_m=25,
            context=1_000_000,
        ),
        # OpenAI, best first. The 5.6 line is the current one; ids and prices were
        # read from the live /v1/models listing and the published pricing table
        # rather than remembered, because a wrong id is a 404 at stage three of
        # seventeen and a wrong price silently corrupts the per-video cost ledger.
        #
        # All of these are reasoning models: `max_completion_tokens`, no
        # temperature, and a thinking reserve. See `_OPENAI_REASONING`.
        ModelSpec(
            "openai", "gpt-5.6-sol", "GPT-5.6 Sol", input_per_m=5, output_per_m=30, context=272_000
        ),
        ModelSpec(
            "openai",
            "gpt-5.6-terra",
            "GPT-5.6 Terra",
            input_per_m=2,
            output_per_m=12,
            context=1_050_000,
        ),
        ModelSpec(
            "openai",
            "gpt-5.6-luna",
            "GPT-5.6 Luna",
            input_per_m=0.2,
            output_per_m=1.2,
            context=1_050_000,
        ),
        # Six times Sol's price. Here because someone will want it for a critique
        # pass, not because the pipeline needs it.
        ModelSpec(
            "openai",
            "gpt-5.5-pro",
            "GPT-5.5 Pro",
            input_per_m=30,
            output_per_m=180,
            context=1_050_000,
        ),
        # Kept for routes already saved on people's machines. A missing catalogue
        # entry silently re-routes a stage, which is worse than a stale option.
        ModelSpec("openai", "gpt-4o", "GPT-4o", input_per_m=2.5, output_per_m=10),
        ModelSpec("openai", "gpt-4o-mini", "GPT-4o mini", input_per_m=0.15, output_per_m=0.6),
        # Gemini. `-preview` in an id is the provider's own warning that it can be
        # withdrawn; the Pro tier has no stable id yet, so it is offered with that
        # caveat rather than left out of a table whose whole job is choice.
        ModelSpec(
            "gemini",
            "gemini-3.1-pro-preview",
            "Gemini 3.1 Pro (preview)",
            input_per_m=2,
            output_per_m=12,
            context=1_000_000,
        ),
        ModelSpec(
            "gemini",
            "gemini-3.6-flash",
            "Gemini 3.6 Flash",
            input_per_m=1.5,
            output_per_m=7.5,
            context=1_000_000,
        ),
        ModelSpec(
            "gemini",
            "gemini-3.5-flash-lite",
            "Gemini 3.5 Flash Lite",
            input_per_m=0.3,
            output_per_m=2.5,
            context=1_000_000,
        ),
        ModelSpec(
            "gemini", "gemini-2.0-flash", "Gemini 2.0 Flash", input_per_m=0.1, output_per_m=0.4
        ),
        # Local. Free, private, and no rate limit — at a real cost in JSON reliability
        # for the smaller ones.
        ModelSpec("ollama", "llama3.1:8b", "Llama 3.1 8B (local)", json_mode=True, context=128_000),
        ModelSpec("ollama", "qwen2.5:14b", "Qwen 2.5 14B (local)", json_mode=True, context=32_000),
        ModelSpec("ollama", "qwen2.5:32b", "Qwen 2.5 32B (local)", json_mode=True, context=32_000),
        ModelSpec("ollama", "mistral:7b", "Mistral 7B (local)", json_mode=False, context=32_000),
        ModelSpec("ollama", "gemma2:9b", "Gemma 2 9B (local)", json_mode=False, context=8_000),
    ]
}

# Sensible defaults: the best model on the tasks that decide whether a video works,
# a cheap one on the mechanical tasks.
#
# The picks, and why they are these and not the tier above or below:
#
#   * **critical → Opus 5.** Hook, draft, critique and titles are the whole product.
#     Opus 5 reasons before answering by default, which is exactly what a critique
#     pass is, and it is the strongest model at Opus pricing. Fable 5 is stronger
#     still and twice the price; on a 2,000-word script that difference is cents, but
#     it buys the least here of anywhere in the pipeline — the ceiling on a hook is
#     the research behind it, not the model's reasoning depth.
#   * **high / medium → Sonnet 5.** Near-Opus quality on structure and prose at 60%
#     of the input price. Research and beats are shape-following, not judgement.
#   * **low → Haiku 4.5.** Tags and chapters are extraction. Paying Opus rates to
#     turn a transcript into timestamps is money set on fire.
DEFAULT_ROUTES: dict[str, str] = {
    task: (
        "anthropic:claude-opus-5"
        if meta["quality"] == "critical"
        else "anthropic:claude-haiku-4-5-20251001"
        if meta["quality"] == "low"
        else "anthropic:claude-sonnet-5"
    )
    for task, meta in TASKS.items()
}

#: The same three-tier shape as `DEFAULT_ROUTES`, expressed once per provider.
#:
#: Tiers, not individual tasks: what a task needs is already recorded in
#: `TASKS[...]["quality"]`, and repeating that judgement per provider is how the
#: table would drift.
_TIERS: dict[Provider, dict[str, str]] = {
    "anthropic": {
        "critical": "anthropic:claude-opus-5",
        "default": "anthropic:claude-sonnet-5",
        "low": "anthropic:claude-haiku-4-5-20251001",
    },
    "openai": {
        "critical": "openai:gpt-5.6-sol",
        "default": "openai:gpt-5.6-terra",
        "low": "openai:gpt-5.6-luna",
    },
    "gemini": {
        "critical": "gemini:gemini-3.1-pro-preview",
        "default": "gemini:gemini-3.6-flash",
        "low": "gemini:gemini-3.5-flash-lite",
    },
}

#: Which provider to prefer when more than one is configured. Anthropic first for
#: the reasons argued above `DEFAULT_ROUTES`; the rest is a stable order rather
#: than a claim that one is better than the next.
_PROVIDER_PREFERENCE: tuple[Provider, ...] = ("anthropic", "openai", "gemini")


def recommended_routes(configured: set[str]) -> dict[str, str]:
    """The best model for each task among the providers that have a key.

    `DEFAULT_ROUTES` names Anthropic for everything, which is the right default
    and completely useless to someone who has an OpenAI key and no Anthropic one:
    every stage fails at the first call, and the fix — eighteen dropdowns, one at
    a time — is the kind of thing people give up in the middle of. This is that
    fix as one button.

    Falls back to `DEFAULT_ROUTES` when nothing is configured, so the screen shows
    the opinionated default rather than an empty table on a fresh install.
    """
    provider = next((p for p in _PROVIDER_PREFERENCE if p in configured), None)
    if provider is None:
        return dict(DEFAULT_ROUTES)

    tier = _TIERS[provider]
    return {
        task: tier["critical"]
        if meta["quality"] == "critical"
        else tier["low"]
        if meta["quality"] == "low"
        else tier["default"]
        for task, meta in TASKS.items()
    }


@dataclass
class Routing:
    routes: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ROUTES))
    catalogue: dict[str, ModelSpec] = field(default_factory=lambda: dict(CATALOGUE))

    def spec_for(self, task: str) -> ModelSpec:
        """The model routed to a task, falling back rather than failing.

        A missing route is not worth crashing a render over — the fallback is
        reported by `problems()` and shown in the UI.
        """
        key = self.routes.get(task) or DEFAULT_ROUTES.get(task, "")
        spec = self.catalogue.get(key)
        if spec is None:
            spec = self.catalogue.get(DEFAULT_ROUTES.get(task, "")) or next(
                iter(self.catalogue.values())
            )
        return spec

    def set_route(self, task: str, model_key: str) -> None:
        if task not in TASKS:
            raise KeyError(f"unknown task {task!r}")
        if model_key not in self.catalogue:
            raise KeyError(f"unknown model {model_key!r}")
        self.routes[task] = model_key

    def set_all(self, model_key: str) -> None:
        """Route everything to one model — the 'run it all locally' button."""
        if model_key not in self.catalogue:
            raise KeyError(f"unknown model {model_key!r}")
        self.routes = {task: model_key for task in TASKS}

    def add_model(self, spec: ModelSpec) -> str:
        self.catalogue[spec.key()] = spec
        return spec.key()

    def problems(self) -> list[dict]:
        """Routing choices that will produce worse output, stated plainly.

        These are warnings, not errors. Running the whole pipeline on a local 7B is a
        legitimate thing to want; being surprised by the results is not.
        """
        out: list[dict] = []
        for task, meta in TASKS.items():
            spec = self.spec_for(task)

            if "JSON" in meta["needs"] and not spec.json_mode:
                out.append(
                    {
                        "task": task,
                        "severity": "warn",
                        "message": (
                            f"{spec.label or spec.model} is unreliable at strict JSON, "
                            f"which this task requires. Expect retries and occasional "
                            f"stage failures."
                        ),
                    }
                )

            if meta["quality"] == "critical" and spec.is_local and spec.context < 32_000:
                out.append(
                    {
                        "task": task,
                        "severity": "warn",
                        "message": (
                            f"{task} is one of the three stages that decide whether a "
                            f"video works. A small local model here saves pennies and "
                            f"costs views."
                        ),
                    }
                )

            if "long output" in meta["needs"] and spec.context < 16_000:
                out.append(
                    {
                        "task": task,
                        "severity": "warn",
                        "message": (
                            f"{spec.label or spec.model} has a {spec.context:,}-token "
                            f"context; long-form drafts will be truncated."
                        ),
                    }
                )
        return out

    def estimated_cost_multiplier(self) -> float:
        """Rough spend relative to the defaults, so the UI can show the trade."""
        base = sum(
            (CATALOGUE[DEFAULT_ROUTES[t]].input_per_m + CATALOGUE[DEFAULT_ROUTES[t]].output_per_m)
            for t in TASKS
        )
        now = sum((self.spec_for(t).input_per_m + self.spec_for(t).output_per_m) for t in TASKS)
        return round(now / base, 3) if base else 0.0

    # ── persistence ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "routes": self.routes,
            "catalogue": {k: asdict(v) for k, v in self.catalogue.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> Routing:
        catalogue = {
            key: ModelSpec(**payload) for key, payload in data.get("catalogue", {}).items()
        }
        return cls(
            routes=data.get("routes", dict(DEFAULT_ROUTES)),
            catalogue=catalogue or dict(CATALOGUE),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Routing:
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            # A corrupt routing file must not brick the engine — defaults still work.
            return cls()

    def reload_from(self, path: Path) -> bool:
        """Re-read the file `save()` writes, **into this object**.

        `load()` above returns a *new* Routing, which is useless to the only
        consumer that matters: every module reaches routing through the singleton
        below (`from engine.models import routing`, or `routing.spec_for(...)` via
        `providers.llm.for_task`). Rebinding a name in one module leaves all of
        them pointing at the old object, so nothing called `load()` and every
        process started on `DEFAULT_ROUTES` — a route set on the Models screen
        survived in the file and was ignored by the engine that wrote it, and the
        API and the worker could route the same task to different models.

        Mutating in place is what makes the hydration visible everywhere. A missing
        or unreadable file leaves the defaults alone: routing that silently emptied
        itself would be worse than routing that is merely not customised.

        Returns whether anything was applied, so startup can say so in the log.
        """
        if not path.exists():
            return False
        try:
            fresh = Routing.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("could not read routing from {} ({}); keeping defaults", path, exc)
            return False
        self.routes = fresh.routes
        self.catalogue = fresh.catalogue
        return True


routing = Routing()


def routing_path() -> Path:
    from engine.settings import get_settings

    return Path(get_settings().storage_root) / "routing.json"


def hydrate_routing() -> bool:
    """Load the persisted routing into the singleton. Called once per process.

    Both the API's lifespan and the arq worker's startup hook call this, and both
    have to: `routing` is module state, so hydrating in one process says nothing
    about the other. A worker that skipped it ran every stage on `DEFAULT_ROUTES`
    while the API reported the operator's real choice on the Models screen — the
    same task on two different models depending on whether Redis was up.

    Lives here rather than in `main` so the worker does not have to import the
    FastAPI app to reach it.
    """
    path = routing_path()
    if routing.reload_from(path):
        logger.info("routing restored from {}", path)
        return True
    return False

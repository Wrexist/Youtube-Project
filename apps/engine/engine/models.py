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

# Every routable task, with what it actually demands. `quality` is the honest
# guidance shown next to the picker — some of these genuinely do not need a big model.
TASKS: dict[str, dict] = {
    # Script chain
    "research": {"group": "Script", "needs": "long context, JSON", "quality": "high"},
    "angle": {"group": "Script", "needs": "judgement", "quality": "high"},
    "hook": {"group": "Script", "needs": "judgement", "quality": "critical"},
    "beats": {"group": "Script", "needs": "structure, JSON", "quality": "high"},
    "draft": {"group": "Script", "needs": "long output", "quality": "critical"},
    "critique": {"group": "Script", "needs": "judgement", "quality": "critical"},
    "revision": {"group": "Script", "needs": "long output", "quality": "high"},
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
        ModelSpec(
            "anthropic", "claude-opus-4-8", "Claude Opus 4.8", input_per_m=5, output_per_m=25
        ),
        ModelSpec(
            "anthropic", "claude-sonnet-5", "Claude Sonnet 5", input_per_m=3, output_per_m=15
        ),
        ModelSpec(
            "anthropic",
            "claude-haiku-4-5-20251001",
            "Claude Haiku 4.5",
            input_per_m=1,
            output_per_m=5,
        ),
        ModelSpec("openai", "gpt-4o", "GPT-4o", input_per_m=2.5, output_per_m=10),
        ModelSpec("openai", "gpt-4o-mini", "GPT-4o mini", input_per_m=0.15, output_per_m=0.6),
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

# Sensible defaults: the best model on the three tasks that decide whether a video
# works, a cheap one on the mechanical tasks.
DEFAULT_ROUTES: dict[str, str] = {
    task: (
        "anthropic:claude-opus-4-8"
        if meta["quality"] == "critical"
        else "anthropic:claude-haiku-4-5-20251001"
        if meta["quality"] == "low"
        else "anthropic:claude-sonnet-5"
    )
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

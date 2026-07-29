"""Model routing endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.models import CATALOGUE, DEFAULT_ROUTES, TASKS, ModelSpec, routing
from engine.providers.llm import DEFAULT_OLLAMA_URL, LLM, ProviderUnavailable, probe_ollama
from engine.settings import get_settings

router = APIRouter(prefix="/v1/models", tags=["models"])


def _config_path() -> Path:
    return Path(get_settings().storage_root) / "routing.json"


class RouteUpdate(BaseModel):
    task: str
    model: str


class BulkRoute(BaseModel):
    model: str


class AddModel(BaseModel):
    provider: str
    model: str
    label: str = ""
    base_url: str = ""
    input_per_m: float = 0.0
    output_per_m: float = 0.0
    json_mode: bool = True
    context: int = Field(default=128_000, gt=0)


class TaskRoute(BaseModel):
    task: str
    group: str
    needs: str
    quality: str
    model: str
    is_local: bool


class CatalogueEntry(BaseModel):
    key: str
    provider: str
    model: str
    label: str
    is_local: bool
    is_free: bool
    json_mode: bool
    context: int
    input_per_m: float
    output_per_m: float


class RoutingProblem(BaseModel):
    task: str
    #: `Routing.problems()` has always set this. The field was missing here, so
    #: Pydantic dropped it on the way out and the web app could not tell a warning
    #: from anything more serious — every problem rendered identically.
    severity: str = "warn"
    message: str


class ModelsResponse(BaseModel):
    """Everything the Models screen needs, typed.

    A response model rather than a bare dict because the Models screen does real
    work with these — grouping tasks, looking specs up by key, summing a monthly
    cost. Returned untyped, `openapi-typescript` produced `unknown` for every
    field, so the screen could not use them at all and instead re-declared the
    whole shape from `lib/demo.ts` and re-implemented `Routing.problems()` by hand.
    Two copies of one rule set is exactly what packages/contracts exists to stop.
    """

    tasks: list[TaskRoute]
    catalogue: list[CatalogueEntry]
    problems: list[RoutingProblem]
    cost_multiplier: float
    defaults: dict[str, str]


@router.get("")
async def list_models() -> ModelsResponse:
    """Everything the Models screen needs in one call."""
    return ModelsResponse.model_validate(
        {
            "tasks": [
                {
                    "task": task,
                    "group": meta["group"],
                    "needs": meta["needs"],
                    "quality": meta["quality"],
                    "model": routing.spec_for(task).key(),
                    "is_local": routing.spec_for(task).is_local,
                }
                for task, meta in TASKS.items()
            ],
            "catalogue": [
                {
                    "key": key,
                    "provider": spec.provider,
                    "model": spec.model,
                    "label": spec.label or spec.model,
                    "is_local": spec.is_local,
                    "is_free": spec.is_free,
                    "json_mode": spec.json_mode,
                    "context": spec.context,
                    "input_per_m": spec.input_per_m,
                    "output_per_m": spec.output_per_m,
                }
                for key, spec in routing.catalogue.items()
            ],
            "problems": routing.problems(),
            "cost_multiplier": routing.estimated_cost_multiplier(),
            "defaults": DEFAULT_ROUTES,
        }
    )


@router.put("/route")
async def set_route(body: RouteUpdate) -> dict:
    try:
        routing.set_route(body.task, body.model)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    routing.save(_config_path())
    return {"task": body.task, "model": body.model, "problems": routing.problems()}


@router.put("/route/all")
async def set_all(body: BulkRoute) -> dict:
    """Route every task to one model — the 'run it all locally' button."""
    try:
        routing.set_all(body.model)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    routing.save(_config_path())
    return {
        "routed": len(TASKS),
        "model": body.model,
        "problems": routing.problems(),
        "cost_multiplier": routing.estimated_cost_multiplier(),
    }


@router.post("/route/reset")
async def reset() -> dict:
    routing.routes = dict(DEFAULT_ROUTES)
    routing.catalogue.update(CATALOGUE)
    routing.save(_config_path())
    return {"reset": True}


@router.post("/catalogue")
async def add_model(body: AddModel) -> dict:
    """Register any model — a new Ollama pull, an OpenAI-compatible gateway, whatever.

    The catalogue is a starting point, not a whitelist.
    """
    spec = ModelSpec(**body.model_dump())
    key = routing.add_model(spec)
    routing.save(_config_path())
    return {"key": key, "is_local": spec.is_local}


@router.get("/ollama")
async def ollama(base_url: str = DEFAULT_OLLAMA_URL) -> dict:
    """What Ollama actually has installed, so the UI offers real models."""
    result = await probe_ollama(base_url)
    if result["available"]:
        known = {s.model for s in routing.catalogue.values() if s.is_local}
        result["unregistered"] = [m["name"] for m in result["models"] if m["name"] not in known]
    else:
        result["hint"] = (
            "Start Ollama with `ollama serve`, then pull a model — "
            "`ollama pull qwen2.5:14b` is a good default for this system."
        )
    return result


@router.post("/ollama/register")
async def register_ollama(base_url: str = DEFAULT_OLLAMA_URL) -> dict:
    """Add every installed Ollama model to the catalogue in one go.

    `json_mode` is set true because Ollama constrains decoding with `format: json`,
    which is what makes small local models usable for the structured stages. It is
    not a promise the output will be *good*.
    """
    probe = await probe_ollama(base_url)
    if not probe["available"]:
        raise HTTPException(503, f"Ollama unreachable at {base_url}: {probe.get('error')}")

    added = []
    for model in probe["models"]:
        spec = ModelSpec(
            provider="ollama",
            model=model["name"],
            label=f"{model['name']} (local)",
            base_url=base_url,
            json_mode=True,
            context=8_000 if "gemma" in model["name"] else 32_000,
        )
        added.append(routing.add_model(spec))

    routing.save(_config_path())
    return {"added": added, "count": len(added)}


@router.post("/test")
async def test_model(body: BulkRoute) -> dict:
    """Round-trip one model so a broken route is found here, not mid-render."""
    spec = routing.catalogue.get(body.model)
    if spec is None:
        raise HTTPException(404, f"unknown model {body.model!r}")

    try:
        result, completion = await LLM(spec).json(
            'Reply with exactly {"ok": true, "model": "<your model name>"}',
            max_tokens=200,
        )
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        # Reached the model, but it could not produce JSON — worth distinguishing.
        raise HTTPException(
            422, f"{spec.key()} responded but returned unusable JSON: {exc}"
        ) from exc

    return {
        "ok": True,
        "model": spec.key(),
        "replied": result,
        "cost_usd": round(completion.cost_usd, 6),
        "tokens": {"in": completion.input_tokens, "out": completion.output_tokens},
    }

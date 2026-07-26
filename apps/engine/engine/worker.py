"""The arq worker.

Jobs used to run as `asyncio.create_task` inside the API process, which had three
consequences: a long render died with the web process, `max_concurrent_renders`
could only ever be enforced within one process, and a deploy killed whatever was
mid-render.

Run it alongside the API:

    apps/engine/.venv/bin/python -m arq engine.worker.WorkerSettings

Events reach subscribers through Redis pub/sub. The API holds the SSE connections
and the worker does the work, so they are different processes and an in-process
`asyncio.Event` cannot bridge them — the API subscribes to a channel per job and
appends what arrives to the same event log a local run would have written to. The
log stays the single source of truth either way, which is what keeps
`stream_job`'s cursor logic identical for both.

If Redis is not reachable the API falls back to running jobs in-process. That is
deliberate: `npm run dev` plus a single uvicorn should work with nothing else
installed, exactly as it did before this file existed.
"""

from __future__ import annotations

import json
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from loguru import logger

from engine.settings import get_settings

# One channel per job. Subscribing per job rather than filtering one firehose
# keeps a busy queue from waking every open browser tab.
CHANNEL = "studio:job:{}"


def build_redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


# Kept as an alias because `main._relay` and `enqueue` both want the connection
# details, and reading them through one function keeps `redis_url` the only source.
redis_settings = build_redis_settings


async def run_job_task(ctx: dict, job_id: str, start_from: str | None = None) -> str:
    """Execute one workflow. The arq entry point.

    The job row is the input: the worker reads it, runs it and writes it back, so
    it needs nothing from the API process except the id. That is what makes this
    safe to run on a different machine.
    """
    from engine import repository
    from engine.workflows import video

    redis = ctx["redis"]
    jobs = await repository.load_jobs(video.get)
    job = jobs.get(job_id)
    if job is None:
        logger.error("worker asked for unknown job {}", job_id)
        return "unknown"

    job["status"] = "running"

    async def emit(event: dict) -> None:
        job["events"].append(event)
        await redis.publish(CHANNEL.format(job_id), json.dumps(event))
        if event["type"].startswith(("stage.completed", "stage.failed", "workflow.")):
            await repository.save_job(job)

    from engine.workflows.base import WorkflowError

    try:
        await job["workflow"].run(
            job_id=job_id,
            inputs=job["inputs"],
            emit=emit,
            states=job["states"],
            budget_usd=get_settings().max_cost_per_video_usd,
            start_from=start_from,
        )
        job["status"] = "completed"
    except WorkflowError as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        logger.error("job {} failed: {}", job_id, exc)
    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(exc)
        logger.exception("job {} crashed", job_id)
        await emit({"type": "workflow.failed", "job_id": job_id, "error": str(exc)})
    finally:
        await repository.save_job(job)
        # A terminal marker so the API's subscriber closes the stream instead of
        # waiting for an event that is never coming.
        await redis.publish(CHANNEL.format(job_id), json.dumps({"type": "__done__"}))

    return job["status"]


async def enqueue(job_id: str, start_from: str | None = None) -> bool:
    """Hand a job to the worker. False if Redis is not there, so the caller runs it locally."""
    try:
        pool = await create_pool(redis_settings())
        await pool.enqueue_job("run_job_task", job_id, start_from)
        await pool.aclose()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not enqueue {} ({}); running in-process", job_id, exc)
        return False


class WorkerSettings:
    """`python -m arq engine.worker.WorkerSettings`.

    arq reads every field here as a plain class *attribute*, so `redis_settings`
    has to be a `RedisSettings` value — a `@staticmethod` is handed to arq
    unevaluated and fails with `'staticmethod' object has no attribute 'host'`.
    """

    functions: list[Any] = [run_job_task]
    redis_settings: RedisSettings = build_redis_settings()
    max_jobs = 4
    # A long-form render legitimately takes many minutes; arq's default would kill
    # it mid-encode and leave a half-written file.
    job_timeout = 60 * 60
    keep_result = 60 * 60

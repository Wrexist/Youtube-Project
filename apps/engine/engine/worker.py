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
from contextlib import suppress
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from arq.constants import default_queue_name, health_check_key_suffix
from loguru import logger

from engine.settings import get_settings

# One channel per job. Subscribing per job rather than filtering one firehose
# keeps a busy queue from waking every open browser tab.
CHANNEL = "studio:job:{}"

# arq's defaults, named here because `enqueue` has to look the health key up.
QUEUE_NAME = default_queue_name
HEALTH_KEY = default_queue_name + health_check_key_suffix


def build_redis_settings() -> RedisSettings:
    """Connection details for the worker process.

    Keeps arq's generous retry defaults on purpose: the worker is long-running and
    should ride out a Redis restart rather than dying on one refused connection.
    """
    return RedisSettings.from_dsn(get_settings().redis_url)


def probe_redis_settings() -> RedisSettings:
    """Connection details for the API's "is there a worker?" question.

    The same defaults that are right for the worker are wrong here. `enqueue` runs
    inside `POST /v1/jobs`, and with no Redis — the documented zero-config setup,
    where renders run in-process — arq's five retries at one second each meant every
    Generate sat for **five seconds** before falling back to the path it was always
    going to take. Measured, not guessed.

    One attempt, one second. Redis is either there or it is not; asking six times
    does not change the answer, and the caller has a working fallback either way.
    """
    settings = RedisSettings.from_dsn(get_settings().redis_url)
    settings.conn_retries = 1
    settings.conn_retry_delay = 0
    settings.conn_timeout = 1
    return settings


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


async def worker_is_alive(pool) -> bool:
    """Is a worker actually consuming this queue?

    Redis being reachable is not the same question. `docker compose up -d` starts
    Postgres and Redis while the worker is a separate command, which is the
    documented development setup — so keying off Redis alone would enqueue into
    an empty queue and leave every Generate sitting in `running` forever with no
    stage events and nothing in any log.

    arq refreshes `arq:queue:health-check` on a TTL, so the key's presence *is*
    the liveness signal. `WorkerSettings.health_check_interval` below is lowered
    to 30s specifically to make it one: arq's default of an hour would keep a
    dead worker looking alive for an hour.
    """
    try:
        return bool(await pool.exists(HEALTH_KEY))
    except Exception:  # noqa: BLE001 — an unanswerable question is a "no"
        return False


async def enqueue(job_id: str, start_from: str | None = None) -> bool:
    """Hand a job to a worker.

    False means the caller should run it in-process — either Redis is not there,
    or it is but nothing is consuming the queue.
    """
    pool = None
    try:
        # The probe settings, not the worker's: this call is on the request path.
        pool = await create_pool(probe_redis_settings())
        if not await worker_is_alive(pool):
            logger.info(
                "redis is up but no arq worker is consuming {}; running {} in-process. "
                "Start one with: python -m arq engine.worker.WorkerSettings",
                QUEUE_NAME,
                job_id,
            )
            return False
        await pool.enqueue_job("run_job_task", job_id, start_from)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not enqueue {} ({}); running in-process", job_id, exc)
        return False
    finally:
        if pool is not None:
            with suppress(Exception):
                await pool.aclose()


class WorkerSettings:
    """`python -m arq engine.worker.WorkerSettings`.

    arq reads every field here as a plain class *attribute*, so `redis_settings`
    has to be a `RedisSettings` value — a `@staticmethod` is handed to arq
    unevaluated and fails with `'staticmethod' object has no attribute 'host'`.
    """

    functions: list[Any] = [run_job_task]
    redis_settings: RedisSettings = build_redis_settings()
    max_jobs = 4
    # 30s, not arq's default hour. The key's TTL is this + 1, and `enqueue` uses
    # its presence to decide whether a worker exists — an hour-long TTL would let
    # a worker that died at breakfast still look alive at lunch, and every job
    # enqueued in between would hang.
    health_check_interval = 30
    # A long-form render legitimately takes many minutes; arq's default would kill
    # it mid-encode and leave a half-written file.
    job_timeout = 60 * 60
    keep_result = 60 * 60

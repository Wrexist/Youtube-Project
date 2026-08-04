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

import asyncio
import json
from contextlib import suppress
from typing import Any

from arq import create_pool, cron
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

    This used to claim arq's defaults let the worker "ride out a Redis restart".
    They do not, and the difference matters because the sentence was load-bearing:
    `conn_retries`/`conn_retry_delay` bound only the *initial* connect, and with
    `retry_on_error` unset redis-py re-raises the moment an established connection
    is dropped. Measured by stopping Redis under a running job: `emit`'s publish
    raised `ConnectionError: Error 111` and the stage that had already done its work
    was recorded as failed.

    So: a wider connect window, and — the part that was actually missing — a retry
    on a dropped connection, which is what a restart or a failover looks like from
    this side.

    What this still does not buy is immortality. arq's own poll loop has no
    reconnect of its own, so a Redis that stays down past redis-py's retry ends the
    worker process; the supervisor is expected to restart it, and an interrupted job
    comes back resumable. Surviving an outage *mid-stage* is `run_job_task.emit`'s
    job, not this function's.
    """
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    settings = RedisSettings.from_dsn(get_settings().redis_url)
    settings.conn_retries = 10
    settings.conn_retry_delay = 3
    settings.retry_on_timeout = True
    settings.retry_on_error = [RedisConnectionError, RedisTimeoutError]
    return settings


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
    # Zero, not one. arq counts *retries*, not attempts — `if retry < conn_retries`
    # with retry starting at 0 — so `1` here was two connections and two seconds,
    # twice what the paragraph above says this function does.
    settings.conn_retries = 0
    settings.conn_retry_delay = 0
    settings.conn_timeout = 1
    return settings


#: How long the whole "is there a worker?" sequence gets, end to end.
#:
#: `conn_timeout` above bounds only the *connection*: arq passes it as
#: `socket_connect_timeout` and never sets `socket_timeout`, so once a socket is
#: open, every command waits indefinitely. A Redis that accepts connections and
#: then stops answering — paused container, failing over, swapping, blocked on a
#: slow command — therefore hung `enqueue`, which runs inside `POST /v1/jobs`.
#: Not reachable-Redis-is-down, which is handled: reachable-Redis-is-catatonic,
#: where the request never returns at all.
#:
#: Three seconds covers a connect, a key lookup and a push on any Redis worth
#: talking to, and the caller has a working in-process fallback the moment this
#: gives up.
PROBE_BUDGET_S = 3.0


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
    from engine.quota import ledger
    from engine.workflows import video

    redis = ctx["redis"]

    # Everything is inside this try, including the job lookup and the ledger
    # refresh. `__done__` is the only thing that closes the API's relay, and the
    # unknown-job branch used to `return` from *above* the try — so a worker started
    # against a different database (or with STUDIO_PERSIST=false, where there are no
    # rows at all) left every SSE connection for that job open until the browser gave
    # up. The refresh below was outside it for the same reason and would have caused
    # the same hang: it is a database round-trip, so a transient failure there — the
    # exact thing the reconnect loops elsewhere exist for — skipped the `finally` and
    # stranded the stream.
    try:
        # Per job, not just at startup. An arq worker is long-lived — days, on a box
        # that is left running — and everything the *API* process spends in between is
        # invisible to this cache. `check()` before a publish would then be answering
        # from whatever the ledger looked like when the worker booted.
        await ledger.refresh()

        jobs = await repository.load_jobs(video.get)
        job = jobs.get(job_id)
        if job is None:
            logger.error("worker asked for unknown job {}", job_id)
            return "unknown"

        job["status"] = "running"

        async def emit(event: dict) -> None:
            job["events"].append(event)
            # Neither of these may take the run down with them. A stage that has
            # already done its work — a finished render, a completed upload — being
            # recorded as `failed` because Redis blinked while its event was being
            # published is a strictly worse outcome than a lost event, and the
            # event log is rebuilt from the row anyway. `main._persist` has guarded
            # the in-process path this way since it was written; this path never was.
            try:
                await redis.publish(CHANNEL.format(job_id), json.dumps(event))
            except Exception:  # noqa: BLE001
                logger.warning("could not publish {} for job {}", event.get("type"), job_id)
            if event["type"].startswith(("stage.completed", "stage.failed", "workflow.")):
                try:
                    await repository.save_job(job)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to persist job {} at {}", job_id, event.get("type"))

        from engine.workflows.base import WorkflowError

        try:
            # A publish job reaches this process as an id and nothing else, and
            # `youtube_client` is stripped from the stored inputs by design — so
            # without this every publish stage would read `ctx.inputs` and find
            # nothing. See `channels.attach_youtube_client`.
            from engine.api.publishing import attach_youtube_client

            await attach_youtube_client(job)

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

        # Before `__done__`, and separately guarded: the relay re-reads the row the
        # moment it sees the marker, so a save that happens after it would be read
        # too late, and a save that *raises* used to skip the marker altogether and
        # strand the stream.
        try:
            await repository.save_job(job)
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist the final state of job {}", job_id)

        return job["status"]
    finally:
        # A terminal marker so the API's subscriber closes the stream instead of
        # waiting for an event that is never coming. Unconditional: an unknown job,
        # a database that is down, a crash in `load_jobs` — every one of them still
        # has a browser tab waiting on this.
        try:
            await redis.publish(CHANNEL.format(job_id), json.dumps({"type": "__done__"}))
        except Exception:  # noqa: BLE001
            logger.warning("could not publish the terminal marker for job {}", job_id)


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
        #
        # Wrapped in a deadline because arq bounds only the connect. Everything
        # after it — the health-key lookup, the enqueue — would otherwise wait on
        # Redis forever, holding open the POST that triggered it. `wait_for`
        # covers the whole sequence rather than each call, so a Redis that is
        # merely slow cannot spend the budget three times over.
        async def _hand_over() -> bool:
            nonlocal pool
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

        return await asyncio.wait_for(_hand_over(), timeout=PROBE_BUDGET_S)
    except TimeoutError:
        # Distinct from the branch below on purpose: "Redis did not answer in
        # time" and "Redis refused the connection" call for different fixes, and
        # a bare exception message would not tell them apart.
        logger.warning(
            "redis did not answer within {}s; running {} in-process", PROBE_BUDGET_S, job_id
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not enqueue {} ({}); running in-process", job_id, exc)
        return False
    finally:
        if pool is not None:
            with suppress(Exception):
                await pool.aclose()


async def startup(_ctx: dict) -> None:
    """Hydrate anything the worker resolves per job but reads from disk once.

    Routing is the whole list so far, and it has to be here as well as in the
    API's lifespan: the singleton `providers.llm.for_task` resolves through is
    per-process, so a worker that skipped this ran every stage on
    `DEFAULT_ROUTES` while the API reported the operator's real choice on the
    Models screen. Same task, two models, depending on whether Redis happened to
    be up.

    The quota ledger is the other one, and it is the more expensive omission. The
    worker is the process that *uploads*, and `QuotaLedger` starts empty — so a
    fresh worker believed nothing had been spent today and happily started a
    1,600-unit upload on a day the API knew was full. `reserve()` re-reads inside
    its own transaction and would catch it at the last moment, but only after the
    stage had done all its work; hydrating here is what makes the pre-flight
    `check()` mean anything.
    """
    from engine.models import hydrate_routing
    from engine.quota import ledger

    hydrate_routing()

    if get_settings().persist:
        await ledger.load()


async def weekly_review_task(ctx: dict) -> dict:  # noqa: ARG001 — arq passes ctx
    """Re-read what the system has learned, and report what changed.

    Returns the review as a dict so it lands in arq's result store, where it can be
    read back without a database round trip.
    """
    from engine import review

    return (await review.run()).as_dict()


class WorkerSettings:
    """`python -m arq engine.worker.WorkerSettings`.

    arq reads every field here as a plain class *attribute*, so `redis_settings`
    has to be a `RedisSettings` value — a `@staticmethod` is handed to arq
    unevaluated and fails with `'staticmethod' object has no attribute 'host'`.
    The same rule is why `on_startup` below is a plain module-level function
    rather than a method: arq reads `__dict__` and calls what it finds with `ctx`.
    """

    functions: list[Any] = [run_job_task]
    cron_jobs: list[Any] = [
        # Monday 06:00 UTC. Deliberately not "every 7 days from whenever the worker
        # last restarted" — a review that lands on a different weekday each time is
        # one nobody builds a habit of reading.
        #
        # `hour` and `minute` are set explicitly because arq reads an unset field
        # as *every* value: `cron(fn, weekday="mon")` alone runs 1,440 times on
        # Monday, not once. (`second` already defaults to 0, so it needs no help.)
        #
        # `run_at_startup` is off for a different reason: a worker restart is not a
        # week passing, and a review triggered by one consumes the snapshot the
        # real weekly diff was going to compare against.
        cron(
            weekly_review_task,
            weekday="mon",
            hour=6,
            minute=0,
            second=0,
            run_at_startup=False,
        )
    ]
    on_startup = startup
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

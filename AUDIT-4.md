# Audit 4 — self-learning and stalled-generation pass

Date: 2026-07-30

Scope: three targeted loops over the current application after the previous PR. The
priority was to make a visible generation run feel unstuck, make the feedback loop
actually collect what future generations need, and leave a complete phase list for
professionalizing the product without adding dashboard clutter.

## Loop 1 — Checks and stuck-generation behavior

### Findings

1. **Previous pytest failure was environmental, not a code failure.** The documented
   `apps/engine/.venv/bin/python` does not exist in this container, and the global
   Python was missing `pytest-asyncio`, so async tests failed collection before any
   assertions ran.
2. **Long stages still needed an explicit liveness signal.** Research and model calls
   can legitimately be quiet while a provider is working; without periodic progress,
   the Create screen looks stuck even when the workflow is healthy.
3. **The Create screen needed a quieter explanation, not more controls.** A banner is
   acceptable only while a real job is running and must not compete with the single
   primary action.

### Started in this pass

- Installed engine dev dependencies in the current Python environment to reproduce
  real test results.
- Kept the heartbeat progress behavior from the previous patch and verified it with
  the engine test suite.
- Kept the running-state Create notice minimal and transient.

## Loop 2 — Feedback-loop wiring

### Findings

1. **The insight prompt injection was only half of the loop.** New jobs could read
   `RECORDS`, but publishing did not seed `RECORDS`, so Analytics refreshes could not
   match YouTube metrics back to the generated title strategy, hook device,
   thumbnail concept, or script model.
2. **The `performance_records` table existed but had no repository helpers.** Phase 8
   had a schema surface without load/save functions, making the learning ledger
   process-local.
3. **Worker-owned publish jobs needed the same capture path as in-process publishes.**
   A publish completed in an arq worker must update the API mirror after `_resync`,
   or the next generation in this API process will still not learn from it.

### Started in this pass

- Added performance-record load/save helpers in the repository.
- Hydrated `RECORDS` on API startup when persistence is enabled.
- Added capture of completed publish jobs into `RECORDS` and persisted
  `performance_records` after in-process completion and worker resync.
- Added a regression test for converting a completed publish job into a
  `VideoRecord` with the attribution dimensions Analytics needs.

## Loop 3 — Product/professionalization backlog

The following backlog is intentionally phased. It is not a request to turn Studio
into a cockpit; every UI item must preserve one primary action per screen.

### Phase 0 — Keep the gate green

1. Keep engine dev dependencies installable in a clean environment; the test suite
   should not depend on an undocumented global pytest plugin.
2. Add a CI smoke command that runs at least `pytest tests/test_workflow.py
   tests/test_feedback.py tests/test_insights.py -q` from `apps/engine`.
3. Add a repository check that fails when `performance_records` exists in the schema
   but no load/save helper is present.

### Phase 1 — Make generation feel reliable

1. Keep stage heartbeat progress on every long-running stage and ensure progress
   always names the stage.
2. Show the latest progress message and elapsed time in the pipeline row; do not add
   a separate activity log to Create.
3. Add a stalled-worker diagnostic to the Queue card when a job is running but has no
   new event after the expected heartbeat window.
4. Add a retry-from-failed-stage affordance to Queue cards, not only expanded Create
   rows.

### Phase 2 — Close the learning loop

1. Persist every published video's attribution seed immediately after upload:
   title strategy, hook device, thumbnail concept, script model, format, and source
   job id.
2. Refresh Analytics metrics into those records and expose how many records matched
   versus unmatched.
3. Feed only confirmed findings into prompts; keep suggestive findings visible in
   Analytics but out of generation.
4. Add retention-to-beat learning once beat maps are persisted with the record rather
   than inferred from a process-local object.
5. Add an operator-facing “learning status” sentence on Analytics: for example,
   “12 videos measured · 2 confirmed patterns · next generation will use them.”

### Phase 3 — Improve research resilience

1. Cache keyword and source-research digests by topic with provenance and expiry.
2. Make research failure messages name the failing backend and the next operator
   action.
3. Add a configured paid search/keyword provider health check when the free sources
   are blocked.
4. Store research prompts, models, sources, and digest hashes per artifact so
   repeated research can be audited later.

### Phase 4 — Professionalize the app without clutter

1. Add accessible live-region announcements for stage completion.
2. Make Queue cards show “last progress N seconds ago” for running jobs.
3. Keep the Create screen one-action-first; move advanced options behind disclosure.
4. Add a compact learning indicator to Analytics, not to every screen.
5. Preserve mobile approval for Queue and publish actions before expanding mobile
   support elsewhere.

### Phase 5 — Mobile and life-simulator request triage

The custom instruction asks for a life simulator app for iOS, Android, and web. That
is a different product than this repository's YouTube automation app. If it becomes a
real requirement, it should start as a separate product brief and architecture phase,
not be mixed into Studio's render/publish codebase.

## Status after this pass

- Phase 0 item 1: started by installing current dev dependencies and running tests.
- Phase 1 item 1: implemented and tested.
- Phase 2 items 1 and 2 foundation: started by persisting attribution seeds and
  hydrating records on startup.
- Remaining items stay open for future focused PRs.

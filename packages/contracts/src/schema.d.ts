/**
 * GENERATED — do not edit.
 *
 * Source: packages/contracts/openapi.json, exported from the engine.
 * Regenerate: npm run generate --workspace=@studio/contracts
 *
 * CLAUDE.md: "Types come from packages/contracts. Never hand-write a type that
 * mirrors an API response."
 */

export interface paths {
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/analytics/audience": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Audience
         * @description The publish-time profile the scheduler uses, and where it came from.
         */
        get: operations["audience_v1_analytics_audience_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/analytics/daily": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Daily */
        get: operations["daily_v1_analytics_daily_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/analytics/retention/{video_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Retention
         * @description The retention curve with script beats located on it.
         */
        get: operations["retention_v1_analytics_retention__video_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/google": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Begin Auth */
        get: operations["begin_auth_v1_auth_google_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/google/callback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Finish Auth */
        get: operations["finish_auth_v1_auth_google_callback_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/calendar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Calendar */
        get: operations["calendar_v1_calendar_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/calendar/auto": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Auto
         * @description Fill the calendar automatically.
         *
         *     Returns a *plan*, and does not apply it. Scheduling a month of uploads is exactly
         *     the kind of thing that should be reviewed before it happens.
         */
        post: operations["auto_v1_calendar_auto_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/calendar/auto/apply": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Apply Plan
         * @description Book a whole plan, or as much of it as the rules allow.
         *
         *     Two things were wrong with the old version, and they compounded.
         *
         *     It **wrote as it went**, so a malformed entry at position three left one and
         *     two booked and the caller with a 500 — no way to know what had landed short
         *     of re-reading the calendar. Everything is now checked before the first write,
         *     so the outcome is all-or-reported, never half-applied-and-unexplained.
         *
         *     And it **never called `validate_move`**, which `schedule_one` twenty lines
         *     above does on every manual drag. The same times that endpoint 409s — in the
         *     past, too close to another upload, over the day's quota — were persisted here
         *     without complaint, which is how a calendar ends up describing a schedule
         *     YouTube will refuse.
         *
         *     Rejected entries come back in `skipped` with a reason rather than failing the
         *     request: a fourteen-video plan with one bad slot should book thirteen and say
         *     which one it did not.
         */
        post: operations["apply_plan_v1_calendar_auto_apply_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/calendar/schedule": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Schedule One
         * @description A manual drag. Validated, and warned about even when permitted.
         */
        post: operations["schedule_one_v1_calendar_schedule_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/calendar/schedule/{video_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Unschedule */
        delete: operations["unschedule_v1_calendar_schedule__video_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/calendar/slots": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Slots
         * @description Ranked publish times, so the calendar can show *why* a slot is good.
         */
        get: operations["slots_v1_calendar_slots_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/channels": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Channels */
        get: operations["list_channels_v1_channels_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/channels/launch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Launch */
        post: operations["launch_v1_channels_launch_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/channels/launch/apply": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Apply
         * @description Push what the API can actually set onto a connected channel.
         *
         *     Only the description, keywords and country. The name and handle are not settable
         *     through the Data API at all — those stay on the manual checklist permanently.
         */
        post: operations["apply_v1_channels_launch_apply_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/channels/launch/{launch_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Launch */
        get: operations["get_launch_v1_channels_launch__launch_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/channels/limits": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Limits
         * @description The constraints the designer works within, so the UI can show live counters.
         */
        get: operations["limits_v1_channels_limits_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/files/{key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get File
         * @description Serve a generated artifact.
         *
         *     `ObjectStore.url()` has always pointed here and this route did not exist, so
         *     nothing could show a thumbnail or play a render — the Library and the variant
         *     picker had URLs that 404'd.
         *
         *     Three separate checks, because this is the one endpoint that turns a string from
         *     a client into a filesystem read: the prefix must be one we publish, the suffix
         *     must be a media type we produce, and `store` must agree the resolved path is
         *     still inside the storage root.
         */
        get: operations["get_file_v1_files__key__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/insights": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Insights
         * @description Findings, each carrying its verdict and sample size.
         *
         *     Suggestive findings are returned so the user can see them; only confirmed ones
         *     are ever fed back into generation.
         */
        get: operations["insights_v1_insights_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/insights/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Refresh Insights
         * @description Pull the last 90 days and re-join metrics onto stored provenance.
         */
        post: operations["refresh_insights_v1_insights_refresh_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Jobs
         * @description Every job, newest first.
         *
         *     This did not exist, so the Queue and Library had nothing to read and rendered
         *     demo data permanently — generate a video and neither screen would ever change.
         *     They are the two screens someone looks at immediately after pressing Generate.
         *
         *     `status` filters to one state; the Library asks for `completed` and the Queue
         *     takes everything.
         */
        get: operations["list_jobs_v1_jobs_get"];
        put?: never;
        /** Create Job */
        post: operations["create_job_v1_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Job */
        get: operations["get_job_v1_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Cancel Job
         * @description Stop a job and tell everyone watching.
         *
         *     The status change alone was not enough. `stream_job` parks on
         *     `await waiting.wait()` and only re-checks the status when that Event fires, so
         *     without the `_wake` below every open SSE connection hung forever — the browser
         *     tab sat on a spinner for a job that had already stopped. And the row was never
         *     written, so the cancellation survived only until the next restart, where it came
         *     back as `interrupted`.
         */
        post: operations["cancel_job_v1_jobs__job_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/edit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Edit Stage
         * @description Accept a user edit and re-run from that point.
         *
         *     This is the interaction the Create screen is built around: change the hook, and
         *     everything downstream regenerates while the research above it is left alone.
         */
        post: operations["edit_stage_v1_jobs__job_id__edit_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Stream Job
         * @description Live progress.
         *
         *     Past events are replayed first so a reload mid-render shows the full pipeline
         *     rather than resuming from a blank screen.
         *
         *     Each subscriber holds a cursor into `job["events"]` — the log is the only
         *     source of truth, and reading it by position is what makes this correct for
         *     more than one viewer. The previous version replayed the log *and then* drained
         *     a shared queue that still held those same events, so everything before a
         *     subscriber connected arrived twice; and because a queue hands each item to
         *     exactly one consumer, two open tabs split the stream between them and both
         *     rendered an incomplete pipeline.
         *
         *     A stream opened against a stale mirror converges on the row rather than
         *     trusting the signal. `waiting.wait()` alone assumed something in *this* process
         *     would eventually fire it, which is true for an in-process job and not for a
         *     worker-run one: if the relay died, nothing ever wakes the stream and the tab
         *     spins on a job that finished ten minutes ago. For worker-owned jobs the wait is
         *     bounded and the row is re-read on each timeout.
         */
        get: operations["stream_job_v1_jobs__job_id__events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Publish Job
         * @description Publish a finished video. **This is the approval gate.**
         *
         *     `CLAUDE.md` non-negotiable #3: nothing publishes without an explicit approval
         *     gate. That gate is this endpoint — it is never a stage of the `video` workflow,
         *     because a workflow that publishes as its last step has no gate at all.
         *
         *     Auto-publish (`automation.py`) calls the same function, so manual and unattended
         *     publishing share one code path and one set of blockers. Skipping the checks is
         *     not offered: a series with `auto_publish=True` skips the *waiting*, not the
         *     *checks*.
         */
        post: operations["publish_job_v1_jobs__job_id__publish_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/rerun": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Rerun Stage
         * @description Re-run one stage and everything downstream of it.
         *
         *     Distinct from `/edit`, which *replaces* a stage's value and keeps it DONE. This
         *     discards the value and regenerates — which is what the Create screen's "Re-run
         *     from here" means, and what its own caption promises: "Everything below this
         *     stage regenerates. Nothing above it is touched."
         *
         *     That control existed and called `console.log`. It could not call `/edit`,
         *     because doing so needs the stage's current value and the API never gives the
         *     client one — `GET /v1/jobs/{id}` returns a `summary` string, not the object.
         */
        post: operations["rerun_stage_v1_jobs__job_id__rerun_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Models
         * @description Everything the Models screen needs in one call.
         */
        get: operations["list_models_v1_models_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models/catalogue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Add Model
         * @description Register any model — a new Ollama pull, an OpenAI-compatible gateway, whatever.
         *
         *     The catalogue is a starting point, not a whitelist.
         */
        post: operations["add_model_v1_models_catalogue_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models/ollama": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ollama
         * @description What Ollama actually has installed, so the UI offers real models.
         */
        get: operations["ollama_v1_models_ollama_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models/ollama/register": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Register Ollama
         * @description Add every installed Ollama model to the catalogue in one go.
         *
         *     `json_mode` is set true because Ollama constrains decoding with `format: json`,
         *     which is what makes small local models usable for the structured stages. It is
         *     not a promise the output will be *good*.
         */
        post: operations["register_ollama_v1_models_ollama_register_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models/route": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Route */
        put: operations["set_route_v1_models_route_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models/route/all": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set All
         * @description Route every task to one model — the 'run it all locally' button.
         */
        put: operations["set_all_v1_models_route_all_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models/route/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reset */
        post: operations["reset_v1_models_route_reset_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Test Model
         * @description Round-trip one model so a broken route is found here, not mid-render.
         */
        post: operations["test_model_v1_models_test_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/quota": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Quota */
        get: operations["quota_v1_quota_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/setup": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Status
         * @description Everything the setup screen needs, and no secret material.
         */
        get: operations["status_v1_setup_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/setup/diagnostics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Diagnostics
         * @description What `scripts/doctor.py` prints, as data.
         *
         *     The same checks, so the terminal and the screen cannot disagree. It existed
         *     only as a script, which meant the answer to "why did my render fail" lived
         *     behind remembering a virtualenv path — on the machine of someone who has, by
         *     construction, just failed to set this up.
         *
         *     `network=false` skips the grounding probe, which reaches out to YouTube with a
         *     six-second timeout. The Setup screen loads with it off and turns it on for the
         *     explicit "Run checks" press.
         */
        get: operations["diagnostics_v1_setup_diagnostics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/setup/keys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Save Keys
         * @description Write credentials to `.env` and make them live in this process.
         *
         *     Returns the new status rather than an acknowledgement, so the screen shows
         *     what is actually in force instead of what it hoped it had set.
         */
        put: operations["save_keys_v1_setup_keys_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/workflows/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Describe Workflow
         * @description The stage graph, so the UI can render the pipeline before anything runs.
         */
        get: operations["describe_workflow_v1_workflows__name__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AddModel */
        AddModel: {
            /**
             * Api Key Env
             * @default
             */
            api_key_env: string;
            /**
             * Base Url
             * @default
             */
            base_url: string;
            /**
             * Context
             * @default 128000
             */
            context: number;
            /**
             * Input Per M
             * @default 0
             */
            input_per_m: number;
            /**
             * Json Mode
             * @default true
             */
            json_mode: boolean;
            /**
             * Label
             * @default
             */
            label: string;
            /** Model */
            model: string;
            /**
             * Output Per M
             * @default 0
             */
            output_per_m: number;
            /** Provider */
            provider: string;
        };
        /** Assignment */
        Assignment: {
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Video Id */
            video_id: string;
        };
        /** AutoScheduleRequest */
        AutoScheduleRequest: {
            /**
             * Horizon Days
             * @default 28
             */
            horizon_days: number;
            /**
             * Long Per Week
             * @default 1
             */
            long_per_week: number;
            /**
             * Shorts Per Week
             * @default 3
             */
            shorts_per_week: number;
            /** Videos */
            videos: components["schemas"]["PendingVideo"][];
        };
        /** BulkRoute */
        BulkRoute: {
            /** Model */
            model: string;
        };
        /** CalendarResponse */
        CalendarResponse: {
            /** Quota By Day */
            quota_by_day: {
                [key: string]: number;
            };
            /** Scheduled */
            scheduled: components["schemas"]["ScheduledVideo"][];
        };
        /** CatalogueEntry */
        CatalogueEntry: {
            /** Context */
            context: number;
            /** Input Per M */
            input_per_m: number;
            /** Is Free */
            is_free: boolean;
            /** Is Local */
            is_local: boolean;
            /** Json Mode */
            json_mode: boolean;
            /** Key */
            key: string;
            /** Label */
            label: string;
            /** Model */
            model: string;
            /** Output Per M */
            output_per_m: number;
            /** Provider */
            provider: string;
            /**
             * Supports Web Search
             * @default false
             */
            supports_web_search: boolean;
        };
        /**
         * CredentialStatus
         * @description One credential, as the setup screen sees it. Never carries the value.
         */
        CredentialStatus: {
            /** Configured */
            configured: boolean;
            /** Effort */
            effort: string;
            /** Env */
            env: string;
            /** Group */
            group: string;
            /** Label */
            label: string;
            /** Required */
            required: boolean;
            /** Tail */
            tail: string;
            /** Unlocks */
            unlocks: string;
            /** Url */
            url: string;
            /** Without It */
            without_it: string;
        };
        /** DiagnosticCheck */
        DiagnosticCheck: {
            /** Command */
            command: string;
            /** Detail */
            detail: string;
            /** Fix */
            fix: string;
            /** Href */
            href: string;
            /** Key */
            key: string;
            /** Level */
            level: string;
            /** Name */
            name: string;
        };
        /** Diagnostics */
        Diagnostics: {
            /** Blockers */
            blockers: number;
            /** Checks */
            checks: components["schemas"]["DiagnosticCheck"][];
            /** Ready */
            ready: boolean;
            /** Warnings */
            warnings: number;
        };
        /** EditRequest */
        EditRequest: {
            /** Stage */
            stage: string;
            /** Value */
            value: unknown;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** JobRequest */
        JobRequest: {
            /**
             * Aspect
             * @default 9:16
             */
            aspect: string;
            /**
             * Format
             * @default short
             */
            format: string;
            /** Target Seconds */
            target_seconds?: number | null;
            /** Topic */
            topic: string;
            /** Voice */
            voice?: string | null;
            /**
             * Workflow
             * @default video
             */
            workflow: string;
        };
        /**
         * JobSummary
         * @description One job, as the Queue and Library list them.
         *
         *     A response model rather than a bare dict because both screens do arithmetic and
         *     filtering on these fields; without it the generated TypeScript types every one
         *     as `unknown` and the UI has to cast, which is what `packages/contracts` exists
         *     to prevent.
         */
        JobSummary: {
            /** Cost Usd */
            cost_usd: number;
            /** Created At */
            created_at?: string | null;
            /** Current Stage */
            current_stage?: string | null;
            /** Error */
            error?: string | null;
            /** Id */
            id: string;
            /** Render Key */
            render_key?: string | null;
            /** Stages Done */
            stages_done: number;
            /** Stages Total */
            stages_total: number;
            /** Status */
            status: string;
            /** Thumbnail Keys */
            thumbnail_keys?: string[];
            /** Topic */
            topic: string;
            /** Updated At */
            updated_at?: string | null;
            /** Workflow */
            workflow: string;
        };
        /**
         * KeyUpdate
         * @description A save. Only the names present here are touched.
         *
         *     `dict[str, str]` rather than a field per credential so that adding one to
         *     `CREDENTIALS` needs no change here — the allowlist is `CREDENTIALS` itself,
         *     checked at write time, which keeps the two from drifting apart.
         */
        KeyUpdate: {
            /** Values */
            values?: {
                [key: string]: string;
            };
        };
        /** LaunchRequest */
        LaunchRequest: {
            /**
             * Country
             * @default US
             */
            country: string;
            /**
             * Language
             * @default en
             */
            language: string;
            /** Niche */
            niche: string;
        };
        /**
         * ModelsResponse
         * @description Everything the Models screen needs, typed.
         *
         *     A response model rather than a bare dict because the Models screen does real
         *     work with these — grouping tasks, looking specs up by key, summing a monthly
         *     cost. Returned untyped, `openapi-typescript` produced `unknown` for every
         *     field, so the screen could not use them at all and instead re-declared the
         *     whole shape from `lib/demo.ts` and re-implemented `Routing.problems()` by hand.
         *     Two copies of one rule set is exactly what packages/contracts exists to stop.
         */
        ModelsResponse: {
            /** Catalogue */
            catalogue: components["schemas"]["CatalogueEntry"][];
            /** Cost Multiplier */
            cost_multiplier: number;
            /** Defaults */
            defaults: {
                [key: string]: string;
            };
            /** Problems */
            problems: components["schemas"]["RoutingProblem"][];
            /** Tasks */
            tasks: components["schemas"]["TaskRoute"][];
        };
        /**
         * PendingVideo
         * @description One video waiting for a slot.
         *
         *     Was `dict`, which meant a missing `id` reached `auto_schedule` and came back
         *     as a 500 with a KeyError — four ordinary malformed payloads all did. Declaring
         *     the shape turns each of them into a 422 naming the field.
         */
        PendingVideo: {
            /**
             * Format
             * @default short
             * @enum {string}
             */
            format: "short" | "long";
            /** Id */
            id: string;
            /** Ready At */
            ready_at?: string | null;
            /**
             * Title
             * @default
             */
            title: string;
        };
        /**
         * PublishRequest
         * @description Choices the operator makes at the approval gate.
         *
         *     All optional: the defaults publish the top-scored title and first thumbnail
         *     immediately and publicly, which is the common case.
         */
        PublishRequest: {
            /**
             * Chosen Thumbnail Index
             * @default 0
             */
            chosen_thumbnail_index: number;
            /**
             * Chosen Title Index
             * @default 0
             */
            chosen_title_index: number;
            /**
             * Made For Kids
             * @default false
             */
            made_for_kids: boolean;
            /** Playlist Id */
            playlist_id?: string | null;
            /**
             * Privacy
             * @default public
             */
            privacy: string;
            /** Publish At */
            publish_at?: string | null;
        };
        /**
         * QuotaResponse
         * @description The daily YouTube budget.
         *
         *     A response model rather than a bare dict because this is the one payload the
         *     web app does arithmetic on — without it the generated TypeScript types every
         *     field as `unknown` and the UI has to cast, which is the hand-written mirror
         *     `packages/contracts` exists to prevent.
         */
        QuotaResponse: {
            /** Breakdown */
            breakdown: {
                [key: string]: number;
            };
            /** By Day */
            by_day: {
                [key: string]: number;
            };
            /** Day */
            day: string;
            /** Limit */
            limit: number;
            /** Remaining */
            remaining: number;
            /** Spent */
            spent: number;
            /** Uploads Left */
            uploads_left: number;
        };
        /** RerunRequest */
        RerunRequest: {
            /** Stage */
            stage: string;
        };
        /** RouteUpdate */
        RouteUpdate: {
            /** Model */
            model: string;
            /** Task */
            task: string;
        };
        /** RoutingProblem */
        RoutingProblem: {
            /** Message */
            message: string;
            /**
             * Severity
             * @default warn
             */
            severity: string;
            /** Task */
            task: string;
        };
        /** ScheduleRequest */
        ScheduleRequest: {
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Video Id */
            video_id: string;
        };
        /** ScheduledVideo */
        ScheduledVideo: {
            /** At */
            at: string;
            /** Video Id */
            video_id: string;
        };
        /** SetupStatus */
        SetupStatus: {
            /** Can Connect */
            can_connect: boolean;
            /** Can Publish */
            can_publish: boolean;
            /** Can Render */
            can_render: boolean;
            /** Channels */
            channels: string[];
            /** Credentials */
            credentials: components["schemas"]["CredentialStatus"][];
            /** Env Path */
            env_path: string;
            /** Missing Required */
            missing_required: string[];
            /** Worker Running */
            worker_running: boolean;
        };
        /** TaskRoute */
        TaskRoute: {
            /** Group */
            group: string;
            /** Is Local */
            is_local: boolean;
            /** Model */
            model: string;
            /** Needs */
            needs: string;
            /** Quality */
            quality: string;
            /** Task */
            task: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** ApplyRequest */
        engine__api__channels__ApplyRequest: {
            /**
             * Confirm Channel Created
             * @default false
             */
            confirm_channel_created: boolean;
            /** Launch Id */
            launch_id: string;
        };
        /** ApplyRequest */
        engine__api__publishing__ApplyRequest: {
            /** Assignments */
            assignments: components["schemas"]["Assignment"][];
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    audience_v1_analytics_audience_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    daily_v1_analytics_daily_get: {
        parameters: {
            query?: {
                days?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retention_v1_analytics_retention__video_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                video_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    begin_auth_v1_auth_google_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    finish_auth_v1_auth_google_callback_get: {
        parameters: {
            query?: {
                code?: string | null;
                state?: string | null;
                error?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    calendar_v1_calendar_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CalendarResponse"];
                };
            };
        };
    };
    auto_v1_calendar_auto_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AutoScheduleRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    apply_plan_v1_calendar_auto_apply_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["engine__api__publishing__ApplyRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    schedule_one_v1_calendar_schedule_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    unschedule_v1_calendar_schedule__video_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                video_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    slots_v1_calendar_slots_get: {
        parameters: {
            query?: {
                days?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_channels_v1_channels_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    launch_v1_channels_launch_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LaunchRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    apply_v1_channels_launch_apply_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["engine__api__channels__ApplyRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_launch_v1_channels_launch__launch_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                launch_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    limits_v1_channels_limits_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    get_file_v1_files__key__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                key: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    insights_v1_insights_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    refresh_insights_v1_insights_refresh_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    list_jobs_v1_jobs_get: {
        parameters: {
            query?: {
                status?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobSummary"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_job_v1_jobs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JobRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_job_v1_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_job_v1_jobs__job_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    edit_stage_v1_jobs__job_id__edit_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EditRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_job_v1_jobs__job_id__events_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    publish_job_v1_jobs__job_id__publish_post: {
        parameters: {
            query?: {
                force?: boolean;
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PublishRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rerun_stage_v1_jobs__job_id__rerun_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RerunRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_models_v1_models_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelsResponse"];
                };
            };
        };
    };
    add_model_v1_models_catalogue_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AddModel"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ollama_v1_models_ollama_get: {
        parameters: {
            query?: {
                base_url?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    register_ollama_v1_models_ollama_register_post: {
        parameters: {
            query?: {
                base_url?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_route_v1_models_route_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RouteUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_all_v1_models_route_all_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BulkRoute"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reset_v1_models_route_reset_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    test_model_v1_models_test_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BulkRoute"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    quota_v1_quota_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuotaResponse"];
                };
            };
        };
    };
    status_v1_setup_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SetupStatus"];
                };
            };
        };
    };
    diagnostics_v1_setup_diagnostics_get: {
        parameters: {
            query?: {
                network?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Diagnostics"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_keys_v1_setup_keys_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KeyUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SetupStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    describe_workflow_v1_workflows__name__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}

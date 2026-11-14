# ADR 0014: Self-hosted Langfuse for traces and prompts

Date: 2026-11-14. Status: accepted.

## Context

PLAN.md Phase 5 asks for a Langfuse trace per PR with nested spans per file and hunk, tagged with
the repo, PR and model, and for every prompt to come from Langfuse prompt management with
`production` and `staging` labels. The user chose to self-host Langfuse rather than send diffs to
Langfuse Cloud. Langfuse is at major version 4 now: web, worker, ClickHouse, S3-compatible storage,
Redis and Postgres; the Python SDK (4.15) is built on OpenTelemetry.

## Decision

**Compose profile `langfuse`** (`make langfuse-up`, UI at http://localhost:3001). Adapted from
the reference compose file in langfuse/langfuse, pinned to 4.72.0 (the newest release more than
two weeks old). Changes from the reference: no AI gateway; every port on 127.0.0.1, and only the
UI and MinIO published; Langfuse's own Postgres and Redis (it needs Redis with `noeviction`, and
the main Postgres volume already exists, so an init script could not add a database to it);
telemetry off; memory limits that fit next to the main stack (`docs/memory-budget.md`).
`langfuse-web` needed `NODE_OPTIONS=--max-old-space-size=1024` and 1.5 GB: under 1 GB it ran out
of heap during start-up. Its healthcheck calls `$HOSTNAME`, because Next.js does not listen on
127.0.0.1 inside the container.

**Access.** First start creates the `prism` organisation and project with API keys
(`LANGFUSE_INIT_*`). No user is created: you sign up in the UI with your own password, and
`LANGFUSE_DEFAULT_ORG_ROLE`/`PROJECT_ROLE` make you owner of the project. The keys and the
internal passwords default to fixed local development values, like the main Postgres password.
That is fine for a server bound to 127.0.0.1 and wrong anywhere else: set your own in `.env`.

**Tracing** (`prism/observability/tracing.py`, `PRISM_TRACING=langfuse`). A small `Span`
interface; `NoopTracer` is the default, so nothing imports Langfuse unless asked. One trace per
review, named `review`, with tags `repo:<id>`, `model:<reviewer>`, `verifier:<name>` and
`pr:<n>`, and repo, PR, head SHA and model as metadata on every observation
(`propagate_attributes`). Children: `planner` (risk, selection, routing), one `file <path>` span
per `Send()` of the fan-out with a `hunk <header>` child per hunk (a generation for a local model
call, linked to the Langfuse prompt it used, or a span for an escalated or cached hunk), `sast`,
`context`, `verifier` (every decision) and `aggregator`. A generation records how long it waited
for a model slot: all hunks queue at once, so most of a span can be waiting, and the trace would
otherwise make the model look slower than it is. Everything sent is masked with the same
redaction as the Claude requests (ADR 0013).

**Prompts** (`prism/observability/prompts.py`, `PRISM_PROMPT_SOURCE=langfuse`). Three prompts:
the reviewer's system prompt and the two Claude rubrics. The code keeps the reference text;
`prism prompts push --label staging` copies it into Langfuse as a new version; promotion to
`production` happens in Langfuse. A review reads each prompt by `PRISM_PROMPT_LABEL` and falls
back to the code's text when Langfuse is off, down or has no such prompt, so a review never fails
over a prompt. The reviewer's prompt is the one the model was fine-tuned on and is also baked
into its Modelfile: serving another text to it is allowed (that is how a staging prompt gets
tried on the base model) but logged as a warning.

**Checked against the running server.** Three traced reviews: the `review` trace for the
c8ac930 diff has 19 observations: the five file spans and the SAST tool start within 3 ms of
each other, the nine generations sit under their files with `model=prism-reviewer:v2` and
`promptName=prism-reviewer-system` v1, the first four generations waited 0 s for a slot and the
next four 8 to 15 s (the concurrency limit of 4 at work), and the tags read back as above. Read
through `GET /api/public/v2/observations`; v4's older trace endpoints are switched off in its
events-only mode.

## Consequences

- Two runs of a handoff review make two traces: the first ends with the verifier pending. The
  second reuses cached replies, so its hunk spans are marked `cached`, not generations.
- The Claude verifier's calls are one `verifier` span, not a generation per batch; the API
  backend's token use is in its cost, not in Langfuse's usage fields.
- The profile adds about 1.9 GiB in use. Start it only when looking at traces.

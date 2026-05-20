# DAP architecture

A bird's-eye view of how the pieces fit together. Pair this with
[`docs/projects.md`](projects.md) (the workspace layer that composes
pipelines), [`docs/runtimes.md`](runtimes.md) (how to plug in a new
executor) and the auto-generated FastAPI docs at
`http://127.0.0.1:7333/docs` (full endpoint reference) once you start
the engine.

## Components

```
                ┌──────────────────────┐
                │  Dashboard (Next.js) │   visual editor + run viewer
                │  React Flow + TanQ   │
                └──────────┬───────────┘
                           │ REST (CORS-allowed
                           │  localhost:3000)
                           ▼
┌──────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
│ dap CLI      │──▶│  Engine (FastAPI)    │◀──│ tests/smoke/         │
│ (Typer)      │   │  - REST endpoints    │   │ FastAPI TestClient   │
│ start/stop/  │   │  - LangGraph runner  │   └──────────────────────┘
│ status/init  │   │  - SQLAlchemy + DB   │
└──────────────┘   └────────┬─────────────┘
                            │
                            │ delegates per-node execution to
                            ▼
                ┌──────────────────────┐
                │  Runtime adapters    │   api-call (Anthropic SDK),
                │  (RuntimeAdapter     │   bash (subprocess), and stubs
                │   Protocol)          │   for claude-code, gemini, codex,
                └──────────────────────┘   aider, http
```

### `apps/engine` — orchestrator

FastAPI app that owns:

- **REST API** — agents, pipelines, runs, runtimes (full reference at
  `/docs` while running).
- **Pipeline runner** — converts a pipeline DAG into a `langgraph.graph.
  StateGraph(PipelineState)`, attaches an `AsyncSqliteSaver` checkpointer,
  and `ainvoke`s it.
- **Persistence** — SQLAlchemy 2 + SQLite (WAL). Two databases on disk:
  `state.db` (Alembic-managed app schema) and `state.checkpoints.db`
  (LangGraph-owned, sibling file).
- **Process supervision** — a `RunRegistry` maps `run_id` → live
  `asyncio.Task` so the API can pause/abort a background run; the lifespan
  marks any `running` rows from a prior crash as `failed` on startup.

Engine code is split by responsibility:

- `api/` owns HTTP shape: route paths, dependencies, auth/ownership gates,
  status codes, audit event placement, and mapping repository exceptions to
  `HTTPException`.
- `execution/` owns orchestration: LangGraph graph construction, checkpoint
  rewind, runtime policy enforcement during execution, background task
  finalisation, and subprocess cancellation semantics.
- `persistence/` owns database access: SQLAlchemy ORM models, query shapes,
  ownership filters, model-to-contract conversion, migrations, and session
  lifecycle helpers.
- `auth/` owns user management, API tokens, password/OAuth flows, audit-log
  recording helpers, and security policy helpers.

The engine should not depend on dashboard code. It may depend on
`packages/types`, `packages/runtimes`, and `packages/prompt-dsl`.

### `apps/dashboard` — Next.js 15

App-router single-page app. Generated OpenAPI types mirror the engine's
FastAPI contracts; a small handwritten typed fetch client wraps those
contracts for browser code. TanStack Query polls active runs every 2s.
React Flow powers the pipeline designer.

Dashboard code owns browser UX, forms, client-side validation hints,
React Query cache keys, and the Next.js API proxy routes under
`apps/dashboard/src/app/api`. It does not own server authorization rules or
schema truth: if a dashboard type disagrees with the engine OpenAPI schema,
the engine schema wins and `pnpm --dir apps/dashboard check:api` should fail
until the generated types are refreshed.

### `apps/cli` — Typer launcher

Wraps `dap-engine` for users who don't want to deal with `uv run` directly.
PID-file-based lifecycle (`init`, `start`, `stop`, `status`).

### `packages/types` — shared schema

Pydantic v2 models that show up on both ends of every wire boundary:
`Agent`, `Pipeline`, `PipelineState`, `Project`, `Run`, `RuntimeTask`,
`RuntimeResult`, `HealthStatus`. Backend tests import these models directly.
The dashboard consumes their wire representation through generated OpenAPI
TypeScript types plus local aliases in `apps/dashboard/src/lib/api/types.ts`.

This package owns Python contract models and state-schema validation. It
should not import `apps/engine`, SQLAlchemy models, FastAPI routers, or
dashboard code. Changes here are API-contract changes and need tests at the
engine boundary that serialise or validate the affected model.

### Workspace layer — `Project`

A `Project` (added in v0.6) is a workspace: a binding of *workflow
kinds* (configure / plan / develop / verify / release, plus any
custom kind you want) to specific `pipeline_id`s, plus context that
the engine folds into every run — `working_directory`, default
`branch`, project-scoped `env_vars`. Pipelines stay reusable; the
project glues them to a concrete codebase. Runs created via
`POST /projects/{id}/run/{kind}` carry `Run.project_id` for grouping
in the dashboard. Full operator guide in
[`docs/projects.md`](projects.md).

### `packages/runtimes` — adapter implementations

The `RuntimeAdapter` Protocol (in `packages/types`) plus concrete
implementations. `RuntimeRegistry` is in-memory, populated at engine
startup with a fixed default set. See [`runtimes.md`](runtimes.md).

Runtime adapters own provider/CLI/subprocess integration details and expose
them through the shared `RuntimeAdapter` protocol. They should not reach into
engine persistence or FastAPI request state. Engine code passes runtime
configuration, state projection, and cancellation context into adapters.

### `packages/prompt-dsl` — Jinja → XML compiler

Renders an agent's `prompt_template` against a `PipelineState` projection,
parses the result with `defusedxml`, and validates it against an XML schema.
Sandboxed Jinja environment (`SandboxedEnvironment`) — agent templates
can't reach attributes outside the state projection.

The prompt DSL owns template rendering and XML validation only. Runtime
selection, persistence, cost accounting, and run finalisation belong to the
engine.

### `packages/code-review-council` — automated review helper

Small package used by CI review workflows to coordinate model-backed review
output. It is not part of the engine runtime path and should not import
engine internals. Changes here should be validated through package tests and
the GitHub Actions workflow that invokes it.

## Ownership Boundaries

### Routers vs services vs persistence

The current codebase uses FastAPI routers plus focused execution and
persistence modules rather than a broad generic service layer. Put logic in
the narrowest owner that can enforce the rule consistently:

| Concern | Owner | Examples |
|---|---|---|
| HTTP shape | `apps/engine/src/dap_engine/api/*` | path params, query params, response models, status codes, dependency injection |
| Auth and ownership at request edge | API routers + repository filters | `current_active_user`, admin checks, 404 anti-enumeration |
| Audit placement | API routers or auth helpers | audit a denied dry-run or token revoke at the point the user action is accepted/denied |
| Business orchestration | `execution/*` or a focused helper module | run/resume/rewind, batch execution, stale run finalisation |
| Database query shape | `persistence/*` | joins, indexes, pagination, ownership predicates, ORM-to-Pydantic mapping |
| Schema contract | `packages/types` and FastAPI response models | `PipelineState`, `Run`, `Agent`, generated OpenAPI components |

Guidelines:

- Keep routers thin, but not empty. They should validate request-local
  invariants, enforce auth dependencies, call persistence/execution helpers,
  and translate domain errors into HTTP responses.
- Do not hide SQLAlchemy queries in API routers. If a query is reused, has
  ownership semantics, or affects pagination/listing shape, it belongs in
  `persistence/`.
- Do not put HTTP exceptions, FastAPI dependencies, or request objects in
  `persistence/` modules. Raise repository/domain errors there and let routers
  map them to HTTP.
- Use focused helper modules when orchestration is bigger than one route but
  not a database query, e.g. runtime policy checks or background execution
  dispatch.
- Add a new abstraction only when two or more callers share behavior or when a
  route is mixing unrelated responsibilities. Avoid creating a generic service
  layer that just forwards calls.

### ORM model ownership

`dap_engine.persistence.model_base.Base` is the one shared declarative base.
Bounded-context ORM declarations live in:

- `auth_models.py` — `UserORM`, `OAuthAccountORM`, `ApiTokenORM`,
  `AuditLogORM`.
- `settings_models.py` — `InstanceEnvVarORM`.
- `agent_models.py` — `AgentORM`, `AgentVersionORM`.
- `pipeline_models.py` — `PipelineORM`, `PipelineVersionORM`.
- `project_models.py` — `ProjectORM`.
- `run_models.py` — `RunORM`, `BatchRunORM`, `StateSnapshotORM`,
  `NodeExecutionLogORM`.

`persistence/models.py` remains the backward-compatible import surface for
callers and for Alembic/metadata discovery. New persistence code may import
from either the bounded module or `persistence.models`; broad call-site churn
is not required just to satisfy module purity.

### Dashboard API types and client ownership

The dashboard API contract has three layers:

- `apps/dashboard/src/lib/api/types.gen.ts` is generated by
  `openapi-typescript` from the engine OpenAPI schema. Do not edit it by
  hand.
- `apps/dashboard/src/lib/api/types.ts` owns dashboard-friendly aliases,
  narrow helper types, and compatibility wrappers around generated types.
- `apps/dashboard/src/lib/api/client.ts` owns browser fetch behavior:
  base URL/proxy path, credentials, JSON parsing, error formatting, and typed
  endpoint helpers.

When an engine route changes request or response shape:

1. Update the FastAPI route and Pydantic response/request contracts.
2. Regenerate or check dashboard types with `pnpm --dir apps/dashboard check:api`
   or `pnpm --dir apps/dashboard gen:api`.
3. Update handwritten aliases/client helpers only when the dashboard needs a
   stable local convenience type or endpoint wrapper.
4. Keep React components and hooks consuming the typed client; avoid raw
   `fetch("/api/...")` in feature components unless there is a narrow reason.

## State schema

`PipelineState` (`packages/types/src/dap_types/state.py`) is the single
source of truth. Categories:

- **Metadata / control** — `run_id`, `repo`, `branch`, `commit_sha`.
- **Task selection** — `available_issues`, `selected_issue_ids`.
- **Test generation** — `tests_generated`, `test_files`,
  `test_generation_errors`.
- **Execution loop** — `max_attempts`, `attempt`, `tests_passed`,
  `last_test_output`.
- **Implementation** — `modified_files`, `implementation_notes`.
- **Verification** — `verification_status`, `verification_reason`.
- **Final output** — `final_status` (`running` | `success` | `failed` |
  `aborted` | `paused`).

`extra="forbid"` is set: unknown fields fail validation. To add a field,
edit `state.py`, bump the schema reference (`PipelineState.v2`), and write
a migration if persisted snapshots need it.

### Versioning

Both `Agent` and `Pipeline` are immutable per version. Editing either
through the API creates a new `*_versions` row and bumps `current_version`
on the parent. Runs always reference a specific `pipeline_version`, so
in-flight runs are unaffected by edits.

## Run lifecycle

```
              POST /runs
                  │
                  ▼
           ┌─────────────┐    POST /runs/{id}/pause
           │   running   │───────────────────────────┐
           └─────┬───────┘                           │
                 │                                   ▼
   ┌─────────────┼─────────────┐                ┌────────┐
   │             │             │                │ paused │
   ▼             ▼             ▼                └────┬───┘
┌──────┐    ┌────────┐    ┌─────────┐                │
│ succ │    │ failed │    │ aborted │                │
└──────┘    └───┬────┘    └─────────┘                │
                │                                    │
                │  POST /runs/{id}/nodes/{n}/retry   │
                │  POST /runs/{id}/nodes/{n}/skip    │
                │  POST /runs/{id}/resume   ◀────────┘
                ▼
            (back to running)
```

- `running` — background `asyncio.Task` is alive; `RunRegistry.is_running`
  returns true.
- `paused` — task was cancelled with the pause flag set; the LangGraph
  checkpoint is intact, `ended_at` is null.
- `aborted` / `success` / `failed` — terminal; `ended_at` set, metrics
  aggregated from `node_execution_logs`.
- Retry/skip-node accept paused **or** failed; they branch a new tip from a
  historical LangGraph checkpoint and resume.

## LangGraph checkpoints

Each run uses its `run_id` as the LangGraph `thread_id`. Between every node
LangGraph writes a checkpoint to `state.checkpoints.db` via
`AsyncSqliteSaver`. This is what makes pause/resume/retry/skip possible —
the engine doesn't replay state itself, it asks LangGraph to fork from an
earlier checkpoint via `aupdate_state`.

`PipelineRunner.rewind_and_run(target_node, mode)` is the integration
point:

- **retry** — `aget_state_history` to find the checkpoint where
  `target_node` is staged as next, then `aupdate_state(values=None)` to
  branch a fresh tip from there. The next `ainvoke(None)` re-runs the node.
- **skip** — same rewind, but `aupdate_state(values={}, as_node=target)` so
  LangGraph treats the node as already done. The next `ainvoke(None)` goes
  straight to its successors.

If the target node never executed during the prior run (graph never reached
it), `aget_state_history` yields no matching checkpoint and the runner
raises `CheckpointNotFoundError` — surfaced to the API as a `failed`
finalisation.

## Background execution model

`POST /runs` returns `201` with the Run row in `running` state immediately;
the actual execution happens in an `asyncio.create_task` invocation
(`_execute_run_background` in `apps/engine/.../api/runs.py`). The task
opens its own SQLAlchemy session via the engine's
`session_factory` so it doesn't share state with the request handler's
session.

Cancellation paths:

- `await run_registry.abort(run_id)` — `task.cancel()`; the task's
  `except asyncio.CancelledError` block finalises the row as `aborted`.
- `await run_registry.pause(run_id)` — same, but the registry sets a
  `_paused` flag first; the cancel handler reads it via `was_paused()` and
  finalises as `paused` instead (no `ended_at`).

The `bash` runtime additionally puts each subprocess in a new POSIX
session group so the cancellation signal reaches background jobs the shell
may have spawned (see `packages/runtimes/README.md`).

## Persistence

Two SQLite databases per `.dap` directory:

| File                    | Owner               | Purpose                                 |
| ----------------------- | ------------------- | --------------------------------------- |
| `state.db`              | Alembic + ORM       | Agents, pipelines, runs, snapshots, logs |
| `state.checkpoints.db`  | `AsyncSqliteSaver`  | LangGraph step-by-step graph state      |

Both run with `journal_mode=WAL`. App tables: `agents`,
`agent_versions`, `pipelines`, `pipeline_versions`, `projects`
(v0.6 — single unversioned row per project, FK target for
`runs.project_id`), `runs`, `state_snapshots`,
`node_execution_logs`. Alembic revisions live under
`apps/engine/src/dap_engine/alembic/`; legacy in-code migrations for older
SQLite upgrades remain in `apps/engine/src/dap_engine/persistence/migrations.py`.
The migration policy and developer workflow are documented in
[`database-migrations.md`](database-migrations.md).

Alembic startup and legacy migration tests must continue importing
`dap_engine.persistence.models.Base`; bounded model modules are considered
registered only when importing `persistence.models` exposes the same metadata.

## Test and CI Expectations

Use the smallest local test set that covers the area, then rely on CI for the
full matrix.

| Area changed | Local expectation | CI expectation |
|---|---|---|
| Engine API routers | `uv run ruff check`, `uv run mypy` for touched modules, targeted `tests/smoke/test_*` for the route group | `Python (ruff + pytest)` plus smoke integration shards |
| Persistence models/queries/migrations | Targeted persistence/API smoke tests plus `Base.metadata` table check; migration tests for schema-sensitive changes | Python fast gate, smoke integration shards, packaging where standalone DB upgrade is involved |
| Execution runner/runtime policy | Runner lifecycle tests, pause/resume, retry/skip, runtime-policy tests | Python fast gate and smoke integration shards |
| Dashboard API/client/components | `pnpm --dir apps/dashboard check:api`, `typecheck`, `lint`, `test`, and build when UI behavior changes | `Dashboard (typecheck + build)` and Playwright smoke |
| Runtime adapters | Adapter unit tests plus integration smoke when subprocess/provider behavior changes | Python fast gate, smoke shards, and any runtime-specific workflow coverage |
| Packaging/CLI/release docs | CLI or standalone tests when packaging behavior changes | Packaging build and bundle-wheel jobs |
| Documentation only | Link/path sanity and relevant nearby docs review | Review workflow; code-heavy gates may still run by policy |

CI check names that matter for normal PRs:

- `Python (ruff + pytest)` — required fast Python gate.
- `Python smoke integration (1/3..3/3)` — broader engine/persistence/runtime
  integration coverage.
- `Python deps CVE scan` — locked Python dependency audit.
- `Dashboard (typecheck + build)` — generated API type check, TypeScript,
  lint, tests, and Next.js build.
- `Playwright smoke` — browser-level smoke coverage.
- `build` / `bundle-wheel` — packaging sanity.
- `review` / `gemini-review` — automated review council output.

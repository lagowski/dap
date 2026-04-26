# DAP architecture

A bird's-eye view of how the pieces fit together. Pair this with
`docs/runtimes.md` (how to plug in a new executor) and the auto-generated
FastAPI docs at `http://127.0.0.1:7333/docs` (full endpoint reference) once
you start the engine.

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

### `apps/dashboard` — Next.js 15

App-router single-page app. Hand-rolled API types mirror the Pydantic
models in `packages/types`. TanStack Query polls active runs every 2s.
React Flow powers the pipeline designer.

### `apps/cli` — Typer launcher

Wraps `dap-engine` for users who don't want to deal with `uv run` directly.
PID-file-based lifecycle (`init`, `start`, `stop`, `status`).

### `packages/types` — shared schema

Pydantic v2 models that show up on both ends of every wire boundary:
`Agent`, `Pipeline`, `PipelineState`, `Run`, `RuntimeTask`, `RuntimeResult`,
`HealthStatus`. The dashboard hand-mirrors these in TypeScript; backend
tests import them directly.

### `packages/runtimes` — adapter implementations

The `RuntimeAdapter` Protocol (in `packages/types`) plus concrete
implementations. `RuntimeRegistry` is in-memory, populated at engine
startup with a fixed default set. See [`runtimes.md`](runtimes.md).

### `packages/prompt-dsl` — Jinja → XML compiler

Renders an agent's `prompt_template` against a `PipelineState` projection,
parses the result with `defusedxml`, and validates it against an XML schema.
Sandboxed Jinja environment (`SandboxedEnvironment`) — agent templates
can't reach attributes outside the state projection.

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

Both run with `journal_mode=WAL`. Five app tables: `agents`,
`agent_versions`, `pipelines`, `pipeline_versions`, `runs`,
`state_snapshots`, `node_execution_logs`. Migrations live in
`apps/engine/migrations/`.

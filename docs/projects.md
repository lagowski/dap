# Projects — workspaces that compose pipelines

DAP pipelines are **reusable** — a single "develop" pipeline can drive
many codebases. A **project** is the workspace that glues that pipeline
to a concrete repo, default branch, env vars, and the workflow kinds
your team works in. One pipeline, many projects; one project, many
workflow phases.

This doc is the operator-facing guide. Pair it with
[`docs/architecture.md`](architecture.md) (where projects sit in the
component picture) and the FastAPI auto-docs at
`http://127.0.0.1:7333/docs`.

## Concept

```
   Pipeline (reusable)         Project (workspace)
   ┌──────────────────┐        ┌────────────────────────┐
   │ "lint+test"      │◀──┐    │ working_directory      │
   │  - selector node │   │    │ repo_url               │
   │  - tests node    │   │    │ default_branch         │
   │  - report node   │   │    │ env_vars (overlay)     │
   └──────────────────┘   ├────│ pipelines:             │
                          │    │   develop → "...id..." │
                          │    │   verify  → "...id..." │
                          │    └────────────────────────┘
                          │
                          │  Project binds a workflow kind
                          │  (develop / verify / …) to a
                          │  pipeline_id and runs it with
                          │  the project's context filled in.
```

A run created via `POST /projects/{id}/run/{kind}`:

1. Looks up `pipeline_id = project.pipelines[kind]` (404 if not bound).
2. Stamps `Run.project_id` so the dashboard can group runs per project.
3. Seeds `PipelineState.repo` from `project.repo_url` and
   `PipelineState.branch` from `project.default_branch` (caller can
   override per-call).
4. Sets `RuntimeTask.working_directory = project.working_directory`
   so subprocess-spawning adapters (`bash`, `claude-code`, `codex`,
   `gemini-cli`) run in the right cwd.
5. Layers `project.env_vars` between engine env (base) and per-agent
   `runtime_config.env` (highest) — see
   [`packages/runtimes/README.md#env-layering-v06`](../packages/runtimes/README.md#env-layering-v06).

## Recommended workflow kinds

The dashboard surfaces these slots first-class on the project detail
page; the engine accepts any string as `kind`, so you're free to add
custom phases your team needs.

| Kind        | Typical purpose                                              |
| ----------- | ------------------------------------------------------------ |
| `configure` | One-time setup: scan repo structure, register agents         |
| `plan`      | Per-task: break an issue into actionable steps               |
| `develop`   | Per-task: implement the planned steps                        |
| `verify`    | Per-task: run tests, type-checks, lint                       |
| `release`   | Per-cycle: tag, push, deploy                                 |

Custom examples that show up in real projects: `hotfix`, `backfill`,
`migrate`, `rollback`. Add them via the dashboard's
**Add custom workflow** button or by including them in
`POST /projects` / `PUT /projects/{id}` `pipelines` map.

## Recipes

### Recipe 1 — "My first project"

Minimal flow: create one pipeline, bind it to `develop`, trigger from
the dashboard.

```bash
# 1. Create a stub pipeline (assume the AGENT_ID env var is already
#    set from earlier `dap` walkthroughs).
PIPELINE_ID=$(curl -s -X POST http://127.0.0.1:7333/pipelines \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "Hello pipeline",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id":"n1","agent_id":"'$AGENT_ID'","position":{"x":0,"y":0}}],
        "edges": [{"id":"e1","source":"n1","target":"__end__"}],
        "defaults": {"max_attempts":3,"budget_limit_usd":5,"approval_required_nodes":[]}
      }' | jq -r .id)

# 2. Create a project pointing at a local checkout, bind the pipeline
#    to the develop kind.
PROJECT_ID=$(curl -s -X POST http://127.0.0.1:7333/projects \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "my-first-project",
        "working_directory": "/Users/me/code/sample",
        "default_branch": "main",
        "pipelines": {"develop": "'$PIPELINE_ID'"}
      }' | jq -r .id)

# 3. Trigger.
curl -s -X POST http://127.0.0.1:7333/projects/$PROJECT_ID/run/develop \
  -H 'Content-Type: application/json' -d '{}' | jq .id
```

**Dashboard equivalent:** Pipelines → New (build the same shape via
the designer) → Projects → New → fill name + working_directory →
detail page → bind the pipeline to the `develop` slot → Trigger.

### Recipe 2 — "Issue-to-PR workflow"

Multi-pipeline: separate `configure`, `plan`, `develop`, `verify`
pipelines chained by triggering each in order, with state passed via
`initial_state`. Common for projects where each phase is owned by
different agents (cheap selectors, expensive coders).

```bash
# Project bound to four kinds — one pipeline per phase.
curl -s -X PUT http://127.0.0.1:7333/projects/$PROJECT_ID \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "my-first-project",
        "description": "Issue-to-PR cycle",
        "working_directory": "/Users/me/code/sample",
        "default_branch": "main",
        "pipelines": {
          "configure": "'$CONFIGURE_PIPE'",
          "plan":      "'$PLAN_PIPE'",
          "develop":   "'$DEVELOP_PIPE'",
          "verify":    "'$VERIFY_PIPE'"
        },
        "env_vars": {}
      }'

# Run plan first — captures `selected_issue_ids` and `implementation_notes`
# on the run's final state. The dashboard surfaces them on the run page.
PLAN_RUN=$(curl -s -X POST http://127.0.0.1:7333/projects/$PROJECT_ID/run/plan \
  -H 'Content-Type: application/json' \
  -d '{"initial_state": {"available_issues": [{"id":123,"title":"…"}]}}' \
  | jq -r .id)

# Wait for completion, then read state — feed selected_issue_ids forward.
curl -s http://127.0.0.1:7333/runs/$PLAN_RUN/state \
  | jq '{selected_issue_ids, implementation_notes}'

# Trigger develop with the carried-forward state.
curl -s -X POST http://127.0.0.1:7333/projects/$PROJECT_ID/run/develop \
  -H 'Content-Type: application/json' \
  -d '{"initial_state": {"selected_issue_ids":[123],"implementation_notes":"…"}}'

# Then verify.
curl -s -X POST http://127.0.0.1:7333/projects/$PROJECT_ID/run/verify \
  -H 'Content-Type: application/json' -d '{}'
```

**Dashboard equivalent:** Project detail → click **Trigger** on each
workflow card in order. The run page shows the final state — paste the
relevant fields into the next phase's "Initial state" textarea.

### Recipe 3 — "Mixed-runtime project"

Combines LLM agents (`api-call`) with `bash` steps (lint, tests) in
the same pipeline. Demonstrates the env layering: project supplies
`WORKSPACE_NAME` and a pre-built virtualenv path; the bash agent's
own `runtime_config.env` adds a per-step toggle.

```bash
curl -s -X POST http://127.0.0.1:7333/projects \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "mixed-runtime-demo",
        "working_directory": "/Users/me/code/sample",
        "default_branch": "develop",
        "pipelines": {"verify": "'$VERIFY_PIPE'"},
        "env_vars": {
          "WORKSPACE_NAME": "demo",
          "PYTHONPATH": "/Users/me/code/sample/.venv/lib/python3.13/site-packages",
          "FEATURE_FLAG_NEW_LINTER": "off"
        }
      }'
```

Inside `VERIFY_PIPE`'s bash agent, `runtime_config.env` can override
that per-step:

```json
{
  "command": "ruff check .",
  "env": {"FEATURE_FLAG_NEW_LINTER": "on"}
}
```

The subprocess sees `FEATURE_FLAG_NEW_LINTER=on` because per-agent
env beats project env. Engine env (with `ANTHROPIC_API_KEY` etc.)
stays the lowest layer for the LLM agents in the same pipeline.

**Why this matters:** the same pipeline can be re-used by other
projects with different `WORKSPACE_NAME` / `PYTHONPATH` values
without touching the agent definitions.

## Lifecycle notes

- **Archive** (`DELETE /projects/{id}`) is soft. Existing runs keep
  their `project_id` for historical inspection. New triggers via
  `POST /projects/{id}/run/{kind}` return **409 Conflict** with
  `Project is archived: {project_id}`.
- **Pipeline binding** is validated at write time. If you try to bind
  a non-existent or archived pipeline, the engine returns 422 with the
  offending ids listed (one for `unknown` and one for `archived`).
- **Editing a project** (`PUT /projects/{id}`) should be treated as a
  full replacement. The request schema supplies defaults for most
  fields, so omitted values are not rejected; they may be reset to
  defaults and overwrite the existing project state. The dashboard
  form preserves bindings + env_vars when you only edit metadata; the
  API does not apply that courtesy automatically, so clients should
  send the complete project object.

## See also

- [`packages/runtimes/README.md`](../packages/runtimes/README.md) —
  full env-layering rules for subprocess adapters.
- [`docs/architecture.md`](architecture.md) — where projects sit in
  the component picture and how `Run.project_id` is wired through.
- [`docs/runtimes.md`](runtimes.md) — adding a new runtime adapter
  (its `RuntimeTask` will receive `project_env_vars` and the project's
  `working_directory` automatically when the run is bound to a
  project).

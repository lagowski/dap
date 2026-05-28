# ops-pipeline

Drop-in pipeline bundle for **command-execution issues** — issues that require
running shell commands on a remote host (benchmarks, migrations, service
restarts) rather than producing a code change.

Instead of the `trigger → coder → PR` sequence used by code-change pipelines,
this bundle wires:

```
__start__ → trigger → specify → executor → done → __end__
```

Import via the dashboard's **Import JSON** button on `/pipelines`, or:

```bash
POST /pipelines/import
Content-Type: application/json
# body: contents of ops-pipeline.pipeline-bundle.json
```

---

## Node table

| Node | Agent template | Runtime | Role | What it does |
|---|---|---|---|---|
| `trigger` | `tpl_ops_trigger` | bash | `post_check` | Validates that `extensions.execution_target` is present; aborts with a clear error if missing |
| `specify` | `tpl_ops_specify` | api-call (glm) | `task_selector` | Validates `execution_commands` is non-empty, produces a human-readable run-plan in `implementation_notes` |
| `executor` | `tpl_ops_executor` | cortex-agent | `post_check` | Runs each command on `execution_target`, collects stdout/stderr + exit codes, writes the full report to `implementation_notes` |
| `done` | `tpl_ops_done` | bash | `post_check` | Posts `implementation_notes` as a GitHub issue comment (if `GITHUB_ISSUE_URL` is set), otherwise logs to engine output |

---

## Extension-key contract

Pass the following keys inside `initial_state.extensions` when triggering a
run. The engine's `POST /runs` 422 guard (introduced alongside this bundle)
rejects `execution_commands` that is not a list.

| Key | Type | Required | Description |
|---|---|---|---|
| `execution_target` | `string` | **yes** | Name of the remote host or Cortex executor node to run commands on |
| `execution_commands` | `list[string]` | **yes** | Ordered list of shell commands to execute |
| `execution_env` | `dict[string, string]` | no | Additional environment variables injected for every command. Pass `GITHUB_ISSUE_URL` here to enable automatic GitHub write-back |

All three keys live in `PipelineState.extensions` (a `dict[str, Any]` field)
and do **not** require any schema change.

---

## Trigger payload example

```bash
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline_id": "<your-imported-pipeline-id>",
    "initial_state": {
      "repo": "owner/repo",
      "branch": "main",
      "extensions": {
        "execution_target": "dixter-pc",
        "execution_commands": [
          "cd /home/dixter/Projects/news-sentiment && python benchmark.py",
          "alembic upgrade head"
        ],
        "execution_env": {
          "PYTHONPATH": "/home/dixter/Projects/news-sentiment",
          "GITHUB_ISSUE_URL": "https://github.com/owner/repo/issues/42"
        }
      }
    }
  }'
```

---

## GitHub write-back ownership

**GitHub write-back is the responsibility of this bundle's `done` node, not
the Cortex executor node** (Dixter999/cortex-project#446). The `done` node
calls `gh issue comment` using the `GITHUB_ISSUE_URL` value from
`extensions.execution_env`. If `GITHUB_ISSUE_URL` is absent the comment step
is silently skipped — results remain available in `state.implementation_notes`
and the run logs at `/runs/<id>`.

The Cortex executor node (Dixter999/cortex-project#446) is responsible solely
for executing commands on the remote host and returning results. It does not
interact with GitHub.

---

## Prerequisites

- **Cortex executor node** (Dixter999/cortex-project#446) must be registered
  as a DAP agent with a known `agent_id`. After import, remap `tpl_ops_executor`
  to your locally registered agent id via the agent edit page
  (`/agents/<id>/edit`) or by editing the bundle JSON before import.
- **`gh` CLI** installed and authenticated (`gh auth login`) on the host
  running the `done` node (the engine host), if GitHub write-back is desired.
- **`GLM_API_KEY`** (or equivalent for your provider) exported in the engine's
  env for the `specify` node. Edit the bundle JSON to swap `glm` → `anthropic`
  / `openai` / `gemini` if preferred.
- **`execution_target`** must be a host or executor-node name that the Cortex
  executor node can reach. See Dixter999/cortex-project#446 for how executor
  targets are registered on the Cortex side.

---

## After import

1. Open the pipeline in the Designer (`/pipelines/<id>/edit`).
2. Edit the `executor` node's agent to point at your locally registered Cortex
   executor agent (the bundle ships a `cortex-agent` runtime stub as
   `tpl_ops_executor`).
3. Trigger a run using the payload example above.
4. Inspect node-by-node logs at `/runs/<id>`.

---

## Relationship to code-change pipelines

This pipeline is a **parallel variant** — it does not replace or modify
`github-issue-triage` or `cortex-github-issue`. Existing code-change pipelines
are unaffected. Choose which pipeline to trigger based on the issue type:

| Issue type | Pipeline |
|---|---|
| Code change (bug fix, feature) | `cortex-github-issue` or `github-issue-triage` |
| Command execution (benchmark, migration, restart) | `ops-pipeline` |

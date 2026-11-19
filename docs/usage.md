# Using DAP: agents, pipelines and logs

Install and start the stack first: see the [README](../README.md) and
[quick-start.md](quick-start.md). Every setting is in [configuration.md](configuration.md).

## Agents

Each agent has:

- A **runtime**: `api-call` for SDK calls; `claude-code`, `gemini-cli`, `codex` or `aider`
  for agentic CLIs; `github` for declarative GitHub reads and writes (read issue/PR,
  comment, update an issue section, create a branch, open a PR, merge a PR pinned to its
  head sha); `bash` for shell; `python-func` for an in-process Python callable; `http` for
  any REST call.
- A **prompt template** (Jinja → XML, validated against `PipelineState`).
- An **`input_schema`** and **`output_schema`** that declare which `PipelineState` fields it
  reads and writes.
- A **role** (`task_selector`, `prompt_builder`, `test_author`, `implementer`, `verifier`,
  `post_check`, or any custom string).

**Test panel (dry run).** On `/agents/<id>/edit` or `/agents/new`, the **Test** panel runs
the agent against sample state without saving anything: no `Run` row, no
`NodeExecutionLog`, no state-machine effects. It shows the rendered XML prompt, the runtime's
real output, and a soft check of that output against `output_schema`. Each dry run is capped
by `DAP_DRY_RUN_BUDGET_USD` (default $0.50). `POST /agents/dry-run` does the same from code.

**Render preview.** `POST /agents/<id>/render-preview` compiles the prompt against sample
state and returns the XML without calling the runtime. It catches Jinja errors and undefined
variables before you spend tokens.

**Archive.** `/agents` has an **Archive** action. The engine refuses with HTTP 409 while any
non-archived pipeline still uses the agent, and lists those pipelines. Archiving is soft:
run history keeps the reference, and the agent leaves the pickers and the active list.

**Versions.** Every save bumps the agent's version, and `/agents/<id>/versions` lists them.
A pipeline pins the agent's current version when you save the pipeline.

## Pipelines

`/pipelines/new` and `/pipelines/<id>/edit` open the React Flow designer:

- Drag agents from the picker onto the canvas and wire edges. Conditional edges support
  `field == value`, `field != value`, `is_null`, `not`, and more, against `PipelineState`.
- The Inspector (right side) shows the agent's prompt, schemas and runtime config, plus a
  **State after this node** view: the combined `output_schema` of every upstream agent.
- **Validate** checks that every declared `input_schema` field is written by some upstream
  node.
- Retry loops, workspace diff state and backend fallback chains are covered in
  [docs/pipelines.md](pipelines.md).

**Run a pipeline.** From `/pipelines`, **Run** opens a dialog for optional `initial_state`
JSON, starts the run and redirects to `/runs/<id>`. That page shows live progress (the
running node, a status timeline for each node, the current phase) by polling
`GET /runs/<id>`. You can **Pause**, **Resume** or **Abort** a running run. On a failed,
paused or aborted run, click a node and pick **Retry** (run it again) or **Skip** (mark it
done and move on).

From a terminal, `dap project run cortex <issue-url>` starts a pipeline run for a GitHub
issue (`cortex` is currently the only pipeline type), and `dap project approve`, `reject`
and `state` drive it from there. They take `--engine` (or `DAP_ENGINE_URL`)
and an API token (or `DAP_AUTH_TOKEN`). See [docs/projects.md](projects.md).

**Approval gates.** A pipeline can mark nodes as needing human approval
(`defaults.approval_required_nodes`). When a run reaches one, it pauses and shows the gate
payload inline (task assignments, the spec, and any non-blocking `finalize_warnings`), so you
can **Approve** or **Reject** without reading logs. A finalize node that emits "READY" goes
to a gate that can be auto-approved; "NEEDS_WORK" goes to one that needs a human. A pipeline
that sets `defaults.requires_terminal_final_status` is marked failed, not successful, if it
ends without reaching a terminal state.

## Logs

Every node execution writes a row to `node_execution_logs` (in `./.dap/state.db`, or in
Postgres when `DAP_DATABASE_URL` is set):

| Column | Content |
|---|---|
| `prompt_xml` | The exact prompt sent to the runtime (after Jinja) |
| `stdout` / `stderr` | Raw runtime output |
| `output_json` | The parsed `<output>{…}</output>` block |
| `tokens_used` / `cost_usd` / `duration_ms` | Telemetry |
| `status` / `error_message` | success / failed / timeout / abort |

Inspect them in the dashboard (`/runs/<id>`, click a node), through the API
(`GET /runs/<id>/nodes/<node_id>`), or directly with
`sqlite3 .dap/state.db "select * from node_execution_logs where run_id = '<id>';"`.
State snapshots after each node are in `state_snapshots`, and the run summary is in
`runs.node_statuses`. The engine API reference is at `http://127.0.0.1:7333/docs` while the
engine runs.

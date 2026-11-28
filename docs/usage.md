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

## Running a bundle from the command line (CI)

`dap pipeline run` imports a `*.pipeline-bundle.json`, runs it to the end and exits with the
run's outcome. With `--headless` it needs no running engine and no dashboard: it starts a
throwaway engine inside the command, on a SQLite database in a temporary directory that is
deleted when the command exits (nothing is written to `./.dap/`), and talks to it in-process
(no port is opened).

```bash
dap pipeline run examples/python-func-pipeline/example.pipeline-bundle.json \
  --headless --approve-gates
```

Each node's status, cost and duration are printed as the run goes. Options:

| Option | Effect |
|---|---|
| `--headless` | Throwaway in-process engine. Without it the command uses the engine at `--engine` (default `http://localhost:7333`, or `DAP_ENGINE_URL`) with `--token` / `DAP_AUTH_TOKEN` |
| `--state KEY=VALUE` | Put `KEY` in `initial_state.extensions` (repeatable; a JSON value such as `3` or `[1,2]` is decoded) |
| `--approve-gates` | Approve every human-approval gate automatically |
| `--json` | Print `{"exit_code", "run", "state"}` with the run's final state on stdout; progress goes to stderr |
| `--timeout SECONDS` | Give up after this long (default 3600) |

Exit codes:

| Code | Meaning |
|---|---|
| `0` | The run succeeded |
| `1` | A node failed (named in the last line) or the run ended failed/aborted |
| `2` | Bad input: an unreadable bundle, a bad `--state`, or a bundle or run the engine refused |
| `3` | The run stopped at a human-approval gate and `--approve-gates` was not given |
| `4` | The run did not finish within `--timeout` |

In headless mode the bundle's own directory is put on the import path, so a `python-func`
package that sits next to the bundle (like `examplepipe/` in the example) imports without an
install. Agents that call a model need their provider keys in the environment, as with
`dap start`.

A GitHub Actions job that runs a bundle on every pull request:

```yaml
jobs:
  pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv tool install dap-cli
      - name: Run the pipeline
        run: |
          dap pipeline run pipelines/review.pipeline-bundle.json \
            --headless --approve-gates --json > run.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: pipeline-run
          path: run.json
```

The step fails when the command exits non-zero, so a failed node fails the job. `dap pipeline run`
is new after `v0.3.0`: until the next release, install from the repository instead
(`uv tool install "git+https://github.com/lagowski/dap#subdirectory=apps/cli"`).

## Pipelines as tools for an agent CLI (`dap mcp`)

`dap mcp` is a stdio [MCP](https://modelcontextprotocol.io) server. An agent CLI such as
Claude Code or Cursor starts it and gets DAP pipelines as tools, so the agent can hand a bounded,
auditable job to a pipeline instead of improvising it.

| Tool | What it does |
| --- | --- |
| `list_pipelines` | The pipelines on the engine: id, name, description, version |
| `describe_pipeline` | One pipeline's nodes (agent, runtime, inputs, outputs), edges, gates, and the state keys a run needs |
| `run_pipeline` | Start a run from a `pipeline_id` or a `bundle_path` (imported first), with an optional `initial_state`; returns the run id |
| `run_status` | The run's `final_status`, the gate it waits at (`paused_at_node`), per-node statuses, cost, and state |
| `approve_gate` | Approve a human-approval gate; the run continues |
| `reject_gate` | Reject a human-approval gate; the run is aborted |

Register it with Claude Code:

```bash
# Against a running engine (dap start), with an API token from the dashboard:
claude mcp add dap --env DAP_AUTH_TOKEN=<token> -- dap mcp

# Or with no engine at all: a throwaway in-process engine that lives as long as the server
claude mcp add dap -- dap mcp --headless
```

Without `--headless` the server uses the engine at `--engine` (default `http://localhost:7333`, or
`DAP_ENGINE_URL`) and authenticates with `--token` / `DAP_AUTH_TOKEN`, like `dap pipeline run`.
With `--headless` nothing is written to the working directory and every run is lost when the
server stops; a `python-func` package next to a bundle passed as `bundle_path` is importable, as
with `dap pipeline run --headless`.

Tool results go through the engine's secret redaction: the value of any environment variable of
the server process (except plain ones such as `PATH` and `HOME`) and the API token come back as
`[REDACTED:<NAME>]`, and secret-shaped tokens (bearer tokens, JWTs, provider keys) are scrubbed.
Agent prompt templates are never returned.

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

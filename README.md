# DAP — Deterministic Agent Pipeline

[![CI](https://github.com/rafeekpro/dap/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/rafeekpro/dap/actions/workflows/ci.yml)

DAP is a local, single-user system for building and executing **deterministic agent pipelines**. Pipelines are versioned DAGs of agents — each agent renders a Jinja → XML prompt and dispatches it to a runtime adapter (Anthropic / OpenAI / Gemini / GLM SDK, claude-code / gemini-cli / codex / aider CLIs, plain bash, or HTTP). Execution runs on LangGraph with full pause / resume / abort / retry / skip control. Anti-emergent by design: the state machine, not the model, decides what runs next.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) 0.8+
- Node 22+
- pnpm 9+

`scripts/setup` checks all four for you, so you don't need to verify by hand. If you do want to install them yourself: `pyenv install 3.13 && pyenv local 3.13`, `curl -LsSf https://astral.sh/uv/install.sh | sh`, `nvm install 22 && nvm use 22`, `corepack enable && corepack prepare pnpm@latest --activate`.

## Install

```bash
git clone https://github.com/rafeekpro/dap && cd dap
./scripts/setup
```

`scripts/setup` is idempotent and walks you through:

1. Pre-flights Python / uv / Node / pnpm versions. Each missing or too-old tool fails with a one-line install hint.
2. Copies `.env.example` → `.env.local` if it doesn't exist (gitignored — your provider keys stay local).
3. `uv sync --all-packages` (Python deps for the engine, runtimes, types, prompt-dsl, CLI).
4. `pnpm install` in `apps/dashboard` (Next.js + React Flow).
5. Activates `.githooks/pre-push` (blocks accidental pushes to `main` / `develop`).

After it finishes, edit `.env.local` and add at least one provider key — see [Provider keys](#provider-keys) below.

## Run

```bash
./scripts/dev
```

Brings up engine on `http://127.0.0.1:7333` and dashboard on `http://localhost:3000` in one terminal. Both log streams are prefixed (`[engine] …` / `[dashboard] …`); Ctrl+C tears the whole stack down cleanly.

If you want install + immediate launch in one go:

```bash
./scripts/dev --install      # delegates to scripts/setup, then starts both
```

If you'd rather drive each side from its own terminal — useful when debugging one side in isolation:

```bash
# 1. Backend — engine + CLI + runtimes
uv sync --all-packages
set -a; source .env.local; set +a
uv run dap-engine                         # binds 127.0.0.1:7333

# 2. Dashboard — in a second terminal
cd apps/dashboard
pnpm dev                                  # http://localhost:3000
```

## First pipeline — try it now

The repo ships an importable example bundle that exercises the whole stack without you wiring anything by hand:

1. Open `http://localhost:3000/pipelines` and click **Import JSON**.
2. Pick `examples/pipelines/github-issue-triage.pipeline-bundle.json`.
3. The engine creates the bundled agents (one bash, two GLM api-call, two more bash) and wires them into a 5-node DAG (`fetch_issues → select_issue → load_prd → enrich → create_branch`). You land on `/pipelines/<id>/edit`.
4. Hit **Run** to trigger it. The page redirects to the live run view — you'll see each node's status, prompt, output, and cost as it executes.

Prerequisites for running this specific bundle: `gh auth login` on the host, `GLM_API_KEY` in `.env.local`, and a `docs/PRD.md` in whatever repo the engine is started from. See `examples/pipelines/README.md` for details. The agents and pipeline structure are all editable from the dashboard once imported — the bundle is a starting point, not a black box.

## Working with agents

Each agent has:

- A **runtime** (`api-call` for SDK calls, `claude-code` / `gemini-cli` / `codex` / `aider` for agentic CLIs, `bash` for shell, `http` for arbitrary REST).
- A **prompt template** (Jinja → XML, validated against `PipelineState`).
- An **`input_schema`** / **`output_schema`** declaring which `PipelineState` fields the agent reads and writes.
- A **role** (`task_selector`, `prompt_builder`, `test_author`, `implementer`, `verifier`, `post_check`, or any custom string).

### Test a single agent — dry-run panel

On `/agents/<id>/edit` (or `/agents/new`) the **Test** panel runs the agent end-to-end against sample state without persisting anything: no `Run` row, no `NodeExecutionLog`, no state-machine effects. You see the rendered XML prompt, the actual runtime output, and a soft check of the structured output against `output_schema`. Cost is bounded by `DAP_DRY_RUN_BUDGET_USD` (default $0.50).

`POST /agents/dry-run` is the same thing programmatically — the panel POSTs your draft (so you can test edits before saving).

### Render preview

`POST /agents/<id>/render-preview` compiles the prompt against a sample state and returns the XML without invoking the runtime. Useful to catch Jinja syntax errors / undefined vars before burning tokens.

### Archive an agent

`/agents` has an **Archive** action. The engine refuses (HTTP 409) if any non-archived pipeline still references the agent, listing the blocking pipelines so you can detach or archive them first. The "Used in" column on the same page shows how many pipelines reference each agent.

Archiving is soft — run history keeps the agent reference intact; the agent just disappears from pickers and the active list.

### Versions

Every save bumps the agent's version. `/agents/<id>/versions` lists all of them; pipelines pin to the current version on save. Future pipeline edits can pin to a different version explicitly.

## Working with pipelines

`/pipelines/new` and `/pipelines/<id>/edit` open the React Flow designer:

- Drag agents from the picker onto the canvas.
- Wire edges; conditional edges support `field == value` / `field != value` / `is_null` etc against `PipelineState`.
- The Inspector panel (right side) shows agent details inline (prompt, schemas, runtime config) plus a **State after this node** view that walks back through the DAG and shows the cumulative `output_schema` of every upstream agent — so you can see what fields exist in state by the time execution reaches the selected node.
- **Validate** button runs cohesion checks (every declared `input_schema` field must be written by some upstream node).

### Run a pipeline

From `/pipelines`, **Run** opens a dialog where you can fill optional `initial_state` JSON, then triggers the run and redirects to `/runs/<id>`. The run page shows each node's status as it executes, plus controls:

- **Pause / Resume / Abort** mid-run.
- On a stopped (failed / paused / aborted) run: click any node and pick **Retry** (re-execute) or **Skip** (mark done, advance state machine).

### Logs

Every node execution writes a row to `node_execution_logs` in `./.dap/state.db`:

| Column | Content |
|---|---|
| `prompt_xml` | Exact prompt sent to the runtime (post-Jinja) |
| `stdout` / `stderr` | Raw runtime output |
| `output_json` | Parsed `<output>{…}</output>` block |
| `tokens_used` / `cost_usd` / `duration_ms` | Telemetry |
| `status` / `error_message` | success / failed / timeout / abort |

Three ways to inspect:

- Dashboard: `/runs/<id>` → click a node.
- API: `GET /runs/<id>/nodes/<node_id>`.
- Direct: `sqlite3 .dap/state.db "select * from node_execution_logs where run_id = '<id>';"`.

State snapshots after each node land in `state_snapshots`; the run summary lives in `runs.node_statuses`.

## Provider keys

The engine reads provider keys from process env (or `.env.local` via `scripts/dev`). You only need keys for providers you actually call:

| Provider | Env var | Used by |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `api-call` (provider=`anthropic`), `claude-code` CLI fallback |
| OpenAI | `OPENAI_API_KEY` | `api-call` (provider=`openai`), `codex` CLI |
| Google Gemini | `GEMINI_API_KEY` | `api-call` (provider=`gemini`), `gemini-cli` CLI |
| Z.AI GLM | `GLM_API_KEY` | `api-call` (provider=`glm`) — first-class OpenAI-compatible |

CLI runtimes (`claude-code`, `gemini-cli`) also accept their own OAuth login (`claude code` → `Pro`/`Max` plan, `gemini auth login` → Advanced) instead of an API key.

For custom OpenAI-compatible providers (Together, OpenRouter, internal proxies, Ollama): the agent's `runtime_config` declares `api_key_env`; export whatever env var name it uses. See [`docs/providers.md`](docs/providers.md) for the full provider matrix and per-provider recipes.

The `bash` runtime needs no provider key but **runs commands with the engine's privileges** — see the security note in [`packages/runtimes/README.md`](packages/runtimes/README.md).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components, state schema, Run lifecycle, LangGraph checkpoint model.
- [`docs/projects.md`](docs/projects.md) — projects (workspace layer): binding workflow kinds to pipelines, env layering, multi-pipeline patterns.
- [`docs/providers.md`](docs/providers.md) — provider matrix and per-provider setup recipes.
- [`docs/runtimes.md`](docs/runtimes.md) — adding a new runtime adapter.
- [`packages/runtimes/README.md`](packages/runtimes/README.md) — per-runtime config reference.
- [`examples/pipelines/`](examples/pipelines/) — importable pipeline bundles + their READMEs.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branching, PR flow, commit style.
- Engine API reference: `http://127.0.0.1:7333/docs` (FastAPI auto-docs while the engine runs).

## Repository layout

```
apps/
  engine/      FastAPI + LangGraph + SQLAlchemy — runs pipelines, exposes REST
  dashboard/   Next.js 15 + React Flow + TanStack Query — visual editor + run viewer
  cli/         Typer-based dap CLI (init, start, stop, status)
packages/
  types/       Shared Pydantic types (Agent, Pipeline, Run, PipelineState, RuntimeTask)
  runtimes/    Runtime adapter implementations
  prompt-dsl/  Jinja2 → XML prompt compiler with sandboxing + schema validation
examples/
  pipelines/   Importable .pipeline-bundle.json examples
scripts/
  setup        First-run installer (pre-flight, .env, deps, hooks)
  dev          Day-to-day launcher (engine + dashboard + log multiplexing)
tests/smoke/   Cross-package end-to-end tests (FastAPI TestClient + real adapters)
```

## Common commands

```bash
# Backend
uv run pytest                             # all smoke tests
uv run ruff check apps packages           # lint
uv run ruff format apps packages          # format
uv run mypy apps packages tests           # type-check
uv run dap-engine                         # serve engine on :7333
uv run dap --help                         # CLI

# Dashboard (run from apps/dashboard)
pnpm dev                                  # next dev
pnpm build                                # production build
pnpm typecheck                            # tsc --noEmit
pnpm lint                                 # eslint
```

## Architectural invariants

1. **LangGraph controls flow, runtimes execute steps.** Edges and conditions live in the pipeline definition, not in agents.
2. **Prompts are code.** Compiled from a template + state projection, validated against an XML schema, versioned per agent.
3. **State is the single source of truth.** Adapter output is parsed into a state diff; nothing outside `PipelineState` survives across nodes.
4. **Autonomy is local, not global.** A runtime may use tools internally, but never decides *what runs next* in the pipeline.
5. **Runtimes are executors, not planners.** Even agentic runtimes (`claude-code`, `codex`) receive compiled XML with an explicit task and an output contract.

## Status

Backend (engine, runtimes, prompt-dsl) and the dashboard MVP are functional. See open issues and the `v0.1` milestone on GitHub for what's next.

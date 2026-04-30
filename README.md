# DAP — Deterministic Agent Pipeline

[![CI](https://github.com/rafeekpro/dap/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/rafeekpro/dap/actions/workflows/ci.yml)

DAP is a local, single-user system for building and executing **deterministic
agent pipelines**. Pipelines are versioned DAGs of agents — each agent renders
a Jinja → XML prompt and dispatches it to a runtime adapter (Anthropic SDK,
shell, etc.). Execution runs on LangGraph with full pause/resume/abort/
retry/skip control. Anti-emergent by design: the state machine, not the
model, decides what runs next.

## Quickstart

Requires Python 3.13, `uv >= 0.8`, Node 22, and pnpm 9.

```bash
git clone https://github.com/rafeekpro/dap && cd dap
cp .env.example .env.local                # add your provider keys
./scripts/dev --install                   # one-shot: installs + starts both
```

`scripts/dev` auto-loads `.env.local`, starts the engine on :7333 and the dashboard on :3000, prefixes both log streams, and tears everything down on Ctrl+C. Drop `--install` after the first run.

If you'd rather drive each side from its own terminal:

```bash
# 1. Backend — engine + CLI + runtimes
uv sync --all-packages
set -a; source .env.local; set +a
uv run dap-engine start                  # binds 127.0.0.1:7333

# 2. Dashboard — in a second terminal
cd apps/dashboard
pnpm install
pnpm dev                                  # http://localhost:3000
```

Then in the dashboard:

1. **Agents → New** — pick a runtime (`api-call` for LLM, `bash` for shell),
   set a role, paste a prompt template, save.
2. **Pipelines → New** — drag agents onto the canvas, wire edges, save.
3. **Pipelines** list → **Run** on a row — fill optional `initial_state` JSON,
   submit; the page redirects to the live run view.
4. Use **Pause / Resume / Abort** on the run page; on a stopped run, click any
   node and **Retry** or **Skip** to recover without restarting from scratch.

The `api-call` runtime needs `ANTHROPIC_API_KEY` in the environment of the
process running `dap-engine start`. The `bash` runtime needs no extra setup
but runs commands with the engine's privileges — see the security note in
[`packages/runtimes/README.md`](packages/runtimes/README.md).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components, state schema,
  Run lifecycle, LangGraph checkpoint model.
- [`docs/projects.md`](docs/projects.md) — projects (v0.6 workspace layer):
  binding workflow kinds to pipelines, env layering, recipes for the
  common multi-pipeline patterns.
- [`docs/providers.md`](docs/providers.md) — provider matrix (Anthropic /
  OpenAI / Gemini / GLM / OpenRouter / Ollama), per-provider setup, agent
  recipes for the common patterns.
- [`docs/runtimes.md`](docs/runtimes.md) — how to add a new runtime adapter.
- [`packages/runtimes/README.md`](packages/runtimes/README.md) — per-runtime
  config reference (api-call, bash, claude-code, gemini-cli, http).
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branching, PR flow, commit style.
- Engine API reference: `http://127.0.0.1:7333/docs` (FastAPI auto-docs while
  the engine is running).

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
tests/smoke/   Cross-package end-to-end tests (FastAPI TestClient + real adapters)
```

## Common commands

```bash
# Backend
uv run pytest                             # all smoke tests
uv run ruff check apps packages           # lint
uv run mypy apps packages tests           # type-check
uv run dap-engine start                   # serve engine on :7333
uv run dap --help                         # CLI

# Dashboard (run from apps/dashboard)
pnpm dev / build / typecheck / lint
```

## Architectural invariants

1. **LangGraph controls flow, runtimes execute steps.** Edges and conditions
   live in the pipeline definition, not in agents.
2. **Prompts are code.** Compiled from a template + state projection,
   validated against an XML schema, versioned per agent.
3. **State is the single source of truth.** Adapter output is parsed into
   a state diff; nothing outside `PipelineState` survives across nodes.
4. **Autonomy is local, not global.** A runtime may use tools internally, but
   never decides *what runs next* in the pipeline.
5. **Runtimes are executors, not planners.** Even agentic runtimes
   (claude-code, codex) receive compiled XML with an explicit task and an
   output contract.

## Status

Backend (engine, runtimes, prompt-dsl) and the dashboard MVP are functional.
See open issues and the `v0.1` milestone on GitHub for what's next.

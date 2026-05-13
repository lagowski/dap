# dap-cortex

Pipeline node implementations for [DAP](https://github.com/rafeekpro/dap)
(Deterministic Agent Pipeline). Cortex ships reference nodes that
exercise the engine's full lifecycle — task selection, prompt
building, code generation, test authoring, verification, PR
operations — and serves as both production-ready building blocks
and the canonical example of how to build complex agents on top
of DAP's runtime adapters.

> **Distribution vs. import name.** The PyPI distribution is
> `dap-cortex`; the Python import path remains `cortex`. Analogous
> to `Pillow` → `from PIL import Image`.

## What's in here

| Module | Purpose |
|---|---|
| `cortex.adapters` | Translate between DAP `RuntimeTask` / `RuntimeResult` shapes and the internal `CortexState` used by node implementations |
| `cortex.nodes` | Node implementations: `coder`, `designer`, `code_reviewer`, `pr_merger`, `tester`, `validators`, ... |
| `cortex.config` | YAML-driven agent + pipeline configuration loaded at engine start |
| `cortex.backends` | LLM backend registry with fallback chains (multiple providers per role) |
| `cortex.tools.github` | GitHub API helpers for issue / PR / branch operations |
| `cortex.workspace` | Per-run working directory + memory persistence |

The nodes are consumed by DAP via the `python-func` runtime
adapter — each node is a Python callable with the signature
`async def run(state: dict, config: dict) -> dict`, resolved by
`callable_path` (e.g. `cortex.nodes.coder:run`) at invocation
time.

## Installation

Usually installed transitively via `dap-cli`:

```bash
pipx install dap-cli
```

Standalone (e.g. for embedding in a custom engine launcher):

```bash
pip install dap-cortex
```

## Quick example

Wire a cortex node into an agent definition by setting the
runtime to `python-func` and pointing `callable_path` at the
desired node:

```json
{
  "name": "Coder",
  "role": "implementer",
  "runtime_id": "python-func",
  "runtime_config": {
    "callable_path": "cortex.nodes.coder:run",
    "pass_prompt": true
  },
  "input_schema": ["selected_issue_ids", "implementation_notes"],
  "output_schema": ["modified_files", "implementation_notes"]
}
```

The engine resolves `cortex.nodes.coder:run` via `importlib` at
invocation time; the cortex package must be installed in the
engine's venv on the host where `dap-engine` runs.

## Configuration

Cortex reads its agent / backend configuration from YAML files in
the engine's working directory. Defaults bundled with the package
work out-of-the-box for the reference pipelines; production
deployments override via `cortex/config/*.yaml` at the project
root.

The `cortex.backends.registry` module composes provider fallback
chains (e.g. "try Anthropic Claude Opus first, fall back to
OpenAI GPT-5 if it fails") — useful when one provider is having a
bad day and you don't want the run to fail. Configure per-role
fallback in `cortex/config/backends.yaml`.

## Status

- **Migrated from `cortex-project`** — this package was extracted
  from a standalone repo and folded into the DAP workspace in
  v0.3. The import path stayed `cortex` to avoid breaking
  downstream code that pinned the old package name.
- **Type checking**: `cortex.*` is currently set to
  `ignore_errors = true` under `[[tool.mypy.overrides]]` in the
  root `pyproject.toml`. Type annotations are a tracked
  follow-up (Phase 2 hardening, off the v0.3 critical path).
- **Lint**: standard ruff config with cortex-specific per-file
  ignores in the root `pyproject.toml` (en-dash chars in
  comments / docstrings, intentional inline imports for lazy
  loading, protocol constants).

## Compatibility

- Python 3.13+
- DAP 0.3.0+ (engine + schemas)
- LangChain Core 0.3+
- PostgreSQL recommended for state checkpointing (SQLite works
  for development)

## See also

- [DAP project README](https://github.com/rafeekpro/dap) — architecture overview.
- [`docs/architecture.md`](https://github.com/rafeekpro/dap/blob/main/docs/architecture.md) — where Cortex fits in the engine's state machine.
- [`packages/runtimes`](https://github.com/rafeekpro/dap/tree/main/packages/runtimes) — the `python-func` runtime adapter that invokes Cortex nodes.

## License

See the [main repository](https://github.com/rafeekpro/dap) for licensing details.

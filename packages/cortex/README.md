# dap-cortex

Cortex pipeline nodes — runs inside the DAP engine.

Published to PyPI as `dap-cortex` (import path remains `cortex`).

This package was migrated from a standalone `cortex-project` repo and currently
ships:

- **`cortex.adapters`** — adapters between DAP runtime tasks and pipeline state
- **`cortex.nodes`** — node implementations (coder, designer, code_reviewer,
  pr_merger, tester, validators, …)
- **`cortex.config`** — agent / pipeline configuration loaded from YAML

## Installation

```bash
pip install dap-cortex
# or
uv add dap-cortex
```

The import path is unchanged — use `from cortex.nodes import ...` as before.

## Workspace usage

Inside the monorepo, installed as a workspace member:

```bash
uv sync --all-packages
```

## Status

- Type checking: `cortex.*` is currently set to `ignore_errors = true` under
  `[[tool.mypy.overrides]]` in the root `pyproject.toml` (Phase 2 follow-up —
  type annotations are tracked separately).
- Lint: standard ruff config with cortex-specific per-file ignores documented
  in the root `pyproject.toml` (en-dash chars, intentional inline imports,
  protocol constants).

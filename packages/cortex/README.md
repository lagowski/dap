# cortex

Cortex pipeline nodes — runs inside the DAP engine.

This package was migrated from a standalone `cortex-project` repo and currently
ships:

- **`cortex.adapters`** — adapters between DAP runtime tasks and pipeline state
- **`cortex.nodes`** — node implementations (coder, designer, code_reviewer,
  pr_merger, tester, validators, …)
- **`cortex.config`** — agent / pipeline configuration loaded from YAML

## Usage

This package is consumed by `dap-engine` as a workspace dependency. Not
published to PyPI; install via the repository workspace:

```bash
uv sync --all-packages
```

## Status

- Type checking: `cortex.*` is currently in `[[tool.mypy.overrides]]
  ignore_errors = true` (Phase 2 follow-up — type annotations are tracked
  separately).
- Lint: standard ruff config with cortex-specific per-file ignores documented
  in the root `pyproject.toml` (en-dash chars, intentional inline imports,
  protocol constants).

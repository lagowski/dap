# Testing

DAP keeps the required Python PR gate fast while preserving full smoke
coverage in CI.

## Local Commands

```bash
uv run pytest -q -m "not integration" --ignore=tests/standalone
uv run pytest -q -m unit tests/smoke
uv run pytest -q -m integration tests/smoke
uv run pytest -q --ignore=tests/standalone
```

`tests/standalone/` exercises the pip-installable wheel and bundled
dashboard. It is covered by the packaging/Docker workflow because it
needs the dashboard build toolchain.

## Markers

`tests/smoke/conftest.py` auto-applies these markers to smoke tests:

- `unit`: pure helper or adapter tests with no engine app, DB, or
  subprocess lifecycle.
- `integration`: engine app, database, CLI process, or cross-component
  smoke coverage.

Tests outside `tests/smoke/` are intentionally unmarked. They run in the
fast required gate through `-m "not integration"`.

## CI Gates

- `Python (ruff + pytest)`: required branch-protection check. Runs Ruff,
  Mypy, and the fast Python pytest gate.
- `Python smoke integration (1/3..3/3)`: sharded smoke integration jobs.
  These run on pull requests and pushes to `develop`/`main`.
- `Python deps CVE scan`: dependency audit for the locked Python env.
- `Dashboard (typecheck + build)`: generated API type check, dashboard
  typecheck, lint, test, and build.

Full Python coverage for normal CI is the union of `Python (ruff +
pytest)` and the three `Python smoke integration` shards. A failing smoke
shard must be treated as a merge blocker even before the repository's
branch-protection settings are updated to require the new check names.

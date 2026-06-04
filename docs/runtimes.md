# Adding a new runtime adapter

A runtime adapter is what actually executes a node. The pipeline runner
hands it a `RuntimeTask` (rendered XML prompt + working directory + timeout
+ runtime config) and expects a `RuntimeResult` back. DAP ships with eight
adapters that follow the same shape: `api-call`, `bash`, `claude-code`,
`gemini-cli`, `codex`, `aider`, `python-func`, and `http`.

> Looking to **use** an existing provider (Claude / Gemini / GLM / Ollama
> / …)? See [`providers.md`](providers.md) — that's the operator-facing
> guide with config recipes. This doc is for **adding a new runtime**
> (a new executor type, not a new LLM behind an existing one).

The Protocol lives in `packages/types`, all adapters live in
`packages/runtimes/src/dap_runtimes/adapters/`, and the registry is wired
in `packages/runtimes/src/dap_runtimes/registry.py`.

## The Protocol

```python
# packages/types/src/dap_types/runtime.py

@runtime_checkable
class RuntimeAdapter(Protocol):
    id: str                                          # registry key, kebab-case
    display_name: str                                # human-readable in UI
    kind: RuntimeKind                                # "cli" | "api" | "shell" | "http"

    async def healthcheck(self) -> HealthStatus: ...
    async def execute(self, task: RuntimeTask) -> RuntimeResult: ...
```

`RuntimeTask` (request):

```python
class RuntimeTask(BaseModel):
    execution_id: str            # node-execution UUID, opaque to adapters
    prompt_xml: str              # compiled by packages/prompt-dsl
    working_directory: str       # cwd the engine wants the work to happen in
    allowed_files: list[str] | None    # planned: scope hint for sandboxing
    allowed_tools: list[str] | None    # planned: tool allow-list for agents
    timeout_ms: int = 60_000
    budget_usd: float | None = None    # planned: per-call cost ceiling
    runtime_config: dict[str, Any]     # adapter-specific settings (model_id, command, etc.)
    context: RuntimeContext            # input_fields projection from PipelineState
```

`RuntimeResult` (response):

```python
class RuntimeResult(BaseModel):
    success: bool
    output: str                          # primary text output (stdout, LLM completion)
    structured: dict[str, Any] | None    # parsed/structured payload merged into PipelineState
    files_changed: list[str]             # informational (planned: enforced by sandbox)
    tokens_used: int | None              # for cost aggregation on Run
    cost_usd: float | None
    duration_ms: int
    errors: list[str]                    # human-readable failures; populated when success=False
```

## Skeleton

```python
# packages/runtimes/src/dap_runtimes/adapters/my_runtime.py

from __future__ import annotations

import time
from typing import Any

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter


class MyRuntimeAdapter(BaseAdapter):
    id = "my-runtime"
    display_name = "My Runtime"
    kind: RuntimeKind = "api"  # or "cli" / "shell" / "http"

    async def healthcheck(self) -> HealthStatus:
        # Cheap probe: env var present? Binary on PATH? API reachable?
        # Return missing=[...] when something the user can fix is wrong.
        return HealthStatus(available=True, version="1.0.0")

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        start = time.monotonic()
        # 1. Validate runtime_config (return _failed RuntimeResult on bad input)
        # 2. Dispatch the work
        # 3. Map the result back into RuntimeResult
        return RuntimeResult(
            success=True,
            output="...",
            duration_ms=int((time.monotonic() - start) * 1000),
            structured={"...": "..."},
        )
```

## Registration

```python
# packages/runtimes/src/dap_runtimes/registry.py

from dap_runtimes.adapters.my_runtime import MyRuntimeAdapter

def create_default_registry() -> RuntimeRegistry:
    registry = RuntimeRegistry()
    registry.register(BashAdapter())
    registry.register(ApiCallAdapter())
    # ...
    registry.register(MyRuntimeAdapter())  # ← add here
    return registry
```

Also add the class to the package export in
`packages/runtimes/src/dap_runtimes/__init__.py` so tests can import it.

## What the engine does for you

You don't need to:

- Render the prompt — the runner passes you `prompt_xml` already compiled
  from the agent's template + the current `PipelineState`.
- Persist anything — `node_executor.py` writes `NodeExecutionLog` and
  `StateSnapshot` rows from your `RuntimeResult` automatically.
- Enforce user-level runtime policy — the engine blocks dangerous
  host-local runtimes such as `bash` for non-admin users unless the
  operator explicitly opts in with `DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN=1`.
- Merge results into state — keys in `RuntimeResult.structured` that match
  `PipelineState` field names are merged into the state diff for you;
  unknown keys are dropped.
- Enforce timeouts at the engine layer — *you* must respect
  `task.timeout_ms`. The pipeline runner does not abort your `execute()`
  coroutine on overrun; it relies on the adapter to use
  `asyncio.wait_for(...)` (or equivalent) to honour the budget.

## What you must do

- **Honour `task.timeout_ms`.** If your call can outlive the budget, wrap
  it in `asyncio.wait_for` (api-call uses the SDK's per-request timeout;
  bash uses `asyncio.wait_for` around `process.communicate()`).
- **Be cancellable.** When the engine pauses or aborts a run, the
  background task is cancelled — your `execute()` will receive
  `asyncio.CancelledError`. Tear down anything that would otherwise leak
  (`bash` kills its subprocess group; `api-call` lets the SDK close the
  connection via `client.close()`).
- **Return failures, don't raise them.** The pipeline runner expects every
  node-level error to come back as `RuntimeResult(success=False, errors=
  [...])`. Raising propagates as a `RunnerError` and finalises the whole
  run as `failed`, which is rarely what you want — reserve raises for
  programmer errors (invalid config, etc.).
- **Populate `structured` consistently.** Downstream nodes branch on its
  shape via conditional edges — varying keys per call breaks pipelines.
  See `bash._make_structured()` for an example of guaranteeing the same
  key set across success / failure / timeout paths.
- **Stay deterministic from the engine's perspective.** Internal tool use
  inside the adapter is fine, but the adapter owns no state across calls
  — every `execute` is independent.

## Healthcheck

`GET /runtimes/{id}/health` exposes your `healthcheck()` to the dashboard
sidebar and CLI status command. Keep it fast (≤1s) and side-effect-free:

- Check env vars or files the user can fix (return them in `missing=[...]`).
- Don't make billable API calls — that's what `execute` is for.
- Surface a meaningful `version` when cheap (SDK version, binary `--version`).

## Tests

Add a smoke test in `tests/smoke/test_<your_runtime>_adapter.py`. The
existing tests for `bash` and `api-call` are good models:

- Don't depend on env vars without a fixture that clears them
  (autouse fixture `monkeypatch.delenv(...)` keeps tests hermetic).
- Use the real adapter with a fast surrogate (echo for shell, mocked
  client for SDK) — exercise the integration, not just the type signature.
- Assert the `structured` shape stays consistent across success, failure,
  timeout, and validation-error paths.

---

## Pipeline node authors: the `python-func` runtime and `final_status`

> This section is for authors who write pipeline **nodes** (the Python
> functions invoked by DAP), not authors adding a new runtime adapter.

### How `python-func` nodes work

The `python-func` runtime calls a Python function that has this signature:

```python
async def run(state: dict, config: dict) -> dict:
    ...
    return {"key": "value", ...}
```

DAP merges every key in the returned `dict` whose name matches a field in
`PipelineState` into the live run state. Unknown keys are silently dropped
(`extra="forbid"` is on `PipelineState`, so unrecognised fields never
corrupt state — they just don't survive the merge).

The `stdout` column in `node_execution_logs` stores `json.dumps(result)`
(minus the `__audit` key). This is useful for debugging: query
`node_execution_logs` filtered by `run_id` and `agent_id` to inspect what
a node returned.

### The `final_status` contract

`PipelineState.final_status` starts every run as `"running"`. What happens
at the end of the run depends on whether the pipeline opts in to
**terminal-status enforcement**:

| Pipeline setting | Residual `"running"` at end of run | Meaning |
|---|---|---|
| `requires_terminal_final_status = False` (default) | silently coerced to `"success"` | "no node raised = pipeline succeeded" — safe for simple pipelines that don't manage status explicitly |
| `requires_terminal_final_status = True` | treated as **`"failed"`** with an operator-visible reason | any node that can end the run *must* set `final_status` explicitly |

**Rule**: if your pipeline sets `requires_terminal_final_status = true`,
every node that can be the **last node in the graph** (i.e. routes to
`END`) must return `{"final_status": "success"}` on the happy path — or
`{"final_status": "failed"}` when it decides the run did not succeed.

```python
# ✅ correct — node that ends the pipeline
async def run(state: dict, config: dict) -> dict:
    # ... do the work ...
    return {
        "merged": True,
        "final_status": "success",   # ← required when requires_terminal=True
    }

# ❌ wrong — run will be marked failed even though the merge succeeded
async def run(state: dict, config: dict) -> dict:
    return {
        "merged": True,
        # final_status left as "running" → orchestrator sees no terminal
        # status → marks the run failed
    }
```

If a node is not the last node (it has a successor in the graph), omitting
`final_status` is fine — the orchestrator only checks the value after
*all* nodes have finished.

### When to enable `requires_terminal_final_status`

Enable it when your pipeline contains nodes that **decide** the outcome —
for example a merge node that may refuse to merge, or a verification node
that can declare failure. Leaving it `false` is fine for simple linear
pipelines where "ran without raising" reliably means "succeeded".

The flag is set in the pipeline's `defaults` block:

```json
{
  "defaults": {
    "requires_terminal_final_status": true,
    "gate_timeout_seconds": 3600
  }
}
```

### Debugging `final_status: failed` on an otherwise-green run

If every node shows `success` in the dashboard but the run's
`final_status` is `failed` with a reason like *"pipeline completed with
non-terminal final_status 'running'"*, the cause is always the same: the
last node in the graph did not return `{"final_status": "success"}`.

Check the `node_execution_logs` row for the terminal node:

```sql
SELECT agent_id, stdout
FROM node_execution_logs
WHERE run_id = '<your-run-id>'
ORDER BY started_at DESC
LIMIT 5;
```

`stdout` is `json.dumps(result)` — look for `"final_status"` in it. If
it's absent or still `"running"`, add the explicit return value to that
node.

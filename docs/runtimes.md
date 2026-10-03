# Adding a new runtime adapter

A runtime adapter is what actually executes a node. The pipeline runner
hands it a `RuntimeTask` (rendered XML prompt + working directory + timeout
+ runtime config) and expects a `RuntimeResult` back. DAP ships with nine
adapters that follow the same shape: `api-call`, `bash`, `claude-code`,
`gemini-cli`, `codex`, `aider`, `python-func`, `http`, and `github`.
How to *use* `github`, and the git workspace config on the CLI code
runtimes, is in [Built-in runtime reference](#built-in-runtime-reference)
below.

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
`PipelineState` into the live run state. Any other key is merged into
`state.extensions` (so a node returning `{"plan": …}` is read downstream as
`{{ state.extensions.plan }}`). `PipelineState` itself stays
`extra="forbid"`; the unknown keys never become top-level fields.

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
| `requires_terminal_final_status = False` | silently coerced to `"success"` | "no node raised = pipeline succeeded" — safe for simple pipelines that don't manage status explicitly |
| `requires_terminal_final_status = True` (**default**, since #628) | treated as **`"failed"`** with an operator-visible reason | any node that can end the run *must* set `final_status` explicitly |

A pipeline whose last node can't set `final_status` (e.g. a `bash`, `http`
or `github` node) must set `"requires_terminal_final_status": false` in its
`defaults`, or every run ends `failed` even when every node succeeded.

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

Keep it on (the default) when your pipeline contains nodes that **decide**
the outcome — for example a merge node that may refuse to merge, or a
verification node that can declare failure. Turning it off is fine for
simple linear pipelines where "ran without raising" reliably means
"succeeded".

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

---

## Built-in runtime reference

How to configure the runtimes whose `runtime_config` goes beyond "pick a
model" (for LLM providers see [`providers.md`](providers.md)). Everything
below is plain agent config, editable in the agent editor.

> **Tokens are always referenced by env var name, never pasted into
> config.** `token_env: "GH_TOKEN"` names a variable; its value comes from
> the engine env, instance env vars (`/settings/admin/env-vars`), project
> env vars, or `runtime_config.env`, in increasing precedence. A token
> string in a `runtime_config` would be exported with the pipeline.

### `github`: GitHub operations as nodes (#920, #921)

A node that talks to the GitHub REST API directly. No LLM, no shell.

| `op` | Params | Writes into state (default key) |
|---|---|---|
| `read_issue` | `issue` | `github_issue`: `number`, `title`, `body`, `state`, `labels` (names), `url`, `is_pull_request` |
| `read_pr` | `pr` | `github_pr`: `number`, `title`, `body`, `state`, `draft`, `merged`, `mergeable`, `mergeable_state`, `url`, `head`/`base` `{ref, sha}`, `files` (every changed path) |
| `comment` | `issue`, `body` | `github_comment`: `id`, `url`, `issue` |
| `update_issue_section` | `issue`, `section`, `content` | `github_issue_section`: `issue`, `section`, `changed` |
| `create_branch` | `branch`, `base` | `github_branch`: `branch`, `sha`, `created` |
| `open_pr` | `head`, `base`, `title`, optional `body`, `draft` | `github_opened_pr`: `number`, `url`, `created` |
| `merge_pr` | `pr`, `expected_head_sha`, optional `method` | `github_merge`: `number`, `merged`, `sha` |

Common keys: `repo` (`owner/name`, required), `token_env` (default
`GH_TOKEN`), `state_key` (overrides the default key above), `api_url`
(GitHub Enterprise; https only).

**Templating.** Every text param may be a Jinja template over the run's
state: `"repo": "{{ state.repo }}"`, `"issue": "{{ state.extensions.issue_number }}"`.
Rendering is sandboxed and strict, so an undefined variable fails the node
instead of calling GitHub with an empty value. Results land in
`state.extensions.<state_key>` (they aren't `PipelineState` fields), so a
later node reads `{{ state.extensions.github_issue.title }}`.

**Behaviour worth knowing:**

- `update_issue_section` replaces the text between
  `<!-- dap:section:NAME -->` and `<!-- /dap:section:NAME -->`. It writes
  nothing if the text is already there (`changed: false`), and fails if
  the markers are missing, unclosed or duplicated. It never appends.
- `create_branch` takes a branch name or a full sha as `base`. A branch
  already at that sha is a no-op (`created: false`); one elsewhere fails.
- `open_pr` returns the already-open PR for `head` (`created: false`)
  instead of failing. A cross-fork head is `owner:branch`.
- `merge_pr` **requires** `expected_head_sha`, the full sha of the head
  that was reviewed (from a `read_pr` node: `{{ state.extensions.github_pr.head.sha }}`).
  GitHub refuses the merge (409) if the PR moved since. `method`: `squash`
  (default), `merge` or `rebase`.

**Safety.** The token is sent only as an `Authorization` header and is
scrubbed from every error. Pagination never follows a link off `api_url`.
`repo` may not contain `.`/`..` segments, and branch names are limited to
`A-Z a-z 0-9 . _ / : -`, because both go into request URLs. Every failure
(bad config, missing token, 4xx/5xx, network) fails the node with a message;
nothing is sent before the config validates.

**Role-separated tokens.** Point each node at the least-privileged token:
`"token_env": "CORTEX_GH_TOKEN_READ"` for reads, `…_ISSUES` for comments,
`…_CODE` for branches and PRs, `…_MERGE` for `merge_pr`.

Example: [`examples/pipelines/github-read-issue-comment.pipeline-bundle.json`](../examples/pipelines/github-read-issue-comment.pipeline-bundle.json),
which reads an issue and comments on it.

### Git workspace on the CLI code runtimes (#922, #923)

`claude-code`, `codex` and `gemini-cli` accept these optional keys. With
none set (or only `false` / empty values), nothing changes.

| Key | Effect |
|---|---|
| `workspace` | Directory the CLI runs in: absolute, or relative to the project's working directory (the default). |
| `branch` | Checked out **before** the CLI runs: an existing local branch as is, a remote-only one from `origin`'s tip, otherwise created from `base`. Refused if the workspace has uncommitted changes. |
| `base` | Start point for a new `branch` (`origin/<base>` preferred). Default `develop`. |
| `push` | After a **successful** run, push `branch` to `origin`. Fast-forward only. A failed run pushes nothing. |
| `force_with_lease` | Allow replacing a rewritten `branch`, but only if `origin` still points where it did at checkout. Never a plain `--force`. Requires `push`. |
| `token_env` | Env var holding the token git uses for github.com over HTTPS. It reaches git only via `GIT_CONFIG_*` env, never argv. |

Guards, all off by default. They are checked after a successful run and
**before** any push, so a failing guard never reaches the remote. A failure
names the guard and lists the commits involved (`structured["git"]["guard"]`):

| Guard | Fails the run when | Requires |
|---|---|---|
| `require_nonempty_diff` | The run added no commit to `branch`. Without `push`, uncommitted changes count too; with `push` they don't, since they wouldn't be pushed. | `branch` |
| `append_only` | A commit that was on `branch` before the run is gone from its history (amend, rebase or reset over existing work). Rewriting the run's own new commits is fine. | `branch` |
| `ancestry_guard` | `origin`'s `branch` (freshly fetched) has commits the push would drop, including ones already there at checkout, which `force_with_lease` can't see. | `push` |

Everything after the run reads `refs/heads/<branch>`, never `HEAD`, so a
CLI that ends its turn on another branch doesn't get the wrong commits
judged or pushed. The result's `structured["git"]` carries `workspace`,
`branch`, `base`, `start_sha`, `head_sha` and `pushed`.


# Pipeline loops, workspace state and backend fallback

Pipeline-author reference for the config that turns cortex's tester → retry →
coder loop into a pipeline (#811):

- [`retry`](#retry-a-bounded-loop-on-a-node): a bounded loop declared on a node.
- [Workspace state](#workspace-state-what-a-code-run-changed): what a code run
  with a git `branch` changed, as state fields.
- [`not`](#not-in-conditions) in edge and `short_circuit_on` conditions.
- [Backend `fallback`](#backend-fallback-chain): re-run a node on another
  backend profile when the first one fails with a backend error.

A runnable example uses all four:
[`examples/pipelines/tester-coder-retry.pipeline-bundle.json`](../examples/pipelines/tester-coder-retry.pipeline-bundle.json)
(see [the walkthrough](#example-tester--coder-retry-loop) at the end).

## `retry`: a bounded loop on a node

A node can carry a `retry` block (#931, #932). When an attempt of that node
fails, the run goes back to `retry_to`; after `max_retries` failed retries it
goes to `on_failure` instead.

```json
{
  "id": "tester",
  "agent_id": "…",
  "retry": {
    "max_retries": 3,
    "retry_to": "coder",
    "on_failure": "escalate",
    "feedback_into": "test_feedback",
    "short_circuit_on": {
      "type": "comparison", "field": "tests_passed", "operator": "==", "value": false
    }
  }
}
```

| Key | Default | Meaning |
|---|---|---|
| `max_retries` | required | Retries after the first attempt, `1`–`20`. `3` means up to 4 runs of the node. |
| `retry_to` | required | Node to go back to while retries remain. It must be able to reach this node again through the edges, or the pipeline won't save. Usually an upstream node (the coder), but it can be the node itself. |
| `on_failure` | `"__end__"` | Node (or `__end__`) to go to once the retries are spent. A node reachable only from here still counts as reachable. |
| `feedback_into` | none | A bare `state.extensions` key (letters, digits, `_`; no dots) that receives the failure reason before each retry. |
| `short_circuit_on` | none | A [condition](#not-in-conditions) on the state after the attempt. When it matches, the attempt counts as failed even if the node succeeded. Fields it names that aren't `PipelineState` fields give a save warning. |

### When an attempt fails

An attempt fails when the node failed (its runtime returned `success: false`,
so `final_status` is `failed`), **or** when `short_circuit_on` matches the
state after the node ran. Use `short_circuit_on` for a node that runs fine but
reports a bad result, for example `tests_passed == false` or an empty diff.

### Routing

The engine keeps a counter per node in `state.node_attempts`. After the node runs:

| Counter | Next step |
|---|---|
| `0` (the attempt passed) | The node's normal edges, conditions included. |
| `1` … `max_retries` | `retry_to`. |
| more than `max_retries` | `on_failure`. |

A passing attempt clears the counter, so a later failure of the same node
starts a fresh budget. The counter is in the checkpointed state, so the route
survives a restart and a rewind. The engine raises the LangGraph recursion
limit to fit every retry budget, so a long loop reaches `on_failure` instead of
stopping with `GraphRecursionError`.

### What lands in `feedback_into`

Before each retry (not after the last failure) the engine writes one string
to `state.extensions[feedback_into]`, the first of:

1. the attempt's `verification_reason`. For a failed node this is
   `Node <id> failed: <runtime errors>`, for example the last lines of a
   failing test command's stderr;
2. its `last_test_output`;
3. `Node <id> matched retry.short_circuit_on`, when the node passed but the
   condition matched and set neither field.

The next attempt overwrites it. A prompt reads it like any extension. Escape
it when it goes inside the XML prompt, because test output often contains `<`
or `&`:

```jinja
{% if extensions.test_feedback is defined %}
  <feedback>{{ extensions.test_feedback | e }}</feedback>
{% endif %}
```

### `final_status`

- A failed attempt with retries left resets `final_status` to `running`, so
  the run goes on through `retry_to` instead of ending as failed.
- When the retries are spent, `final_status` stays `failed` and the
  `verification_reason` of the last attempt stays in state. `on_failure` runs
  on that state. A successful `on_failure` node doesn't make the run a success,
  unless it sets `final_status` itself (a `python-func` node can).
- A short-circuited attempt doesn't touch `final_status`, so with retries
  spent the run reaches `on_failure` still `running`, and a successful
  `on_failure` node ends it `success`. If the run has to end `failed`, make
  `on_failure` fail: the example's `escalate` is a `bash` node that exits 1.
  See [the `final_status` contract](runtimes.md#the-final_status-contract).

### Manual retry and skip

`POST /runs/{id}/nodes/{node}/retry` and `/skip` (the run page's buttons)
work on a node with a `retry` block:

- **Retry** rewinds to the last checkpoint where the node was next to run and
  runs it again. The counter is the one at that checkpoint. A pass follows
  the normal edges; a failure is counted against the remaining budget.
- **Skip** treats the node as passed: its counter is cleared and the run
  follows the normal edges, never `retry_to` or `on_failure`.

## Workspace state: what a code run changed

A successful `claude-code`, `codex` or `gemini-cli` run with a git `branch` in
its `runtime_config` writes three `PipelineState` fields (#933):

| Field | Default | Value |
|---|---|---|
| `changed_files` | `[]` | Files this run changed (`git diff --name-only <start>..<branch>`). |
| `commits_since_base` | `[]` | `git log <base>..<branch>`, newest first, each `{sha, subject, author}`. |
| `diff_stats` | `{}` | `{additions, deletions, files}` for this run's changes. |

A run that changes nothing writes empty lists and zero counts. A run without
`branch`, a failed run and any other runtime leave the fields as they were.
How `<start>` and `<base>` are chosen:
[`runtimes.md` → Git workspace](runtimes.md#git-workspace-on-the-cli-code-runtimes-922-923).

Prompts and conditions read them like any state field:

```jinja
Files you changed last time: {{ changed_files | join(", ") }}
```

```json
{"type": "comparison", "field": "diff_stats.files", "operator": ">", "value": 0}
```

## `not` in conditions

Edge conditions and `short_circuit_on` take `and`, `or` and `not` (#930).
`not` takes exactly one child:

```json
{
  "type": "not",
  "children": [
    {"type": "comparison", "field": "diff_stats.files", "operator": ">", "value": 0}
  ]
}
```

A `field` is a dot path into the state (`diff_stats.files`,
`extensions.github_pr.number`). A path that doesn't exist makes its comparison
false, so `not` of it is true. Above, `diff_stats` is `{}` before any code run
has reported a diff, and the condition matches: "no files changed yet".

## Backend fallback chain

A node's backend profile can name fallback profiles (#935). When the node fails
with a **backend error**, the engine runs the same node again on the next
profile in the list, with the same state. Any other failure, or a success, ends
the node.

```json
"backend_profiles": {
  "available": {
    "claude": {"runtime_id": "claude-code"},
    "codex": {"runtime_id": "codex"}
  },
  "agent_assignments": {
    "default_profile": "claude",
    "overrides": {
      "coder": {"profile": "claude", "fallback": ["codex"]}
    }
  }
}
```

- An entry in `agent_assignments.overrides` is keyed by node id, agent id,
  agent name or role. Its value is a profile id or an object
  `{"profile": <id>, "fallback": [<id>, …]}`. Without `profile` the object
  keeps `default_profile` and adds the chain. `fallback` defaults to `[]`.
- Each fallback is resolved like a primary profile, so the agent's own
  `runtime_config` (a coder's `branch`, for example) still applies under it.
  The chain is the one list on the assignment, tried in order.
- Every id in `fallback` must be in `available`. Saving, importing and
  triggering the pipeline reject an unknown id with a 422.

### Backend error categories

A runtime marks a failure as a backend error by setting
`structured["error_category"]` (#934):

| Category | Meaning |
|---|---|
| `rate_limit` | Throttled (HTTP 429, or the CLI/provider reports a rate limit). May recover later. |
| `out_of_credits` | Quota or credit exhausted. Won't recover on retry. |
| `timeout` | The call or the CLI process ran past its time budget. |
| `unavailable` | HTTP 5xx, connection refused or dropped, or the CLI binary is missing. |

Anything else, such as a config error, a 4xx validation error or a failing test
command, has no category and never falls back: another model can't fix it.

### Audit trail and how it combines with `retry`

On a node with a chain, every attempt has its own entry in
`GET /runs/{id}/nodes` with
`extra_data.backend_attempt = {attempt, profile_id, error_category}`, so you can
see which profile failed and why, and which one finished the node. When the
whole chain fails, the node fails with the last profile's error.

The fallback chain runs inside one node attempt. A node with both `retry` and
a chain retries only after every profile failed; a retry starts again from
the primary profile.

## Example: tester → coder retry loop

[`tester-coder-retry.pipeline-bundle.json`](../examples/pipelines/tester-coder-retry.pipeline-bundle.json):

```
__start__ → coder → tester ─pass→ __end__
              ↑        │
              └─retry──┤  (up to 3 retries, feedback in extensions.test_feedback)
                       └─retries spent→ escalate → __end__
```

- **coder** (`claude-code`, `branch: dap/retry-example`, `base: develop`)
  implements `description`. From the second attempt on, its prompt carries
  the tester's failure in `<feedback>`. It falls back to `codex` on a backend
  error.
- **tester** (`bash`, `pytest -q`) runs the suite. A non-zero exit fails the
  attempt. So does a coder run that changed no files: `short_circuit_on` is
  `not diff_stats.files > 0`.
- **escalate** (`bash`) runs once the retries are spent. It prints a message
  and exits 1, so the run ends `failed` whichever way the attempts failed.
  Each test failure is in the tester's node logs.

Before importing, edit the tester's `command`, the coder's `branch` and
`base`, and the profiles. The `bash` nodes need an admin to trigger the run,
or `DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN=1` on the engine. Run it with:

```json
{"repo": "owner/name", "branch": "develop", "description": "Make the failing test in tests/test_x.py pass"}
```

`tests/smoke/test_example_retry_bundle.py` imports the bundle and runs it
through the engine with scripted coder and tester adapters. It covers one
retry then a pass, an empty diff, spent retries (both ways) and a
rate-limit fallback.

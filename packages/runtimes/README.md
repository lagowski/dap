# dap-runtimes

Runtime adapters for [DAP](https://github.com/rafeekpro/dap). Every
agent in a pipeline picks one runtime — this package implements all
seven. Each adapter delegates the actual work to a specific executor
(LLM SDK, agentic CLI, shell, HTTP endpoint, or in-process Python
callable) while exposing the uniform `RuntimeAdapter` protocol the
engine relies on.

## Status

| Runtime | State |
|---|---|
| `api-call` (Anthropic + OpenAI + OpenAI-compat + Gemini) | ✅ Implemented |
| `bash` | ✅ Implemented |
| `claude-code` | ✅ Implemented |
| `codex` | ✅ Implemented |
| `gemini-cli` | ✅ Implemented |
| `http` | ✅ Implemented |
| `python-func` | ✅ Implemented |
| `aider` | 🚧 Stub (lands later) |

## Installation

Usually installed transitively via `dap-cli`:

```bash
pipx install dap-cli
```

Standalone:

```bash
pip install dap-runtimes
```

## Quick start

```python
import asyncio
from dap_runtimes import create_default_registry

async def main() -> None:
    registry = create_default_registry()
    adapter = registry.get("bash")
    health = await adapter.healthcheck()
    print(health)

asyncio.run(main())
```

The engine builds its adapter set the same way at lifespan startup
and dispatches every runtime task through the matching adapter.

---

## `api-call`

Single-shot LLM call via the provider's official SDK — no tool use,
no streaming. The provider is selected by `runtime_config.provider`;
each provider has its own pricing tables and token-extraction logic
under `_providers/`.

| `provider` | SDK | Required env | Pricing |
|---|---|---|---|
| `anthropic` (default) | `anthropic.AsyncAnthropic` | `ANTHROPIC_API_KEY` | Full (Claude 4.x family + cache multipliers) |
| `openai` | `openai.AsyncOpenAI` | `OPENAI_API_KEY` | Full (gpt-5, o-series) |
| `openai-compat` | `openai.AsyncOpenAI(base_url=…)` | env named in `runtime_config.api_key_env` | None (third-party prices not bundled) |
| `gemini` | `google.genai.Client` | `GEMINI_API_KEY` | Full (Gemini 2.x / 3.x) |

Common `runtime_config`:

| key | type | description |
|---|---|---|
| `provider` | `str` | `anthropic` (default) / `openai` / `openai-compat` / `gemini` |
| `model_id` | `str` | Model identifier (e.g. `claude-haiku-4-5`, `gpt-5-mini`, `gemini-3.0-flash`) |
| `max_tokens` | `int` | Defaults to 4096 |
| `system_prompt` | `str?` | Prepended before `prompt_xml` in the system role |

Anthropic-specific:

| key | type | description |
|---|---|---|
| `prompt_cache` | `bool` | Enables ephemeral cache control |
| `enable_thinking` | `bool` | Adaptive thinking mode |
| `effort` | `low`/`medium`/`high`/`xhigh`/`max` | Reasoning effort |

OpenAI-compat-specific:

| key | type | required | description |
|---|---|:---:|---|
| `base_url` | `str` | yes | OpenAI-compatible endpoint (e.g. `https://api.z.ai/api/coding/paas/v4`) |
| `api_key_env` | `str` | yes | Name of the env var holding the API key (e.g. `GLM_API_KEY`) |

Gemini-specific:

| key | type | description |
|---|---|---|
| `temperature` | `float` | Defaults to the SDK default |
| `thinking_budget` | `int` | Reasoning-token cap |

### Examples

**GLM via OpenAI-compat:**

```json
{
  "provider": "openai-compat",
  "model_id": "glm-5-flash",
  "base_url": "https://api.z.ai/api/coding/paas/v4",
  "api_key_env": "GLM_API_KEY",
  "max_tokens": 4096
}
```

**Gemini API:**

```json
{
  "provider": "gemini",
  "model_id": "gemini-3.0-pro",
  "max_tokens": 8192,
  "temperature": 0.2
}
```

---

## `bash`

Executes a single shell command in `task.working_directory` with the
timeout from `task.timeout_ms`. Returns `success=False` on non-zero
exit or timeout; stdout lands in `output`, while stderr and the exit
code go into `structured`.

`runtime_config`:

| key | type | required | description |
|---|---|:---:|---|
| `command` | `str` | no | Command to run. Takes precedence over `<command>` in the prompt. |
| `shell` | `str` | no | Defaults to `/bin/bash`. Also overridable via `DAP_BASH_SHELL`. |
| `env` | `dict[str, str]` | no | Extra environment variables (merged onto the subprocess env). |

If `command` isn't set, the adapter extracts the contents of the
first `<command>...</command>` block from `prompt_xml` — this allows
commands derived from `PipelineState` via Jinja in the agent's
template.

### Security model (v0.1)

**Single-user, local-trust.** The command runs with the engine
process's permissions inside the configured `working_directory` —
**no sandbox, no network isolation, no file confinement**. Every
agent definition that uses the `bash` runtime effectively controls
the host. A multi-user deployment requires auth (which ships in
v0.3) **plus** an additional isolation layer (firejail / Docker /
nsjail) before this runtime can safely accept untrusted agent
definitions.

---

## Env layering

Subprocess-spawning adapters (`bash`, `claude-code`, `codex`,
`gemini-cli`) compose the subprocess environment in three layers.
Higher layers override lower ones:

1. **Engine process env** (lowest) — the environment the
   `dap-engine` process inherits. Keep secrets here
   (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`).
2. **Project `env_vars`** (overlay) — `Project.env_vars` on the
   project bound to the run. Higher than engine env, lower than
   per-agent. Convenient for non-secret values like
   `WORKSPACE_NAME`, feature flags, per-project paths.
3. **Per-agent `runtime_config.env`** (highest) — declared inline
   in the agent definition. The final per-call override.

Ad-hoc runs (without a `project_id`) see only layers 1 + 3; layer 2
is an empty dict. The `api-call` adapter doesn't spawn subprocesses,
so env layering doesn't apply (the SDK reads its API key directly
from `os.environ`).

---

## `claude-code`

Invokes the Claude Code CLI in `--print --output-format json` mode.
The XML prompt is sent via stdin; the response is structured JSON
(`result`, `usage`, `total_cost_usd`, `session_id`, `num_turns`).
Cost and token usage populate `RuntimeResult` automatically.

When to use `claude-code` instead of `api-call` (Anthropic):

| Need | Use |
|---|---|
| Single-shot LLM, deterministic, no tools | `api-call` |
| Agentic loop with file edits, bash, MCP tools | `claude-code` |

`runtime_config`:

| key | type | required | description |
|---|---|:---:|---|
| `model_id` | `str` | yes | Anthropic model (e.g. `claude-opus-4-7`, `claude-sonnet-4-6`) |
| `binary_path` | `str` | no | Defaults to `claude` on `PATH`. Override for multiple installations. |
| `extra_args` | `list[str]` | no | Appended to the CLI invocation, e.g. `["--allowed-tools","Read,Edit,Bash"]` |

API key: `ANTHROPIC_API_KEY` in the engine's env — the Claude Code
CLI reads it itself; the adapter doesn't inject. Alternative: a
`claude auth login` OAuth session at `$HOME/.claude/` (e.g. for Pro
or Max plans). Bind-mount that dir into Docker if the engine runs
in a container — see [`docs/quick-start.md`](https://github.com/rafeekpro/dap/blob/main/docs/quick-start.md#runtime-adapters--how-dap-talks-to-llms).

Process lifecycle is identical to `bash`: POSIX session group +
`asyncio.shield(wait)` cleanup + `CancelledError` handling. Engine
pause/abort kills the entire process group (CLI + its subprocesses),
no zombies.

---

## `codex`

Invokes the OpenAI Codex CLI in `exec --json --model {model_id}`
mode. The XML prompt goes via stdin; the result is structured JSON.
Token usage is extracted from `usage` with name fallbacks
(snake_case from the OpenAI SDK, camelCase in some builds, plus the
`prompt_tokens` / `completion_tokens` variant). The response text is
looked up in `output_text` → `result` → `response` → `text` →
`output`. Cost is returned as `None` (the CLI doesn't report it; if
you want pricing, use `api-call` with `provider="openai"`).

When to use `codex` instead of `api-call` (OpenAI):

| Need | Use |
|---|---|
| Single-shot LLM, deterministic, no tools | `api-call` |
| Agentic loop with file edits, bash, MCP tools | `codex` |

`runtime_config`:

| key | type | required | description |
|---|---|:---:|---|
| `model_id` | `str` | yes | OpenAI model (e.g. `gpt-5-codex`, `gpt-5`, `o3`) |
| `binary_path` | `str` | no | Defaults to `codex` on `PATH`. |
| `extra_args` | `list[str]` | no | Appended to the CLI invocation, e.g. `["--sandbox","workspace-write"]` |

API key: `OPENAI_API_KEY` in the engine's env — the Codex CLI reads
it itself; the adapter doesn't inject.

Process lifecycle is identical to `bash` / `claude-code`. The full
JSON payload from the CLI lands in `RuntimeResult.structured.payload`
— this lets you debug shape drift between CLI releases without
adapter changes.

---

## `gemini-cli`

Invokes the Google Gemini CLI with `-m {model_id} -o json`; the XML
prompt goes via stdin and the result is structured JSON. Token usage
is extracted from `usage_metadata` (snake_case from the SDK or
camelCase from some builds — the adapter tries both). Cost is
returned as `None` (the CLI doesn't report it; if you want pricing,
use `api-call` with `provider="gemini"`).

`runtime_config`:

| key | type | required | description |
|---|---|:---:|---|
| `model_id` | `str` | yes | Gemini model (e.g. `gemini-3.0-pro`, `gemini-3.0-flash`) |
| `binary_path` | `str` | no | Defaults to `gemini` on `PATH`. |
| `thinking_budget` | `int` | no | Reasoning-token cap (mapped to `--thinking-budget`) |

API key: `GEMINI_API_KEY` in the engine's env — or `GOOGLE_API_KEY`
as a fallback (the Gemini CLI accepts either). The adapter doesn't
inject; the CLI reads them itself.

Process lifecycle is identical to `bash` / `claude-code`.

---

## `http`

Generic POST to any JSON-speaking service — Ollama, llama.cpp,
custom gateways, OpenAI/Anthropic-compatible proxies, your team's
internal API. The request body is a Jinja2 template (sandboxed)
rendered against the scope `{prompt_xml, runtime_config}`; the
response is parsed via JSONPath.

`runtime_config`:

| key | type | required | description |
|---|---|:---:|---|
| `url` | `str` | yes | Endpoint (http:// or https://) |
| `method` | `str` | no | `POST` (default) or `PUT` |
| `request_template` | `dict` / `list` / `str` | yes | Template with Jinja2 placeholders. Strings are templated; other types pass through as-is. |
| `response_extractor` | `dict[str, str]` | yes | Map of names → JSONPath. **Must include `output`.** E.g. `{"output":"$.response"}` |
| `auth` | `dict` | no | `{"type":"bearer","env":"OLLAMA_API_KEY"}` / `{"type":"header","name":"...","env":"..."}` / `{"type":"basic","user_env":"...","pass_env":"..."}` |
| `headers` | `dict[str,str]` | no | Extra static headers |

API keys: always via env var names declared in `auth.env` /
`auth.user_env` / `auth.pass_env` — never persisted in the database.

Ollama example:

```json
{
  "url": "http://localhost:11434/api/generate",
  "request_template": {
    "model": "{{ runtime_config.model_id }}",
    "prompt": "{{ prompt_xml }}",
    "stream": false
  },
  "response_extractor": {
    "output": "$.response",
    "tokens_used": "$.eval_count"
  },
  "model_id": "llama3.2"
}
```

Cancellation: httpx connections are cancelled via the async context
manager (`async with`); engine pause/abort ends the request without
residue.

---

## `python-func`

Executes any Python callable (sync or async) as a DAP agent. The
entry point is resolved at invocation time — installing a package
doesn't require an engine restart.

### Function signature

```python
async def run(state: dict, config: dict) -> dict:
    """
    state  — dict built from the fields toggled by pass_prompt / pass_context.
    config — runtime_config from the agent definition.
    returns — dict of fields to merge into PipelineState.
              Reserved key __audit: dict is extracted by the adapter
              and lands in RuntimeResult.structured["audit"] (NOT in
              state_delta).
    """
    return {
        "output_field": result_text,
        "__audit": {"tokens_used": 1240, "cost_usd": 0.003},
    }
```

A sync `def run(state, config)` also works — the adapter wraps it
in `loop.run_in_executor(None, ...)`.

`runtime_config`:

| key | type | required | description |
|---|---|:---:|---|
| `callable_path` | `str` | yes | `"package.module:func_name"`. The package must be installed in the engine's venv. |
| `pass_prompt` | `bool` | no | Whether to inject `prompt_xml` into `state["prompt_xml"]`. Defaults to `True`. |
| `pass_context` | `bool` | no | Whether to inject `RuntimeContext` into `state["context"]`. Defaults to `False`. |

`RuntimeResult.structured`:

| key | type | description |
|---|---|---|
| `state_delta` | `dict` | The function's return after `__audit` is stripped — ready to merge into state. |
| `audit` | `dict` | The `__audit` payload from the return value (tokens, cost, custom fields). |

### Example

```json
{
  "runtime_id": "python-func",
  "runtime_config": {
    "callable_path": "cortex.nodes.mockup:run",
    "pass_prompt": true
  }
}
```

### Package installation

The package containing the callable must be installed in the engine's
venv separately. It is **not** a build dependency of `dap-engine`.

The `python-func` adapter resolves `callable_path` with `importlib`
at invocation time — the engine starts fine without the package and
only fails when that specific node runs. Install the package in the
engine's venv on the host where `dap-engine` runs (e.g.
`uv pip install -e /path/to/cortex-project`).

### Security model (v0.1)

**Single-user, local-trust.** Same trust level as `bash` — the
callable runs with the engine process's permissions, **no sandbox,
no import isolation, no network confinement**. The package must be
installed in the engine's venv. A multi-user deployment requires
auth (which ships in v0.3) **plus** an additional isolation layer
before this runtime can safely accept untrusted agent definitions.

---

## See also

- [DAP project README](https://github.com/rafeekpro/dap) — full architecture.
- [`docs/runtimes.md`](https://github.com/rafeekpro/dap/blob/main/docs/runtimes.md) — design notes on adding a new adapter.
- [`docs/providers.md`](https://github.com/rafeekpro/dap/blob/main/docs/providers.md) — per-provider setup recipes.
- [`docs/quick-start.md`](https://github.com/rafeekpro/dap/blob/main/docs/quick-start.md#runtime-adapters--how-dap-talks-to-llms) — choosing between `api-call` and CLI runtimes per deployment shape.

## License

See the [main repository](https://github.com/rafeekpro/dap) for licensing details.

# LLM providers — recipes for API + CLI

Companion to `docs/runtimes.md`. Where that doc explains the adapter
abstraction (how an executor plugs into the engine), this one answers
the operator question: **I want to use provider X. How do I configure
DAP?**

Two access modes per provider where it makes sense:

- **API mode** — single-shot SDK call via the `api-call` adapter.
  Deterministic, no tools, fast, full cost tracking. Use for selection
  / planning / verification.
- **CLI mode** — agentic loop via a dedicated adapter (`claude-code`,
  `gemini-cli`, eventually `codex` / `aider`). Slower, more expensive,
  has tool use + file edits + MCP. Use for implementation.

Pipelines compose them: cheap API call decides which task to do,
expensive CLI does the actual coding.

## Provider matrix

| Provider              | API runtime                                  | CLI runtime    | Cost tracking          |
| --------------------- | -------------------------------------------- | -------------- | ---------------------- |
| **Anthropic**         | `api-call` (`provider="anthropic"`)          | `claude-code`  | Full (Claude 4.x + cache) |
| **OpenAI**            | `api-call` (`provider="openai"`)             | `codex` (stub) | Full (gpt-5, o-series) |
| **Google**            | `api-call` (`provider="gemini"`)             | `gemini-cli`   | Full (Gemini 2.x/3.x)  |
| **GLM (z.ai)**        | `api-call` (`provider="openai-compat"`)      | —              | None (3rd-party)       |
| **OpenRouter**        | `api-call` (`provider="openai-compat"`)      | —              | OR-reported            |
| **Together / Groq**   | `api-call` (`provider="openai-compat"`)      | —              | None (3rd-party)       |
| **Ollama / llama.cpp**| `http` adapter                               | `bash` (`ollama run`) | n/a (local)     |

`openai-compat` covers anything that speaks the OpenAI Chat Completions
shape — see the per-provider sections for `base_url` + env var hints.

## Per-provider setup

### Anthropic (Claude)

**Env vars:** `ANTHROPIC_API_KEY`.

**Models** (cached pricing in `_providers/_anthropic.py`):

- `claude-opus-4-7` — best, $5/$25 per 1M (in/out)
- `claude-opus-4-6`, `claude-opus-4-5` — $5/$25
- `claude-sonnet-4-6`, `claude-sonnet-4-5` — $3/$15
- `claude-haiku-4-5` — $1/$5

API mode `runtime_config`:

```json
{
  "provider": "anthropic",
  "model_id": "claude-haiku-4-5",
  "max_tokens": 4096,
  "effort": "high",
  "prompt_cache": true,
  "enable_thinking": false
}
```

CLI mode (`claude-code`): install via `npm install -g @anthropic-ai/claude-code`
or `brew install claude` (depending on platform). DAP reads
`ANTHROPIC_API_KEY` for both modes.

```json
{
  "model_id": "claude-opus-4-7",
  "binary_path": "/opt/homebrew/bin/claude",
  "extra_args": ["--allowed-tools", "Read,Edit,Bash"]
}
```

Verify: `curl http://127.0.0.1:7333/runtimes/api-call/health` (API) or
`/runtimes/claude-code/health` (CLI).

### OpenAI

**Env vars:** `OPENAI_API_KEY`.

**Models:** `gpt-5`, `gpt-5-codex`, `gpt-5-mini`, `gpt-5-nano`,
`o1`, `o1-mini`, `o3`, `o3-mini` (pricing in `_providers/_openai.py`).

API mode `runtime_config`:

```json
{
  "provider": "openai",
  "model_id": "gpt-5-mini",
  "max_tokens": 4096,
  "temperature": 0.2
}
```

CLI mode: `codex` adapter is a stub. Use API mode until #53 lands.

### Google (Gemini)

**Env vars:** `GEMINI_API_KEY` (or `GOOGLE_API_KEY` as fallback —
both Gen AI SDK and CLI accept either).

**Models:** `gemini-3.0-pro`, `gemini-3.0-flash`, `gemini-2.5-pro`,
`gemini-2.5-flash`, `gemini-2.0-flash`.

API mode `runtime_config`:

```json
{
  "provider": "gemini",
  "model_id": "gemini-3.0-pro",
  "max_tokens": 8192,
  "temperature": 0.2,
  "thinking_budget": 8192
}
```

CLI mode (`gemini-cli`): install via `npm install -g @google/gemini-cli`.

```json
{
  "model_id": "gemini-3.0-pro",
  "binary_path": "/opt/homebrew/bin/gemini",
  "thinking_budget": 8192
}
```

### GLM (z.ai)

**Env vars:** any name you choose; pass via `runtime_config.api_key_env`.
Convention: `GLM_API_KEY`.

**Models:** `glm-4-flash`, `glm-4-plus`, `glm-5-flash` (verify against
your z.ai account).

`runtime_config`:

```json
{
  "provider": "openai-compat",
  "model_id": "glm-5-flash",
  "base_url": "https://api.z.ai/api/coding/paas/v4",
  "api_key_env": "GLM_API_KEY",
  "max_tokens": 4096,
  "temperature": 0.2
}
```

Cost is reported as `null` — pricing is per-account on z.ai and we
don't ship a table.

### OpenRouter

**Env vars:** `OPENROUTER_API_KEY` (or any name you pick via
`api_key_env`).

`runtime_config`:

```json
{
  "provider": "openai-compat",
  "model_id": "anthropic/claude-opus-4-7",
  "base_url": "https://openrouter.ai/api/v1",
  "api_key_env": "OPENROUTER_API_KEY",
  "max_tokens": 4096
}
```

Model IDs follow OpenRouter's `provider/model` convention. Cost is
reported by OpenRouter in the response — extracted via the standard
OpenAI usage shape.

### Ollama (local)

Two ways: native `http` adapter, or shell out via `bash`.

**Via http adapter** — best when you want structured cost/token tracking
and proper cancellation:

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

No env var needed for local Ollama. For a remote / authenticated
deployment, add an `auth: {"type": "bearer", "env": "OLLAMA_API_KEY"}`.

**Via bash** — quick and dirty for `ollama run`:

```json
{
  "command": "ollama run llama3.2 < /dev/stdin"
}
```

(Token counts and cost won't be captured.)

## Agent recipes

Three concrete agents matching common roles. Drop them into the
dashboard via the New Agent form, or POST `/agents` directly.

### DeveloperJr — GLM 5 (cheap, fast)

```json
{
  "name": "DeveloperJr",
  "role": "implementer",
  "runtime_id": "api-call",
  "runtime_config": {
    "provider": "openai-compat",
    "model_id": "glm-5-flash",
    "base_url": "https://api.z.ai/api/coding/paas/v4",
    "api_key_env": "GLM_API_KEY",
    "max_tokens": 4096
  },
  "prompt_template": "<agent_prompt><role>{{ role }}</role><task>Implement: {{ implementation_notes }}</task></agent_prompt>"
}
```

Runs via `api-call` — single-shot, no tools. Best for trivial tasks
where speed and cost matter more than agentic exploration.

### DeveloperSenior — Claude Opus 4.7 via Claude Code CLI

```json
{
  "name": "DeveloperSenior",
  "role": "implementer",
  "runtime_id": "claude-code",
  "runtime_config": {
    "model_id": "claude-opus-4-7",
    "extra_args": ["--allowed-tools", "Read,Edit,Bash,Grep"]
  },
  "prompt_template": "<agent_prompt><role>senior_dev</role><task>{{ implementation_notes }}</task></agent_prompt>"
}
```

Runs via `claude-code` CLI — gets the full agentic loop. Use for
non-trivial implementation where the agent needs to read files, edit,
run tests.

### DeveloperFrontend — Gemini 3 Pro

```json
{
  "name": "DeveloperFrontend",
  "role": "implementer",
  "runtime_id": "gemini-cli",
  "runtime_config": {
    "model_id": "gemini-3.0-pro",
    "thinking_budget": 8192
  },
  "prompt_template": "<agent_prompt><role>frontend_dev</role><task>Build a React component for: {{ implementation_notes }}</task></agent_prompt>"
}
```

Runs via `gemini-cli` — Google's agentic CLI, fits front-end work
where the model excels at component generation.

## Adding a new provider

If your service speaks Chat Completions, you don't need code — set
`provider="openai-compat"` with a `base_url` and `api_key_env`. Done.

For everything else (custom JSON shape, non-OpenAI semantics):

1. **Create a provider module** in
   `packages/runtimes/src/dap_runtimes/adapters/_providers/`. Mirror
   `_anthropic.py`: expose `ID`, `DEFAULT_ENV_VAR`, `validate_config`,
   `env_var_for`, async `call`, `healthcheck`. Each function has a
   30-line doc-of-truth in the existing modules — copy the shape.

2. **Register in the registry**: add a `ProviderInfo` entry to
   `PROVIDER_REGISTRY` in `_providers/__init__.py`. The lazy-load path
   handles SDK imports — only loaded when `get_provider(id)` is called.

3. **Add pricing table**: keep it next to the call site (per-module).
   Unknown models return `cost_usd=None`; known ones get full numbers.

4. **Tests**: copy `tests/smoke/test_provider_openai.py` shape. Mock
   the SDK client, cover validation + happy path + error mapping.

5. **Update the dashboard schema**: add the new provider value to the
   `provider` select in `apps/dashboard/src/components/agents/runtime-config-schemas.ts`.

For a brand new **runtime** (not just a new LLM provider — a new
executor type, like a sandboxed VM or a different agentic CLI), see
`docs/runtimes.md`.

## See also

- [`runtimes.md`](runtimes.md) — runtime adapter abstraction (engine
  perspective).
- [`packages/runtimes/README.md`](../packages/runtimes/README.md) —
  per-runtime configuration reference.
- Engine `/settings` page — live healthchecks per runtime + per
  provider env-var presence.

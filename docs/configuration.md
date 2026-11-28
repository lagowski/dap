# Configuration reference

The engine reads its settings from the process environment at startup only, so restart it
after a change. `scripts/dev` loads `.env.local`. [`.env.example`](../.env.example) has the
commented template, and [docs/self-hosting.md](self-hosting.md) explains each setting
for production.

## Engine

| Variable | Default | Purpose |
|---|---|---|
| `DAP_ENGINE_HOST` | `127.0.0.1` | Listen address. Use `0.0.0.0` in containers. |
| `DAP_ENGINE_PORT` | `7333` | Listen port. |
| `DAP_LOG_LEVEL` | `INFO` | Engine log level. An unknown name stops startup. |
| `DAP_DB_PATH` | `./.dap/state.db` | SQLite file. Its directory is created if missing. |
| `DAP_DATABASE_URL` | unset | `postgresql+asyncpg://…` switches to Postgres and overrides `DAP_DB_PATH`. Migrations run at startup. |
| `DAP_PG_POOL_MIN_SIZE` / `DAP_PG_POOL_MAX_SIZE` | `4` / `10` | Postgres checkpointer pool size. |
| `DAP_PG_POOL_RECONNECT_TIMEOUT` | `300` | Seconds the Postgres pool keeps trying to reconnect. |
| `DAP_CORS_ORIGINS` | local dev origins | Comma-separated origins allowed to call the engine. **Set in production.** |
| `DAP_DRY_RUN_BUDGET_USD` | `0.50` | Cost cap for each dry run in the agent Test panel. |
| `DAP_BASH_SHELL` | `/bin/bash` | Shell for the `bash` runtime (agents can override with `runtime_config.shell`). |
| `DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN` | off | `1` lets non-admin users run `bash` agents. These run with the engine's privileges. |
| `DAP_PYTHON_FUNC_MAX_WORKERS` | `10` | Worker threads for the `python-func` runtime. |
| `DAP_INSTANCE_ENV_VARS_KEY` | unset | Fernet key for the encrypted instance env-var store (`/admin` settings). Without it, writing an env var returns 503. Must stay the same across restarts. |
| `DAP_INTERACTION_LOG_ENABLED` | `1` | Records model interactions in `interaction_log`. `0` turns it off. |
| `DAP_INTERACTION_LOG_RETENTION_DAYS` | `90` | Days kept; purged at startup. `0` or negative keeps them forever. |
| `DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS` | empty (off) | Comma-separated hosts that `POST /pipelines/import-from-url` may fetch bundles from. |
| `DAP_TEMPLATE_REGISTRY_AUTH_TOKEN` | unset | Bearer token sent with those fetches. |
| `DAP_PIPELINE_GENERATOR_COMMAND` | unset (off) | Command behind the prompt box on New pipeline, e.g. `cortex pipeline new --json --out /srv/dap-generated`. The engine appends the prompt as the last argument (no shell) and reads one JSON object with a `bundle` key from stdout; exit 2 means refused. Unset hides the box and `POST /pipelines/generate` answers 503. |
| `DAP_PIPELINE_GENERATOR_TIMEOUT_SECONDS` | `600` | Kill the generator after this many seconds (504). |
| `DAP_ASSISTANT_PROVIDER` / `DAP_ASSISTANT_MODEL` | first provider with a key | Provider and model for the configuration assistant. `claude-code` uses the host's `claude` login. `ASSISTANT_PROVIDER` / `ASSISTANT_MODEL` also work as instance env vars. |

## Auth

| Variable | Default | Purpose |
|---|---|---|
| `DAP_AUTH_JWT_SECRET` | random per process | Signs dashboard session tokens. **Required** for anything that restarts or has more than one replica (`openssl rand -hex 32`). API tokens don't depend on it. |
| `DAP_AUTH_ACCESS_TTL_SECONDS` | `900` | Session token lifetime. |
| `DAP_AUTH_LOG_RESET_TOKENS` | off | `1` logs password-reset tokens, for installs without email. Keep it off in production. |
| `DAP_AUTH_OAUTH_REDIRECT_URL` | unset | After OAuth, the browser is redirected here with `?token=` (for example `http://localhost:3000/api/auth/oauth/callback`). Unset returns JSON. |
| `DAP_OAUTH_GITHUB_CLIENT_ID` / `_SECRET` | unset | Turns on GitHub login when both are set. |
| `DAP_OAUTH_GOOGLE_CLIENT_ID` / `_SECRET` | unset | Turns on Google login when both are set. |

## Provider keys

You only need keys for the providers you call:

| Provider | Env var | Used by |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `api-call` (provider=`anthropic`), `claude-code` CLI fallback |
| OpenAI | `OPENAI_API_KEY` | `api-call` (provider=`openai`), `codex` CLI |
| Google Gemini | `GEMINI_API_KEY` (or `GOOGLE_API_KEY` for the CLI) | `api-call` (provider=`gemini`), `gemini-cli` |
| Z.AI GLM | `GLM_API_KEY` | `api-call` (provider=`glm`) |
| OpenRouter | `OPENROUTER_API_KEY` | `api-call` (provider=`openrouter`), slash-namespaced model ids |

The `claude-code` and `gemini-cli` runtimes can also use their own logins (`claude` on a
Pro/Max plan, `gemini auth login`) instead of an API key. For any other OpenAI-compatible
provider (Together, internal proxies, Ollama), use `api-call` with `provider=openai-compat`,
set `api_key_env` and `base_url` in the agent's `runtime_config`, and export that env var.
See [docs/providers.md](providers.md). The `bash` runtime needs no key, but **it runs
commands with the engine's privileges**. See the security note in
[packages/runtimes/README.md](../packages/runtimes/README.md).

## CLI

| Variable | Purpose |
|---|---|
| `DAP_ENGINE_URL` | Engine base URL for `dap project …` (same as `--engine`). |
| `DAP_AUTH_TOKEN` | API token for `dap project …`. |

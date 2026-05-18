# dap-engine

The execution engine for [DAP](https://github.com/rafeekpro/dap)
(Deterministic Agent Pipeline). FastAPI service that owns the
canonical state of agents, pipelines, runs, projects, and users;
drives execution via a LangGraph state machine; and dispatches
every node to a runtime adapter from `dap-runtimes`.

Most operators don't install this directly — it arrives as a
dependency of `dap-cli` and gets started by `dap start`. Install it
directly if you're embedding the engine in a different process tree
or writing a custom launcher.

## Installation

```bash
pip install dap-engine
```

Or, more commonly, transitively via `dap-cli`:

```bash
pipx install dap-cli
```

## Running standalone

```bash
# Defaults: 127.0.0.1:7333, SQLite at ./.dap/state.db
dap-engine

# Bind everywhere (e.g. inside Docker):
DAP_ENGINE_HOST=0.0.0.0 dap-engine

# With Postgres backend:
DAP_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/dap dap-engine
```

The full env-var surface is enumerated in
[`.env.example`](https://github.com/rafeekpro/dap/blob/main/.env.example)
at the repo root. The big ones:

| Variable | Required in prod? | Purpose |
|---|---|---|
| `DAP_AUTH_JWT_SECRET` | yes (any multi-replica or persistent deploy) | Signs the dashboard cookie session. 32+ bytes from `openssl rand`. |
| `DAP_DATABASE_URL` | no (SQLite is the default) | Postgres connection string. Overrides `DAP_DB_PATH` when set. |
| `DAP_CORS_ORIGINS` | yes in prod | Comma-separated dashboard origins. Defaults to a local-dev allow-list. |
| `DAP_OAUTH_GITHUB_CLIENT_ID` / `_SECRET` | optional | Enables `/auth/github/*` when both are set. |
| `DAP_OAUTH_GOOGLE_CLIENT_ID` / `_SECRET` | optional | Enables `/auth/google/*` when both are set. |
| `DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN` | no (default `0`) | Opt-in escape hatch for single-user/local-trust installs. When unset, only admins may execute `bash` agents or dry-runs. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GLM_API_KEY` | per-runtime | Provider API keys; only needed for the runtimes you actually call. |

## API surface (v0.3)

Auto-generated OpenAPI docs are served at `http://<engine>/docs`
when the engine is running. Major resource groups:

| Group | Routes | Notes |
|---|---|---|
| `/health` | health check | Returns `{"status":"ok","service":"dap-engine","version":"<x.y.z>","db_dialect":"sqlite"\|"postgresql","timestamp":"..."}` — anonymous-readable, useful for liveness probes |
| `/auth/jwt/*` | login, logout, refresh | fastapi-users default JWT backend |
| `/auth/register`, `/auth/forgot-password`, `/auth/reset-password` | password lifecycle | Password reset is token-based; no SMTP shipped in v0.3 |
| `/auth/github/*`, `/auth/google/*` | OAuth | Mounted only when the corresponding env vars are set |
| `/auth/api-tokens` | self-managed opaque tokens | `dap_<43chars>`, SHA-256-hashed in DB |
| `/users`, `/users/{id}` | user CRUD | Admin-only list at `GET /users` |
| `/agents`, `/pipelines`, `/projects`, `/runs` | resource CRUD | 404 anti-enumeration on cross-user access |
| `/audit/events` | audit log read | Admin-only |
| `/settings/admin` | read-only instance config | Admin-only |
| `/runtimes`, `/runtimes/{id}/health` | runtime adapter introspection | Useful for health probes |

Every resource route requires authentication — JWT cookie or
`Authorization: Bearer dap_<...>` API token. Anonymous calls get
401; authenticated calls hitting other users' resources get 404
(anti-enumeration).

## Database backends

Two SQL backends are supported, chosen automatically by which env
var is set:

- **SQLite** (default) — `DAP_DB_PATH=./.dap/state.db`. Single-file
  database, WAL mode, fine for one operator. The standalone Docker
  compose uses this by default.
- **PostgreSQL** — `DAP_DATABASE_URL=postgresql+asyncpg://...`.
  Required for any deployment with concurrent writers (5+ users).
  Connection pooling tuned via `DAP_PG_POOL_MIN_SIZE` /
  `_MAX_SIZE`; in-code migrations run on startup.

Schema migrations (in `dap_engine.persistence.migrations`) are
applied automatically on engine startup — there's no separate
`alembic upgrade` step. The migration ledger lives in
`schema_migrations` table.

## Upgrading from 0.0.1

On first startup against a pre-v0.3 `.dap/state.db`, migration 015
auto-promotes the legacy single-user installation: synthesises a
`legacy-admin@local` admin and **prints a generated password
exactly once on stdout**:

```text
Migrated single-user install. Bootstrap admin: legacy-admin@local
with password=<random>. Change immediately at /admin/users.
```

Capture the password from the engine log on first boot. If you
miss it, run `dap init --force --admin-email=legacy-admin@local
--admin-password=<new>` (via `dap-cli`) to rotate.

## Compatibility

- Python 3.13+
- FastAPI 0.115+
- SQLAlchemy 2.0+
- PostgreSQL 14+ (asyncpg driver) or SQLite 3.35+ (WAL mode)

## See also

- [DAP project README](https://github.com/rafeekpro/dap) — architecture + first pipeline.
- [`docs/architecture.md`](https://github.com/rafeekpro/dap/blob/main/docs/architecture.md) — state machine, Run lifecycle, LangGraph checkpoint model.
- [`docs/auth.md`](https://github.com/rafeekpro/dap/blob/main/docs/auth.md) — credential mechanisms (password, OAuth, API tokens).
- [`docs/admin-guide.md`](https://github.com/rafeekpro/dap/blob/main/docs/admin-guide.md) — `/admin/*` operator manual.
- [`docs/security.md`](https://github.com/rafeekpro/dap/blob/main/docs/security.md) — threat model, secrets handling, hardening checklist.

## License

See the [main repository](https://github.com/rafeekpro/dap) for licensing details.

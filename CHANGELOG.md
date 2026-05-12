# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet — v0.3.0 just shipped. Next changes land here._

## [0.3.0] — 2026-05-12

First multi-user release. Engine + dashboard ship together for
self-hosting; auth covers password + GitHub/Google OAuth + opaque
API tokens; ownership enforcement covers every resource route.
Distribution paths: PyPI wheels (`pipx install dap-cli`),
multi-platform Docker image (`ghcr.io/rafeekpro/dap:0.3.0`), or
source (`./scripts/setup`). Existing 0.0.1 installs auto-migrate
to a `legacy-admin@local` admin account on first engine start of
this version — see *Migrated* below.

### Added

#### Phase A — backend auth + ownership (#299)

- fastapi-users (password) + OAuth (GitHub + Google) + opaque
  API tokens (`dap_<43chars>`, SHA-256-hashed, audit-trail
  revoke-anywhere).
- `users` / `oauth_accounts` / `api_tokens` / `audit_log` tables
  (migrations 9–14).
- Ownership filtering + 404 anti-enumeration on every `/agents`,
  `/pipelines`, `/projects`, `/runs` endpoint (sub-A4b2 series).
- Audit-log helpers + UserManager hooks for login, register,
  password reset, role change, API-token mint/revoke.

#### Phase B — dashboard auth flow (#300)

- Next.js cookie-proxy session (httpOnly `dap-jwt`),
  `middleware.ts` redirect to `/login?next=...`, login + signup +
  forgot/reset pages, UserMenu in sidebar.
- OAuth buttons (GitHub + Google) wired to the engine's OAuth
  callback redirect via `DAP_AUTH_OAUTH_REDIRECT_URL`.
- Per-user data filtering — every page shows only the operator's
  resources unless they're admin.

#### Phase C — admin panel (#301)

- `/admin/users` (list, role toggle, suspend, soft-delete).
- `/admin/audit-log` (filter by `event_type` + `user_id`, paginated).
- `/admin/api-tokens` (admin-wide view, revoke any user's token).
- `/admin/settings` (read-only instance config — JWT TTL, OAuth
  providers, CORS, storage backend; secrets presence-only).

#### Phase D — standalone packaging (#302)

- Multi-stage Dockerfile (`ghcr.io/rafeekpro/dap:<version>`,
  `linux/amd64` + `linux/arm64`) + `examples/standalone/docker-compose.yml`.
- Dashboard bundled into the `dap-cli` wheel via
  `scripts/build-dashboard-bundle.sh`; `dap start` spawns it
  inline when `node` is on PATH.
- Release pipeline at `.github/workflows/release.yml` — PyPI
  trusted-publisher OIDC + GHCR multi-platform + GitHub Release
  on `v*.*.*` tags.
- `dap init` admin bootstrap: idempotent superuser creation on
  the local SQLite (honors `DAP_DB_PATH` for the Docker compose
  case; Postgres deployments seed via the running engine over
  `DAP_DATABASE_URL` instead) with three credential modes
  (flag / stdin / interactive + random-password). `dap status`
  surfaces the bootstrap line.
- `dap init --force --admin-password=...` now also rotates the
  password on existing admins (lost-admin-password recovery
  path).

#### Phase E — migration, docs, cutover (#303)

- **Backfill migration** (#343, sub-E1): migration 015 promotes
  the synthetic `system@local` backfill anchor into a usable
  `legacy-admin@local` admin with a freshly-generated Argon2id
  password printed once on stdout. Idempotent. See *Migrated*
  below for the operator-facing flow.
- **`docs/auth.md`** (#344, sub-E2): step-by-step setup for all
  four credential mechanisms (password + GitHub + Google + API
  tokens), including provider-app registration walkthroughs.
- **`docs/admin-guide.md`** (#345, sub-E3): operator manual for
  `/admin/*` (users, audit log, API tokens) + recovery
  procedures (lost admin password, JWT secret rotation,
  corrupted SQLite).
- **`docs/security.md`** (#346, sub-E4): threat model, secrets
  handling, password / JWT / API-token internals,
  reverse-proxy hardening templates (Caddy + nginx with rate
  limiting), GDPR / soft-delete cascade semantics, explicit
  "v0.3 does NOT do" matrix.
- **`docs/self-hosting.md`** (#337, sub-D5): three install paths
  (PyPI / Docker / source), production checklist, troubleshooting
  table.
- **`.env.example`** rewritten with every engine env var grouped
  by concern (Auth / OAuth / CORS / Storage / Engine / providers)
  and a per-var rationale.
- **`examples/pipelines/team-collaboration.pipeline-bundle.json`**:
  three-node example demonstrating the v0.3 multi-user import
  flow — bundle has no embedded `user_id`, importer becomes
  owner.

### Changed

- Engine requires `DAP_AUTH_JWT_SECRET` to be set explicitly for
  any multi-replica or production deployment; falls back to a
  per-process random for single-worker dev convenience. The
  standalone Docker entrypoint fails fast (exit 64) when the
  secret is unset.
- `DAP_CORS_ORIGINS` describes the effective allow-list in
  `GET /settings/admin` with a `using_default` flag — admins
  can no longer mistake the local-dev defaults for a permissive
  policy.
- Workspace versioning aligned: every `pyproject.toml` and
  `dap_cli.__version__` bumped to `0.3.0`.

### Migrated

- **0.0.1 → 0.3.0 single-user data**: on first engine start
  against a pre-v0.3 `.dap/state.db`, migration 013 creates a
  synthetic `system@local` backfill row and migration 015
  promotes it to `legacy-admin@local` with a freshly-generated
  Argon2id password **printed exactly once on stdout**:

  ```
  Migrated single-user install. Bootstrap admin: legacy-admin@local
  with password=<random>. Change immediately at /admin/users.
  ```

  Capture this password from the engine log / terminal on first
  boot. If you miss it, re-bootstrap with
  `dap init --force --admin-email=legacy-admin@local --admin-password=<new>`
  — the existing row is promoted (and now password-rotated) in
  place. The audit_log carries `user.deleted` /
  `api_token.created` / etc. events tied to this user_id from the
  point of upgrade onwards.

### Removed

- Pre-v0.3 unauthenticated `localhost:7333` access path. Every
  resource endpoint now requires authentication (JWT cookie or
  `Authorization: Bearer dap_*` API token). Operators upgrading
  from 0.0.1: capture the `legacy-admin@local` password printed
  by migration 015, log in, mint API tokens for any CLIs /
  scripts that previously hit `localhost:7333` unauthenticated.

### Security

- Argon2id password hashing via fastapi-users' `PasswordHelper`
  (pwdlib backend), parameters `m=65536 t=3 p=4`.
- JWT signing HS256 against `DAP_AUTH_JWT_SECRET` (default TTL
  900s; no refresh-token flow — short TTL bounds revocation
  latency).
- API tokens hashed SHA-256 in the DB; the raw `dap_*` value
  is returned exactly once at creation and never persisted.
  Token revocation is immediate.
- Audit log captures `user.logged_in`, `user.forgot_password`,
  `user.password_reset`, `user.deleted`, `api_token.created`,
  `api_token.revoked`, plus resource lifecycle events
  (`agent.created`, etc.).
- 404 anti-enumeration on every admin-only list endpoint
  (`GET /users`, `GET /audit/events`, `GET /auth/api-tokens/admin`).
- See `docs/security.md` for the full threat model and explicit
  out-of-scope list (rate limiting, 2FA, SSO, encrypted-at-rest
  — all deferred to v0.4 or off-roadmap).

## [0.0.1]

Initial single-user release. Engine + LangGraph + SQLAlchemy +
SQLite, Next.js dashboard, no authentication. Superseded by
v0.3.0; see *Migrated* above for the upgrade story.

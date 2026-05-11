# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Admin bootstrap (#336, sub-D4): `dap init` now creates or promotes
  an admin user idempotently. Supports `--admin-email` with three
  password modes — `--admin-password=...` (flag), `--admin-password-stdin`
  (kubectl-style), or interactive `getpass` with random-password
  fallback. Writes `.dap/bootstrap.json` (chmod 600) tracking the
  admin email + creation timestamp; `dap status` surfaces the
  bootstrap line at the top of its output. Replaces the manual SQL
  ``UPDATE users SET is_superuser=1`` step in the standalone README.
- Release pipeline (#335, sub-D3): `.github/workflows/release.yml`
  triggered on `v*.*.*` tags. Builds six wheels with the dashboard
  bundle, publishes to PyPI via trusted-publisher OIDC, builds a
  multi-platform (`linux/amd64` + `linux/arm64`) Docker image and
  pushes to GHCR, and creates a GitHub Release with auto-generated
  changelog + attached wheels.
- Operator docs: `docs/release.md` covering the one-time
  trusted-publisher setup, per-release checklist, dry-run path
  (`workflow_dispatch`), and rollback procedures.

### Changed
### Fixed
### Removed

## [0.3.0] — TBD (cut by D5)

First multi-user release. Engine + dashboard ship together for
self-hosting; auth covers password + GitHub/Google OAuth + API
tokens; ownership enforcement covers every resource route.

### Added
- **Phase A (#299)** — backend auth + ownership:
  - fastapi-users (password) + OAuth (GitHub + Google) + opaque
    API tokens (`dap_*` SHA-256, audit-trail revoke-anywhere).
  - `users` / `oauth_identities` / `api_tokens` / `audit_log`
    tables (migrations 9–14). Legacy single-user data backfilled
    to a synthetic `system@local` admin.
  - Ownership filtering + 404 anti-enumeration on every `/agents`,
    `/pipelines`, `/projects`, `/runs` endpoint (sub-A4b2 series).
  - Audit-log helpers + UserManager hooks for login, register,
    password reset, role change, API-token mint/revoke.
- **Phase B (#300)** — dashboard auth flow:
  - Next.js cookie-proxy session (httpOnly `dap-jwt`), middleware
    redirect to `/login?next=...`, login + signup + forgot/reset
    pages, UserMenu in sidebar.
  - OAuth buttons (GitHub + Google) wired to the engine's
    OAuth callback redirect.
  - Per-user data filtering — every page shows only the
    operator's resources unless they're admin.
- **Phase C (#301)** — admin panel:
  - `/admin/users` (list, role toggle, suspend, soft-delete).
  - `/admin/audit-log` (filter by event_type + user_id,
    paginated).
  - `/admin/api-tokens` (admin-wide view, revoke any).
  - `/admin/settings` (read-only instance config — JWT TTL,
    OAuth providers, CORS, storage backend; secrets
    presence-only).
- **Phase D (#302)** — standalone packaging:
  - Multi-stage Dockerfile (`ghcr.io/<owner>/dap:<version>`)
    + `examples/standalone/docker-compose.yml`.
  - Dashboard bundled into the `dap-cli` wheel via
    `scripts/build-dashboard-bundle.sh`; `dap start` spawns it
    inline.
  - Release pipeline (this section).

### Changed
- Engine now requires `DAP_AUTH_JWT_SECRET` to be set explicitly
  for multi-worker deployments; falls back to a per-process random
  for single-worker dev convenience.
- `DAP_CORS_ORIGINS` now describes the effective allow-list in
  `/settings/admin` with a `using_default` flag — admins can no
  longer mistake the local-dev defaults for a permissive policy.

### Removed
- Pre-v0.3 unauthenticated `localhost:7333` access path. Operators
  upgrading from `0.0.1` should set `DAP_AUTH_JWT_SECRET` and
  follow `docs/self-hosting.md` (landing in sub-D5).

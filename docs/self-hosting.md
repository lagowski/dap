# Self-hosting DAP

DAP ships as a single multi-user instance: one engine + one dashboard, a
shared database, your team logging in over JWT (with optional OAuth).
This document covers the three supported install paths, the
production checklist, and the most common failure modes.

## Install paths

### 1. PyPI (`pipx`) — simplest

For a single-machine, single-operator install with no Docker. The
`dap-cli` wheel bundles the dashboard as a static Next.js build —
`dap start` spawns both the engine and the dashboard from one binary.

```bash
# 1. Install.
pipx install dap-cli            # uses ~/.local/bin/dap

# 2. Initialise (creates ./.dap/, bootstraps an admin user).
dap init --admin-email=you@example.com
#   → prompts for a password, or pass --admin-password / --admin-password-stdin

# 3. Start engine + dashboard.
dap start                       # engine on :7333, dashboard on :7332
#   Open http://localhost:7332 manually — `dap start` doesn't auto-launch
#   a browser. The dashboard only starts when the wheel ships the
#   bundled Next.js build AND `node` is on PATH; otherwise it runs
#   engine-only (dap status prints a hint when this happens).

# 4. Check status whenever.
dap status                      # admin email, engine PID/uptime, runtime adapters
```

`dap stop` cleans up the engine PID file and terminates the dashboard.
The data lives in `./.dap/` next to your shell's CWD.

### 2. Docker — recommended for shared / production deployments

Multi-platform image at `ghcr.io/lagowski/dap:<version>` (`linux/amd64`
+ `linux/arm64`). Pinned `0.3.0` tag for stability; `latest` follows
the most recent stable release (no pre-releases).

Two flavours:

**A. One-liner — minimal smoke test**

```bash
docker run --rm \
    -e DAP_AUTH_JWT_SECRET=$(openssl rand -hex 32) \
    -p 3000:3000 -p 7333:7333 \
    ghcr.io/lagowski/dap:0.3.0
```

The container listens on `:3000` (dashboard) and `:7333` (engine).
That differs from the pipx install where the dashboard binds to
`:7332` — the Docker image follows Next.js's own port convention.

This boots both processes inside the container but uses an ephemeral
SQLite — fine for trying things, not for keeping data.

**B. Compose — production**

See [`examples/standalone/`](../examples/standalone/):

```bash
cp examples/standalone/.env.example .env
# edit .env — DAP_AUTH_JWT_SECRET is required

docker compose -f examples/standalone/docker-compose.yml up -d

# Bootstrap an admin against the in-container database. ``dap init``
# honors the same ``DAP_DB_PATH`` the engine reads, so it lands in the
# right SQLite file regardless of CWD.
docker compose -f examples/standalone/docker-compose.yml exec dap \
    dap init --admin-email=you@example.com --force   # idempotent
```

The compose file mounts a named volume at `/data` for the SQLite
database, and exposes the dashboard on `:3000`. A Postgres service is
commented out — uncomment when you have more than one user.

### 3. From source — for contributors

Existing dev workflow, unchanged by v0.3:

```bash
git clone https://github.com/lagowski/dap.git
cd dap
./scripts/setup            # uv sync + pnpm install
./scripts/dev              # engine + dashboard in parallel, hot reload
```

## Production checklist

Before exposing DAP to the internet, work through this list. Every item
is a real failure mode someone has hit in self-host, not a theoretical
hardening exercise.

- [ ] **Set `DAP_AUTH_JWT_SECRET`** to a 32-byte random value
      (`openssl rand -hex 32`). Without it the engine generates a
      per-process random — fine for local dev, but in multi-replica
      deployments each replica signs with a different key and tokens
      issued by one fail validation on another. Tokens also invalidate
      on every restart, which logs every active user out.
- [ ] **Set `DAP_CORS_ORIGINS`** to the dashboard's actual origin(s).
      Unset falls back to a local-dev allow-list
      (`http://localhost:3000`, `http://127.0.0.1:3000`) — not a
      security policy.
- [ ] **Set `DAP_AUTH_OAUTH_REDIRECT_URL`** if you've configured GitHub
      or Google OAuth. Without it the OAuth flow ends on a JSON
      response instead of the dashboard.
- [ ] **Leave `DAP_AUTH_LOG_RESET_TOKENS` unset (or `=0`).** When on,
      password-reset tokens print to stdout — useful for self-hosted
      dev with no email delivery, dangerous in production logs (raw
      reset tokens are account-takeover credentials).
- [ ] **Switch to Postgres** via `DAP_DATABASE_URL` for >1 active user.
      SQLite is fine for a single operator; concurrent writes beyond
      that benefit from real row locking.
- [ ] **Pin the image tag.** Use `ghcr.io/lagowski/dap:0.3.0`, not
      `latest`. A pin lets you upgrade on your schedule.
- [ ] **Put DAP behind a reverse proxy** (Traefik, Caddy, nginx) that
      terminates TLS. The container speaks plain HTTP on `3000`
      (dashboard) + `7333` (engine); pipx uses `7332` (dashboard) +
      `7333` (engine). Only the dashboard port needs to be public —
      the dashboard's same-origin proxy at `/api/*` forwards backend
      calls so the engine port can stay private.
- [ ] **Configure OAuth providers** if your team uses them:
      - GitHub: <https://github.com/settings/developers> → New OAuth
        App. Callback: `https://<your-engine>/auth/github/callback`.
      - Google: <https://console.cloud.google.com/apis/credentials> →
        OAuth 2.0 client. Callback:
        `https://<your-engine>/auth/google/callback`.
- [ ] **Rotate the JWT secret** periodically. Rolling rotation isn't
      supported in v0.3 — a rotation invalidates every outstanding
      token, so plan for a brief mass logout.

## Verifying a fresh install

After `dap start` (PyPI) or `docker compose up -d` (Docker):

```bash
# 1. Engine health.
curl http://localhost:7333/health
#   → {"status":"ok",...}

# 2. Dashboard reachable.
curl -fsS http://localhost:7332/ -o /dev/null && echo OK
# or, for compose: curl -fsS http://localhost:3000/

# 3. Admin user wired up.
dap status                              # or: docker compose exec dap dap status
#   ✓ admin bootstrap: you@example.com (2026-05-12T…)
#   ● engine: running  PID … port 7333 uptime …
```

Log in at the dashboard URL with the credentials you passed to
`dap init`. The "Admin" link should appear in the sidebar.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Container exits with `DAP_AUTH_JWT_SECRET is not set.` | Missing env var. Set it in `.env` (Docker) or your shell (`pipx`). |
| Every API call returns 401 after a restart | `DAP_AUTH_JWT_SECRET` not persisted — each restart generates a new per-process random and invalidates all tokens. Set the env var. |
| Multi-replica setup logs users out randomly | Same root cause — each replica is signing with its own random secret. Set `DAP_AUTH_JWT_SECRET` to the same value on every replica. |
| `Failed to fetch` on every dashboard API call | CORS allow-list doesn't include the origin. Set `DAP_CORS_ORIGINS=https://your-dashboard.example.com`. |
| OAuth flow lands on a JSON page instead of the dashboard | `DAP_AUTH_OAUTH_REDIRECT_URL` is unset. Set it to the dashboard's `/api/auth/oauth/callback`. |
| `psycopg.OperationalError: could not connect` after switching to Postgres | The Postgres container isn't ready yet. Uncomment the healthcheck + `depends_on: service_healthy` block in the compose file. |
| `dap init` can't reach a running engine | It doesn't need to — `dap init` opens the SQLite file directly. Make sure no other process holds an exclusive lock on `.dap/state.db` (stop `dap start` first). |
| Login works but the "Admin" link is missing | The bootstrap user isn't a superuser. Re-run `dap init --force --admin-email=you@example.com --admin-password=...`; it's idempotent and promotes the existing row. |
| `dap status` shows "○ admin bootstrap: none" after `dap init` | You're in a different CWD than the one that holds `.dap/`. `dap` looks for `.dap/` in the current directory. |
| Browser shows the login page but `/api/auth/login` returns 502 | Reverse proxy isn't forwarding correctly. Curl the dashboard container directly to confirm it works, then check `proxy_pass` / `Host` headers. |

## Upgrade notes

Cross-version upgrades from < v0.3 land in [`CHANGELOG.md`](../CHANGELOG.md)
and Phase E's migration docs. The short version for v0.2 → v0.3: stop the
engine, bump the version (`pipx upgrade dap-cli` or pull a new image),
start it back up, run `dap init --force --admin-email=...` to promote
your operator account to admin. Existing data is owned by a synthetic
`system@local` account created during the migration; reassign ownership
via the admin UI if needed.

## Where to find things

- **Engine API docs**: `http://<engine>/docs` (FastAPI Swagger).
- **Audit log**: `/admin/audit-log` in the dashboard.
- **All user accounts + roles**: `/admin/users`.
- **API tokens (admin-wide view)**: `/admin/api-tokens`.
- **Instance settings (read-only)**: `/admin/settings`.
- **Release notes**: <https://github.com/lagowski/dap/releases>.

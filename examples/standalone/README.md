# Standalone Docker deployment

Self-host DAP with one container (engine + dashboard) and an optional
PostgreSQL sidecar.

## Quick start

```bash
# 1. Pull the compose file + env template.
cp .env.example .env

# 2. Mint a JWT secret. Anything 32+ random bytes works.
openssl rand -hex 32   # → paste into DAP_AUTH_JWT_SECRET in .env

# 3. Start.
docker compose up -d

# 4. Create the bootstrap admin (the engine has no admin out of the box).
#    Two paths, pick one:
#
#    a) Inside the running container — interactive prompt:
#       docker compose exec dap dap init --admin-email=you@example.com
#       (or pass --admin-password=... / --admin-password-stdin for non-TTY)
#
#    b) From the host before first start — when you have ``dap`` installed
#       locally and the data volume is bind-mounted, run ``dap init`` in a
#       directory that contains the same ``.dap/`` layout.
#
#    On a fresh database the command creates the admin with the supplied
#    password. Re-running is idempotent: an existing email gets promoted.

# 5. Open the dashboard.
open http://localhost:3000

# 6. Verify health.
docker compose ps        # dap should be ``healthy``
curl http://localhost:7333/health
```

## What this runs

- **DAP container** (`ghcr.io/lagowski/dap:latest`):
  - Engine on `:7333`
  - Dashboard on `:3000`
  - SQLite database in the `dap-data` volume (`/data/state.db`).
  - Non-root user (UID 1000).
- **PostgreSQL** (commented out) — uncomment when you have more than
  one user. SQLite is fine for a single operator; concurrent writes
  beyond that benefit from real row locking.

## Promote an existing user to admin

`dap init` is the supported path — it creates a new admin or promotes
an existing email idempotently. The SQL helpers below are a backstop
for the rare case where you've lost CLI access to the container.

```bash
# Inside the container (uses the bundled dap binary):
docker compose exec dap dap init --admin-email=you@example.com --admin-password=$(openssl rand -hex 16)

# SQLite (default) — direct DB edit.
docker compose exec dap sqlite3 /data/state.db \
    "UPDATE users SET is_superuser=1 WHERE email='you@example.com';"

# Postgres deployments:
docker compose exec postgres psql -U dap -d dap \
    -c "UPDATE users SET is_superuser=true WHERE email='you@example.com';"
```

Verify by logging out + back in — the "Admin" link appears in the
sidebar. ``dap status`` (inside the container) reports the bootstrap
state on its first line.

## Production checklist

- [ ] Set `DAP_AUTH_JWT_SECRET` to a 32-byte random value (never the
      default).
- [ ] Set `DAP_CORS_ORIGINS` explicitly to your dashboard origin.
      The unset state falls back to a **local-dev allow-list**, not a
      permissive policy — the engine's safer default but probably
      wrong for prod.
- [ ] Set `DAP_AUTH_OAUTH_REDIRECT_URL` if you're using OAuth, to
      `https://<dashboard>/api/auth/oauth/callback`.
- [ ] Leave `DAP_AUTH_LOG_RESET_TOKENS` unset (or `=0`). The flag
      makes the engine log raw reset tokens to stdout — useful for
      self-hosted dev without email delivery, dangerous in prod logs.
- [ ] Switch to Postgres for >1 user.
- [ ] Pin `DAP_IMAGE_TAG` to a specific version (e.g. `0.3.0`) rather
      than `latest`.

## Reverse proxy / HTTPS

The container speaks plain HTTP on its two ports. Put it behind a
reverse proxy that terminates TLS:

- **Caddy** — single-line config (auto-TLS via Let's Encrypt):
  ```
  dap.example.com {
      reverse_proxy /api/* dap:7333
      reverse_proxy * dap:3000
  }
  ```
- **nginx** — standard `proxy_pass` config; preserve `Host` + `X-Forwarded-*`. Full template with rate-limiting on `/auth/*` in [`docs/security.md`](../../docs/security.md#example-nginx-no-auto-tls-you-handle-certs).
- **Traefik** — no canned compose example shipped yet; the labels follow the same pattern as Caddy (one router for `/api/*` → engine, one for everything else → dashboard).

The dashboard's same-origin proxy at `/api/*` forwards engine calls,
so the reverse proxy only needs to route to the dashboard container —
the engine port doesn't have to be public.

## Troubleshooting

| Symptom | Likely cause |
|---------|---|
| Container exits with `DAP_AUTH_JWT_SECRET is not set.` | Missing env var. Set it in `.env`. |
| Browser shows the login page but `/api/auth/login` returns 502 | Reverse proxy isn't passing through the right host/port. Curl the dashboard container directly to verify. |
| `Failed to fetch` on every API call | CORS allow-list doesn't include the origin. Set `DAP_CORS_ORIGINS`. |
| OAuth flow ends on a JSON page instead of the dashboard | `DAP_AUTH_OAUTH_REDIRECT_URL` is unset — set it to the dashboard's `/api/auth/oauth/callback`. |
| `psycopg.OperationalError: could not connect` after switching to Postgres | The postgres service isn't ready yet. The compose file has a healthcheck + `depends_on: service_healthy` block (uncomment both). |

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

# 4. Create the bootstrap admin (the engine refuses to mint accounts
#    over OAuth without one). Until D4 ships the interactive
#    bootstrap, use the dashboard /signup page — the first registered
#    user is promoted to admin manually via the SQL helper below.
open http://localhost:3000

# 5. Verify health.
docker compose ps        # dap should be ``healthy``
curl http://localhost:7333/health
```

## What this runs

- **DAP container** (`ghcr.io/rafeekpro/dap:latest`):
  - Engine on `:7333`
  - Dashboard on `:3000`
  - SQLite database in the `dap-data` volume (`/data/state.db`).
  - Non-root user (UID 1000).
- **PostgreSQL** (commented out) — uncomment when you have more than
  one user. SQLite is fine for a single operator; concurrent writes
  beyond that benefit from real row locking.

## Promote the first user to admin

Until `dap init` (D4) lands the interactive bootstrap, the first
registered user has the default `is_superuser=False`. Flip it with a
one-line SQL update against the bind-mounted database:

```bash
# SQLite (default) — works against the file in the dap-data volume.
docker compose exec dap sqlite3 /data/state.db \
    "UPDATE users SET is_superuser=1 WHERE email='you@example.com';"
```

For Postgres deployments:

```bash
docker compose exec postgres psql -U dap -d dap \
    -c "UPDATE users SET is_superuser=true WHERE email='you@example.com';"
```

Verify by logging out + back in — the "Admin" link appears in the
sidebar.

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

- **Traefik** — see `examples/standalone/docker-compose.traefik.yml`
  (lands in D5).
- **Caddy** — single-line config:
  ```
  dap.example.com {
      reverse_proxy /api/* dap:7333
      reverse_proxy * dap:3000
  }
  ```
- **nginx** — standard `proxy_pass` config; preserve `Host` + `X-Forwarded-*`.

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

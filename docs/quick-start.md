# Quick start — pick your install path

You've got ~5 minutes and want DAP running. This doc decides
for you which of the three deployment paths fits, then walks
through it concretely. Every other doc in `docs/` goes deeper into
one slice — start here.

## TL;DR — answer 3 questions

```
Q1. Who'll use it?
    ├─ Just me  ────────────────────────────────►  PATH A
    └─ Team of 2-20

Q2. Where does it run?
    ├─ My laptop, ad-hoc  ──────────────────────►  PATH A
    └─ Server / VPS (always on)

Q3. Database?
    ├─ SQLite file (default, simplest)  ────────►  PATH B
    └─ Postgres (managed DB or sidecar)  ───────►  PATH C
```

| Path | Sounds like | Time to "logged in" |
|---|---|---|
| **A** — Local single-user (pipx + SQLite) | "I want to try it on my laptop" | ~5 min |
| **B** — Self-host on a VPS (Docker + SQLite) | "Small team, one machine, I'll add Postgres later" | ~15 min |
| **C** — Production (Docker + managed Postgres + TLS reverse proxy) | "Real users, real domain, real backups" | ~30-60 min |

You can always start at A and graduate to B or C later — the data
moves cleanly (SQLite → Postgres has a documented migration in
`docs/admin-guide.md`).

---

## Path A — Local single-user (pipx + SQLite)

**For:** trying DAP on your dev machine. Single operator. Data
lives in `./.dap/state.db` next to wherever you run `dap`.

### Requirements

| Tool | Version | Check |
|---|---|---|
| Python | 3.13+ | `python3 --version` |
| pipx | any | `pipx --version` |
| Node | 22+ (optional — for bundled dashboard) | `node --version` |

If `node` is missing, `dap start` will run engine-only and print a
hint. Install Node 22+ later via `nvm install 22` if you want the
web UI.

### Install

```bash
# 1. Install the CLI (pulls dap-engine + bundled dashboard transitively).
pipx install dap-cli

# 2. Initialise the project (creates ./.dap/ + bootstraps admin).
dap init --admin-email=you@example.com
# Prompts for password (twice). Or use --admin-password=... for
# automation. Or pipe via stdin: echo 'pw' | dap init --admin-password-stdin

# 3. Start engine + dashboard.
dap start

# 4. Open the dashboard manually:
#    Engine    → http://localhost:7333    (FastAPI Swagger at /docs)
#    Dashboard → http://localhost:7332    (Next.js UI)
```

### Verify

```bash
# In a second terminal:
curl http://localhost:7333/health
#   → {"status":"ok","version":"0.3.0"}

dap status
#   ✓ admin bootstrap: you@example.com (2026-05-13T...)
#   ● engine: running  PID 12345 port 7333 uptime 30s
```

Login via the dashboard with the credentials you passed to
`dap init`. You should see the **Admin** link in the sidebar.

### Stop / restart

```bash
dap stop          # graceful shutdown
dap start         # again
```

`.dap/state.db` persists between restarts in your CWD.

---

## Path B — Self-host on a VPS (Docker + SQLite)

**For:** team of 2-20 sharing one always-on instance. Docker
compose. SQLite by default (file in a named volume). Upgrade to
Postgres later by uncommenting the sidecar in the compose file.

### Requirements

| Tool | Version | On the VPS |
|---|---|---|
| Docker | 24+ | `docker --version` |
| Docker compose | v2 | `docker compose version` |
| Reverse proxy with TLS | recommended | Caddy 2 / nginx / Traefik |
| Open ports | 80/443 to internet, 3000 internal | `ss -tlnp` |

### Install

```bash
# 1. Grab the compose example from the repo (or clone).
mkdir -p /opt/dap && cd /opt/dap
curl -O https://raw.githubusercontent.com/rafeekpro/dap/main/examples/standalone/docker-compose.yml
curl -O https://raw.githubusercontent.com/rafeekpro/dap/main/examples/standalone/.env.example

# 2. Configure secrets.
cp .env.example .env
# Edit .env — the only REQUIRED value is DAP_AUTH_JWT_SECRET.
# Mint one with: openssl rand -hex 32
nano .env

# 3. Start.
docker compose pull        # fetches ghcr.io/rafeekpro/dap:0.3.0
docker compose up -d

# 4. Bootstrap the admin (idempotent — re-run with --force later
#    if you forget the password).
docker compose exec dap dap init \
    --admin-email=you@example.com \
    --admin-password=$(openssl rand -hex 16) --force
# Capture the password it prints; you'll need it to log in.
```

### Verify

```bash
# Engine health (inside compose network):
docker compose exec dap curl http://localhost:7333/health

# Dashboard externally:
curl http://<vps-ip>:3000/                # → 200 OK on login page
docker compose ps                          # → dap should be "healthy"

# Bootstrap state:
docker compose exec dap dap status
```

### Add TLS (recommended before exposing to the internet)

Put Caddy in front (one-line config, auto-HTTPS via Let's Encrypt):

```caddyfile
# /etc/caddy/Caddyfile
dap.example.com {
    reverse_proxy localhost:3000
}
```

Now `https://dap.example.com` works; only Caddy is public. Full
nginx + rate-limiting template in [`security.md`](security.md).

### Switch to Postgres later

Uncomment the `postgres` service in `docker-compose.yml` and set
`DAP_DATABASE_URL` in `.env`. See Path C below for what that looks
like — same compose file, just different env var.

---

## Path C — Production (Docker + managed Postgres + TLS)

**For:** real users, real domain, real backups. Postgres handles
concurrent writes properly; managed (RDS / Supabase / Crunchy
Bridge / Aiven / DO Managed) handles backups, point-in-time
recovery, automated upgrades.

### Architecture

```
                Internet
                   │
                   ▼ HTTPS :443
            ┌──────────────┐
            │ Caddy / nginx│  (TLS termination + rate limiting)
            └──────┬───────┘
                   │ HTTP :3000
                   ▼
        ┌──────────────────┐
        │ DAP container    │  ghcr.io/rafeekpro/dap:0.3.0
        │  - Engine :7333  │  (engine + dashboard, monolit)
        │  - Dashboard:3000│
        └────────┬─────────┘
                 │ TCP :5432 over private network or internet+TLS
                 ▼
         ┌────────────────┐
         │ Postgres       │  managed (RDS / Supabase / ...) or sidecar
         │ (separate)     │
         └────────────────┘
```

### Requirements

Everything from Path B, plus:

- A Postgres database accessible from the VPS — managed service or
  a separate Postgres container/server.
- A connection URL: `postgresql+asyncpg://user:pass@host:5432/dbname`.

### Install

```bash
# 1-2. Same as Path B (compose + .env).
cd /opt/dap
# ...

# 3. Set DAP_DATABASE_URL in .env. Comment out DAP_DB_PATH.
echo 'DAP_DATABASE_URL=postgresql+asyncpg://dap:secret@db.example.com:5432/dap_prod' >> .env

# 4. Start. Engine detects DAP_DATABASE_URL, runs migrations on
#    the Postgres schema automatically (in-code, no separate
#    alembic step).
docker compose up -d

# 5. Bootstrap admin against the Postgres DB.
docker compose exec dap dap init \
    --admin-email=you@example.com \
    --admin-password=$(openssl rand -hex 16) --force
```

### Verify

```bash
# Same as Path B; additionally:

# Confirm engine actually used Postgres (not silently fell back to SQLite).
docker compose exec dap psql "$DAP_DATABASE_URL" -c '\dt'
# Should list users, agents, pipelines, audit_log, etc.
```

### TLS + reverse proxy

Required. Use the Caddy snippet from Path B, or the full nginx
template from [`security.md`](security.md) which adds rate limiting
on `/auth/*` (recommended for any public deployment).

### Backups

Managed Postgres handles this. If you're running Postgres yourself
(sidecar mode):

```bash
# Daily backup via cron:
docker compose exec -T postgres \
    pg_dump -U dap dap | gzip > "/var/backups/dap-$(date +%F).sql.gz"
```

Test restore quarterly. Backup is not a backup until you've
restored from it.

---

## Pre-flight check (any path)

Before opening the dashboard for the first time, run through this
quick sanity list:

| Check | Command | Expected |
|---|---|---|
| Engine alive | `curl <engine-url>/health` | `{"status":"ok",...}` |
| Dashboard alive | `curl -fI <dashboard-url>/` | `HTTP/1.1 200` |
| Admin bootstrapped | `dap status` (or `docker compose exec dap dap status`) | `✓ admin bootstrap: you@example.com (...)` |
| DB reachable | `<engine-url>/health` returns `version` + no startup errors in logs | Check `docker compose logs dap` |
| JWT secret set (prod) | `docker compose exec dap printenv DAP_AUTH_JWT_SECRET \| wc -c` | ≥ 32 chars (not the per-process random fallback) |
| CORS allow-list set (prod) | `docker compose exec dap printenv DAP_CORS_ORIGINS` | Your dashboard origin(s), comma-separated |

If anything in this table fails, jump to the troubleshooting matrix
in [`self-hosting.md`](self-hosting.md#troubleshooting).

---

## Going further

Once you're logged in:

- **First pipeline** — [`README.md`](../README.md#first-pipeline--try-it-now)
- **Invite a teammate** — [`admin-guide.md`](admin-guide.md#user-management--adminusers) walks through `/admin/users`
- **GitHub / Google login** — [`auth.md`](auth.md#github-oauth) for the OAuth app registration walkthrough
- **API tokens for CI / scripts** — [`auth.md`](auth.md#api-tokens) for mint + revoke
- **Backups, JWT rotation, recovery from lost admin password** — [`admin-guide.md`](admin-guide.md#recovery-procedures)
- **Hardening checklist** — [`security.md`](security.md#hardening-checklist-for-production) (12 items, in impact order)
- **Cutting a release of your own fork** — [`release.md`](release.md)

## When something's stuck

Order of operations for triage:

1. `docker compose logs dap --tail=100` (or `dap` stdout for Path A) — startup errors land here.
2. `dap status` — confirms the bootstrap marker exists and engine is alive.
3. Try the symptom in [`self-hosting.md` troubleshooting](self-hosting.md#troubleshooting) — most common failure modes are listed.
4. If it's auth-related, [`auth.md` troubleshooting](auth.md#where-to-look-when-things-break).
5. If you've lost the admin password, [`admin-guide.md` recovery](admin-guide.md#lost-admin-password).

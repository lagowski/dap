# Deployment Guide

DAP runs best when the engine stays close to the agent CLIs it invokes.
The dashboard can be remote, but runtimes such as `claude-code`,
`gemini-cli`, `codex`, `aider`, and `bash` execute on the engine host
with that host's accounts, filesystem, and environment.

This guide covers three common setups:

- local-only on one machine with SQLite;
- LAN access with engine and dashboard on different machines;
- SSH tunnel access when you do not want to change firewall or CORS.

For production hardening, OAuth, reverse proxies, and Docker compose,
also see [docs/self-hosting.md](docs/self-hosting.md).

## Environment Variables

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `DAP_ENGINE_HOST` | engine | `127.0.0.1` | Bind address for the FastAPI engine. Use `0.0.0.0` only when another machine must reach it directly. |
| `DAP_ENGINE_PORT` | engine | `7333` | Engine HTTP port. |
| `DAP_CORS_ORIGINS` | engine | local dev origins | Comma-separated browser origins allowed to call the engine directly. Set this for LAN or production access. |
| `DAP_DATABASE_URL` | engine | unset | PostgreSQL URL. When unset, the engine uses SQLite at the configured `.dap/state.db` path. |
| `DAP_AUTH_JWT_SECRET` | engine | random per process | JWT signing secret. Required for stable multi-user or long-running deployments. |
| `NEXT_PUBLIC_DAP_ENGINE_URL` | dashboard | `http://127.0.0.1:7333` in source dev | Engine URL the dashboard should target. Use the URL as seen from the dashboard/browser setup. |

Provider/runtime variables such as `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, and `GLM_API_KEY` must be set on the engine host, not
only on the machine opening the dashboard.

## Default Credentials

Fresh installs should create an admin explicitly:

```bash
dap init --admin-email=you@example.com
```

`dap init` prompts for a password. For automation, use
`--admin-password` or `--admin-password-stdin`.

When upgrading an older single-user database, DAP creates
`legacy-admin@local` and prints a generated password once in the engine
log:

```text
Migrated single-user install. Bootstrap admin: legacy-admin@local
with password=<random>. Change immediately at /admin/users.
```

Sign in with that password, then change it immediately from the
dashboard. If you missed the generated password, reset/promote the admin
row with:

```bash
dap init --force --admin-email=legacy-admin@local --admin-password=<new-password>
```

## Pattern 1: Local-Only SQLite

Use this for a single operator on one machine. No database server is
required.

### PyPI or Bundled CLI

```bash
dap init --admin-email=you@example.com
dap start
```

Default ports:

- engine: `http://localhost:7333`
- bundled dashboard: `http://localhost:7332`

### Source Checkout

```bash
# Terminal 1: engine
uv sync --all-packages
set -a; source .env.local; set +a
uv run dap-engine

# Terminal 2: dashboard
cd apps/dashboard
NEXT_PUBLIC_DAP_ENGINE_URL=http://localhost:7333 pnpm dev
```

Open `http://localhost:3000`.

SQLite data lives under the active DAP state directory. For source
development that is usually `.dap/state.db`; for `dap start`, it is the
`.dap/` directory in the current working directory.

## Pattern 2: LAN Access

Use this when the engine runs on an always-on machine and another
machine opens the dashboard or browser.

The engine must run on the machine that has the agent CLIs logged in.
For example, if `claude-code` uses a local Claude subscription session,
that session must exist on the engine host. Forwarding only the browser
to another machine does not forward those local CLI accounts.

### Machine A: Engine Host

```bash
export DAP_ENGINE_HOST=0.0.0.0
export DAP_CORS_ORIGINS=http://192.168.1.B:3000,http://localhost:3000
export DAP_AUTH_JWT_SECRET="$(openssl rand -hex 32)"

# Optional: use PostgreSQL instead of SQLite.
export DAP_DATABASE_URL=postgresql+asyncpg://USER:PASS@DB_HOST:5432/dap

dap init --admin-email=you@example.com
dap start
```

If you run from source:

```bash
uv run dap-engine
```

Make sure the host firewall allows TCP `7333` from the dashboard
machine or from your trusted LAN only.

### Machine B: Dashboard or Browser

For source development dashboard:

```bash
cd apps/dashboard
NEXT_PUBLIC_DAP_ENGINE_URL=http://192.168.1.A:7333 pnpm dev
```

Open `http://localhost:3000`.

For a bundled or deployed dashboard, configure it to use the engine URL
reachable from that deployment. If the dashboard and browser are on
different machines, use the engine address that the browser can reach.

## Pattern 3: SSH Tunnel

Use this for quick remote access without changing CORS, firewall rules,
or engine bind address. The engine and dashboard can keep listening on
`127.0.0.1` on machine A.

### One-Off Tunnel

Run this on machine B:

```bash
ssh -N \
  -L 3000:127.0.0.1:3000 \
  -L 7333:127.0.0.1:7333 \
  user@machine-A
```

Then open `http://localhost:3000` on machine B. Browser calls to
`http://localhost:7333` are forwarded to machine A.

If you use the bundled dashboard from `dap start`, forward `7332`
instead of `3000`:

```bash
ssh -N \
  -L 7332:127.0.0.1:7332 \
  -L 7333:127.0.0.1:7333 \
  user@machine-A
```

Open `http://localhost:7332`.

### Persistent User Service

On Linux systems with systemd user services, create
`~/.config/systemd/user/dap-tunnel.service` on machine B:

```ini
[Unit]
Description=DAP SSH tunnel to machine A

[Service]
ExecStart=/usr/bin/ssh -N -o ServerAliveInterval=60 -o ExitOnForwardFailure=yes \
  -L 3000:127.0.0.1:3000 \
  -L 7333:127.0.0.1:7333 \
  user@machine-A
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now dap-tunnel
systemctl --user status dap-tunnel
```

## What Can Be Centralized

| Component | Can be centralized? | Notes |
|---|---|---|
| PostgreSQL database | Yes | Point `DAP_DATABASE_URL` at a managed or shared PostgreSQL server. |
| Dashboard | Yes | It is a web UI and can run separately from the engine if it can reach the engine URL. |
| Engine | Usually no | Keep it on the machine that has the agent CLIs, local repo checkout, and provider env vars. |
| Agent CLIs | No | CLI sessions are local/account-bound and must be installed and authenticated on the engine host. |
| Provider API keys | No | Set them in the engine process environment or encrypted instance env vars, because runtimes execute there. |

## Quick Checks

```bash
curl http://localhost:7333/health
curl http://localhost:7333/version
```

If the dashboard loads but API calls fail:

- verify `NEXT_PUBLIC_DAP_ENGINE_URL` points at the reachable engine URL;
- verify `DAP_CORS_ORIGINS` includes the exact dashboard origin for direct
  LAN access;
- verify the engine host firewall allows the relevant port;
- for SSH tunnels, verify the tunnel process is still running.

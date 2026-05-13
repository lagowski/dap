# dap-cli

The command-line launcher for [DAP](https://github.com/rafeekpro/dap)
(Deterministic Agent Pipeline). Installs as a single binary that
spawns both the engine and the Next.js dashboard, with all six
first-party packages (`dap-engine`, `dap-schemas`, `dap-runtimes`,
`dap-prompt-dsl`, `dap-cortex`, and this CLI) pulled in as
dependencies.

For most installations, `dap-cli` is the only package you install
directly — everything else arrives transitively.

## Installation

The recommended path is `pipx`, which installs the CLI in an
isolated virtualenv but exposes the `dap` binary on `PATH`:

```bash
pipx install dap-cli
```

Standard `pip --user` works too:

```bash
pip install --user dap-cli
```

After install:

```bash
dap --version              # → 0.3.0
dap --help                 # subcommand list
```

The wheel ships with the Next.js dashboard pre-built — `dap start`
spawns it inline when Node is available on `PATH`; otherwise it
falls back to engine-only mode (the CLI prints a hint).

## Commands

| Command | Purpose |
|---|---|
| `dap --version` | Print the installed version |
| `dap --help` | List subcommands |
| `dap init` | Initialise `./.dap/` and bootstrap the admin user |
| `dap start` | Spawn engine (127.0.0.1:7333) + dashboard (127.0.0.1:7332) |
| `dap stop` | Stop the running engine + dashboard cleanly |
| `dap status` | Show project state: admin bootstrap, engine PID/uptime, runtime adapter health |

### `dap init`

Creates `./.dap/` with `config.json`, `state.db` (SQLite), and the
admin bootstrap. Three credential modes:

```bash
# 1. Flag-driven (password lands in shell history — local dev).
dap init --admin-email=you@example.com --admin-password=hunter12345

# 2. Stdin (kubectl-style — automation-friendly).
echo 'hunter12345' | dap init --admin-email=you@example.com --admin-password-stdin

# 3. Interactive (getpass + confirmation, no echo).
dap init --admin-email=you@example.com
```

Empty password in interactive mode triggers a random 22-character
generation, printed exactly once. Re-running with `--force` is
idempotent — an existing email gets promoted to admin; the password
is rotated only when `--admin-password=...` is supplied explicitly.

`DAP_DB_PATH` is honored — useful when bootstrapping inside the
Docker container (where compose sets `DAP_DB_PATH=/data/state.db`).

### `dap start`

Starts both processes in parallel under the current shell. Both log
streams are prefixed (`[engine] …` / `[dashboard] …`); Ctrl-C tears
the whole stack down cleanly.

Flags:

| Flag | Default | Description |
|---|---|---|
| `--engine-host` | `127.0.0.1` | Engine bind address |
| `--engine-port` | `7333` | Engine port |
| `--dashboard-port` | `7332` | Dashboard port |
| `--no-dashboard` | off | Run engine-only even if Node is present |

### `dap status`

Reports the current state without modifying anything:

```text
✓ admin bootstrap: you@example.com (2026-05-13T10:00:00+00:00)
● engine: running  PID 12345 port 7333 uptime 3h12m

  Runtime adapters
  ├─ api-call   ✓ healthy
  ├─ bash       ✓ healthy
  ├─ claude-code  ✓ healthy
  ├─ codex      ✗ codex not found on PATH
  └─ ...
```

Useful in CI / health probes — exits non-zero if anything is
broken.

## Quick start

```bash
# 1. Install.
pipx install dap-cli

# 2. Initialise.
dap init --admin-email=you@example.com

# 3. Run.
dap start
# Browser: http://localhost:7332 (dashboard)
#          http://localhost:7333 (engine API + /docs)

# 4. Stop when done.
dap stop
```

See [`docs/quick-start.md`](https://github.com/rafeekpro/dap/blob/main/docs/quick-start.md)
for the full decision tree (laptop vs VPS, SQLite vs Postgres,
Docker vs no-Docker) and the per-path walkthroughs.

## Running from a source checkout

If you're developing DAP itself or want to run against an
unreleased commit:

```bash
git clone https://github.com/rafeekpro/dap.git
cd dap
./scripts/setup
./scripts/dev
```

The `scripts/dev` wrapper brings engine + dashboard up in one
terminal with hot reload on both sides.

## Compatibility

- Python 3.13+
- Node 22+ (optional — required only for the bundled dashboard;
  engine works without it)

## See also

- [DAP project README](https://github.com/rafeekpro/dap) — architecture overview.
- [`docs/quick-start.md`](https://github.com/rafeekpro/dap/blob/main/docs/quick-start.md) — install + first-pipeline tutorial.
- [`docs/self-hosting.md`](https://github.com/rafeekpro/dap/blob/main/docs/self-hosting.md) — production deployment + reverse-proxy hardening.
- [`docs/admin-guide.md`](https://github.com/rafeekpro/dap/blob/main/docs/admin-guide.md) — operator manual for `/admin/*`.

## License

See the [main repository](https://github.com/rafeekpro/dap) for licensing details.

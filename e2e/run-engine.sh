#!/usr/bin/env bash
# Launches dap-engine for Playwright's webServer. The ``exec`` replaces this
# shell with the engine process so Playwright's SIGTERM lands directly on
# the engine (no orphan-after-bash-dies situation as with scripts/dev).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env.local ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.local
  set +a
fi

if [[ ! -x .venv/bin/dap-engine ]]; then
  echo "[e2e] .venv/bin/dap-engine not found or not executable." >&2
  echo "[e2e] Run scripts/setup (or scripts/dev --install) to provision the venv." >&2
  exit 1
fi

exec .venv/bin/dap-engine

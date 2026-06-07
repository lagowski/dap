#!/usr/bin/env bash
#
# deploy-engine.sh — deploy the DAP engine on the self-hosted host while
# preserving cortex's out-of-lock dependencies.
#
# WHY THIS EXISTS
# ---------------
# dap-cortex is linked into the engine venv via a ``.pth`` that adds its
# *source* to sys.path — but NOT its dependencies. Cortex's runtime deps
# (pydantic-settings, pygithub, asyncpg, tiktoken, …) are not in the DAP
# lockfile, so ``uv sync --all-packages`` removes them on every run. The
# result: every cortex ``python-func`` node fails with ``ModuleNotFoundError``
# at the next run, even though nothing in cortex changed. (This is exactly how
# cortex regressed on 2026-06-06 — a deploy's ``uv sync`` wiped them.)
#
# This script runs the engine sync, then reinstalls those deps, verifies cortex
# still imports, and restarts the engine — so a routine deploy can't silently
# break cortex again.
#
# USAGE (on the engine host)
#   scripts/deploy-engine.sh
#
# ENV OVERRIDES
#   DAP_REPO            repo path (default: this script's repo root)
#   CORTEX_EXTRA_DEPS   space-separated deps to restore after sync (default:
#                       the known cortex out-of-lock set). Update this when
#                       cortex adds a dependency the DAP lock doesn't carry.
#   SKIP_FF=1           skip the git fast-forward (deploy the current HEAD)
#   ENGINE_HEALTH_URL   health endpoint (default: http://localhost:7333/health)
#
set -euo pipefail

REPO="${DAP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CORTEX_EXTRA_DEPS="${CORTEX_EXTRA_DEPS:-pydantic-settings pygithub asyncpg tiktoken}"
ENGINE_HEALTH_URL="${ENGINE_HEALTH_URL:-http://localhost:7333/health}"

export PATH="$HOME/.local/bin:$PATH"
cd "$REPO"

if [[ "${SKIP_FF:-0}" != "1" ]]; then
    echo "→ fast-forward develop"
    git fetch -q origin develop
    git merge --ff-only origin/develop
fi
echo "  HEAD: $(git log --oneline -1)"

echo "→ uv sync --all-packages (engine deps)"
uv sync --all-packages

echo "→ restore cortex out-of-lock deps: ${CORTEX_EXTRA_DEPS}"
# shellcheck disable=SC2086  # intentional word-splitting of the space-separated dep list
uv pip install ${CORTEX_EXTRA_DEPS}

echo "→ verify cortex imports"
if uv run python -c "import cortex" 2>/dev/null; then
    if uv run python -c "import cortex.nodes.mockup" 2>/dev/null; then
        echo "  ✓ cortex imports OK"
    else
        echo "  ✗ cortex is on the path but a node failed to import — deps still missing." >&2
        uv run python -c "import cortex.nodes.mockup" || true # surface the error
        exit 1
    fi
else
    echo "  · cortex not installed on this host — skipping cortex verification"
fi

echo "→ restart dap-engine"
systemctl --user restart dap-engine
sleep 5
systemctl --user is-active dap-engine
code="$(curl -fsS -o /dev/null -w "%{http_code}" "$ENGINE_HEALTH_URL" 2>/dev/null || echo "000")"
echo "  engine health: ${code}"

echo "✅ engine deploy complete"

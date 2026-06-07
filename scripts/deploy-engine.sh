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
# Safe to re-run: every step is idempotent.
#
# ENV OVERRIDES
#   DAP_REPO            repo path (default: this script's repo root)
#   DAP_VENV_PYTHON     engine venv interpreter (default: $DAP_REPO/.venv/bin/python)
#   CORTEX_EXTRA_DEPS   space-separated deps to restore after sync (default:
#                       the known cortex out-of-lock set). Update this when
#                       cortex adds a dependency the DAP lock doesn't carry.
#   CORTEX_TEST_IMPORT  module the verify step imports (default:
#                       cortex.config.settings). Set to a node for a deeper probe.
#   SKIP_FF=1           skip the git fast-forward (deploy current HEAD). Only the
#                       exact value "1" skips.
#   ENGINE_HEALTH_URL   health endpoint (default: http://localhost:7333/health)
#
set -euo pipefail

REPO="${DAP_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CORTEX_EXTRA_DEPS="${CORTEX_EXTRA_DEPS:-pydantic-settings pygithub asyncpg tiktoken}"
ENGINE_HEALTH_URL="${ENGINE_HEALTH_URL:-http://localhost:7333/health}"

# Every step below is idempotent (fetch / ff / sync / install / restart), so a
# re-run after an interruption is safe and simply converges to the desired
# state — there is no partial-state cleanup to do.
export PATH="$HOME/.local/bin:$PATH"
cd "$REPO"

# Pin every Python op to the engine's own venv so a stray $VIRTUAL_ENV or a
# second interpreter can't land the deps (or the verification) elsewhere.
VENV_PY="${DAP_VENV_PYTHON:-$REPO/.venv/bin/python}"
CORTEX_TEST_IMPORT="${CORTEX_TEST_IMPORT:-cortex.config.settings}"

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
uv pip install --python "$VENV_PY" ${CORTEX_EXTRA_DEPS}

# Verify the deps actually resolve in the engine venv. Default probe is
# ``cortex.config.settings`` — the module whose missing ``pydantic_settings``
# hard-breaks every cortex node; override CORTEX_TEST_IMPORT to probe deeper
# (e.g. a specific node) per install.
echo "→ verify cortex imports (${CORTEX_TEST_IMPORT})"
if "$VENV_PY" -c "import cortex" 2>/dev/null; then
    if "$VENV_PY" -c "import ${CORTEX_TEST_IMPORT}" 2>/dev/null; then
        echo "  ✓ cortex imports OK"
    else
        echo "  ✗ cortex is on the path but ${CORTEX_TEST_IMPORT} failed to import — deps still missing." >&2
        "$VENV_PY" -c "import ${CORTEX_TEST_IMPORT}" || true # surface the error
        exit 1
    fi
else
    echo "  · cortex not installed on this host — skipping cortex verification"
fi

echo "→ restart dap-engine"
systemctl --user restart dap-engine

# Gate on real readiness: poll health for ~30s so a slow start doesn't read as
# failure, and a refused/timed-out connection is a hard failure — never a
# false-positive "deployed".
echo "→ wait for engine health at ${ENGINE_HEALTH_URL}"
code="no-response"
for _ in $(seq 1 15); do
    code="$(curl -fsS -o /dev/null -w "%{http_code}" "$ENGINE_HEALTH_URL" 2>/dev/null || echo "no-response")"
    [[ "$code" == "200" ]] && break
    sleep 2
done
if [[ "$code" != "200" ]]; then
    echo "  ✗ engine did not become healthy (last: ${code})" >&2
    systemctl --user is-active dap-engine || true
    exit 1
fi
echo "  ✓ engine healthy (200)"

echo "✅ engine deploy complete"

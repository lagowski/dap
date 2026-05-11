#!/usr/bin/env bash
# Container entrypoint — spawn the engine + dashboard in parallel and
# exit when either dies. tini (PID 1) handles signal propagation so a
# ``docker stop`` lands as SIGTERM on both children.

set -euo pipefail

# Bail early if the operator forgot the JWT secret. The engine would
# also generate a per-process random and complain, but failing fast
# at container start surfaces the misconfig before any session lands.
if [[ -z "${DAP_AUTH_JWT_SECRET:-}" ]]; then
    echo >&2 "DAP_AUTH_JWT_SECRET is not set."
    echo >&2 "Set it via -e DAP_AUTH_JWT_SECRET=<random-32-bytes> or"
    echo >&2 "use 'openssl rand -hex 32' to mint one."
    exit 64
fi

ENGINE_PORT="${PORT_ENGINE:-7333}"
DASHBOARD_PORT="${PORT_DASHBOARD:-3000}"

# Engine — uvicorn comes via the dap-engine wheel. The module reads
# host/port from env vars (not CLI flags), and ``127.0.0.1`` is the
# safe default outside containers; we override to ``0.0.0.0`` so the
# docker port mapping actually works.
DAP_ENGINE_HOST="${DAP_ENGINE_HOST:-0.0.0.0}" \
DAP_ENGINE_PORT="${ENGINE_PORT}" \
    python -m dap_engine &
engine_pid=$!

# Dashboard — Next.js standalone bundles its own minimal node_modules
# and a ``server.js`` entrypoint at the bundle root.
PORT="${DASHBOARD_PORT}" \
DAP_ENGINE_URL="${DAP_ENGINE_URL:-http://127.0.0.1:${ENGINE_PORT}}" \
HOSTNAME=0.0.0.0 \
    node /opt/dap/dashboard/server.js &
dashboard_pid=$!

# Trap SIGTERM/SIGINT so a ``docker stop`` exits gracefully.
trap 'kill -TERM "${engine_pid}" "${dashboard_pid}" 2>/dev/null || true' \
    TERM INT

# ``wait -n`` returns when *any* child exits. We propagate its status
# so docker reports the real failure instead of always "exit 0".
wait -n "${engine_pid}" "${dashboard_pid}"
status=$?

# One died → take the other down so the container doesn't linger as a
# zombie half-service.
kill -TERM "${engine_pid}" "${dashboard_pid}" 2>/dev/null || true
wait "${engine_pid}" "${dashboard_pid}" 2>/dev/null || true
exit ${status}

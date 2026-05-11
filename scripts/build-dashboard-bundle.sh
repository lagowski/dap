#!/usr/bin/env bash
# Build the Next.js dashboard and stage the standalone bundle inside
# the dap-cli source tree so the wheel ships it (#302 sub-D2, #334).
#
# Layout produced (idempotent — wipes + rebuilds on every run):
#
#   apps/cli/src/dap_cli/_dashboard/
#       server.js                       # Next standalone entrypoint
#       package.json
#       node_modules/                   # Next's minimal trace
#       .next/                          # server + static chunks
#       BUNDLE_INFO.txt                 # git sha + timestamp
#
# At runtime, ``dap start`` looks at this directory and spawns
# ``node server.js`` if present. The directory is gitignored — built
# on demand by this script, by the Docker image build, or by the
# release pipeline (D3).

set -euo pipefail

# Resolve paths relative to the repo root regardless of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DASHBOARD_SRC="${REPO_ROOT}/apps/dashboard"
BUNDLE_DEST="${REPO_ROOT}/apps/cli/src/dap_cli/_dashboard"

echo "==> Building dashboard at ${DASHBOARD_SRC}"
cd "${DASHBOARD_SRC}"

# ``pnpm`` is expected on PATH. CI uses corepack; local dev uses the
# repo's own pnpm version pinned via packageManager.
if ! command -v pnpm >/dev/null 2>&1; then
    echo >&2 "error: pnpm not on PATH."
    echo >&2 "       Install via 'corepack enable && corepack prepare pnpm@10 --activate'"
    exit 1
fi

# Install deps + build. Frozen lockfile so the bundle is reproducible
# from CI / release pipeline.
pnpm install --frozen-lockfile
NEXT_TELEMETRY_DISABLED=1 pnpm build

echo "==> Staging bundle at ${BUNDLE_DEST}"
# Wipe the previous bundle so removed files don't linger. ``rm -rf``
# is fine here — every file under ``_dashboard/`` is gitignored
# except the ``.gitkeep`` placeholder, which we re-create below so
# the directory always exists in dev wheels too.
rm -rf "${BUNDLE_DEST}"
mkdir -p "${BUNDLE_DEST}"
touch "${BUNDLE_DEST}/.gitkeep"

# Next standalone output lands at ``.next/standalone`` with
# ``server.js`` at the root + a self-contained ``node_modules``. The
# static assets (``.next/static``) and the public dir (if any) must
# sit next to ``server.js`` so the runtime finds them — same shape
# the Dockerfile assembles.
cp -R "${DASHBOARD_SRC}/.next/standalone/." "${BUNDLE_DEST}/"
cp -R "${DASHBOARD_SRC}/.next/static" "${BUNDLE_DEST}/.next/static"

# ``public/`` doesn't exist in this repo today, but ship it when it
# does so the bundle stays correct without script edits later.
if [[ -d "${DASHBOARD_SRC}/public" ]]; then
    cp -R "${DASHBOARD_SRC}/public" "${BUNDLE_DEST}/public"
fi

# Drop a marker file so the operator can see at a glance whether the
# wheel they installed actually carries the bundle and which commit
# it came from.
git_sha="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
cat > "${BUNDLE_DEST}/BUNDLE_INFO.txt" <<EOF
DAP dashboard bundle
Built at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Git SHA:  ${git_sha}
EOF

echo "==> Done. Bundle at ${BUNDLE_DEST}"
echo "    Size: $(du -sh "${BUNDLE_DEST}" | awk '{print $1}')"

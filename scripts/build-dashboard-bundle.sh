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
# repo's own pnpm version pinned via packageManager. The dashboard's
# pnpm-lock.yaml is v9 format — pnpm 9 and 10 both read it, so we
# don't pin a major here.
if ! command -v pnpm >/dev/null 2>&1; then
    echo >&2 "error: pnpm not on PATH."
    echo >&2 "       Install via 'corepack enable && corepack prepare pnpm@latest --activate'"
    exit 1
fi

# Install deps + build. Frozen lockfile so the bundle is reproducible
# from CI / release pipeline.
pnpm install --frozen-lockfile
NEXT_TELEMETRY_DISABLED=1 pnpm build

echo "==> Staging bundle at ${BUNDLE_DEST}"
# Stash the .gitkeep placeholder so its committed content isn't lost
# by the wipe. Every other file under ``_dashboard/`` is gitignored
# (rebuilt fresh each run); the .gitkeep is committed so dev wheels
# built without running this script still have the directory on disk
# for hatchling's force-include.
GITKEEP_BACKUP=""
if [[ -f "${BUNDLE_DEST}/.gitkeep" ]]; then
    GITKEEP_BACKUP="$(mktemp)"
    cp "${BUNDLE_DEST}/.gitkeep" "${GITKEEP_BACKUP}"
fi

rm -rf "${BUNDLE_DEST}"
mkdir -p "${BUNDLE_DEST}"

# Restore the placeholder. If the script was somehow run from a
# state with no .gitkeep (clean checkout where git removed the file
# between the read and write — unlikely but worth guarding), fall
# back to a tiny synthesised note so the file still ships in the
# wheel.
if [[ -n "${GITKEEP_BACKUP}" ]]; then
    mv "${GITKEEP_BACKUP}" "${BUNDLE_DEST}/.gitkeep"
else
    cat > "${BUNDLE_DEST}/.gitkeep" <<'EOF'
# Placeholder so the directory exists for hatchling's force-include.
# The dashboard bundle is staged on top of this by
# scripts/build-dashboard-bundle.sh.
EOF
fi

# Next standalone output used to land directly at ``.next/standalone``.
# Newer Next/Turbopack builds can preserve the workspace-relative path
# instead (``.next/standalone/apps/dashboard``). Stage the directory
# that actually contains ``server.js`` so the wheel still ships
# ``dap_cli/_dashboard/server.js``.
STANDALONE_ROOT="${DASHBOARD_SRC}/.next/standalone"
if [[ ! -f "${STANDALONE_ROOT}/server.js" && -f "${STANDALONE_ROOT}/apps/dashboard/server.js" ]]; then
    STANDALONE_ROOT="${STANDALONE_ROOT}/apps/dashboard"
fi

if [[ ! -f "${STANDALONE_ROOT}/server.js" ]]; then
    echo >&2 "error: Next standalone server.js not found under ${DASHBOARD_SRC}/.next/standalone"
    exit 1
fi

cp -R "${STANDALONE_ROOT}/." "${BUNDLE_DEST}/"
mkdir -p "${BUNDLE_DEST}/.next"
cp -R "${DASHBOARD_SRC}/.next/static" "${BUNDLE_DEST}/.next/static"

# Next 16 + pnpm can leave dangling hoist links in the traced
# standalone ``node_modules``. They are unusable at runtime and
# hatchling refuses to package them because ``os.stat`` fails.
if [[ -d "${BUNDLE_DEST}/node_modules" ]]; then
    while IFS= read -r broken_link; do
        rm -f "${broken_link}"
    done < <(find "${BUNDLE_DEST}/node_modules" -type l -exec test ! -e {} \; -print)
fi

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

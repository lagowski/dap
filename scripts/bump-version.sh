#!/usr/bin/env bash
# Bump every workspace package + the CLI's __version__ to the same value.
#
# Usage:
#   ./scripts/bump-version.sh 0.4.0           # full version
#   ./scripts/bump-version.sh 0.4.0-rc1       # pre-release
#
# Idempotent: re-running with the same arg is a no-op (sed matches the
# current value and rewrites it identically). Intended workflow is part
# of the release checklist in ``docs/release.md`` — run this, commit,
# open the PR, merge to main, then tag.

set -euo pipefail

NEW="${1:?usage: $0 <new-version>}"

# Loose semver check: 0.4.0, 1.2.3-rc1, etc. Reject obvious typos
# without trying to enforce the full spec — pypi will catch genuine
# violations on publish.
if ! [[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "ERROR: '$NEW' doesn't look like a semver version (x.y.z or x.y.z-rcN)." >&2
    exit 64
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Detect every pyproject.toml under the workspace. Excludes anything
# under .venv / node_modules / dist / build so we don't accidentally
# rewrite vendored dependencies.
PYPROJECTS=$(
    find . \
        -name pyproject.toml \
        -not -path "./.venv/*" \
        -not -path "./node_modules/*" \
        -not -path "*/_dashboard/*" \
        -not -path "*/dist/*" \
        -not -path "*/build/*" \
    | sort
)

echo "Bumping workspace to $NEW..."
for f in $PYPROJECTS; do
    if ! grep -q '^version = "' "$f"; then
        echo "  skip (no version field): $f"
        continue
    fi
    OLD=$(grep '^version = "' "$f" | head -1 | sed 's/^version = "\(.*\)"$/\1/')
    if [[ "$OLD" == "$NEW" ]]; then
        echo "  unchanged ($NEW): $f"
        continue
    fi
    # macOS / BSD sed needs an explicit empty backup arg.
    sed -i.bak 's/^version = "[^"]*"$/version = "'"$NEW"'"/' "$f"
    rm "${f}.bak"
    echo "  $OLD -> $NEW: $f"
done

# Sync every first-party ``__version__`` constant we ship.
# Hatchling can derive these from the pyproject at build time, but the
# source files are what ``import <pkg>`` returns during development, so
# they need to stay aligned (otherwise ``dap --version`` lies and the
# wheel + the editable install drift apart).
#
# Each entry is a path; discover more with
# ``grep -rln '^__version__ =' apps/*/src packages/*/src``.
VERSION_FILES=(
    "apps/cli/src/dap_cli/__init__.py"
)
for f in "${VERSION_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "  skip (missing): $f"
        continue
    fi
    if ! grep -q '^__version__ = "' "$f"; then
        echo "  WARN: no __version__ line in $f — manual edit needed" >&2
        continue
    fi
    OLD=$(grep '^__version__ = "' "$f" | head -1 | sed 's/^__version__ = "\(.*\)"$/\1/')
    if [[ "$OLD" == "$NEW" ]]; then
        echo "  unchanged ($NEW): $f"
        continue
    fi
    sed -i.bak 's/^__version__ = "[^"]*"$/__version__ = "'"$NEW"'"/' "$f"
    rm "${f}.bak"
    echo "  $OLD -> $NEW: $f"
done

# uv.lock embeds the workspace member versions; refresh it so the
# lock matches the bumped pyprojects before we commit.
if command -v uv >/dev/null 2>&1; then
    echo "Refreshing uv.lock..."
    uv lock --quiet
else
    echo "WARN: uv not on PATH; remember to run 'uv lock' before committing." >&2
fi

echo "Done. Next steps:"
echo "  git diff       # review the version bumps"
echo "  git add -A && git commit -m 'release: bump workspace to $NEW'"
echo "  # Open PR to main, merge, tag v$NEW, push tag."

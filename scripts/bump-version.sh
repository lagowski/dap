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

# The CLI also exposes ``__version__`` as a Python-level constant
# (read by ``dap --version``). Hatchling can derive this from the
# pyproject at build time, but the source file is what
# ``import dap_cli`` returns during development, so keep them aligned.
CLI_INIT="apps/cli/src/dap_cli/__init__.py"
if [[ -f "$CLI_INIT" ]]; then
    OLD=$(grep '^__version__ = "' "$CLI_INIT" | head -1 | sed 's/^__version__ = "\(.*\)"$/\1/')
    if [[ "$OLD" != "$NEW" ]]; then
        sed -i.bak 's/^__version__ = "[^"]*"$/__version__ = "'"$NEW"'"/' "$CLI_INIT"
        rm "${CLI_INIT}.bak"
        echo "  $OLD -> $NEW: $CLI_INIT"
    else
        echo "  unchanged ($NEW): $CLI_INIT"
    fi
fi

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

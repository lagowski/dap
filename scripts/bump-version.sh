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

MAJOR="${NEW%%.*}"
REST="${NEW#*.}"
MINOR="${REST%%.*}"
NEXT_MINOR=$((MINOR + 1))
UPPER_BOUND="${MAJOR}.${NEXT_MINOR}"
if [[ "$NEW" == *-* ]]; then
    LOWER_BOUND="$NEW"
else
    LOWER_BOUND="${MAJOR}.${MINOR}"
fi
FIRST_PARTY_SPEC=">=${LOWER_BOUND},<${UPPER_BOUND}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Bump the root workspace metadata plus the five packages published by
# release.yml. Internal tooling packages can keep their own cadence.
PYPROJECTS=(
    "pyproject.toml"
    "packages/types/pyproject.toml"
    "packages/runtimes/pyproject.toml"
    "packages/prompt-dsl/pyproject.toml"
    "apps/engine/pyproject.toml"
    "apps/cli/pyproject.toml"
)

echo "Bumping workspace to $NEW..."
for f in "${PYPROJECTS[@]}"; do
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
    "apps/engine/src/dap_engine/version.py"
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

# Keep PyPI dependency metadata aligned with the compatibility matrix.
# Workspace sources hide these specifiers in local development, but
# published wheels rely on them to prevent mixed minor versions such as
# dap-engine 0.4.x with dap-schemas 0.3.x.
FIRST_PARTY_DEPS=(
    "packages/runtimes/pyproject.toml:dap-schemas"
    "apps/engine/pyproject.toml:dap-schemas"
    "apps/engine/pyproject.toml:dap-runtimes[all]"
    "apps/engine/pyproject.toml:dap-prompt-dsl"
    "apps/cli/pyproject.toml:dap-engine"
)
echo "Aligning first-party dependency bounds to ${FIRST_PARTY_SPEC}..."
for entry in "${FIRST_PARTY_DEPS[@]}"; do
    file="${entry%%:*}"
    dep="${entry#*:}"
    if [[ ! -f "$file" ]]; then
        echo "  skip (missing): $file"
        continue
    fi
    if ! grep -Fq "\"${dep}" "$file"; then
        echo "  WARN: dependency '${dep}' not found in $file — manual edit needed" >&2
        continue
    fi
    dep_regex=$(printf '%s' "$dep" | sed 's/[][\/.^$*+?{}()|]/\\&/g')
    sed -E -i.bak 's#"'${dep_regex}'([>=<][^"]*)?"#"'${dep}${FIRST_PARTY_SPEC}'"#' "$file"
    rm "${file}.bak"
    echo "  ${file}: ${dep}${FIRST_PARTY_SPEC}"
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

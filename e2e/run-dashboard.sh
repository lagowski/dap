#!/usr/bin/env bash
# Launches the Next.js dashboard for Playwright's webServer. ``exec`` replaces
# this shell with next-dev so SIGTERM from Playwright propagates directly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/apps/dashboard"

exec ./node_modules/.bin/next dev

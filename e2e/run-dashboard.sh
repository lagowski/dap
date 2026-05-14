#!/usr/bin/env bash
# Launches the Next.js dashboard for Playwright's webServer. ``exec`` replaces
# this shell with next-dev so SIGTERM from Playwright propagates directly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/apps/dashboard"

# Pin port + host so next-dev fails fast if 3000 is already taken instead of
# silently falling back to 3001 (Playwright's webServer.url is hard-coded to
# 127.0.0.1:3000 and would hang waiting for that exact URL).
exec ./node_modules/.bin/next dev -p 3000 -H 127.0.0.1

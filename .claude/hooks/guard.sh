#!/usr/bin/env bash
# .claude/hooks/guard.sh — PreToolUse guard for a PUBLIC repo.
# Keeps real .env files out of the session transcript and out of git, and keeps the
# lockfiles (uv.lock, pnpm-lock.yaml) changing only through `uv lock` / `pnpm install`.
in=$(cat); tool=$(jq -r '.tool_name // ""' <<<"$in")
if [ "$tool" = "Bash" ]; then
  cmd=$(jq -r '.tool_input.command // ""' <<<"$in")
  if grep -Eq '(^|[;&|[:space:]])git[[:space:]]+(add|commit)' <<<"$cmd"; then
    cd "$CLAUDE_PROJECT_DIR" || exit 0
    bad=$( { git diff --cached --name-only; git ls-files --others --exclude-standard; } | grep -E '(^|/)\.env(\.[^/]*)?$' | grep -v '\.example$')
    [ -n "$bad" ] && { echo "BLOCKED: env file would be committed to a PUBLIC repo: $bad" >&2; exit 2; }
  fi
  exit 0
fi
f=$(jq -r '.tool_input.file_path // ""' <<<"$in")
case "$f" in
  *.env.example|*.env.local.example) exit 0 ;;
  .env|.env.*|*/.env|*/.env.*) echo "BLOCKED: $f holds real secrets (public repo). Use .env.example for shape." >&2; exit 2 ;;
esac
if [ "$tool" != "Read" ]; then
  case "$f" in uv.lock|*/uv.lock|pnpm-lock.yaml|*/pnpm-lock.yaml|package-lock.json|*/package-lock.json)
    echo "BLOCKED: regenerate $f with uv lock / pnpm install (pinned pnpm 9.15.9), don't hand-edit." >&2; exit 2;; esac
fi
exit 0

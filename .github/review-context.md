# Project Context Brief — dap (lagowski/dap)

> Council reviewer's repo-truth source. Check name `gemini-review`; marker family `ai-*` (frozen — branch protection + cache idempotency depend on both; do not rename).

## Stack summary
Python 3.13 monorepo. Backend `apps/engine`: FastAPI / SQLAlchemy / LangGraph. Frontend `apps/dashboard`: Next.js 14 App Router / TypeScript 5 / shadcn/ui / Tailwind. Also `apps/cli`. Shared `packages/{types,runtimes,prompt-dsl,schemas,code-review-council}`. Tests: pytest smoke (`tests/smoke`) + Playwright e2e (`tests/e2e`). The AI gate itself is the in-tree `packages/code-review-council` package, driven by `.github/scripts/gemini_review.py` on every PR to `develop`/`main` (self-hosted runner). Provider routes via OpenRouter (preferred, `OPENROUTER_MODEL`-switchable) with Gemini free-tier fallback.

## Reuse hot-spots
- admin authorization → the `require_admin_user` dependency (do NOT hand-roll `if not user.is_superuser`)
- audit logging → `record_audit_event`, typed with the `AuditEventType` Literal (string literals are a smell)
- smoke-test fixtures → `client` / `authed_client` in `tests/smoke/conftest.py` (don't re-roll the fixture locally in new tests)

## IN SCOPE — flag these
- auth / authorization / anti-enumeration
- data correctness (SQL, race conditions, transaction boundaries)
- real error handling (uncaught throws, retry semantics)
- type safety (no `Any` in route signatures, no unsafe casts)
- real performance (N+1, blocking renders, memory leaks)
- real a11y for keyboard / screen reader / focus
- tests that genuinely cover new behaviour

Anti-enumeration convention: admin endpoints return **404 (not 403)** for non-admins. A diff that "fixes" a 404 into a 403 on an admin route is a regression, not an improvement.

## OUT OF SCOPE — DO NOT flag
- i18n / internationalization / locale / RTL / `dir` attribute
- CSP nonce / `unsafe-inline`
- AAA accessibility (AA-level focus/keyboard/SR is in scope; AAA is not)
- `type="button"` on shadcn primitives (Button, Dialog, etc.) — framework defaults are fine
- defensive nits on framework defaults / missing docstrings on internal helpers

## Deployment security context
Private repo (org `lagowski`), internal contributors. Fork PRs get a read-only `GITHUB_TOKEN`, so posting fails and the gate blocks the merge — the desired outcome for unmaintained forks, and the reason the prompt-injection surface is essentially empty. Defense-in-depth already present: PR-author `title`/`body` pass through `safe_title`/`safe_body`; all agent-supplied finding text is `strip_marker_tags`'d before embedding (anti marker-poisoning); the diff is marked as data, not instructions. The verdict is advisory-leaning — `--request-changes` falls back to `--comment` on self-authored PRs, and branch protection gates on the `gemini-review` check_run. Do NOT REQUEST_CHANGES for the absence of per-run-cred isolation alone; that's operator-side work in the LAGOVALUT vault. Concrete diff-introduced regressions in the sanitize / marker-strip / check-run layers ARE in scope.

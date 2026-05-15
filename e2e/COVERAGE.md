# E2E coverage — scope rationale

A decision log for the Playwright suite in this directory. Records which flows
we deliberately **chose not to automate** and why. This is not a TODO list —
treat it as architectural guidance so a future maintainer doesn't waste a
sprint re-litigating the same trade-offs.

If a flow listed here later becomes cheap to test (see [When to revisit](#when-to-revisit)),
move it out of this file and add a spec.

## What is covered

The suite has five Playwright projects (see `playwright.config.ts`):

| Project | Purpose |
| --- | --- |
| `setup` | Registers the regular fixture user once per run, persists session cookie. |
| `admin-setup` | Registers the admin fixture user and shells out to `sqlite3` to flip `is_superuser=1`. |
| `auth` | Signup / login / form-validation specs against an unauthenticated origin. |
| `app` | All authenticated specs as the regular fixture user (agents / pipelines / projects / runs / account / settings). |
| `admin` | Admin-only specs (users panel, api-tokens, audit log, admin settings). |

CRUD-level coverage is reasonably complete for **agents**, **pipelines**
(except the visual designer — see below), **projects**, and the **admin users
panel** (including destructive promote / demote / suspend / soft-delete and
self-action guards). Runs cover the full lifecycle (trigger / pause / resume
/ abort) and the gate approve / abort flow via `interrupt_before`.

## Out-of-scope flows

### OAuth callback (GitHub / Google)

The OAuth flow leaves the dashboard origin and bounces through a real
provider. Mocking it for e2e is high-effort (intercept the provider's
authorize endpoint, fake a callback) for low value — the dashboard's role is
just "redirect, then handle the callback's cookie."

- **What we do cover:** link `href` correctness on `/login` and `/signup`
  OAuth buttons (verifies the proxy + provider config didn't break the
  start URL).
- **What we don't cover:** end-to-end "user clicks GitHub → lands logged in".
- **Better tested via:** manual smoke on staging, plus pytest unit tests
  against the dashboard's `/api/auth/oauth/callback` handler.

### Workspace init / sync

`/projects/<id>` has "Initialize Workspace" and "Sync" buttons that do real
`git clone` / `git pull` against the project's `repo_url`. Real git
operations in CI are slow (cloning a repo per run) and flaky (network,
SSH keys, branch protection rules).

- **Better tested via:** pytest smoke against the engine's workspace
  endpoints with a local `file://` repo fixture.

### Agent dry-run test panel

The Test panel on agents calls the engine's `/agents/dry-run` endpoint,
which invokes the chosen runtime (`api-call`, `claude-code`, etc.) — each
test would spend real LLM tokens unless the test pins `runtime_id=bash`
**and** embeds a bash command. Even with bash, the panel has compare-mode
(variant A/B), promote-B-to-form, and schema-validation result rendering —
each is a feature that drifts independently.

- **Better tested via:** existing pytest smoke
  (`tests/smoke/test_agent_dry_run.py`) which exercises the engine
  without the browser-overhead overhead.

### Forgot-password / reset-password flow

The forgot-password endpoint is intentionally anti-enumeration: the success
card is shown whether the email exists or not. From the e2e perspective the
only assertion possible is "card shown" — which has near-zero regression
value (single template render).

Reset-password requires a real token from the engine. Today the token
surfaces via WARN-level log when `DAP_AUTH_LOG_RESET_TOKENS=1`. Parsing
engine logs in Playwright to extract that token is brittle and couples
the e2e suite to log format.

- **Better tested via:** pytest smoke against `/auth/forgot-password` and
  `/auth/reset-password` with direct token inspection.

### Project workflow trigger with `cortex` kind

The "Trigger" button for the `cortex` workflow opens an issue-picker modal
that fetches the project's GitHub repo issues. Requires:

- a real `repo_url`
- workspace initialized (`git clone`)
- a valid `GH_TOKEN_*` in environment
- GitHub API reachable + rate-limit available

Each of those is its own flake source. Triggering a non-`cortex` workflow
(no issue picker) is in scope and can be folded into a future runs spec.

- **Better tested via:** pytest smoke against the engine's run-trigger
  endpoint with a stubbed GitHub client.

### Pipeline visual designer (React Flow drag-drop)

Decided during round-1 (issue #400). Documented again here for
completeness — full graph manipulation via Playwright is brittle and
slow; the template-import path covers the create flow at the
user-meaningful level.

## When to revisit

- The engine ships a built-in OAuth provider mock for tests.
- LLM dry-run cost becomes near-zero (cached responses, local model
  fixtures).
- Forgot-password adds an in-DB `pending_resets` table that lets a
  test admin fetch the token directly.

## History

Derived from issue #425, opened during the round-2 e2e coverage audit
(#426). Move bullets out of this file as conditions in
[When to revisit](#when-to-revisit) become true.

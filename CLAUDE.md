# dap: self-hostable engine, dashboard and CLI for deterministic agent pipelines
Verified against `develop@883a178` on 2026-11-19 (open PRs: 0). Public repo: yes.

DAP (Deterministic Agent Pipeline): a pipeline is a versioned DAG of agents; each agent renders a Jinja → XML
prompt and sends it to a runtime adapter, and LangGraph decides what runs next (never the model). MIT. The latest
tag is `v0.3.0`; `develop` is far ahead of it and is what the maintainers run on a private instance. Merging to
`develop` deploys nothing. Engine: FastAPI on `127.0.0.1:7333`, SQLite under `.dap/` by default, PostgreSQL when
`DAP_DATABASE_URL` is set (Alembic migrations run at engine startup). Dashboard: Next.js on port 3000, its API
types generated from the engine's OpenAPI. `packages/types` is published as `dap-schemas` (import `dap_types`).
**This repo is public:** no hosts, addresses, accounts, credentials or private tooling in anything committed
here, in issues or in PR text.

## Commands

```bash
./scripts/setup                 # checks Python 3.13, uv, Node 22, pnpm 9; .env.local; deps; pre-push hook
./scripts/dev                   # engine :7333 + dashboard :3000 in one terminal
uv sync --all-packages          # Python deps only (what scripts/setup runs)

uv run pytest -q tests/test_redaction.py                          # run one test file
uv run pytest -q -m "not integration" --ignore=tests/standalone   # fast gate
uv run pytest -q -m integration tests/smoke                       # smoke integration
uv run ruff check apps packages tests
uv run ruff format --check apps packages tests
uv run mypy apps packages tests

cd apps/dashboard && pnpm check:api && pnpm typecheck && pnpm lint && pnpm test && pnpm build
pnpm --dir apps/dashboard gen:api                # regenerate src/lib/api/types.gen.ts after an engine schema change
cd e2e && npx playwright test --project=app      # starts its own engine + Next dev server; ports 3000 and 7333 free
uv run python scripts/generate-assistant-corpus.py   # after editing docs/assistant-corpus.md (CI fails on drift)
```

Deploy: tagged releases (PyPI, GHCR, GitHub Release) go through `.github/workflows/release.yml`
(`docs/release.md`). The private instance is updated by hand: `.claude/rules/operator-context.md`.

## Owner rules

- **AI review gate, public-repo variant** (owner 2026-11-04): `.github/workflows/gemini-review.yml` reviews PRs
  into `develop`/`main` and posts the `gemini-review` check. The file is rendered from the maintainers' gate canon
  (named in its header): do not hand-edit it; the `chore: sync AI review gate` PRs refresh it. A fork PR is
  reviewed only after a maintainer adds the `safe-to-review` label.
- **Who may approve** (owner 2026-10-15): the PR's creator and every account that committed to it can't approve
  it; any other maintainer account can. No single account is a mandatory approver. So keep **one identity per
  PR**: create, commit and push follow-ups as the same account, and never push to a PR as an account that should
  approve it (that turns an approver into a committer). Dependabot auto-approve reviews as `github-actions[bot]`,
  which is never a committer.
- **GitHub API polling** (owner 2026-10-15): REST core is 5000 calls/hour per user, shared by every token and
  session of that user; on 2026-10-15 two maintainer accounts were exhausted for most of the day. No REST poller
  faster than every 120 s. Prefer GraphQL (`gh pr view --json statusCheckRollup,reviews,mergeStateStatus`) over
  `gh run list/view`, `gh api repos/...` and REST `gh pr checks`; for repeated reads `gh api --cache 60s`. Every
  waiter has an iteration cap and stops on a 403 / "rate limit exceeded" (report it, don't retry). Kill a
  background loop when the PR merges or the task ends (a 23.7 h orphan loop was found). On the maintainers'
  machines `gh` reads go through a read shim under another account; writes stay on yours. Don't work around it.
- **A release to the private instance** needs the owner's OK each time, only when no engine runs are active, and
  with a database backup first (owner 2026-10-14). Procedure: `.claude/rules/operator-context.md`.
- **The last pusher can't approve** (owner 2026-10-08): the account that pushed the latest commit can't approve
  it; GitHub enforces it (`require_last_push_approval` in the default-branch ruleset).
- **An approval is an approval, whichever account gives it** (owner 2026-10-02): an APPROVED review from any of
  the three maintainer accounts (the owners listed in `.github/CODEOWNERS`) counts. Don't hold back a merge
  because the approval came from a different account than you expected: not the PR author's, not this session's,
  not the "usual" owner's. An author can't approve their own PR (GitHub enforces it).

## Traps

- A dispatched epic produced seven issues in one 3,074-line PR, three of them in no sprint -> the issue had no
  labels -> every issue carries `type:`, `area:` and `size:` at creation, also when filed with `gh issue create`
  or the REST API (neither picks up issue forms); `needs-split`, `not-code` and `tracking` mean do not dispatch.
- A hand-merge of an item PR into a collector branch skipped the verification lane and raced the sprint
  automation (2026-10-11) -> item PRs into `feature/*` / `sprint/*` are merged by that automation -> don't
  hand-merge them; a "Merge it" for such a PR means "the approval is in place". If it has stalled for hours,
  report it and offer to merge. The collector → `develop` promotion PR is the owner's to review.
- Every cortex `python-func` node fails with `ModuleNotFoundError` on the instance -> a plain
  `uv sync --all-packages` removed cortex's out-of-lock dependencies from the engine venv (2026-06-06) -> on the
  instance use `uv sync --all-packages --frozen --inexact`, or `scripts/deploy-engine.sh`, which restores them.
- A Dependabot PR fails `Python deps CVE scan` on packages it doesn't touch -> the vulnerable package is already
  in develop's `uv.lock` -> fix it with a separate lock-bump PR (`uv lock --upgrade-package <name>`), then rebase
  the Dependabot PRs; if `@dependabot rebase` is ignored, `@dependabot recreate` works.
- `Dashboard (typecheck + build)` fails after an engine request/response schema change -> the generated API
  types drifted -> `pnpm --dir apps/dashboard gen:api` and commit `apps/dashboard/src/lib/api/types.gen.ts`.
- An edit of `.env`, `uv.lock`, `pnpm-lock.yaml` or `package-lock.json` is blocked -> `.claude/hooks/guard.sh`
  keeps real env files out of a public repo and lockfiles machine-generated -> regenerate with `uv lock` /
  `pnpm install` (pnpm pinned 9.15.9; Dependabot drops the pnpm overrides with any other version, #905).
- `git stash` / `pkill` are refused -> `.claude/hooks/guard-shared-refs.py` (shared copy, don't hand-edit):
  the stash is shared by every worktree and `pkill` matches other sessions' processes.
- Dashboard tests fail on a host with Node 20 -> the dashboard needs Node 22 (`.nvmrc`) -> run vitest with
  `npx -y node@22 node_modules/vitest/vitest.mjs run` and pnpm with `npx -y pnpm@9.15.9`.
- `gh api rate_limit` can misreport the REST quota -> read the `x-ratelimit-*` headers of a real request.
- A change under `.github/` doesn't stick or is flagged as drift -> `.github/` is code-owned and partly rendered
  from the gate canon -> change the gate in canon, not here.
- A red `Python smoke integration` shard is not a required check but blocks a merge all the same -> fix it.
- Instance traps (unit restarts, `pg_dump` version): `.claude/rules/operator-context.md`.

## Workflow

- Branch from `develop` (`issue/<n>-<slug>`, `fix/…`, `chore/…`, `docs/…`), Conventional Commits, PR into
  `develop`. `develop → main` PRs are releases (`.github/workflows/enforce-main-source.yml` refuses any other
  source). Never push to `main` or `develop` (`.githooks/pre-push` and branch protection both block it).
- Required checks on `develop`: `Python (ruff + pytest)`, `Dashboard (typecheck + build)`, `Playwright smoke`,
  `Python deps CVE scan`, `Playwright full`. On `main` the first four plus `Verify PR source is develop` and
  `gemini-review` (so the AI gate is required for a release PR, not for a PR into `develop`).
- Path classifiers skip jobs a PR doesn't touch; `Playwright full` runs on PRs that touch the dashboard, the
  engine or the specs, and nightly. PR checks also run on item PRs into `feature/**` and `sprint/**`.
- Also running: three `Python smoke integration` shards, CodeQL, `build` / `bundle-wheel` packaging, a daily CVE
  scan, Dependabot auto-merge (patch/minor squash-merged after the bot's
  approval; majors wait for a human).
- Reviews: one approving review on `develop` and `main`; a push dismisses stale approvals; admins are not
  exempt. The default-branch ruleset adds code-owner review (`.github/CODEOWNERS`: `.github/workflows/`,
  `.github/scripts/`, `.github/review-context.md`) and the last-pusher rule.
- A session may merge a PR itself once all hold: an APPROVED review from a maintainer account is on the PR's
  **current head commit**; every required check is green and GitHub reports the PR mergeable (the ruleset
  decides whether the approval satisfies a code-owner rule); no PR comment asks to hold ("DO NOT MERGE").
  Merge with `gh pr merge <n> --match-head-commit <approved-sha>`, squash like the rest of `develop`'s history.
  Never use `--admin` to get past a missing approval or a missing check.
- Labels: `type:` = `feat` | `bug` | `chore` | `infra` | `spike` | `docs`; `area:` = the part of the tree (the
  issue forms list the set); `size:` = `S` (one sitting) | `M` (a day or so) | `L` (more than a day: consider
  `tracking` and splitting). An epic also carries `tracking` and is never dispatched to a worker.
- Secrets: no credentials in code, commits, comments, issues, PR bodies or LLM prompts; the four
  secrets-handling rules are in `.claude/rules/operator-context.md`.
- Without access to the maintainers' private vault you may read code, run tests locally, open PRs and comment
  on issues; nothing against the production instance (details in the same file).
- After a major bump of a runtime dependency (a provider SDK), smoke-test that adapter on the instance at its
  next release; after a dashboard major, load a form page while logged in.

## Pointers

- `.claude/rules/operator-context.md`: operator context (vault pointer, authorized actions, infrastructure
  facts, the four secrets-handling rules), releasing to the private instance, instance traps. Read it before
  anything destructive or anything that touches the instance.
- `CONTRIBUTING.md` (branching, commits, DoD, hooks), `docs/testing.md` (markers, gates), `docs/release.md`
  (tagged releases), `docs/dependency-updates.md` (Dependabot), `docs/architecture.md` (packages, state, run
  lifecycle), `docs/runtimes.md` (the 9 adapters), `DEPLOYMENT.md` and `docs/self-hosting.md` (installs).
- `e2e/COVERAGE.md` (what the Playwright suite covers), `CHANGELOG.md` (what changed), @README.md.

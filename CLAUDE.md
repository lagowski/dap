# dap — context for Claude

Last full refresh: 2026-11-18. README.md says what DAP is and how to install, run and test it; `docs/` has the
reference. This file has the rules, the operator context and the traps.

## Purpose

DAP (Deterministic Agent Pipeline) is a self-hostable, multi-user system for building and running deterministic
agent pipelines: a pipeline is a versioned DAG of agents, each agent renders a Jinja → XML prompt and sends it to
a runtime adapter, and LangGraph decides what runs next (never the model). The repo is **public** (MIT); the
latest tag is `v0.3.0` and `develop` is far ahead of it. The maintainers run a private instance of `develop`.

## Architecture (what lives where)

- `apps/engine`: FastAPI + LangGraph + SQLAlchemy + Alembic (`apps/engine/src/dap_engine/alembic/versions`,
  latest `0009_interaction_log`). Listens on `127.0.0.1:7333`. SQLite at `.dap/state.db` by default, PostgreSQL
  when `DAP_DATABASE_URL` is set; migrations run at engine startup.
- `apps/dashboard`: Next.js 16 + React 19 (pnpm, `apps/dashboard/pnpm-lock.yaml`), port 3000. Its API types are
  generated from the engine's OpenAPI (`pnpm gen:api`, checked in CI by `pnpm check:api`).
- `apps/cli`: Typer CLI `dap` (`init`, `start`, `stop`, `status`, `project run/approve/reject/state`).
- `packages/`: `types` (published as `dap-schemas`, import `dap_types`), `runtimes` (9 adapters), `prompt-dsl`,
  `database`, `code-review-council` (internal, not published, not run by any workflow today).
- `tests/smoke` (pytest, markers `unit` / `integration`), `tests/standalone` (wheel + bundled dashboard),
  `e2e/` (Playwright, npm, `e2e/package-lock.json`).
- `docs/assistant-corpus.md` is the source for the generated
  `apps/engine/src/dap_engine/assistant/docs_corpus.py` (`scripts/generate-assistant-corpus.py`; CI fails on drift).

## Run / test

```bash
./scripts/setup                 # checks Python 3.13, uv, Node 22, pnpm 9; .env.local; deps; pre-push hook
./scripts/dev                   # engine :7333 + dashboard :3000 in one terminal

uv run ruff check apps packages tests
uv run ruff format --check apps packages tests
uv run mypy apps packages tests
uv run pytest -q -m "not integration" --ignore=tests/standalone   # fast gate
uv run pytest -q -m integration tests/smoke                       # smoke integration

cd apps/dashboard && pnpm check:api && pnpm typecheck && pnpm lint && pnpm test && pnpm build
cd e2e && npx playwright test --project=app      # starts its own engine + Next dev server; ports 3000 and 7333 free
```

Branch from `develop` (`issue/<n>-<slug>`, `fix/…`, `chore/…`, `docs/…`), Conventional Commits, PR into `develop`.
`develop → main` PRs are releases (`enforce-main-source.yml` refuses any other source). Never push to `main` or
`develop`. Details: CONTRIBUTING.md, docs/testing.md.

## CI and the merge gate

- **Required status checks on `develop`:** `Python (ruff + pytest)`, `Dashboard (typecheck + build)`,
  `Playwright smoke`, `Python deps CVE scan`, `Playwright full`. Path classifiers skip jobs a PR doesn't touch;
  `Playwright full` runs on PRs that touch the dashboard, the engine or the specs, and nightly.
- **Review rule (ruleset on the default branch):** one approving review, code-owner review required
  (`.github/CODEOWNERS` covers `.github/workflows/`, `.github/scripts/`, `.github/review-context.md`), and the last
  pusher can't approve.
- **AI review gate:** `.github/workflows/gemini-review.yml` (the public-repo variant rendered by
  lagowski/pr-review-gate, owner 2026-11-04; "do not hand-edit", refreshed by the `chore: sync AI review gate`
  PRs) reviews PRs into `develop`/`main` and posts the `gemini-review` check. It is not one of the required
  status checks above, and it posts a check without the verdict markers the auto-merge approver reads: so the
  approver keeps dap in `no_gate` and approves on the required checks alone (decided by the auto-merge tab for the
  owner 2026-11-18; making it gate = make the check required or teach the approver to read it, open for the owner).
  A fork PR is reviewed only after a maintainer adds the `safe-to-review` label.
- Also running: three `Python smoke integration` shards (not required, but a red shard blocks a merge all the
  same), CodeQL, `build` / `bundle-wheel` packaging, a daily CVE scan, Dependabot auto-merge (patch/minor are
  approved by `github-actions[bot]` and squash-merged; majors wait for a human).
- PR checks also run on item PRs into `feature/**` and `sprint/**` collectors.

## Approvals and merging (fleet policy, owner decision 2026-10-02)

**An approval is an approval, whichever account gives it.** An APPROVED review from any of
`rlagowski`, `rafeekpro` or `Dixter999` counts. Don't hold back a merge because the approval came
from a different account than you expected: not the PR author's, not this session's, and not the
"usual" owner's. Two identity rules, both enforced by GitHub: an author can't approve their own PR,
and (since 2026-10-08, owner rule) the account that pushed the latest commit can't approve it either
(`require_last_push_approval: true` on every org ruleset; pr-review-gate `rulesets/branch-protection.md`).

**Who may approve (owner rule 2026-10-15).** The PR's creator and every account that committed to it
can't approve it; any other of the three accounts can. Usually one account creates the PR and makes every
commit, so the other two can approve; if two accounts committed, the third still can. `rlagowski` is not a
mandatory approver: rlagowski can create a PR and Dixter999 approve it. So keep **one identity per PR**:
create, commit and push follow-ups as the same account, and never push to a PR as an account that should
approve it — that turns an approver into a committer. Auto-approve approves as `github-actions[bot]`, which
is never a committer.

A session may merge a PR itself once all of these hold:
- an APPROVED review from one of those three accounts is on the PR's **current head commit** (a
  push that changes the diff dismisses it);
- every required check is green and GitHub reports the PR mergeable. The ruleset decides whether
  that approval satisfies any code-owner rule;
- no PR comment asks to hold (for example "DO NOT MERGE").

Merge with `gh pr merge <n> --match-head-commit <approved-sha>`, using this repo's merge method.
Never use `--admin` to get past a missing approval or a missing check.

**Walk items.** Item PRs into a collector branch that a VeraCrew walk manages (`feature/*`, `sprint/*`) are
merged by the walk after its verification lane; don't hand-merge them (2026-10-11: a hand-merge skipped the lane
and raced the walk). A "Merge it" line for such a PR means "the approval is in place". If the walk has stalled for
hours, report it and offer to merge. The collector → `develop` promotion PR is the owner's to review.

## Labels

Every issue opened in this repository carries `type:`, `area:` and `size:` **at creation** —
including issues filed by `gh issue create` or the REST API, neither of which picks up issue
forms.

- `type:` — `feat` | `bug` | `chore` | `infra` | `spike` | `docs`
- `area:` — the part of the tree the work lands in; the issue forms list the current set
- `size:` — `S` (one sitting) | `M` (a day or so) | `L` (more than a day — consider filing it
  as `tracking` and splitting it instead)

An epic also carries `tracking`, and is never dispatched to a worker.

`needs-split`, `not-code` and `tracking` all mean **do not dispatch**.

These exist because dispatching the wrong issue is expensive: an unlabelled epic once produced
seven issues in one 3,074-line PR, three of them in no sprint at all.

## GitHub API polling (fleet rule, owner 2026-10-15)

REST core is **5000 calls/hour per USER**, shared by every token and every session of that user. On 2026-10-15 Dixter999 and rlagowski were exhausted for most of the day.
- No REST poller faster than every **120 s**. Prefer GraphQL (`gh pr view --json statusCheckRollup,reviews,mergeStateStatus`) over `gh run list/view`, `gh api repos/...` and REST `gh pr checks`.
- For repeated reads, use `gh api --cache 60s`.
- Every waiter or watcher has an **iteration cap** and **stops on a 403 / "rate limit exceeded"**. Don't retry; report it.
- Never leave a background loop running past its purpose: kill it when the PR merges or the task ends. A 23.7 h orphan loop was found.
- On DIXTER and dixter-pc, `gh` **reads** go out as rafeekpro via a shim; writes stay on your account. Don't work around it.

## Operator Context (Authorized Contributors Only)

This repo runs in production against a private infrastructure that is **NOT** described in this CLAUDE.md or anywhere else in the repo, and intentionally so. The full operational context — credentials, host addresses, cred-rotation procedures, review-gate engine specs, run brief, and machine restart procedures — lives in a private Obsidian vault.

**If you have authorized access to that vault:**

- **Vault index:** `~/OneDrive/LAGOVALUT/dap/00_START_HERE.md` (Windows: `C:\Users\<you>\OneDrive\LAGOVALUT\dap\00_START_HERE.md` ; WSL: `/mnt/c/Users/<you>/OneDrive/LAGOVALUT/dap/00_START_HERE.md`).
- Read it once before doing anything destructive in this repo or against the production infra.
- The vault contains the canonical reading order, the architecture diagram, and the first-run quick-start.

**If you don't have vault access, the following are the only authorized actions on this repo:**

- Read code, run tests locally, open PRs, comment on issues.
- **Do not** push to `main` / `develop`, do not run `cortex` commands, do not trigger DAP engine runs against production projects, do not modify `instance_env_vars`, do not touch the PostgreSQL pod referenced below.

### Canonical infrastructure facts (NOT secrets — safe to keep in-repo)

| Fact | Value | Note |
|---|---|---|
| Execution host | `dixter-pc` — 192.168.1.100, SSH port 2222 | Self-hosted runner for the AI-review gate also lives here. |
| DAP engine | `http://dixter-pc:7333` | systemd user unit `dap-engine`. |
| DAP dashboard | `http://dixter-pc:3000` (typically accessed as `http://localhost:3001` via SSH `LocalForward`) | systemd user unit `dap-dashboard`. |
| Postgres pod | Postgres deployment in the `database` namespace on the k3s node 10.0.0.5 | Specific pod identity, image, and PVC are operational details that live in the vault, not in this public repo. **Canonical persistent store — never recreate the deployment or the PVC.** |
| Postgres NodePort | `10.0.0.5:30432` → the pod above | All DAP-related databases live here. |
| Review gate check name | `gemini-review` | Posted by `.github/workflows/gemini-review.yml` on PRs into `develop`/`main`. Stable across engine swaps. |

### The four secrets-handling rules

1. **No credentials in this repo.** Connection strings, PATs, passwords, Fernet keys, and OAuth tokens never appear in code, commits, comments, issues, PR bodies, or any file tracked by git. They live in the vault and (encrypted) in the `instance_env_vars` table on the canonical Postgres pod.
2. **No credentials in LLM prompts.** Any Claude / Codex / Gemini call that originates inside this repo MUST NOT include real tokens or passwords in its prompt. The bridge wrappers (`claude-bridge`, `codex-bridge`) strip provider env vars from the child process specifically to prevent silent flips to metered APIs.
3. **Three role-separated GitHub identities.** Production runs require four `CORTEX_GH_TOKEN_<ROLE>` variables resolving to three accounts (read=Dixter999, issues=Dixter999, code=rafeekpro, merge=rlagowski). These live in `instance_env_vars` once globally — never duplicated per-project. The single `GH_TOKEN` you see on a project record is the *git-push identity* for that repo only.
4. **The pod is the source of truth.** If state appears inconsistent (missing project, stale env var, wrong password hash), the fix is always a targeted update inside the existing pod — never a new pod, never a new PVC, never an alternative deployment.

### Boundary

Anything beyond the table and four rules above — actual host credentials, the API token, the password hash, the Fernet key, the per-account PAT values, the codex-bridge runner setup steps — is in the vault and stays there. If you find yourself wanting to write any of those values into this repo, stop and re-read rule 1.

## Deploying the private instance

Merging to `develop` deploys nothing: the instance on dixter-pc is a checkout of `develop` that is updated by
hand. Tagged releases (PyPI, GHCR, GitHub Release) go through `.github/workflows/release.yml`; see docs/release.md.

- **A release to the instance needs the owner's OK each time, only when no engine runs are active, and with a
  database backup first** (owner 2026-10-14).
- Order used for the releases of 2026-10-14 to 2026-11-03: check that no runs are active → back up the database →
  `git pull --ff-only` → `uv sync --all-packages --frozen --inexact` → restart `dap-engine` (migrations run at
  startup) → in `apps/dashboard`: `pnpm install --frozen-lockfile && pnpm build` → restart `dap-dashboard` →
  verify the engine's `/health` (`db_reachable=true`), the dashboard's `/login` (200) and the Alembic version.
  Note the previous commit as the rollback target before you start. The helper scripts and the full procedure
  are on the host and in the vault, not in this repo.
- `scripts/deploy-engine.sh` does the engine half (sync, restore cortex's out-of-lock dependencies, verify the
  import, restart).
- After a major bump of a runtime dependency (a provider SDK, for example), smoke-test that adapter on the
  instance; after a dashboard major, load a form page while logged in.

## Current state (2026-11-18)

- `develop` = `833f0c8`. Since the 2026-11-03 README refresh (#973) it has only received dependency bumps
  (#984–#988, #992–#995) and AI-gate syncs (#974–#997). No open PRs.
- The private instance was last released on 2026-11-03 at `072c338` (per the tab's hand-off of 2026-11-09);
  everything after it is unreleased and waits for the owner's OK. `google-genai` 3.0 (#988) is a runtime major:
  smoke-test the gemini adapter at that release.
- Open issues: #776 (tracking: native declarative runtimes), #813 (its slice 3, `needs-split`), #797 (`not-code`).
  All three are do-not-dispatch as labelled.
- The last feature work: the `aider` runtime (#958, 2026-10-17) and the #811 sprint (retry loops, workspace
  state, fallback chains; #954, 2026-10-12).

## Known traps

- **Public repo.** `.claude/hooks/guard.sh` blocks reading or committing real `.env` files and hand-editing
  lockfiles (`uv.lock`, `pnpm-lock.yaml`, `package-lock.json`: regenerate with `uv lock` / `pnpm install`, pnpm
  pinned 9.15.9). `.claude/hooks/guard-shared-refs.py` (fleet copy, don't hand-edit) refuses `git stash` and `pkill`.
- **`--inexact` on the instance.** A plain `uv sync --all-packages` removes cortex's dependencies and its
  `.pth` link from the engine venv; every cortex `python-func` node then fails with `ModuleNotFoundError`
  (2026-06-06). Use `--inexact` or `scripts/deploy-engine.sh`.
- **A Dependabot PR that fails `Python deps CVE scan` on packages it doesn't touch:** the vulnerable package
  is already in develop's `uv.lock`. Fix it with a separate lock-bump PR (`uv lock --upgrade-package <name>`),
  then rebase the Dependabot PRs; if `@dependabot rebase` is ignored, `@dependabot recreate` works.
- **`gh api rate_limit` can misreport the REST quota.** Read the `x-ratelimit-*` headers of a real request.
- **Restarting the instance's units:** try `systemctl --user` first. When the user bus is unreachable, `kill -9`
  the process (both units have `Restart=on-failure`); a SIGTERM exits cleanly and leaves the unit stopped. Find
  the pid by port (`ss -ltnp`), never with `pgrep -f` over ssh: it matches your own remote shell.
- **Host `pg_dump` is older than the server.** Back up with the pod's `pg_dump`.
- **Local Node:** the dashboard needs Node 22 (`.nvmrc`). On a host with Node 20, run vitest with
  `npx -y node@22 node_modules/vitest/vitest.mjs run` and pnpm with `npx -y pnpm@9.15.9`.
- **Generated API types:** after changing an engine request/response schema, run `pnpm --dir apps/dashboard gen:api`
  and commit `src/lib/api/types.gen.ts`, or `Dashboard (typecheck + build)` fails.
- **`.github/` is code-owned and partly rendered from lagowski/pr-review-gate.** Change the gate in canon, not here.

## What changed recently

- 2026-11-18: this file rewritten (the generic agent/TDD/Docker-first template that filled its first 770 lines
  described a setup this repo never had).
- 2026-11-04: the AI review gate for public repos was rolled out here (`gemini-review.yml`).
- 2026-11-03: README audit (#973); instance released at `072c338`.
- 2026-10-17: `aider` runtime (#958). 2026-10-12: #811 sprint promoted (#954).
- 2026-10-03: #810 delivered (git workspace + push guards on CLI code runtimes, `github` runtime); Playwright full
  runs on PRs (#918).

---
paths:
  - "scripts/deploy-engine.sh"
  - "scripts/dap-db-snapshot.sh"
  - "DEPLOYMENT.md"
  - "docs/self-hosting.md"
  - "docs/release.md"
  - "k8s/**"
---

# Operator context and the private instance (authorized contributors only)

Moved here from CLAUDE.md on 2026-11-19 with its full text. What is said about the instance comes from the
maintainers' operational notes: it cannot be checked from this repository (verify on the host before acting).

## Operator context

This repo runs in production against a private infrastructure that is **NOT** described in CLAUDE.md or anywhere
else in the repo, and intentionally so. The full operational context (credentials, host addresses, cred-rotation
procedures, review-gate engine specs, run brief, and machine restart procedures) lives in a private Obsidian
vault.

**If you have authorized access to that vault:**

- **Vault index:** `~/OneDrive/LAGOVALUT/dap/00_START_HERE.md` (Windows:
  `C:\Users\<you>\OneDrive\LAGOVALUT\dap\00_START_HERE.md`; WSL:
  `/mnt/c/Users/<you>/OneDrive/LAGOVALUT/dap/00_START_HERE.md`).
- Read it once before doing anything destructive in this repo or against the production infra.
- The vault contains the canonical reading order, the architecture diagram, and the first-run quick-start.

**If you don't have vault access, the following are the only authorized actions on this repo:**

- Read code, run tests locally, open PRs, comment on issues.
- **Do not** push to `main` / `develop`, do not run `cortex` commands, do not trigger DAP engine runs against
  production projects, do not modify `instance_env_vars`, do not touch the PostgreSQL pod referenced below.

## Canonical infrastructure facts (no hosts or addresses: this repo is public; those are in the vault)

| Fact | Value | Note |
|---|---|---|
| Execution host | the maintainers' execution host (name, address and SSH port are in the private vault) | Self-hosted runner for the AI-review gate also lives here. |
| DAP engine | port `7333` on the execution host | systemd user unit `dap-engine`. |
| DAP dashboard | port `3000` on the execution host (typically reached through an SSH `LocalForward`) | systemd user unit `dap-dashboard`. |
| Postgres pod | Postgres deployment in the `database` namespace on the maintainers' k3s cluster | Pod identity, image, PVC, node address and NodePort are operational details that live in the vault, not in this public repo. **Canonical persistent store: never recreate the deployment or the PVC.** |
| Postgres access | the k3s Postgres NodePort (see the vault) → the pod above | All DAP-related databases live here. |
| Review gate check name | `gemini-review` | Posted by `.github/workflows/gemini-review.yml` on PRs into `develop`/`main`. Stable across engine swaps. |

## The four secrets-handling rules

1. **No credentials in this repo.** Connection strings, PATs, passwords, Fernet keys, and OAuth tokens never
   appear in code, commits, comments, issues, PR bodies, or any file tracked by git. They live in the vault and
   (encrypted) in the `instance_env_vars` table on the canonical Postgres pod.
2. **No credentials in LLM prompts.** Any Claude / Codex / Gemini call that originates inside this repo MUST NOT
   include real tokens or passwords in its prompt. The bridge wrappers (`claude-bridge`, `codex-bridge`) strip
   provider env vars from the child process specifically to prevent silent flips to metered APIs.
3. **Three role-separated GitHub identities.** Production runs require four `CORTEX_GH_TOKEN_<ROLE>` variables
   (read, issues, code, merge) resolving to three accounts; which account holds which role is in the vault.
   These live in `instance_env_vars` once globally, never duplicated per project. The single `GH_TOKEN` you see
   on a project record is the *git-push identity* for that repo only.
4. **The pod is the source of truth.** If state appears inconsistent (missing project, stale env var, wrong
   password hash), the fix is always a targeted update inside the existing pod: never a new pod, never a new
   PVC, never an alternative deployment.

**Boundary.** Anything beyond the table and four rules above (actual host credentials, the API token, the
password hash, the Fernet key, the per-account PAT values, the codex-bridge runner setup steps) is in the vault
and stays there. If you find yourself wanting to write any of those values into this repo, stop and re-read
rule 1.

## Releasing to the private instance

Merging to `develop` deploys nothing: the instance on the execution host is a checkout of `develop` that is
updated by hand. Tagged releases (PyPI, GHCR, GitHub Release) go through `.github/workflows/release.yml`; see
`docs/release.md`.

- **A release to the instance needs the owner's OK each time, only when no engine runs are active, and with a
  database backup first** (owner 2026-10-14).
- Order used for the releases of 2026-10-14 to 2026-11-03 (verify): check that no runs are active → back up the
  database → `git pull --ff-only` → `uv sync --all-packages --frozen --inexact` → restart `dap-engine`
  (migrations run at startup) → in `apps/dashboard`: `pnpm install --frozen-lockfile && pnpm build` → restart
  `dap-dashboard` → verify the engine's `/health` (`db_reachable=true`), the dashboard's `/login` (200) and the
  Alembic version. Note the previous commit as the rollback target before you start. The helper scripts and the
  full procedure are on the host and in the vault, not in this repo.
- `scripts/deploy-engine.sh` does the engine half (fast-forward, sync, restore cortex's out-of-lock
  dependencies, verify the import, restart).
- After a major bump of a runtime dependency (a provider SDK, for example), smoke-test that adapter on the
  instance; after a dashboard major, load a form page while logged in. `google-genai` 3.0 (#988, merged
  2026-11-09) is such a major: smoke-test the gemini adapter at the first instance release that includes it.
- The instance was last released on 2026-11-03 at `072c338` (recorded 2026-11-09; verify on the host).

## Instance traps

- **`--inexact` on the instance.** A plain `uv sync --all-packages` removes cortex's dependencies and its
  `.pth` link from the engine venv; every cortex `python-func` node then fails with `ModuleNotFoundError`
  (2026-06-06). Use `--inexact` or `scripts/deploy-engine.sh`.
- **Restarting the instance's units:** try `systemctl --user` first. When the user bus is unreachable, `kill -9`
  the process (both units have `Restart=on-failure`); a SIGTERM exits cleanly and leaves the unit stopped. Find
  the pid by port (`ss -ltnp`), never with `pgrep -f` over ssh: it matches your own remote shell. (verify)
- **Host `pg_dump` is older than the server.** Back up with the pod's `pg_dump`. (verify)

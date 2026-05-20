# Dependency Updates

DAP uses Dependabot to keep dependency update PRs small enough to review
while avoiding one full CI run per patch bump.

## Grouping Policy

Dependabot targets `develop` and groups low-risk updates by ecosystem:

- Python workspace dependencies via `uv` at `/`: grouped minor and patch
  updates, with major updates left as standalone PRs.
- Dashboard dependencies at `/apps/dashboard`: grouped minor and patch
  updates, with major updates left standalone.
- E2E Playwright dependencies at `/e2e`: grouped minor and patch updates,
  separate from dashboard dependencies because they exercise different
  lockfiles and CI jobs.
- GitHub Actions versions: grouped minor and patch updates monthly, with
  major action upgrades left standalone.

Security updates still run through normal CI. Lockfile-only bot PRs are
compatible with the `gemini-review` low-risk classifier, so the external
AI review gate can auto-pass while standard CI remains the merge gate.

## Review Expectations

Patch/minor grouped PRs should be reviewed as dependency maintenance:
check the ecosystem, scan changelog summaries where relevant, and rely on
the required CI jobs for install, typecheck, tests, packaging, and smoke
coverage.

Major updates should stay separate because they often require manual
code, config, or runtime changes. Do not mix major upgrades with unrelated
refactors.

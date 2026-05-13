# Cutting a release

DAP ships through three channels on every `v*.*.*` tag:

- **PyPI** — six wheels (one per first-party package), published
  via trusted-publisher OIDC so no long-lived API token has to
  live in this repo.
- **GHCR** — multi-platform Docker image
  (`ghcr.io/<owner>/dap:<version>`), `linux/amd64` + `linux/arm64`.
- **GitHub Release** — release notes with auto-generated changelog
  + every wheel/sdist attached for offline operators.

Everything runs from `.github/workflows/release.yml`. This document
covers the **one-time operator setup** and the **per-release
checklist**.

> **History note.** v0.3.0 was published in "bootstrap mode" with
> an account-scoped API token because PyPI's *pending* publishers
> form rate-limited the initial registration of six new projects.
> Once the six projects existed on PyPI, trusted-publisher rules
> were added through the reliable Manage → Publishing → Add flow
> on each project page. v0.3.1+ uses OIDC end-to-end.

## One-time setup

### PyPI trusted publishers

Each PyPI project (`dap-cli`, `dap-engine`, `dap-schemas`,
`dap-runtimes`, `dap-prompt-dsl`, `dap-cortex`) needs a
trusted-publisher rule pointing at this repo's `release.yml`
workflow. For an existing project (which is the case for all six
after v0.3.0):

1. Sign in to PyPI as the project owner.
2. Open the project's PyPI page → **Manage** → **Publishing**.
   (Direct URL shape: `https://pypi.org/manage/project/<package>/settings/publishing/`,
   exact path subject to PyPI's UI evolution.)
3. Add a new trusted publisher (GitHub provider). Fill in:
   - **Owner**: `rafeekpro` (this repo's GitHub org/user).
   - **Repository name**: `dap`.
   - **Workflow name**: `release.yml`.
   - **Environment name**: `pypi`.
4. Save. PyPI will mint short-lived OIDC tokens for the
   `release.yml` workflow whenever it runs against a tag.

The `environment: pypi` block in the workflow scopes publishing
to a specific GitHub Environment. Attach required-reviewers or
tag-pattern restrictions to that environment in repo settings if
you want manual release gating on top of the OIDC flow.

### GHCR access

GHCR uses the `GITHUB_TOKEN` minted per workflow run, with
`packages: write` granted at the workflow level. No extra secret
needed. The image is pushed to
`ghcr.io/<repo-owner>/dap`.

Make the package public (one-time, in the GitHub package settings)
unless you want `docker pull` to require auth.

### Branch protection (recommended)

`v*.*.*` tags should only land on `main` after a release-prep PR
merges. Suggested branch protection:

- Require a pull request, 1 approval, status checks (CI, Packaging).
- Allow tag-push events only from a small set of maintainers.

## Per-release checklist

Substitute `<version>` (e.g. `0.4.0`) consistently throughout — the
commands below use the placeholder so you can copy them verbatim.

1. **Pick a version.** Semantic versioning: `MAJOR.MINOR.PATCH`. For
   pre-releases use `MAJOR.MINOR.PATCH-rc1`, `-beta2`, etc. The
   workflow detects pre-release suffixes (a `-` in the version)
   and refuses to bump the `latest` Docker tag / GitHub "latest
   release" pointer.

2. **Bump versions** across the workspace:
   ```bash
   ./scripts/bump-version.sh <version>
   ```
   Rewrites every `pyproject.toml` version field, every first-party
   `__version__` constant (CLI + cortex), and refreshes `uv.lock`
   so the lock matches the bumped pyprojects. Idempotent —
   re-running with the same value is a no-op. Validates the input
   shape (loose semver: `x.y.z` or `x.y.z-rcN`).

3. **Update `CHANGELOG.md`** with the section for this release.
   Keep-a-Changelog style:
   ```markdown
   ## [<version>] — YYYY-MM-DD

   ### Added
   - ...

   ### Changed
   - ...

   ### Fixed
   - ...
   ```

4. **PR + merge** the bump + changelog to `main`.

5. **Tag + push**:
   ```bash
   git checkout main
   git pull origin main
   git tag -a v<version> -m "DAP v<version>"
   git push origin v<version>
   ```

6. **Watch the run.** `release.yml` should:
   - Build all six wheels.
   - Publish to PyPI (skipped if `workflow_dispatch`).
   - Build + push the multi-platform Docker image to GHCR.
   - Create the GitHub Release with attached wheels.

7. **Smoke test.**
   ```bash
   # PyPI
   pipx install dap-cli==<version>
   dap --version

   # Docker
   docker run --rm -p 7333:7333 \
       -e DAP_AUTH_JWT_SECRET=$(openssl rand -hex 32) \
       ghcr.io/<owner>/dap:<version>
   curl http://127.0.0.1:7333/health
   ```

## Dry-run / rehearsal

Without cutting a real tag, you can rehearse the build by manually
dispatching the workflow:

1. Actions tab → "Release" → "Run workflow".
2. The build-wheels stage runs end-to-end with a synthetic
   `0.0.0-dev+<sha>` version.
3. `publish-pypi`, `build-docker`, and `github-release` skip on
   manual dispatch (they `if:
   startsWith(github.ref, 'refs/tags/v')`) so no artefacts leak.

This is the safe way to verify your trusted-publisher setup and
the dashboard bundle script without affecting real publishing.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `publish-pypi` fails with `403 Forbidden` | Trusted publisher not registered for that package, or PyPI environment name mismatch. Check the workflow's `environment: name: pypi` matches what's in PyPI's publisher rule. |
| Docker push fails with `denied: permission_denied` | `packages: write` permission missing or the GHCR package is in a different org. Re-check the workflow-level `permissions:` block. |
| `latest` tag didn't update | Version has a `-` (pre-release). By design — `latest` only follows stable releases. |
| `softprops/action-gh-release` says "tag does not exist" | The tag push hasn't been received yet, or `fetch-depth: 0` isn't set on the checkout step. |
| Bundle script fails on a clean runner | Node / pnpm not installed. The build-wheels job sets them up; if you adapted the workflow, make sure the setup steps are still present. |

## Rollback

PyPI doesn't allow deleting a version, only yanking. To yank a
broken `<version>`:

```bash
pipx upgrade twine
twine yank dap-cli==<version>
# repeat for each of the six packages
```

For Docker: keep the bad version available but bump `latest` to
the prior good one (replace `<good-version>` with the tag you
want `latest` to follow):

```bash
docker buildx imagetools create \
    --tag ghcr.io/<owner>/dap:latest \
    ghcr.io/<owner>/dap:<good-version>
```

GitHub Releases can be deleted from the UI; this won't break
`pipx install` for users who already got the old wheels but it
removes the entry point from `/releases`.

## Past releases

| Version | Date | Notes |
|---|---|---|
| `0.3.0` | 2026-05-12 | First multi-user release. Auth (password + GitHub/Google OAuth + API tokens), admin panel, standalone packaging (PyPI + multi-platform Docker). See [`CHANGELOG.md`](../CHANGELOG.md#030--2026-05-12). |
| `0.0.1` | (pre-tag) | Initial single-user release. No published artefacts. |

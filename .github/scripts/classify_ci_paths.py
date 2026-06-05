#!/usr/bin/env python3
"""Classify changed paths for cost-aware GitHub Actions skips."""

from __future__ import annotations

import argparse


def _under(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix)


def _is_docs(path: str) -> bool:
    return (
        path.endswith(".md")
        or _under(path, "docs/")
        or _under(path, ".claude/")
        or path in {"README.md", "DEPLOYMENT.md", "CONTRIBUTING.md", "CHANGELOG.md"}
    )


def _is_lockfile(path: str) -> bool:
    return path in {
        "uv.lock",
        "apps/dashboard/pnpm-lock.yaml",
        "package-lock.json",
        "e2e/package-lock.json",
    }


def _is_ci_policy(path: str) -> bool:
    return path in {
        ".github/workflows/ci.yml",
        ".github/workflows/docker-build.yml",
        ".github/workflows/e2e.yml",
        ".github/workflows/gemini-review.yml",
        ".github/scripts/classify_ci_paths.py",
    }


def _is_dashboard(path: str) -> bool:
    return _under(path, "apps/dashboard/") or path in {"package.json", "package-lock.json"}


def _is_python_backend(path: str) -> bool:
    return (
        _under(path, "apps/engine/")
        or _under(path, "apps/cli/")
        or _under(path, "packages/")
        or _under(path, "tests/")
        or path
        in {
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".pre-commit-config.yaml",
        }
    )


def _is_api_contract(path: str) -> bool:
    return (
        _under(path, "apps/engine/src/dap_engine/api/")
        or _under(path, "apps/engine/src/dap_engine/auth/")
        or path
        in {
            "apps/engine/src/dap_engine/app.py",
            "apps/engine/src/dap_engine/contracts.py",
            "apps/dashboard/src/lib/api/types.gen.ts",
            "pyproject.toml",
            "uv.lock",
        }
    )


def _is_docker(path: str) -> bool:
    return (
        path in {"Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock"}
        or _under(path, "docker/")
        or _under(path, "apps/")
        or _under(path, "packages/")
        or path == "scripts/build-dashboard-bundle.sh"
        or _under(path, "tests/standalone/")
    )


def _is_bundle_wheel(path: str) -> bool:
    return (
        _under(path, "apps/dashboard/")
        or _under(path, "apps/cli/")
        or _under(path, "packages/")
        or path in {"pyproject.toml", "uv.lock", "scripts/build-dashboard-bundle.sh"}
        or _under(path, "tests/standalone/")
    )


def _is_e2e(path: str) -> bool:
    return (
        _under(path, "apps/dashboard/")
        or _under(path, "apps/engine/")
        or _under(path, "apps/cli/")
        or _under(path, "packages/")
        or _under(path, "e2e/")
        or path in {"pyproject.toml", "uv.lock", "package-lock.json"}
    )


def _is_gemini_review(path: str) -> bool:
    return path in {
        ".github/workflows/gemini-review.yml",
        ".github/scripts/gemini_review.py",
    } or _is_ci_policy(path)


def _emit(outputs: dict[str, str]) -> None:
    for key, value in outputs.items():
        print(f"{key}={value}")


def classify(paths: list[str]) -> dict[str, str]:
    if not paths:
        return {
            "run_python": "true",
            "run_python_smoke": "true",
            "run_pip_audit": "true",
            "run_dashboard": "true",
            "run_docker": "true",
            "run_bundle_wheel": "true",
            "run_e2e": "true",
            "skip_ai_review": "false",
            "ai_review_reason": "No changed files were returned; running full review.",
        }

    docs_only = all(_is_docs(path) for path in paths)
    lockfiles_only = all(_is_lockfile(path) for path in paths)
    docs_or_lockfiles_only = all(_is_docs(path) or _is_lockfile(path) for path in paths)
    ci_policy_changed = any(_is_ci_policy(path) for path in paths)
    dashboard_changed = any(_is_dashboard(path) for path in paths)
    python_changed = any(_is_python_backend(path) for path in paths)
    api_contract_changed = any(_is_api_contract(path) for path in paths)
    docker_changed = any(_is_docker(path) for path in paths)
    bundle_changed = any(_is_bundle_wheel(path) for path in paths)
    e2e_changed = any(_is_e2e(path) for path in paths)
    gemini_changed = any(_is_gemini_review(path) for path in paths)
    unknown_changed = any(
        not (
            _is_docs(path)
            or _is_lockfile(path)
            or _is_ci_policy(path)
            or _is_dashboard(path)
            or _is_python_backend(path)
            or _is_docker(path)
            or _is_bundle_wheel(path)
            or _is_e2e(path)
        )
        for path in paths
    )

    dashboard_only = (
        dashboard_changed
        and not ci_policy_changed
        and not python_changed
        and not unknown_changed
        and all(_is_docs(path) or _is_dashboard(path) or _is_lockfile(path) for path in paths)
    )

    run_python = ci_policy_changed or unknown_changed or (python_changed and not docs_only)
    run_python_smoke = (
        ci_policy_changed
        or unknown_changed
        or (python_changed and not docs_only and not dashboard_only)
    )
    run_pip_audit = ci_policy_changed or unknown_changed or (python_changed and not docs_only)
    run_dashboard = (
        ci_policy_changed
        or unknown_changed
        or (dashboard_changed and not docs_only)
        or (api_contract_changed and not docs_only)
    )
    run_docker = ci_policy_changed or unknown_changed or (docker_changed and not docs_only)
    run_bundle_wheel = ci_policy_changed or unknown_changed or (bundle_changed and not docs_only)
    run_e2e = ci_policy_changed or unknown_changed or (e2e_changed and not docs_only)

    if docs_only:
        review_reason = "Docs-only PR - AI review skipped by path policy."
        skip_ai_review = not gemini_changed
    elif lockfiles_only:
        review_reason = "Lockfile-only PR - AI review skipped by path policy."
        skip_ai_review = True
    elif docs_or_lockfiles_only:
        review_reason = "Docs/lockfiles-only PR - AI review skipped by path policy."
        skip_ai_review = True
    else:
        review_reason = "Code or workflow paths changed; running AI review."
        skip_ai_review = False

    return {
        "run_python": str(run_python).lower(),
        "run_python_smoke": str(run_python_smoke).lower(),
        "run_pip_audit": str(run_pip_audit).lower(),
        "run_dashboard": str(run_dashboard).lower(),
        "run_docker": str(run_docker).lower(),
        "run_bundle_wheel": str(run_bundle_wheel).lower(),
        "run_e2e": str(run_e2e).lower(),
        "skip_ai_review": str(skip_ai_review).lower(),
        "ai_review_reason": review_reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path_file", help="Newline-delimited changed file list")
    args = parser.parse_args()

    with open(args.path_file, encoding="utf-8") as file:
        paths = [line.strip() for line in file if line.strip()]

    _emit(classify(paths))


if __name__ == "__main__":
    main()

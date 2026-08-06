from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_classifier() -> ModuleType:
    path = Path(__file__).parents[2] / ".github" / "scripts" / "classify_ci_paths.py"
    spec = importlib.util.spec_from_file_location("classify_ci_paths", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


classifier: Any = _load_classifier()


def test_empty_file_list_runs_full_ci() -> None:
    outputs = classifier.classify([])

    assert outputs["run_python"] == "true"
    assert outputs["run_python_smoke"] == "true"
    assert outputs["run_pip_audit"] == "true"
    assert outputs["run_dashboard"] == "true"
    assert outputs["run_docker"] == "true"
    assert outputs["run_bundle_wheel"] == "true"
    assert outputs["run_e2e"] == "true"
    assert outputs["skip_ai_review"] == "false"


def test_docs_only_skips_expensive_ci_and_ai_review() -> None:
    outputs = classifier.classify(["docs/architecture.md", "README.md", ".claude/notes.md"])

    assert outputs["run_python"] == "false"
    assert outputs["run_python_smoke"] == "false"
    assert outputs["run_pip_audit"] == "false"
    assert outputs["run_dashboard"] == "false"
    assert outputs["run_docker"] == "false"
    assert outputs["run_bundle_wheel"] == "false"
    assert outputs["run_e2e"] == "false"
    assert outputs["skip_ai_review"] == "true"


def test_lockfile_only_skips_ai_review_but_keeps_dependency_ci() -> None:
    outputs = classifier.classify(["uv.lock"])

    assert outputs["run_python"] == "true"
    assert outputs["run_python_smoke"] == "true"
    assert outputs["run_pip_audit"] == "true"
    assert outputs["run_dashboard"] == "true"
    assert outputs["run_docker"] == "true"
    assert outputs["run_bundle_wheel"] == "true"
    assert outputs["run_e2e"] == "true"
    assert outputs["skip_ai_review"] == "true"


def test_dashboard_only_skips_python_but_keeps_frontend_and_packaging_checks() -> None:
    outputs = classifier.classify(
        ["apps/dashboard/src/app/page.tsx", "apps/dashboard/pnpm-lock.yaml"]
    )

    assert outputs["run_python"] == "false"
    assert outputs["run_python_smoke"] == "false"
    assert outputs["run_pip_audit"] == "false"
    assert outputs["run_dashboard"] == "true"
    assert outputs["run_docker"] == "true"
    assert outputs["run_bundle_wheel"] == "true"
    assert outputs["run_e2e"] == "true"
    assert outputs["skip_ai_review"] == "false"


def test_backend_without_api_contract_skips_dashboard_and_bundle_wheel() -> None:
    outputs = classifier.classify(["apps/engine/src/dap_engine/execution/runner.py"])

    assert outputs["run_python"] == "true"
    assert outputs["run_python_smoke"] == "true"
    assert outputs["run_pip_audit"] == "true"
    assert outputs["run_dashboard"] == "false"
    assert outputs["run_docker"] == "true"
    assert outputs["run_bundle_wheel"] == "false"
    assert outputs["run_e2e"] == "true"
    assert outputs["skip_ai_review"] == "false"


def test_backend_api_contract_keeps_dashboard_check_api() -> None:
    outputs = classifier.classify(["apps/engine/src/dap_engine/api/runs.py"])

    assert outputs["run_python"] == "true"
    assert outputs["run_python_smoke"] == "true"
    assert outputs["run_dashboard"] == "true"
    assert outputs["run_e2e"] == "true"
    assert outputs["skip_ai_review"] == "false"


def test_gate_sync_pr_skips_expensive_ci_but_keeps_ai_review() -> None:
    """The fleet deployer's "chore: sync AI review gate" PR (real shape: dap#852).

    It rewrites gate/responder workflows and nothing else. Those files define none of
    the CI below, so none of it can be affected -- but until this case was handled they
    matched no predicate, counted as `unknown`, and ran the full gate every time. AI
    review still runs: a workflow change is exactly the kind of diff a reviewer should
    see, and it is the cheap check anyway.
    """
    outputs = classifier.classify([".github/workflows/copilot-comment-responder.yml"])

    assert outputs["run_python"] == "false"
    assert outputs["run_python_smoke"] == "false"
    assert outputs["run_pip_audit"] == "false"
    assert outputs["run_dashboard"] == "false"
    assert outputs["run_docker"] == "false"
    assert outputs["run_bundle_wheel"] == "false"
    assert outputs["run_e2e"] == "false"
    assert outputs["skip_ai_review"] == "false"


def test_workflow_not_delivered_by_the_canon_is_not_inert() -> None:
    """The hole this rule exists to close.

    Under a `.github/workflows/` PREFIX rule, a PR rewriting a workflow this repo owns
    would skip the CI and report green -- a broken pipeline would then surface only
    after merge, on the default branch. Only files the fleet deployer actually writes
    (`node deploy/canon-fileset.js dap`) are inert; dap carries `deploy: skip`, so that
    is one file. Anything else under .github/workflows/ is this repository's own code.
    """
    outputs = classifier.classify([".github/workflows/copilot-review-required.yml"])

    assert outputs["run_python"] == "true"
    assert outputs["run_dashboard"] == "true"
    assert outputs["run_e2e"] == "true"


def test_ci_defining_scripts_are_not_inert() -> None:
    """.github/scripts/** is executed BY the jobs, so it is never inert.

    Guards the obvious over-generalisation of the rule above ("anything under .github/
    is harmless"): a change to a script the CI runs must still run that CI.
    """
    outputs = classifier.classify([".github/scripts/select_pytest_files.py"])

    assert outputs["run_python"] == "true"
    assert outputs["run_dashboard"] == "true"
    assert outputs["run_e2e"] == "true"


def test_workflow_change_mixed_with_code_still_runs_code_ci() -> None:
    """One code file is enough: `every()`, not `any()`. A PR is mechanical only if
    ALL of it is."""
    outputs = classifier.classify(
        [
            ".github/workflows/copilot-review-required.yml",
            "apps/engine/src/dap_engine/app.py",
        ]
    )

    assert outputs["run_python"] == "true"
    assert outputs["run_dashboard"] == "true"


def test_workflow_change_runs_everything_and_ai_review() -> None:
    outputs = classifier.classify([".github/workflows/ci.yml"])

    assert outputs["run_python"] == "true"
    assert outputs["run_python_smoke"] == "true"
    assert outputs["run_pip_audit"] == "true"
    assert outputs["run_dashboard"] == "true"
    assert outputs["run_docker"] == "true"
    assert outputs["run_bundle_wheel"] == "true"
    assert outputs["run_e2e"] == "true"
    assert outputs["skip_ai_review"] == "false"

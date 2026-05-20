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

"""Every pip-audit run skips the workspace's own editable packages explicitly.

pip-audit looks up every installed distribution on PyPI. The workspace packages
(code-review-council, dap-cli, dap-database, dap-engine, dap-prompt-dsl, dap-runtimes,
dap-schemas) are installed editable from uv.lock. For the ones PyPI doesn't have,
pip-audit relied on PyPI answering 404, but PyPI intermittently answers 503 for unknown
packages, which pip-audit treats as fatal, so the CVE scan failed at random (#906).
`--skip-editable` drops exactly these local dists. Their third-party dependencies are
separate registry installs and are still audited.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"
WORKFLOWS_RUNNING_PIP_AUDIT = {"ci.yml", "cve-scan.yml"}


def _pip_audit_runs() -> dict[str, list[str]]:
    runs = {
        workflow.name: re.findall(r"^\s*run:.*\bpip-audit\b.*$", workflow.read_text(), re.M)
        for workflow in WORKFLOWS.glob("*.yml")
    }
    return {name: lines for name, lines in runs.items() if lines}


def test_pip_audit_runs_in_the_expected_workflows() -> None:
    # An exact set, so a moved or reworded invocation fails here instead of leaving
    # nothing for the flag check below to look at.
    assert set(_pip_audit_runs()) == WORKFLOWS_RUNNING_PIP_AUDIT


def test_every_pip_audit_run_skips_editable_installs() -> None:
    for name, lines in _pip_audit_runs().items():
        for line in lines:
            assert "--skip-editable" in line, f"{name}: {line.strip()}"

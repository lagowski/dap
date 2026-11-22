"""Code and fixtures in this public repo carry no internal hosts, addresses or paths.

The docs were scrubbed in #999; the pipeline bundle example and the execution-target
smoke test still held a private node address, a vault path and a home directory
(#1000). They now use placeholders (`example.internal`, `/opt/example/project`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CHECKED_FILES = (
    ROOT / "examples" / "pipelines" / "cortex-github-issue.pipeline-bundle.json",
    ROOT / "tests" / "smoke" / "test_execution_target.py",
)
FORBIDDEN = ("10.0.0.5", "192.168.1.100", "LAGOVALUT", "/home/dixter")


@pytest.mark.parametrize("path", CHECKED_FILES, ids=lambda p: p.name)
def test_file_has_no_internal_addresses(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    found = [token for token in FORBIDDEN if token in text]
    assert not found, f"{path.relative_to(ROOT)} contains internal values: {found}"

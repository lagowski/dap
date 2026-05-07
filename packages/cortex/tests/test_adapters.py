"""Round-trip tests for cortex_to_dap / dap_to_cortex adapters."""

from cortex.adapters.pipeline_state import (
    cortex_to_dap,
    dap_to_cortex,
    preserve_extensions,
)


def _base_cortex_state() -> dict:
    return {
        "issue_url": "https://github.com/test/repo/issues/1",
        "repo": "test/repo",
        "issue_number": 1,
        "branch_name": "feat/issue-1",
        "task_assignments": [{"task": "add tests", "agent": "coder"}],
        "files_changed": ["src/main.py"],
    }


def test_cortex_to_dap_puts_cortex_fields_in_extensions() -> None:
    state = _base_cortex_state()
    result = cortex_to_dap(state)

    assert "extensions" in result
    assert result["extensions"]["issue_url"] == "https://github.com/test/repo/issues/1"
    assert result["extensions"]["issue_number"] == 1
    assert result["extensions"]["task_assignments"] == [{"task": "add tests", "agent": "coder"}]


def test_cortex_to_dap_exposes_top_level_fields() -> None:
    state = _base_cortex_state()
    result = cortex_to_dap(state)

    assert result["repo"] == "test/repo"
    assert result["branch"] == "feat/issue-1"


def test_dap_to_cortex_round_trip() -> None:
    cortex_state = _base_cortex_state()
    dap_state = cortex_to_dap(cortex_state)
    recovered = dap_to_cortex(dap_state)

    assert recovered["issue_url"] == cortex_state["issue_url"]
    assert recovered["repo"] == cortex_state["repo"]
    assert recovered["branch_name"] == cortex_state["branch_name"]
    assert recovered["task_assignments"] == cortex_state["task_assignments"]


def test_preserve_extensions_merges_old_and_new() -> None:
    original = {"issue_url": "https://github.com/test/repo/issues/1", "task_assignments": []}
    delta = {"extensions": {"branch_name": "feat/new"}, "repo": "test/repo"}

    result = preserve_extensions(delta, original)

    # Old extension fields preserved
    assert result["extensions"]["issue_url"] == "https://github.com/test/repo/issues/1"
    # New fields from delta merged in
    assert result["extensions"]["branch_name"] == "feat/new"
    # Top-level DAP fields untouched
    assert result["repo"] == "test/repo"


def test_preserve_extensions_new_overrides_old() -> None:
    original = {"branch_name": "feat/old"}
    delta = {"extensions": {"branch_name": "feat/new"}}

    result = preserve_extensions(delta, original)
    assert result["extensions"]["branch_name"] == "feat/new"


def test_dap_to_cortex_handles_empty_extensions() -> None:
    dap_state = {"repo": "test/repo", "branch": "main", "extensions": {}}
    result = dap_to_cortex(dap_state)
    assert result["repo"] == "test/repo"

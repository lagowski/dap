"""Tests for pr_merger infra-only failure bypass (#222)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from cortex.nodes.pr_merger import _is_infra_only_failure, run

# ---------------------------------------------------------------------------
# _is_infra_only_failure unit tests
# ---------------------------------------------------------------------------


def test_connection_refused_is_infra() -> None:
    output = (
        "FAILED tests/test_db.py::test_connect\n"
        'psycopg.OperationalError: connection to server at "10.0.0.5", '
        "port 30432 failed: Connection refused"
    )
    assert _is_infra_only_failure(output) is True


def test_could_not_connect_is_infra() -> None:
    assert _is_infra_only_failure("could not connect to server: timeout") is True


def test_operational_error_is_infra() -> None:
    assert _is_infra_only_failure("OperationalError: server closed connection") is True


def test_no_route_to_host_is_infra() -> None:
    assert _is_infra_only_failure("No route to host") is True


def test_name_not_known_is_infra() -> None:
    assert _is_infra_only_failure("Name or service not known") is True


def test_assertion_error_is_not_infra() -> None:
    output = "AssertionError: expected 'json' output but got 'table'"
    assert _is_infra_only_failure(output) is False


def test_attribute_error_is_not_infra() -> None:
    assert _is_infra_only_failure("AttributeError: 'NoneType' object has no attribute") is False


def test_empty_output_is_not_infra() -> None:
    assert _is_infra_only_failure("") is False


def test_none_like_output_is_not_infra() -> None:
    assert _is_infra_only_failure("   ") is False


def test_mixed_infra_and_real_failure_is_not_infra() -> None:
    """A mix of infra and real failures must NOT bypass the gate (#222 tightening)."""
    output = (
        "FAILED tests/test_audit.py::test_db_write - OperationalError: connection refused\n"
        "FAILED tests/test_cli.py::test_format_json - AssertionError: expected json got table\n"
        "============================== 2 failed in 1.23s =============================="
    )
    assert _is_infra_only_failure(output) is False


def test_all_infra_failures_is_infra() -> None:
    """All FAILED blocks containing infra patterns → bypass allowed."""
    output = (
        "FAILED tests/test_a.py::test_one - OperationalError: connection refused\n"
        "FAILED tests/test_b.py::test_two - could not connect to server\n"
        "============================== 2 failed in 0.50s =============================="
    )
    assert _is_infra_only_failure(output) is True


# ---------------------------------------------------------------------------
# pr_merger.run integration tests
# ---------------------------------------------------------------------------

_INFRA_OUTPUT: str = (
    'psycopg.OperationalError: connection to server at "10.0.0.5", '
    "port 30432 failed: Connection refused"
)

# tests_passed / repo are PipelineState direct fields (top-level).
# Cortex-specific fields (pr_number, review_approved, test_output …) live
# in extensions and are unpacked by dap_to_cortex inside pr_merger.run.
_BASE_STATE: dict[str, Any] = {
    "repo": "Dixter999/cortex-project",
    "tests_passed": False,
    "extensions": {
        "pr_number": 338,
        "review_approved": True,
        "test_output": _INFRA_OUTPUT,
        "decisions": [],
        "branch_name": "cortex/issue-332/coder",
        "issue_url": "https://github.com/Dixter999/cortex-project/issues/332",
        "issue_number": 332,
    },
}


def _run_merger(state: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(run(state, {}))


def test_pr_merger_proceeds_on_infra_only_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """When tests_passed=False but output has infrastructure errors, merge proceeds."""
    merge_calls: list[dict[str, Any]] = []

    def _fake_merge(inputs: dict[str, Any]) -> dict[str, Any]:
        merge_calls.append(inputs)
        return {"sha": "abc123def456"}

    monkeypatch.setattr(
        "cortex.nodes.pr_merger.merge_pull_request",
        type("T", (), {"invoke": staticmethod(_fake_merge)})(),
    )
    monkeypatch.setattr(
        "cortex.nodes.pr_merger.load_settings",
        type("S", (), {"get_github_token": lambda self, r: "tok"}),
    )

    result = _run_merger(_BASE_STATE)
    from cortex.adapters.pipeline_state import dap_to_cortex

    state_out = dap_to_cortex(result)

    assert merge_calls, "merge_pull_request should have been called"
    assert state_out.get("merged") is True
    assert state_out.get("merge_sha") == "abc123def456"
    # Bypass must be recorded in state for audit trail (#222 fix 2)
    assert state_out.get("tests_infra_bypass") is True
    assert state_out.get("tests_bypass_reason") is not None


def test_pr_merger_blocks_on_real_test_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """When tests_passed=False and output shows assertion errors, merge is blocked."""
    state = {
        **_BASE_STATE,
        "extensions": {
            **_BASE_STATE["extensions"],
            "test_output": "AssertionError: expected JSON output got table",
        },
    }

    merge_calls: list[dict[str, Any]] = []

    def _fake_merge(inputs: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        merge_calls.append(inputs)
        return {"sha": "abc123"}

    monkeypatch.setattr(
        "cortex.nodes.pr_merger.merge_pull_request",
        type("T", (), {"invoke": staticmethod(_fake_merge)})(),
    )

    result = _run_merger(state)
    from cortex.adapters.pipeline_state import dap_to_cortex

    state_out = dap_to_cortex(result)

    assert not merge_calls, "merge_pull_request must NOT be called on real failures"
    assert state_out.get("merged") is False
    assert "tests did not pass" in (state_out.get("error") or "").lower()


def test_pr_merger_blocks_when_review_not_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    """review_approved=False always blocks, regardless of test status."""
    state = {
        **_BASE_STATE,
        "tests_passed": True,  # tests_passed is a PipelineState direct field
        "extensions": {
            **_BASE_STATE["extensions"],
            "review_approved": False,
            "test_output": "",
        },
    }

    merge_calls: list[dict[str, Any]] = []

    def _fake_merge(inputs: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        merge_calls.append(inputs)
        return {"sha": "abc123"}

    monkeypatch.setattr(
        "cortex.nodes.pr_merger.merge_pull_request",
        type("T", (), {"invoke": staticmethod(_fake_merge)})(),
    )
    monkeypatch.setattr(
        "cortex.nodes.pr_merger.load_settings",
        type("S", (), {"get_github_token": lambda self, r: "tok"}),
    )

    result = _run_merger(state)
    from cortex.adapters.pipeline_state import dap_to_cortex

    state_out = dap_to_cortex(result)

    assert not merge_calls
    assert state_out.get("merged") is False
    assert "reviewer" in (state_out.get("error") or "").lower()

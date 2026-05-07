"""Tests for `dap project state cortex --format json`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from dap_cli.__main__ import app
from dap_cli.commands.cortex import _json_default, _state_to_dict
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_RUN = {
    "id": "run-abc-123",
    "final_status": "paused",
    "pipeline_id": "pipe-001",
    "pipeline_version": 2,
    "project_id": "proj-456",
    "started_at": "2026-05-07T10:00:00Z",
    "ended_at": None,
}

_SAMPLE_STATE = {
    "extensions": {
        "issue_title": "Add JSON output to state",
        "current_phase": "phase-1",
        "next_nodes": ["coder", "reviewer"],
        "task_assignments": {"coder": "agent-1", "reviewer": "agent-2"},
        "decisions": [
            {"node": f"node-{i}", "action": "approve"} for i in range(10)
        ],
    }
}


def _patch_run(run: dict = _SAMPLE_RUN):
    return patch("dap_cli.commands.cortex._get_run", return_value=run)


def _patch_state(state: dict = _SAMPLE_STATE):
    return patch("dap_cli.commands.cortex._get_run_state", return_value=state)


def _patch_run_not_found():
    import httpx
    return patch(
        "dap_cli.commands.cortex._get_run",
        side_effect=httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        ),
    )


# ---------------------------------------------------------------------------
# CLI-level tests
# ---------------------------------------------------------------------------


class TestStateHelpShowsFormatOption:
    def test_state_help_shows_format_option(self) -> None:
        result = runner.invoke(app, ["project", "state", "--help"])
        assert result.exit_code == 0
        assert "--format" in result.output


class TestStateFormatJsonOutputsValidJson:
    def test_state_format_json_outputs_valid_json(self) -> None:
        with _patch_run(), _patch_state():
            result = runner.invoke(
                app,
                ["project", "state", "cortex", "run-abc-123", "--format", "json"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)


class TestStateFormatJsonIncludesRequiredKeys:
    def test_state_format_json_includes_required_keys(self) -> None:
        with _patch_run(), _patch_state():
            result = runner.invoke(
                app,
                ["project", "state", "cortex", "run-abc-123", "--format", "json"],
            )
        parsed = json.loads(result.output)
        required = {
            "issue_title", "current_phase", "run_id",
            "status", "next_nodes", "task_assignments", "decisions",
        }
        assert required.issubset(parsed.keys())


class TestStateFormatJsonNotFoundReturnsErrorObject:
    def test_state_format_json_not_found_returns_error_object(self) -> None:
        with _patch_run_not_found():
            result = runner.invoke(
                app,
                ["project", "state", "cortex", "run-missing", "--format", "json"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["error"] == "not_found"
        assert parsed["run_id"] == "run-missing"


class TestStateFormatJsonCapsDecisionsAt5:
    def test_state_format_json_caps_decisions_at_5(self) -> None:
        with _patch_run(), _patch_state():
            result = runner.invoke(
                app,
                ["project", "state", "cortex", "run-abc-123", "--format", "json"],
            )
        parsed = json.loads(result.output)
        assert len(parsed["decisions"]) == 5


class TestStateDefaultFormatUnchanged:
    def test_state_default_format_unchanged(self) -> None:
        with _patch_run():
            result = runner.invoke(
                app,
                ["project", "state", "cortex", "run-abc-123"],
            )
        assert result.exit_code == 0
        # Default output is Rich text, not JSON
        assert "run-abc-123" in result.output
        assert "paused" in result.output.lower()
        # Ensure it is NOT JSON
        try:
            json.loads(result.output)
            is_json = True
        except json.JSONDecodeError:
            is_json = False
        assert not is_json


class TestStateFormatTableMatchesDefault:
    def test_state_format_table_matches_default(self) -> None:
        with _patch_run():
            default_result = runner.invoke(
                app,
                ["project", "state", "cortex", "run-abc-123"],
            )
        with _patch_run():
            table_result = runner.invoke(
                app,
                ["project", "state", "cortex", "run-abc-123", "--format", "table"],
            )
        assert default_result.output == table_result.output


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestStateToDictUnit:
    def test_caps_decisions(self) -> None:
        result = _state_to_dict(_SAMPLE_RUN, _SAMPLE_STATE)
        assert len(result["decisions"]) == 5
        # Should be the last 5
        assert result["decisions"][0]["node"] == "node-5"

    def test_empty_state(self) -> None:
        result = _state_to_dict(_SAMPLE_RUN, {})
        assert result["issue_title"] == ""
        assert result["decisions"] == []
        assert result["task_assignments"] == {}
        assert result["next_nodes"] == []

    def test_missing_extensions(self) -> None:
        result = _state_to_dict(_SAMPLE_RUN, {"extensions": None})
        assert result["status"] == "paused"
        assert result["decisions"] == []


class TestJsonDefault:
    def test_datetime(self) -> None:
        from datetime import datetime
        assert _json_default(datetime(2026, 1, 1, 12, 0)) == "2026-01-01T12:00:00"

    def test_decimal(self) -> None:
        from decimal import Decimal
        assert _json_default(Decimal("3.14")) == "3.14"

    def test_unsupported_raises(self) -> None:
        import pytest
        with pytest.raises(TypeError):
            _json_default(set())

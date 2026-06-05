"""Tests for dap project run/approve/reject/state cortex commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dap_cli.__main__ import app
from dap_cli.commands.cortex import (
    _format_progress,
    _poll_until_settled,
    _sync_workspace,
    cortex_approve,
    cortex_reject,
    cortex_state,
    default_workspace_path,
    ensure_pipeline_imported,
    load_cortex_bundle,
    parse_issue_url,
)
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestParseIssueUrl:
    def test_standard_url(self) -> None:
        repo, num = parse_issue_url("https://github.com/Dixter999/cortex-project/issues/332")
        assert repo == "Dixter999/cortex-project"
        assert num == 332

    def test_trailing_slash(self) -> None:
        repo, num = parse_issue_url("https://github.com/owner/repo/issues/42/")
        assert repo == "owner/repo"
        assert num == 42

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse GitHub issue URL"):
            parse_issue_url("https://github.com/owner/repo")

    def test_not_github_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_issue_url("https://gitlab.com/owner/repo/issues/1")

    def test_non_numeric_issue_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_issue_url("https://github.com/owner/repo/issues/abc")


# ---------------------------------------------------------------------------
# Workspace path
# ---------------------------------------------------------------------------


class TestDefaultWorkspacePath:
    def test_slashes_replaced(self) -> None:
        # Separator must be dash to match cortex/init/profile.py:_repo_slug (#224)
        path = default_workspace_path("Dixter999/cortex-project")
        assert "Dixter999-cortex-project" in path
        assert "/" not in path.split("cortex/projects/")[1].split("/repo")[0]

    def test_ends_with_repo(self) -> None:
        path = default_workspace_path("owner/myrepo")
        assert path.endswith("/repo")


# ---------------------------------------------------------------------------
# _sync_workspace
# ---------------------------------------------------------------------------


class TestSyncWorkspace:
    def test_skips_when_workspace_absent(self, tmp_path: Path) -> None:
        """Non-existent workspace is a soft skip, not an error."""
        missing = str(tmp_path / "no-such-repo")
        _sync_workspace(missing)  # must not raise

    def test_syncs_with_expected_git_calls(self, tmp_path: Path) -> None:
        """Happy path: git fetch + symbolic-ref + checkout + reset are called."""
        from unittest.mock import patch

        workspace = tmp_path / "repo"
        workspace.mkdir()

        symref_result = MagicMock(returncode=0, stdout="refs/remotes/origin/HEAD\n")
        symref_result.stdout = "refs/remotes/origin/HEAD\n"

        with patch("dap_cli.commands.cortex.subprocess.run") as mock_run:
            mock_run.return_value = symref_result
            _sync_workspace(str(workspace))

        calls = [c.args[0] for c in mock_run.call_args_list]
        assert calls[0] == ["git", "fetch", "origin"]
        assert calls[1] == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]
        assert calls[2][0:2] == ["git", "checkout"]
        assert calls[3][0:2] == ["git", "reset"]

    def test_non_fatal_on_git_error(self, tmp_path: Path) -> None:
        """CalledProcessError, TimeoutExpired, and OSError are caught, not raised."""
        import subprocess as _sp
        from unittest.mock import patch

        workspace = tmp_path / "repo"
        workspace.mkdir()

        for exc in [
            _sp.CalledProcessError(1, "git", stderr=b"auth error"),
            _sp.TimeoutExpired("git", 60),
            OSError("git not found"),
        ]:
            with patch("dap_cli.commands.cortex.subprocess.run", side_effect=exc):
                _sync_workspace(str(workspace))  # must not raise

    def test_expands_tilde(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """~/ in ws_path is expanded before the exists() check."""
        from unittest.mock import patch

        monkeypatch.setenv("HOME", str(tmp_path))
        workspace = tmp_path / "repo"
        workspace.mkdir()

        with patch("dap_cli.commands.cortex.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="refs/remotes/origin/HEAD\n")
            _sync_workspace("~/repo")  # should resolve to tmp_path/repo

        assert mock_run.called


# ---------------------------------------------------------------------------
# Bundle loading
# ---------------------------------------------------------------------------


class TestLoadCortexBundle:
    def test_raises_import_error_when_package_missing(self) -> None:
        with patch("importlib.resources.files") as mock_files:
            mock_files.side_effect = ModuleNotFoundError("No module named 'cortex'")
            with pytest.raises(ImportError, match=r"cortex.*package must be installed"):
                load_cortex_bundle()

    def test_returns_parsed_json(self) -> None:
        fake_bundle = {"schema_version": "pipeline-export/1", "pipeline": {"name": "test"}}
        mock_resource = MagicMock()
        mock_resource.read_text.return_value = json.dumps(fake_bundle)

        with patch("importlib.resources.files") as mock_files:
            mock_files.return_value.__truediv__.return_value = mock_resource
            result = load_cortex_bundle()

        assert result == fake_bundle


# ---------------------------------------------------------------------------
# Pipeline import - idempotent
# ---------------------------------------------------------------------------


class TestEnsurePipelineImported:
    def test_reuses_existing_pipeline(self) -> None:
        existing = {"items": [{"id": "pipe-123", "name": "cortex-full"}], "total": 1}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = MagicMock(
                status_code=200, json=MagicMock(return_value=existing)
            )
            mock_client.get.return_value.raise_for_status = MagicMock()
            mock_client_cls.return_value = mock_client

            pipeline_id = ensure_pipeline_imported(
                "http://localhost:7333",
                {"schema_version": "pipeline-export/1", "pipeline": {"name": "cortex-full"}},
            )

        assert pipeline_id == "pipe-123"

    def test_imports_when_not_found(self) -> None:
        list_response = {"items": [], "total": 0}
        import_response = {"id": "new-pipe-456", "name": "cortex-full"}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=list_response),
            )
            mock_client.get.return_value.raise_for_status = MagicMock()
            mock_client.post.return_value = MagicMock(
                status_code=201,
                json=MagicMock(return_value=import_response),
            )
            mock_client_cls.return_value = mock_client

            pipeline_id = ensure_pipeline_imported(
                "http://localhost:7333",
                {
                    "schema_version": "pipeline-export/1",
                    "pipeline": {"name": "cortex-full"},
                    "bundled_agents": {},
                },
            )

        assert pipeline_id == "new-pipe-456"


# ---------------------------------------------------------------------------
# CLI smoke tests - project run/approve/reject/state
# ---------------------------------------------------------------------------


class TestCliProjectCommands:
    """Smoke tests for the Typer CLI layer - verify routing and argument parsing."""

    def test_project_no_args_shows_help(self) -> None:
        result = runner.invoke(app, ["project"])
        # no_args_is_help=True - may return 0 or 2 depending on Typer version
        assert result.exit_code in (0, 2)
        assert "run" in result.output
        assert "approve" in result.output

    def test_project_run_unknown_pipeline(self) -> None:
        result = runner.invoke(
            app, ["project", "run", "unknown-pipeline", "https://github.com/o/r/issues/1"]
        )
        assert result.exit_code != 0
        assert "Unknown pipeline type" in result.output

    def test_project_approve_unknown_pipeline(self) -> None:
        result = runner.invoke(app, ["project", "approve", "unknown", "run-id-123"])
        assert result.exit_code != 0
        assert "Unknown pipeline type" in result.output

    def test_project_reject_unknown_pipeline(self) -> None:
        result = runner.invoke(app, ["project", "reject", "unknown", "run-id-123"])
        assert result.exit_code != 0
        assert "Unknown pipeline type" in result.output

    def test_project_state_unknown_pipeline(self) -> None:
        result = runner.invoke(app, ["project", "state", "unknown", "run-id-123"])
        assert result.exit_code != 0
        assert "Unknown pipeline type" in result.output

    def test_project_run_cortex_bad_url(self) -> None:
        """cortex_run should fail fast on unparseable URL before hitting engine."""
        with patch("dap_cli.commands.cortex.check_engine"):
            result = runner.invoke(
                app,
                ["project", "run", "cortex", "not-a-valid-url"],
            )
        assert result.exit_code != 0
        assert "Cannot parse" in result.output or result.exit_code == 1

    def test_project_run_cortex_engine_down(self) -> None:
        """Should fail with clear message when engine is not reachable."""
        with patch("dap_cli.commands.cortex.check_engine", side_effect=SystemExit(1)):
            result = runner.invoke(
                app,
                [
                    "project",
                    "run",
                    "cortex",
                    "https://github.com/Dixter999/cortex-project/issues/1",
                ],
            )
        assert result.exit_code == 1

    def test_project_run_cortex_no_bundle(self) -> None:
        """Should fail with helpful ImportError when cortex package not installed."""
        with (
            patch("dap_cli.commands.cortex.check_engine"),
            patch(
                "dap_cli.commands.cortex.load_cortex_bundle",
                side_effect=ImportError("cortex package must be installed"),
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "project",
                    "run",
                    "cortex",
                    "https://github.com/Dixter999/cortex-project/issues/1",
                ],
            )
        assert result.exit_code == 1
        assert "cortex" in result.output.lower()


# ---------------------------------------------------------------------------
# cortex_approve / cortex_reject / cortex_state unit tests
# ---------------------------------------------------------------------------


class TestCortexApprove:
    def test_not_paused_exits(self) -> None:
        with (
            patch("dap_cli.commands.cortex._get_run", return_value={"final_status": "running"}),
            pytest.raises(SystemExit),
        ):
            cortex_approve("run-abc", "http://localhost:7333")

    def test_approves_paused_run(self) -> None:
        mock_approve = MagicMock()
        with (
            patch(
                "dap_cli.commands.cortex._get_run",
                return_value={"final_status": "paused"},
            ),
            patch("dap_cli.commands.cortex._find_pending_gate", return_value="gate-phase1"),
            patch("dap_cli.commands.cortex._approve_gate", mock_approve),
        ):
            cortex_approve("run-abc", "http://localhost:7333")
        mock_approve.assert_called_once_with("http://localhost:7333", "run-abc", "gate-phase1")


class TestCortexReject:
    def test_not_paused_exits(self) -> None:
        with (
            patch("dap_cli.commands.cortex._get_run", return_value={"final_status": "running"}),
            pytest.raises(SystemExit),
        ):
            cortex_reject("run-abc", "reason", "http://localhost:7333")

    def test_rejects_paused_run(self) -> None:
        mock_reject = MagicMock()
        with (
            patch(
                "dap_cli.commands.cortex._get_run",
                return_value={"final_status": "paused"},
            ),
            patch("dap_cli.commands.cortex._find_pending_gate", return_value="gate-phase1"),
            patch("dap_cli.commands.cortex._reject_gate", mock_reject),
        ):
            cortex_reject("run-abc", "not good enough", "http://localhost:7333")
        mock_reject.assert_called_once_with(
            "http://localhost:7333", "run-abc", "gate-phase1", "not good enough"
        )


class TestCortexState:
    def test_prints_run_info(self) -> None:
        run_data = {
            "final_status": "paused",
            "pipeline_id": "pipe-123",
            "pipeline_version": 1,
            "project_id": "proj-456",
            "started_at": "2026-05-07T10:00:00Z",
            "ended_at": None,
        }
        with patch("dap_cli.commands.cortex._get_run", return_value=run_data):
            # Should not raise
            cortex_state("run-abc-def-ghi", "http://localhost:7333")


# ---------------------------------------------------------------------------
# Live per-node progress formatter (#662 Phase 1)
# ---------------------------------------------------------------------------


class TestFormatProgress:
    def test_renders_current_node_checklist_and_elapsed(self) -> None:
        run = {
            "final_status": "running",
            "current_node": "coder",
            "node_statuses": {
                "task_selector": "success",
                "prompt_builder": "success",
                "coder": "running",
                "verifier": "pending",
                "pr_merger": "pending",
            },
        }
        out = _format_progress(run, 252.0)

        # Header mentions the current node.
        assert "coder" in out
        # Elapsed formatted as minutes/seconds (252s -> 4m 12s).
        assert "4m" in out
        # Per-status glyphs present.
        assert "✓" in out  # success glyph for done nodes
        assert "▶" in out  # running glyph for coder
        assert "·" in out  # pending glyph for the rest
        # Every node id rendered.
        for node_id in run["node_statuses"]:
            assert node_id in out
        # Insertion / execution order is preserved.
        positions = [out.index(node_id) for node_id in run["node_statuses"]]
        assert positions == sorted(positions)

    def test_empty_node_statuses_header_only(self) -> None:
        run = {
            "final_status": "running",
            "current_node": "coder",
            "node_statuses": {},
        }
        # Must not crash and should still render the header node.
        out = _format_progress(run, 5.0)
        assert "coder" in out

    def test_no_current_node_falls_back_to_final_status(self) -> None:
        run = {
            "final_status": "success",
            "current_node": None,
            "node_statuses": {"task_selector": "success"},
        }
        out = _format_progress(run, 3661.0)
        # Falls back to final_status when no current node.
        assert "success" in out.lower() or "SUCCESS" in out
        # 3661s -> 1h 1m 1s — hours component shown.
        assert "1h" in out

    def test_unknown_status_uses_neutral_glyph(self) -> None:
        run = {
            "final_status": "running",
            "current_node": "mystery",
            "node_statuses": {"mystery": "warp-speed"},
        }
        # Unknown status must not crash and the node id still appears.
        out = _format_progress(run, 1.0)
        assert "mystery" in out


# ---------------------------------------------------------------------------
# Poll loop with live progress view (#662 Phase 1)
# ---------------------------------------------------------------------------


class TestPollUntilSettledProgress:
    def test_progress_view_preserves_control_flow(self) -> None:
        runs = [
            {"final_status": "running", "current_node": "coder", "node_statuses": {"coder": "running"}},
            {"final_status": "success", "current_node": None, "node_statuses": {"coder": "success"}},
        ]
        with (
            patch("dap_cli.commands.cortex._get_run", side_effect=runs),
            patch("dap_cli.commands.cortex.time.sleep", return_value=None),
        ):
            status, last_run = _poll_until_settled(
                "http://localhost:7333", "run-abc", "Running pipeline...", show_progress=True
            )
        assert status == "success"
        assert last_run["final_status"] == "success"

    def test_no_progress_path_still_settles(self) -> None:
        runs = [
            {"final_status": "running", "current_node": "coder", "node_statuses": {"coder": "running"}},
            {"final_status": "success", "current_node": None, "node_statuses": {"coder": "success"}},
        ]
        with (
            patch("dap_cli.commands.cortex._get_run", side_effect=runs),
            patch("dap_cli.commands.cortex.time.sleep", return_value=None),
        ):
            status, last_run = _poll_until_settled(
                "http://localhost:7333", "run-abc", "Running pipeline...", show_progress=False
            )
        assert status == "success"
        assert last_run["final_status"] == "success"

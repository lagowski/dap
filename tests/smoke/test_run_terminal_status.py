"""Tests for terminal final_status handling (#381).

The orchestrator reads ``final_state.final_status`` after the runner
finishes. The default value on a fresh ``PipelineState`` is
``"running"``. Previously this got silently coerced into ``"success"``,
which masked the real failure mode (a node like cortex's ``pr-merger``
finishing without updating state on refusal).

The new behaviour is opt-in per pipeline via
``PipelineDefaults.requires_terminal_final_status``:

- **Default (opt-out)**: a non-terminal ``final_status`` at the end of
  a runner-completed run still coerces to ``success``. Preserves the
  historical "no node raised" = "successful run" contract for generic
  pipelines that don't manage state-level ``final_status``.
- **Opt-in (cortex bundle sets the flag)**: a residual ``running``
  means *no node ever set a terminal status* — surface that as
  ``failed`` with a clear ``failure_reason``. ``paused`` at this
  point is also a bug (real pauses raise ``RunnerInterrupt``).
  Unknown values fall into the same defensive bucket.
- Explicit ``success`` / ``failed`` / ``aborted`` from a node pass
  through unchanged regardless of the flag — the orchestrator must
  never overwrite a deliberate terminal status.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.execution.run_orchestrator import _resolve_terminal_status
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import RunORM
from dap_types import PipelineState
from fastapi.testclient import TestClient

# --------------------------------------------------------------------- #
# Unit-level: _resolve_terminal_status pure helper
# --------------------------------------------------------------------- #


def _state(final_status: str) -> PipelineState:
    """Build a minimal PipelineState with the given final_status field."""
    return PipelineState(
        run_id="r1",
        repo="",
        branch="",
        final_status=final_status,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "explicit",
    ["success", "failed", "aborted"],
)
@pytest.mark.parametrize("requires_terminal", [True, False])
def test_resolve_terminal_status_passes_through_explicit_terminal(
    explicit: str, requires_terminal: bool
) -> None:
    """When the runner left a real terminal status, return it verbatim
    regardless of the opt-in flag — the orchestrator must never overwrite
    a deliberate node-set status."""
    status, reason = _resolve_terminal_status(_state(explicit), requires_terminal=requires_terminal)
    assert status == explicit
    assert reason is None


def test_resolve_terminal_status_running_default_coerces_to_success() -> None:
    """Without the opt-in flag, ``running`` at end → ``success`` (legacy
    contract for pipelines that don't manage state-level final_status)."""
    status, reason = _resolve_terminal_status(_state("running"), requires_terminal=False)
    assert status == "success"
    assert reason is None


def test_resolve_terminal_status_running_opt_in_becomes_failed() -> None:
    """With ``requires_terminal=True`` (cortex bundle opts in), a residual
    ``running`` means *no node ever set a terminal status*. Treat that as
    a buggy pipeline, not a silent success."""
    status, reason = _resolve_terminal_status(_state("running"), requires_terminal=True)
    assert status == "failed"
    assert reason is not None
    assert "final_status" in reason  # operator-readable explanation


def test_resolve_terminal_status_paused_opt_in_becomes_failed() -> None:
    """``paused`` at orchestrator-completion time is impossible to reach
    cleanly — RunnerInterrupt would have handled a real pause. With the
    opt-in flag on, surface the residual as a failure."""
    status, reason = _resolve_terminal_status(_state("paused"), requires_terminal=True)
    assert status == "failed"
    assert reason is not None


def test_resolve_terminal_status_unknown_opt_in_becomes_failed() -> None:
    """Defensive: any value outside the documented enum → ``failed`` when
    the opt-in flag is on. Pydantic's Literal validation usually prevents
    this from reaching us, but if a future schema migration ever widens
    ``FinalStatus`` this path keeps the contract intact instead of
    silently green-lighting.
    """
    # Bypass Pydantic validation by constructing the state then
    # mutating the field.
    state = _state("success")
    state.__dict__["final_status"] = "weird-new-status"
    status, reason = _resolve_terminal_status(state, requires_terminal=True)
    assert status == "failed"
    assert reason is not None


def test_resolve_terminal_status_unknown_default_coerces_to_success() -> None:
    """Without the opt-in, even unknown values still coerce to success
    (matches the historical contract — only opt-in pipelines get the
    defensive treatment)."""
    state = _state("success")
    state.__dict__["final_status"] = "weird-new-status"
    status, reason = _resolve_terminal_status(state, requires_terminal=False)
    assert status == "success"
    assert reason is None


# --------------------------------------------------------------------- #
# Integration-level: failure_reason persists on the Run row
# --------------------------------------------------------------------- #


def test_finalize_run_records_failure_reason() -> None:
    """``finalize_run`` must persist ``failure_reason`` when supplied.

    Used by the orchestrator to record *why* a run that "looked
    successful" at the node level ended up failed (e.g.
    "pipeline finished without setting final_status").
    """
    tmp = tempfile.mkdtemp(prefix="dap-terminal-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="terminal-test-secret",
    )
    app = create_app(config)

    # Bootstrap a minimal run row in "running" state so finalize_run
    # has something to update. TestClient as a context manager runs
    # the FastAPI lifespan, which is what populates
    # ``app.state.session_factory`` — we don't need the client itself,
    # just the side effect of bringing the app up.
    from datetime import UTC, datetime

    from dap_engine.persistence.models import PipelineORM, PipelineVersionORM

    now = datetime.now(UTC)
    with TestClient(app):
        factory = app.state.session_factory

        with factory() as session:
            pipeline = PipelineORM(
                id="reason-pipe",
                name="Reason Pipe",
                description="",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
            pipeline_v = PipelineVersionORM(
                id="reason-pipe-v1",
                pipeline_id="reason-pipe",
                version=1,
                name="Reason Pipe",
                description="",
                schema_version="langgraph/1.0",
                state_schema_ref="PipelineState.v1",
                entry_point="n1",
                nodes=[],
                edges=[],
                defaults={
                    "max_attempts": 3,
                    "budget_limit_usd": 5.0,
                    "approval_required_nodes": [],
                },
                created_at=now,
            )
            run = RunORM(
                id="reason-run",
                pipeline_id="reason-pipe",
                pipeline_version=1,
                trigger_source="api",
                initial_state={
                    "run_id": "reason-run",
                    "repo": "",
                    "branch": "",
                    "available_issues": [],
                    "selected_issue_ids": [],
                    "tests_generated": False,
                    "test_files": [],
                    "test_generation_errors": [],
                    "max_attempts": 3,
                    "attempt": 0,
                    "tests_passed": False,
                    "last_test_output": "",
                    "modified_files": [],
                    "implementation_notes": None,
                    "verification_status": "pending",
                    "verification_reason": None,
                    "final_status": "running",
                },
                current_node=None,
                node_statuses={},
                final_status="running",
                started_at=now,
                ended_at=None,
                tokens_used=0,
                cost_usd=0.0,
            )
            session.add(pipeline)
            session.add(pipeline_v)
            session.add(run)
            session.commit()

        with factory() as session:
            repo.finalize_run(
                session,
                "reason-run",
                final_status="failed",
                failure_reason="pipeline finished without setting final_status",
            )
            session.commit()

        with factory() as session:
            run_row = session.get(RunORM, "reason-run")
            assert run_row is not None
            assert run_row.final_status == "failed"
            assert run_row.failure_reason == "pipeline finished without setting final_status"
            assert run_row.ended_at is not None

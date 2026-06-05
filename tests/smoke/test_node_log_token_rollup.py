"""Regression tests for #637 — per-node audit tokens/cost roll up into run totals.

python-func adapters (cortex runs as python-func) leave ``RuntimeResult.tokens_used``
and ``cost_usd`` as ``None``; the real per-node usage is carried in the audit dict at
``result.structured["audit"]``. Before the fix that audit data only landed in
``node_execution_logs.extra_data`` and was never extracted into the summed
``tokens_used`` / ``cost_usd`` columns that ``_compute_run_totals`` aggregates, so
``runs.tokens_used`` / ``runs.cost_usd`` stayed at 0.

These tests drive ``_save_execution_log`` directly with a real session and a
constructed ``NodeContext``, then assert the persisted ``NodeExecutionLogORM`` row and
the run rollup.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.execution.node_executor import NodeContext, _save_execution_log
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import (
    AgentORM,
    AgentVersionORM,
    NodeExecutionLogORM,
    RunORM,
    UserORM,
)
from dap_engine.persistence.runs import _compute_run_totals
from dap_runtimes import RuntimeRegistry
from dap_types import PipelineState, RuntimeResult
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000637")


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    tmp = tempfile.mkdtemp(prefix="dap-rollup-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app):
        yield app.state.session_factory


def _seed_agent(session: Session, *, runtime_id: str = "python-func") -> tuple[AgentORM, AgentVersionORM]:
    now = datetime.now(UTC)
    agent = AgentORM(
        id=f"agent-{runtime_id}",
        name="Audit Agent",
        role="task_selector",
        current_version=1,
        created_at=now,
        updated_at=now,
    )
    version = AgentVersionORM(
        id=f"agent-version-{runtime_id}",
        agent_id=agent.id,
        version=1,
        name="Audit Agent",
        runtime_id=runtime_id,
        runtime_config={},
        prompt_template="<agent_prompt><role>x</role></agent_prompt>",
        input_schema=[],
        output_schema=[],
        constraints=[],
        budget_limit_usd=None,
        timeout_ms=30_000,
        created_at=now,
    )
    session.add(agent)
    session.add(version)
    session.commit()
    return agent, version


def _seed_run(session: Session) -> str:
    if session.get(UserORM, _OWNER_ID) is None:
        now = datetime.now(UTC)
        session.add(
            UserORM(
                id=_OWNER_ID,
                email="rollup-test@local.dev",
                hashed_password="x" * 64,
                is_active=False,
                is_superuser=False,
                is_verified=False,
                created_at=now,
                updated_at=now,
                deleted_at=None,
                last_login_at=None,
            )
        )
        session.flush()
    initial_state = PipelineState(run_id="pending", repo="test", branch="main")
    run = repo.create_run(
        session,
        user_id=_OWNER_ID,
        pipeline_id="pipe-1",
        pipeline_version=1,
        trigger_source="cli",
        initial_state=initial_state,
    )
    session.commit()
    return run.id


def _save_log(
    *,
    session: Session,
    run_id: str,
    agent: AgentORM,
    version: AgentVersionORM,
    registry: RuntimeRegistry,
    node_id: str,
    result: RuntimeResult,
) -> str:
    execution_id = str(uuid.uuid4())
    started = datetime.now(UTC)
    ctx = NodeContext(
        run_id=run_id,
        node_id=node_id,
        agent=agent,
        agent_version=version,
        registry=registry,
        session=session,
        runtime_id="python-func",
    )
    _save_execution_log(
        ctx=ctx,
        execution_id=execution_id,
        started_at=started,
        ended_at=datetime.now(UTC),
        prompt_xml="<p/>",
        result=result,
    )
    session.commit()
    return execution_id


def test_audit_fallback_tokens_used_key(factory: sessionmaker[Session]) -> None:
    """audit ``tokens_used``/``cost_usd`` fall through to the summed columns."""
    registry = RuntimeRegistry()
    with factory() as session:
        agent, version = _seed_agent(session)
        run_id = _seed_run(session)
        result = RuntimeResult(
            success=True,
            output="ok",
            tokens_used=None,
            cost_usd=None,
            structured={"audit": {"tokens_used": 1234, "cost_usd": 0.56}},
        )
        log_id = _save_log(
            session=session,
            run_id=run_id,
            agent=agent,
            version=version,
            registry=registry,
            node_id="n1",
            result=result,
        )

    with factory() as session:
        log = session.get(NodeExecutionLogORM, log_id)
        assert log is not None
        assert log.tokens_used == 1234
        assert log.cost_usd == pytest.approx(0.56)
        # Audit metadata must still be preserved in extra_data.
        assert log.extra_data == {"tokens_used": 1234, "cost_usd": 0.56}


def test_audit_fallback_input_output_split(factory: sessionmaker[Session]) -> None:
    """``input_tokens`` + ``output_tokens`` are summed when no ``tokens_used`` key."""
    registry = RuntimeRegistry()
    with factory() as session:
        agent, version = _seed_agent(session)
        run_id = _seed_run(session)
        result = RuntimeResult(
            success=True,
            output="ok",
            tokens_used=None,
            cost_usd=None,
            structured={"audit": {"input_tokens": 800, "output_tokens": 434}},
        )
        log_id = _save_log(
            session=session,
            run_id=run_id,
            agent=agent,
            version=version,
            registry=registry,
            node_id="n1",
            result=result,
        )

    with factory() as session:
        log = session.get(NodeExecutionLogORM, log_id)
        assert log is not None
        assert log.tokens_used == 1234


def test_result_fields_win_over_audit(factory: sessionmaker[Session]) -> None:
    """A real ``result.tokens_used`` always wins; audit is fallback only."""
    registry = RuntimeRegistry()
    with factory() as session:
        agent, version = _seed_agent(session)
        run_id = _seed_run(session)
        result = RuntimeResult(
            success=True,
            output="ok",
            tokens_used=500,
            cost_usd=1.25,
            structured={"audit": {"tokens_used": 9999, "cost_usd": 88.0}},
        )
        log_id = _save_log(
            session=session,
            run_id=run_id,
            agent=agent,
            version=version,
            registry=registry,
            node_id="n1",
            result=result,
        )

    with factory() as session:
        log = session.get(NodeExecutionLogORM, log_id)
        assert log is not None
        assert log.tokens_used == 500
        assert log.cost_usd == pytest.approx(1.25)


def test_no_audit_none_result_stays_zero(factory: sessionmaker[Session]) -> None:
    """No audit + ``None`` result fields → unchanged 0 behaviour."""
    registry = RuntimeRegistry()
    with factory() as session:
        agent, version = _seed_agent(session)
        run_id = _seed_run(session)
        result = RuntimeResult(
            success=True,
            output="ok",
            tokens_used=None,
            cost_usd=None,
            structured=None,
        )
        log_id = _save_log(
            session=session,
            run_id=run_id,
            agent=agent,
            version=version,
            registry=registry,
            node_id="n1",
            result=result,
        )

    with factory() as session:
        log = session.get(NodeExecutionLogORM, log_id)
        assert log is not None
        assert log.tokens_used == 0
        assert log.cost_usd == 0.0
        assert log.extra_data is None


def test_run_rollup_sums_audit_tokens(factory: sessionmaker[Session]) -> None:
    """Per-node audit usage rolls up into the run totals (#637 end-to-end)."""
    registry = RuntimeRegistry()
    with factory() as session:
        agent, version = _seed_agent(session)
        run_id = _seed_run(session)
        for node_id, audit in (
            ("n1", {"tokens_used": 1000, "cost_usd": 0.10}),
            ("n2", {"input_tokens": 200, "output_tokens": 34, "cost_usd": 0.46}),
        ):
            _save_log(
                session=session,
                run_id=run_id,
                agent=agent,
                version=version,
                registry=registry,
                node_id=node_id,
                result=RuntimeResult(
                    success=True,
                    output="ok",
                    tokens_used=None,
                    cost_usd=None,
                    structured={"audit": audit},
                ),
            )

    with factory() as session:
        tokens, cost = _compute_run_totals(session, run_id)
        assert tokens == 1234
        assert cost == pytest.approx(0.56)

        # And the finalized run row surfaces the same non-zero totals.
        repo.finalize_run(session, run_id, final_status="success")
        session.commit()
        run = session.get(RunORM, run_id)
        assert run is not None
        assert run.tokens_used == 1234
        assert run.cost_usd == pytest.approx(0.56)

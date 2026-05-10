"""Integration test for PipelineRunner — builds graph from DB pipeline and runs end-to-end.

Uses a stub runtime adapter so no real LLM calls happen.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.execution import PipelineRunner
from dap_engine.persistence.models import (
    AgentORM,
    AgentVersionORM,
    PipelineORM,
    PipelineVersionORM,
    UserORM,
)
from dap_runtimes import RuntimeRegistry
from dap_types import (
    HealthStatus,
    PipelineState,
    RuntimeKind,
    RuntimeResult,
    RuntimeTask,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


class StubAdapter:
    """In-memory adapter that returns canned RuntimeResults — no I/O."""

    def __init__(
        self,
        *,
        adapter_id: str = "stub",
        outputs: list[RuntimeResult] | None = None,
    ) -> None:
        self.id = adapter_id
        self.display_name = f"Stub: {adapter_id}"
        self.kind: RuntimeKind = "api"
        self._outputs = outputs or []
        self.calls: list[RuntimeTask] = []

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True, version="stub")

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        self.calls.append(task)
        if not self._outputs:
            return RuntimeResult(success=True, output="ok", duration_ms=1)
        return self._outputs.pop(0)


@pytest.fixture
def app_session_factory() -> Iterator[tuple[TestClient, sessionmaker[Session], RuntimeRegistry]]:
    tmp = tempfile.mkdtemp(prefix="dap-runner-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as client:
        yield client, app.state.session_factory, app.state.runtime_registry


def _seed_agent(
    session: Session,
    *,
    runtime_id: str = "stub",
    template: str = "<agent_prompt><role>{{ run_id }}</role></agent_prompt>",
) -> str:
    now = datetime.now(UTC)
    agent_id = f"agent-{runtime_id}"
    agent = AgentORM(
        id=agent_id,
        name="Stub Agent",
        role="task_selector",
        current_version=1,
        created_at=now,
        updated_at=now,
    )
    version = AgentVersionORM(
        id=f"agent-version-{runtime_id}",
        agent_id=agent_id,
        version=1,
        name="Stub Agent",
        runtime_id=runtime_id,
        runtime_config={},
        prompt_template=template,
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
    return agent_id


def _seed_pipeline(
    session: Session,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    entry_point: str,
) -> tuple[PipelineORM, PipelineVersionORM]:
    now = datetime.now(UTC)
    pipeline = PipelineORM(
        id="pipe-1",
        name="Test Pipeline",
        description="",
        current_version=1,
        created_at=now,
        updated_at=now,
    )
    version = PipelineVersionORM(
        id="pipe-version-1",
        pipeline_id="pipe-1",
        version=1,
        name="Test Pipeline",
        description="",
        schema_version="langgraph/1.0",
        state_schema_ref="PipelineState.v1",
        entry_point=entry_point,
        nodes=nodes,
        edges=edges,
        defaults={"max_attempts": 3, "budget_limit_usd": 5.0, "approval_required_nodes": []},
        created_at=now,
    )
    session.add(pipeline)
    session.add(version)
    session.commit()
    return pipeline, version


async def _run_pipeline(
    factory: sessionmaker[Session],
    registry: RuntimeRegistry,
    pipeline: PipelineORM,
    version: PipelineVersionORM,
) -> PipelineState:
    from dap_engine.persistence import repository as repo

    with factory() as session:
        # Re-fetch ORM rows in this session
        pipeline_orm = session.get(PipelineORM, pipeline.id)
        version_orm = session.get(PipelineVersionORM, version.id)
        assert pipeline_orm is not None
        assert version_orm is not None

        # Seed a synthetic owner — the runner doesn't care who owns the
        # row, but ``runs.user_id`` has a FK to ``users.id`` so the row
        # must exist. Single shared user keeps the seed minimal.
        owner_id = uuid.uuid4()
        if session.get(UserORM, owner_id) is None:
            session.add(
                UserORM(
                    id=owner_id,
                    email=f"runner-{owner_id}@local.dev",
                    hashed_password="x" * 64,  # unhashable / unusable
                    is_active=False,
                    is_superuser=False,
                    is_verified=False,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    deleted_at=None,
                    last_login_at=None,
                )
            )
            session.flush()

        # Seed a Run row so node_execution_logs FK is satisfied.
        initial_state = PipelineState(run_id="pending", repo="test", branch="main")
        run = repo.create_run(
            session,
            user_id=owner_id,
            pipeline_id=pipeline_orm.id,
            pipeline_version=version_orm.version,
            trigger_source="cli",
            initial_state=initial_state,
        )
        initial_state = initial_state.model_copy(update={"run_id": run.id})

        runner = PipelineRunner(session=session, registry=registry)
        result = await runner.run(
            run_id=run.id,
            pipeline_orm=pipeline_orm,
            pipeline_version_orm=version_orm,
            initial_state=initial_state,
        )
        session.commit()
        return result


async def test_runner_executes_single_node(
    app_session_factory: tuple[TestClient, sessionmaker[Session], RuntimeRegistry],
) -> None:
    _client, factory, registry = app_session_factory

    stub = StubAdapter()
    registry.register(stub)

    with factory() as session:
        agent_id = _seed_agent(session, runtime_id="stub")
        pipeline, version = _seed_pipeline(
            session,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
            ],
            edges=[
                {"id": "e1", "source": "n1", "target": "__end__"},
            ],
            entry_point="n1",
        )

    final_state = await _run_pipeline(factory, registry, pipeline, version)

    assert len(stub.calls) == 1
    # Default success → final_status remains "running" (no diff applied).
    # Caller is responsible for setting final_status = success after run.
    assert final_state.repo == "test"


async def test_runner_two_node_linear(
    app_session_factory: tuple[TestClient, sessionmaker[Session], RuntimeRegistry],
) -> None:
    _client, factory, registry = app_session_factory

    stub = StubAdapter(adapter_id="stub-linear")
    registry.register(stub)

    with factory() as session:
        agent_id = _seed_agent(session, runtime_id="stub-linear")
        pipeline, version = _seed_pipeline(
            session,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n2", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            edges=[
                {"id": "e1", "source": "n1", "target": "n2"},
                {"id": "e2", "source": "n2", "target": "__end__"},
            ],
            entry_point="n1",
        )

    await _run_pipeline(factory, registry, pipeline, version)
    assert len(stub.calls) == 2  # both nodes called


async def test_runner_conditional_edge(
    app_session_factory: tuple[TestClient, sessionmaker[Session], RuntimeRegistry],
) -> None:
    """Pipeline: n1 → (if tests_passed → end, else → n2). Stub n1 sets tests_passed=True."""
    _client, factory, registry = app_session_factory

    # Stub returns structured output that merges into state
    stub = StubAdapter(
        adapter_id="stub-cond",
        outputs=[
            RuntimeResult(
                success=True,
                output="done",
                structured={"tests_passed": True},
                duration_ms=1,
            ),
        ],
    )
    registry.register(stub)

    with factory() as session:
        agent_id = _seed_agent(session, runtime_id="stub-cond")
        pipeline, version = _seed_pipeline(
            session,
            nodes=[
                {"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
                {"id": "n2", "agent_id": agent_id, "position": {"x": 100, "y": 0}},
            ],
            edges=[
                {
                    "id": "e1",
                    "source": "n1",
                    "target": "__end__",
                    "condition": {
                        "type": "comparison",
                        "field": "tests_passed",
                        "operator": "==",
                        "value": True,
                    },
                },
                {"id": "e2", "source": "n1", "target": "n2"},
                {"id": "e3", "source": "n2", "target": "__end__"},
            ],
            entry_point="n1",
        )

    final_state = await _run_pipeline(factory, registry, pipeline, version)

    # Only n1 should have been called; conditional edge sent us to END.
    assert len(stub.calls) == 1
    assert final_state.tests_passed is True


async def test_runner_parses_role_output_into_state(
    app_session_factory: tuple[TestClient, sessionmaker[Session], RuntimeRegistry],
) -> None:
    """Per-role parser turns adapter text output into typed state diff."""
    _client, factory, registry = app_session_factory

    stub = StubAdapter(
        adapter_id="role-parse-stub",
        outputs=[
            RuntimeResult(
                success=True,
                output='<output>{"selected_issue_ids": [101, 202]}</output>',
                duration_ms=1,
            ),
        ],
    )
    registry.register(stub)

    with factory() as session:
        agent_id = _seed_agent(session, runtime_id="role-parse-stub")
        pipeline, version = _seed_pipeline(
            session,
            nodes=[{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            edges=[{"id": "e1", "source": "n1", "target": "__end__"}],
            entry_point="n1",
        )

    final_state = await _run_pipeline(factory, registry, pipeline, version)

    # task_selector role declares selected_issue_ids — parser writes it into state.
    assert final_state.selected_issue_ids == [101, 202]


async def test_runner_unknown_agent_raises(
    app_session_factory: tuple[TestClient, sessionmaker[Session], RuntimeRegistry],
) -> None:
    from dap_engine.execution import RunnerError

    _client, factory, registry = app_session_factory

    with factory() as session:
        pipeline, version = _seed_pipeline(
            session,
            nodes=[{"id": "n1", "agent_id": "missing-agent", "position": {"x": 0, "y": 0}}],
            edges=[{"id": "e1", "source": "n1", "target": "__end__"}],
            entry_point="n1",
        )

    with pytest.raises(RunnerError, match="unknown agent"):
        await _run_pipeline(factory, registry, pipeline, version)

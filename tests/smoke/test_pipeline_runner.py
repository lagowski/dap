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
    OutputCallback,
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

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        self.calls.append(task)
        if not self._outputs:
            return RuntimeResult(success=True, output="ok", duration_ms=1)
        return self._outputs.pop(0)


class StreamingStubAdapter:
    """Adapter that streams a fixed list of fragments via ``on_output`` before
    returning, exercising the node_executor → ``node_output_chunks`` wiring
    (#662 Phase 3b-2b).

    The fragments are emitted synchronously on the event loop; the node's flush
    task coalesces them and the guaranteed final flush on context exit persists
    them even though ``execute`` returns instantly (no need to wait the 0.5s
    flush interval).
    """

    def __init__(
        self,
        *,
        adapter_id: str = "streaming-stub",
        fragments: list[str] | None = None,
        success: bool = True,
    ) -> None:
        self.id = adapter_id
        self.display_name = f"Stub: {adapter_id}"
        self.kind: RuntimeKind = "api"
        self._fragments = fragments or []
        self._success = success
        self.calls: list[RuntimeTask] = []

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True, version="stub")

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        self.calls.append(task)
        if on_output is not None:
            for fragment in self._fragments:
                on_output(fragment)
        if self._success:
            return RuntimeResult(success=True, output="ok", duration_ms=1)
        return RuntimeResult(success=False, output="", errors=["boom"], duration_ms=1)


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
        # must exist. Deterministic UUID so repeat ``_run_pipeline``
        # calls share the user instead of inserting a fresh row each
        # time (Copilot review on PR #313).
        owner_id = uuid.UUID("00000000-0000-0000-0000-00000000beef")
        if session.get(UserORM, owner_id) is None:
            session.add(
                UserORM(
                    id=owner_id,
                    email="runner-test@local.dev",
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
        # Commit the run-creation transaction before running the pipeline so
        # the harness mirrors production, where the run row is committed in the
        # request session before the background session executes nodes. Without
        # this, SQLite's single-writer lock is held by the open run-creation
        # transaction, deadlocking the concurrent ``node_output_chunks`` writes
        # the node streams via a separate short-lived session (#662).
        session.commit()

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


async def _seed_single_node_pipeline(
    factory: sessionmaker[Session],
    *,
    runtime_id: str,
) -> tuple[PipelineORM, PipelineVersionORM]:
    """Seed a one-node pipeline wired to *runtime_id* and return its ORM rows."""
    with factory() as session:
        agent_id = _seed_agent(session, runtime_id=runtime_id)
        return _seed_pipeline(
            session,
            nodes=[{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            edges=[{"id": "e1", "source": "n1", "target": "__end__"}],
            entry_point="n1",
        )


async def test_streamed_output_is_persisted_to_node_output_chunks(
    app_session_factory: tuple[TestClient, sessionmaker[Session], RuntimeRegistry],
) -> None:
    """A node whose adapter calls ``on_output("foo")`` then ``on_output("bar")``
    persists the concatenated text to ``node_output_chunks`` for that run, tagged
    with the run/node ids and a non-null ``execution_id`` matching the node's
    ``node_execution_logs`` row (#662 Phase 3b-2b — closes the streaming loop).
    """
    from dap_engine.persistence import repository as repo
    from dap_engine.persistence.models import NodeExecutionLogORM

    _client, factory, registry = app_session_factory

    stub = StreamingStubAdapter(adapter_id="stream-foobar", fragments=["foo", "bar"])
    registry.register(stub)

    pipeline, version = await _seed_single_node_pipeline(factory, runtime_id="stream-foobar")
    final_state = await _run_pipeline(factory, registry, pipeline, version)
    run_id = final_state.run_id

    with factory() as session:
        chunks = repo.list_output_chunks_since(session, run_id, after_id=0)
        log = session.query(NodeExecutionLogORM).filter_by(run_id=run_id, node_id="n1").one()

    assert "".join(c.content for c in chunks) == "foobar"
    assert {c.run_id for c in chunks} == {run_id}
    assert {c.node_id for c in chunks} == {"n1"}
    # Every chunk carries a non-null execution_id matching the node's log row.
    assert all(c.execution_id == log.id for c in chunks)
    assert log.id is not None


async def test_node_without_streamed_output_writes_no_chunks(
    app_session_factory: tuple[TestClient, sessionmaker[Session], RuntimeRegistry],
) -> None:
    """A node whose adapter never calls ``on_output`` produces zero
    ``node_output_chunks`` rows — the final flush must not write a spurious
    empty chunk (#662 Phase 3b-2b).
    """
    from dap_engine.persistence import repository as repo

    _client, factory, registry = app_session_factory

    stub = StreamingStubAdapter(adapter_id="stream-silent", fragments=[])
    registry.register(stub)

    pipeline, version = await _seed_single_node_pipeline(factory, runtime_id="stream-silent")
    final_state = await _run_pipeline(factory, registry, pipeline, version)

    with factory() as session:
        chunks = repo.list_output_chunks_since(session, final_state.run_id, after_id=0)
    assert chunks == []


async def test_streamed_output_persisted_even_when_node_fails(
    app_session_factory: tuple[TestClient, sessionmaker[Session], RuntimeRegistry],
) -> None:
    """Output buffered before a failing ``RuntimeResult`` is still persisted by
    the guaranteed final flush on context exit (#662 Phase 3b-2b).
    """
    from dap_engine.persistence import repository as repo

    _client, factory, registry = app_session_factory

    stub = StreamingStubAdapter(adapter_id="stream-fail", fragments=["partial"], success=False)
    registry.register(stub)

    pipeline, version = await _seed_single_node_pipeline(factory, runtime_id="stream-fail")
    final_state = await _run_pipeline(factory, registry, pipeline, version)

    with factory() as session:
        chunks = repo.list_output_chunks_since(session, final_state.run_id, after_id=0)
    assert "".join(c.content for c in chunks) == "partial"

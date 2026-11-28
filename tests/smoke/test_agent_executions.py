"""Per-agent execution log query — node_executions_for_agent (#697 slice 3)."""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import (
    AgentORM,
    AgentVersionORM,
    NodeExecutionLogORM,
    UserORM,
)
from dap_types import PipelineState
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000698")


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    tmp = tempfile.mkdtemp(prefix="dap-agent-exec-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app):
        yield app.state.session_factory


def _seed_agent(session: Session, agent_id: str) -> None:
    now = datetime.now(UTC)
    session.add(
        AgentORM(
            id=agent_id,
            name="Exec Agent",
            role="task_selector",
            current_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        AgentVersionORM(
            id=f"{agent_id}-v1",
            agent_id=agent_id,
            version=1,
            name="Exec Agent",
            runtime_id="python-func",
            runtime_config={},
            prompt_template="<agent_prompt><role>x</role></agent_prompt>",
            input_schema=[],
            output_schema=[],
            constraints=[],
            budget_limit_usd=None,
            timeout_ms=30_000,
            created_at=now,
        )
    )
    session.commit()


def _seed_run(session: Session) -> str:
    if session.get(UserORM, _OWNER_ID) is None:
        now = datetime.now(UTC)
        session.add(
            UserORM(
                id=_OWNER_ID,
                email="exec-test@local.dev",
                hashed_password="not-a-real-hash-placeholder",
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
    run = repo.create_run(
        session,
        user_id=_OWNER_ID,
        pipeline_id="pipe-1",
        pipeline_version=1,
        trigger_source="cli",
        initial_state=PipelineState(run_id="pending", repo="test", branch="main"),
    )
    session.commit()
    return run.id


def _log(
    session: Session,
    *,
    run_id: str,
    agent_id: str,
    node_id: str,
    started_at: datetime,
    status: str = "success",
) -> None:
    session.add(
        NodeExecutionLogORM(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id=node_id,
            agent_id=agent_id,
            runtime_id="python-func",
            started_at=started_at,
            ended_at=started_at,
            prompt_xml="<p/>",
            status=status,
        )
    )
    session.commit()


def test_executions_filtered_newest_first_and_paginated(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        _seed_agent(session, "agent-a")
        _seed_agent(session, "agent-b")
        run_id = _seed_run(session)
        base = datetime.now(UTC)
        _log(
            session,
            run_id=run_id,
            agent_id="agent-a",
            node_id="n1",
            started_at=base - timedelta(minutes=3),
        )
        _log(
            session,
            run_id=run_id,
            agent_id="agent-a",
            node_id="n2",
            started_at=base - timedelta(minutes=1),
            status="failed",
        )
        # A different agent's row must not leak in.
        _log(
            session,
            run_id=run_id,
            agent_id="agent-b",
            node_id="n3",
            started_at=base - timedelta(minutes=2),
        )

        rows, total = repo.node_executions_for_agent(session, "agent-a")
        assert total == 2
        assert all(r.agent_id == "agent-a" for r in rows)
        # newest first (n2 at -1m before n1 at -3m)
        assert [r.node_id for r in rows] == ["n2", "n1"]

        page, page_total = repo.node_executions_for_agent(session, "agent-a", limit=1, offset=1)
        assert page_total == 2
        assert [r.node_id for r in page] == ["n1"]


def test_executions_empty_for_unused_agent(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        _seed_agent(session, "agent-lonely")
        rows, total = repo.node_executions_for_agent(session, "agent-lonely")
        assert rows == []
        assert total == 0

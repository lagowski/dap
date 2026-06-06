"""Delete a run with strict children-first cascade — no orphaned rows (#700).

The central invariant: after deleting a run, *zero* rows reference its id in
any child table. We assert each child table is empty so a future child table
someone forgets to wire into the cascade is caught here.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import (
    AuditLogORM,
    NodeExecutionLogORM,
    NodeOutputChunkORM,
    StateSnapshotORM,
    UserORM,
)
from dap_engine.persistence.run_models import RunORM
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.smoke._auth import DEFAULT_TEST_EMAIL, authed_test_client

_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000700")
_OTHER_ID = uuid.UUID("00000000-0000-0000-0000-000000000701")


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    tmp = tempfile.mkdtemp(prefix="dap-run-delete-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app):
        yield app.state.session_factory


def _ensure_user(session: Session, user_id: uuid.UUID, email: str) -> None:
    if session.get(UserORM, user_id) is not None:
        return
    now = datetime.now(UTC)
    session.add(
        UserORM(
            id=user_id,
            email=email,
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


def _seed_run_with_children(
    session: Session,
    *,
    run_id: str,
    user_id: uuid.UUID,
    final_status: str = "success",
) -> None:
    now = datetime.now(UTC)
    session.add(
        RunORM(
            id=run_id,
            user_id=user_id,
            project_id=None,
            pipeline_id="pipe-1",
            pipeline_version=1,
            trigger_source="test",
            initial_state={},
            current_node=None,
            node_statuses={},
            final_status=final_status,
            started_at=now,
            tokens_used=0,
            cost_usd=0.0,
            created_at=now,
            updated_at=now,
        )
    )
    # Flush the run row before its children: NodeOutputChunkORM has no ORM
    # relationship to RunORM, so the unit-of-work won't reliably insert the
    # parent first under SQLite FK enforcement.
    session.flush()
    session.add(
        StateSnapshotORM(
            id=f"{run_id}-snap-1", run_id=run_id, node_id="n1", timestamp=now, state={}
        )
    )
    session.add(
        NodeExecutionLogORM(
            id=f"{run_id}-log-1",
            run_id=run_id,
            node_id="n1",
            agent_id="agent-1",
            runtime_id="python-func",
            started_at=now,
            ended_at=now,
            prompt_xml="",
            stdout="",
            stderr="",
            output_json=None,
            tokens_used=0,
            cost_usd=0.0,
            duration_ms=10,
            status="success",
            error_message=None,
        )
    )
    session.add(
        NodeOutputChunkORM(
            run_id=run_id, node_id="n1", stream="stdout", content="hello", created_at=now
        )
    )
    session.commit()


def _child_counts(session: Session, run_id: str) -> dict[str, int]:
    return {
        "state_snapshots": session.scalar(
            select(StateSnapshotORM).where(StateSnapshotORM.run_id == run_id).limit(1)
        )
        is not None,
        "node_execution_logs": session.scalar(
            select(NodeExecutionLogORM).where(NodeExecutionLogORM.run_id == run_id).limit(1)
        )
        is not None,
        "node_output_chunks": session.scalar(
            select(NodeOutputChunkORM).where(NodeOutputChunkORM.run_id == run_id).limit(1)
        )
        is not None,
    }


def test_delete_run_removes_run_and_every_child(factory: sessionmaker[Session]) -> None:
    run_id = "run-cascade-1"
    with factory() as s:
        _ensure_user(s, _OWNER_ID, "owner-700@local.dev")
        _seed_run_with_children(s, run_id=run_id, user_id=_OWNER_ID)

    with factory() as s:
        repo.delete_run(s, run_id, actor_id=_OWNER_ID, is_admin=False)
        s.commit()

    with factory() as s:
        assert s.get(RunORM, run_id) is None
        present = _child_counts(s, run_id)
        # No orphaned rows anywhere — the core invariant of #700.
        assert present == {
            "state_snapshots": False,
            "node_execution_logs": False,
            "node_output_chunks": False,
        }


def test_delete_run_non_owner_raises_not_found(factory: sessionmaker[Session]) -> None:
    run_id = "run-owned"
    with factory() as s:
        _ensure_user(s, _OWNER_ID, "owner-700@local.dev")
        _ensure_user(s, _OTHER_ID, "other-700@local.dev")
        _seed_run_with_children(s, run_id=run_id, user_id=_OWNER_ID)

    with factory() as s, pytest.raises(repo.NotFoundError):
        repo.delete_run(s, run_id, actor_id=_OTHER_ID, is_admin=False)

    # Run + children still intact after the rejected delete.
    with factory() as s:
        assert s.get(RunORM, run_id) is not None


def test_admin_can_delete_other_users_run(factory: sessionmaker[Session]) -> None:
    run_id = "run-admin-del"
    with factory() as s:
        _ensure_user(s, _OWNER_ID, "owner-700@local.dev")
        _seed_run_with_children(s, run_id=run_id, user_id=_OWNER_ID)

    with factory() as s:
        repo.delete_run(s, run_id, actor_id=_OTHER_ID, is_admin=True)
        s.commit()

    with factory() as s:
        assert s.get(RunORM, run_id) is None


def test_delete_in_flight_run_raises_conflict(factory: sessionmaker[Session]) -> None:
    run_id = "run-running"
    with factory() as s:
        _ensure_user(s, _OWNER_ID, "owner-700@local.dev")
        _seed_run_with_children(s, run_id=run_id, user_id=_OWNER_ID, final_status="running")

    with factory() as s, pytest.raises(repo.ConflictError):
        repo.delete_run(s, run_id, actor_id=_OWNER_ID, is_admin=False)

    with factory() as s:
        assert s.get(RunORM, run_id) is not None


def test_delete_unknown_run_raises_not_found(factory: sessionmaker[Session]) -> None:
    with factory() as s, pytest.raises(repo.NotFoundError):
        repo.delete_run(s, "nope", actor_id=_OWNER_ID, is_admin=False)


# ---- HTTP layer -----------------------------------------------------------


@pytest.fixture
def http() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    tmp = tempfile.mkdtemp(prefix="dap-run-delete-http-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="test-secret")
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c, app.state.session_factory


def _authed_user_id(factory: sessionmaker[Session]) -> uuid.UUID:
    with factory() as s:
        return s.execute(select(UserORM.id).where(UserORM.email == DEFAULT_TEST_EMAIL)).scalar_one()


def test_delete_endpoint_204_and_audits(
    http: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = http
    uid = _authed_user_id(factory)
    run_id = "run-http-del"
    with factory() as s:
        _seed_run_with_children(s, run_id=run_id, user_id=uid)

    resp = client.delete(f"/runs/{run_id}")
    assert resp.status_code == 204, resp.text

    # Gone via API …
    assert client.get(f"/runs/{run_id}").status_code == 404
    # … and no orphaned child rows, and a run.deleted audit row exists.
    with factory() as s:
        assert s.get(RunORM, run_id) is None
        assert _child_counts(s, run_id) == {
            "state_snapshots": False,
            "node_execution_logs": False,
            "node_output_chunks": False,
        }
        audit = s.scalar(
            select(AuditLogORM).where(AuditLogORM.event_type == "run.deleted").limit(1)
        )
        assert audit is not None


def test_delete_endpoint_in_flight_returns_409(
    http: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = http
    uid = _authed_user_id(factory)
    run_id = "run-http-running"
    with factory() as s:
        _seed_run_with_children(s, run_id=run_id, user_id=uid, final_status="running")

    resp = client.delete(f"/runs/{run_id}")
    assert resp.status_code == 409, resp.text

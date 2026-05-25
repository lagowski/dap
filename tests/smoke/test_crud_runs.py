"""Read-only tests for /runs endpoints. We seed runs directly in DB
since trigger endpoint isn't implemented until F5."""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import (
    NodeExecutionLogORM,
    ProjectORM,
    RunORM,
    StateSnapshotORM,
)
from fastapi.testclient import TestClient
from sqlalchemy import Table
from sqlalchemy.orm import Session, sessionmaker

from tests.smoke._auth import authed_test_client


def _seed_run(session_factory: sessionmaker[Session], **overrides: Any) -> str:
    """Insert a run + 1 snapshot + 1 node log directly. Returns run_id."""
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "rafeekpro/test-repo",
        "branch": "main",
        "commit_sha": None,
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
        "extensions": {},
    }

    with session_factory() as session:
        started_at = overrides.get("started_at", now)
        run = RunORM(
            id=run_id,
            project_id=overrides.get("project_id"),
            pipeline_id=str(overrides.get("pipeline_id", "pipe-1")),
            pipeline_version=int(overrides.get("pipeline_version", 1)),
            trigger_source=str(overrides.get("trigger_source", "cli")),
            initial_state=initial_state,
            current_node=None,
            node_statuses={},
            final_status=str(overrides.get("final_status", "running")),
            started_at=started_at,
            ended_at=None,
            tokens_used=0,
            cost_usd=0.0,
        )
        snapshot = StateSnapshotORM(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id="select_task",
            timestamp=started_at,
            state={**initial_state, "selected_issue_ids": [42]},
        )
        log = NodeExecutionLogORM(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id="select_task",
            agent_id="agent-1",
            runtime_id="api-call",
            started_at=started_at,
            ended_at=started_at,
            prompt_xml="<agent_prompt><role>task_selector</role></agent_prompt>",
            stdout="",
            stderr="",
            output_json={"selected_issue_ids": [42]},
            tokens_used=120,
            cost_usd=0.001,
            duration_ms=850,
            status="success",
            error_message=None,
        )
        session.add(run)
        session.add(snapshot)
        session.add(log)
        session.commit()

    return run_id


def _seed_project(session_factory: sessionmaker[Session], project_id: str, name: str) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add(
            ProjectORM(
                id=project_id,
                name=name,
                description="",
                working_directory=None,
                repo_url=None,
                default_branch="main",
                pipelines={},
                env_vars={},
                created_at=now,
                updated_at=now,
                archived_at=None,
            )
        )
        session.commit()


@pytest.fixture
def client_and_factory() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    tmp = tempfile.mkdtemp(prefix="dap-crud-runs-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="crud-runs-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        # The fixture-default user is admin=False; the seeded runs have
        # NULL user_id (legacy backfill semantics), so promote to admin
        # to let the test scenarios see them. The cross-user gate has
        # its own test surface in test_ownership_runs.py.
        from dap_engine.persistence.models import UserORM

        with app.state.session_factory() as session:
            user_orm = session.query(UserORM).filter(UserORM.email == "test@local.dev").one()
            user_orm.is_superuser = True
            session.commit()
            # The bearer token was minted before the promotion — refresh it.
            login = c.post(
                "/auth/jwt/login",
                data={"username": "test@local.dev", "password": "test-password-123"},
            )
            c.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield c, app.state.session_factory


def test_list_runs_empty(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = client_and_factory
    response = client.get("/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["has_more"] is False


def test_list_runs_seeded(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    _seed_run(factory)
    _seed_run(factory, final_status="success")
    _seed_run(factory, pipeline_id="other-pipe")

    listing = client.get("/runs").json()
    assert listing["total"] == 3
    assert listing["has_more"] is False

    successful = client.get("/runs?final_status=success").json()
    assert successful["total"] == 1
    assert successful["has_more"] is False

    by_pipeline = client.get("/runs?pipeline_id=other-pipe").json()
    assert by_pipeline["total"] == 1
    assert by_pipeline["has_more"] is False


def test_list_runs_filters_status_date_and_project(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = client_and_factory
    _seed_project(factory, "project-1", "Project One")
    _seed_project(factory, "project-2", "Project Two")
    _seed_run(
        factory,
        final_status="success",
        project_id="project-1",
        started_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    _seed_run(
        factory,
        final_status="failed",
        project_id="project-1",
        started_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
    )
    _seed_run(
        factory,
        final_status="aborted",
        project_id="project-2",
        started_at=datetime(2026, 5, 12, 0, 0, tzinfo=UTC),
    )
    _seed_run(
        factory,
        final_status="running",
        project_id=None,
        started_at=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
    )

    by_status = client.get("/runs?status=success&status=failed").json()
    assert by_status["total"] == 2

    by_date = client.get("/runs?from=2026-05-11&to=2026-05-12").json()
    assert by_date["total"] == 2

    by_project = client.get("/runs?project_id=project-1&status=failed").json()
    assert by_project["total"] == 1
    assert by_project["items"][0]["project_id"] == "project-1"

    ad_hoc = client.get("/runs?project_id=null").json()
    assert ad_hoc["total"] == 1
    assert ad_hoc["items"][0]["project_id"] is None

    invalid_range = client.get("/runs?from=2026-05-12&to=2026-05-11")
    assert invalid_range.status_code == 422


def test_run_listing_index_metadata_matches_documented_query_shapes() -> None:
    table = RunORM.__table__
    assert isinstance(table, Table)
    indexes = {
        str(index.name): [column.name for column in index.columns]
        for index in table.indexes
        if index.name is not None
    }

    assert indexes["ix_runs_started_at"] == ["started_at"]
    assert indexes["ix_runs_project_started"] == ["project_id", "started_at"]
    assert indexes["ix_runs_pipeline_started"] == ["pipeline_id", "started_at"]
    assert indexes["ix_runs_user_id"] == ["user_id"]
    assert all("final_status" not in columns for columns in indexes.values())


def test_get_run(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert body["pipeline_id"] == "pipe-1"


def test_get_run_node_statuses_from_logs(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """GET /runs/{id} must return node_statuses derived from node_execution_logs.

    python-func pipelines never write back to runs.node_statuses — the column
    stays {} after every run.  The fix (#233) joins node_execution_logs on the
    detail endpoint so the dashboard graph can show per-node state.
    """
    client, factory = client_and_factory
    run_id = _seed_run(factory)  # seeds one log: node_id="select_task" status="success"

    body = client.get(f"/runs/{run_id}").json()
    assert body["id"] == run_id
    # node_statuses must be populated from the execution log, not from the
    # runs.node_statuses column (which is always {}).
    assert body["node_statuses"] == {"select_task": "success"}


def test_get_run_node_statuses_multiple_nodes(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """node_statuses includes every node in execution order, including failures."""
    client, factory = client_and_factory
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "r/r",
        "branch": "main",
        "commit_sha": None,
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
        "final_status": "failed",
        "extensions": {},
    }

    def _make_log(node_id: str, status: str, offset_ms: int) -> NodeExecutionLogORM:
        from datetime import timedelta

        t = now + timedelta(milliseconds=offset_ms)
        return NodeExecutionLogORM(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id=node_id,
            agent_id="agent-1",
            runtime_id="python-func",
            started_at=t,
            ended_at=t,
            prompt_xml="",
            stdout="",
            stderr="",
            output_json=None,
            tokens_used=0,
            cost_usd=0.0,
            duration_ms=10,
            status=status,
            error_message=None,
        )

    with factory() as session:
        session.add(
            RunORM(
                id=run_id,
                pipeline_id="pipe-1",
                pipeline_version=1,
                trigger_source="cli",
                initial_state=initial_state,
                current_node=None,
                node_statuses={},
                final_status="failed",
                started_at=now,
                ended_at=now,
                tokens_used=0,
                cost_usd=0.0,
            )
        )
        session.add(_make_log("node-a", "success", 0))
        session.add(_make_log("node-b", "success", 100))
        session.add(_make_log("node-c", "failed", 200))
        session.commit()

    body = client.get(f"/runs/{run_id}").json()
    assert body["node_statuses"] == {
        "node-a": "success",
        "node-b": "success",
        "node-c": "failed",
    }


def test_get_run_node_statuses_empty_when_no_logs(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """node_statuses stays {} when no execution logs exist yet (run just started)."""
    client, factory = client_and_factory
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "r/r",
        "branch": "main",
        "commit_sha": None,
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
        "extensions": {},
    }
    with factory() as session:
        session.add(
            RunORM(
                id=run_id,
                pipeline_id="pipe-1",
                pipeline_version=1,
                trigger_source="cli",
                initial_state=initial_state,
                current_node=None,
                node_statuses={},
                final_status="running",
                started_at=now,
                ended_at=None,
                tokens_used=0,
                cost_usd=0.0,
            )
        )
        session.commit()

    body = client.get(f"/runs/{run_id}").json()
    assert body["node_statuses"] == {}


def test_list_runs_does_not_join_node_logs(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """list_runs must NOT join node_execution_logs (performance guard).

    The detail endpoint joins logs; the list endpoint must stay lean and
    return whatever is stored in runs.node_statuses ({} for python-func runs).
    """
    client, factory = client_and_factory
    _seed_run(factory)  # run with one log: select_task=success

    items = client.get("/runs").json()["items"]
    assert len(items) == 1
    # List endpoint returns the stored {} — node_statuses join is detail-only.
    assert items[0]["node_statuses"] == {}


def test_get_run_404(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _ = client_and_factory
    response = client.get("/runs/nope")
    assert response.status_code == 404


def test_get_run_state(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}/state")
    assert response.status_code == 200
    state = response.json()
    # Latest snapshot was set with selected_issue_ids=[42]
    assert state["selected_issue_ids"] == [42]


def test_state_history(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}/state/history")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 1
    assert history[0]["run_id"] == run_id


def test_get_node_log(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}/nodes/select_task")
    assert response.status_code == 200
    log = response.json()
    assert log["node_id"] == "select_task"
    assert log["agent_id"] == "agent-1"
    assert log["status"] == "success"
    assert log["tokens_used"] == 120


def test_get_node_log_404(client_and_factory: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = client_and_factory
    run_id = _seed_run(factory)

    response = client.get(f"/runs/{run_id}/nodes/missing_node")
    assert response.status_code == 404


def test_pipeline_state_extensions_round_trip(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """PipelineState.extensions survives a DB round-trip via the snapshot endpoint."""
    from dap_types.state import PipelineState

    client, factory = client_and_factory
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    ext_data: dict[str, Any] = {
        "issue_url": "https://github.com/rafeekpro/dap/issues/144",
        "approved": True,
        "score": 42,
        "tags": ["alpha", "beta"],
    }
    state_with_ext: dict[str, Any] = {
        "run_id": run_id,
        "repo": "rafeekpro/test-repo",
        "branch": "main",
        "commit_sha": None,
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
        "extensions": ext_data,
    }

    # Validate that PipelineState accepts the extensions field
    parsed = PipelineState(**state_with_ext)
    assert parsed.extensions == ext_data

    # Seed a snapshot carrying extensions data
    with factory() as session:
        run = RunORM(
            id=run_id,
            pipeline_id="pipe-ext",
            pipeline_version=1,
            trigger_source="cli",
            initial_state=state_with_ext,
            current_node=None,
            node_statuses={},
            final_status="running",
            started_at=now,
            ended_at=None,
            tokens_used=0,
            cost_usd=0.0,
        )
        snapshot = StateSnapshotORM(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id="ext_node",
            timestamp=now,
            state=state_with_ext,
        )
        session.add(run)
        session.add(snapshot)
        session.commit()

    # Retrieve state via API and assert extensions are preserved
    response = client.get(f"/runs/{run_id}/state")
    assert response.status_code == 200
    returned = response.json()
    assert returned["extensions"] == ext_data


def test_node_log_extra_data_round_trip(
    client_and_factory: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """extra_data stored on NodeExecutionLogORM is returned by GET /runs/{id}/nodes/{node_id}."""
    client, factory = client_and_factory
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    audit_payload: dict[str, Any] = {
        "github_user": "Dixter999",
        "section": "mockup",
        "content_before": "old content",
        "content_after": "new content",
        "tokens_used": 1240,
        "cost_usd": 0.003,
    }
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "rafeekpro/test-repo",
        "branch": "main",
        "commit_sha": None,
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
        "extensions": {},
    }
    with factory() as session:
        run = RunORM(
            id=run_id,
            pipeline_id="pipe-audit",
            pipeline_version=1,
            trigger_source="cli",
            initial_state=initial_state,
            current_node=None,
            node_statuses={},
            final_status="success",
            started_at=now,
            ended_at=now,
            tokens_used=0,
            cost_usd=0.0,
        )
        log = NodeExecutionLogORM(
            id=str(uuid.uuid4()),
            run_id=run_id,
            node_id="audit_node",
            agent_id="agent-audit",
            runtime_id="python-func",
            started_at=now,
            ended_at=now,
            prompt_xml="<agent_prompt/>",
            stdout="",
            stderr="",
            output_json={"mockup_section": "text", "audit": audit_payload},
            tokens_used=1240,
            cost_usd=0.003,
            duration_ms=500,
            status="success",
            error_message=None,
            extra_data=audit_payload,
        )
        session.add(run)
        session.add(log)
        session.commit()

    response = client.get(f"/runs/{run_id}/nodes/audit_node")
    assert response.status_code == 200
    returned = response.json()
    assert returned["extra_data"] == audit_payload
    assert returned["extra_data"]["github_user"] == "Dixter999"
    assert returned["extra_data"]["section"] == "mockup"

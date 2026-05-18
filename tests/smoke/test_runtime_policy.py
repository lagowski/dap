"""Runtime policy checks for dangerous adapters (#491)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from dap_engine.app import EngineConfig, create_app
from dap_engine.persistence.models import AuditLogORM
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.smoke._auth import authed_test_client


def _bash_agent_payload() -> dict[str, object]:
    return {
        "name": "Bash Probe",
        "role": "post_check",
        "runtime_id": "bash",
        "runtime_config": {"command": "echo should-not-run"},
        "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        "input_schema": [],
        "output_schema": [],
        "constraints": [],
        "budget_limit_usd": None,
        "timeout_ms": 5_000,
    }


def _create_bash_agent(client: TestClient) -> str:
    response = client.post("/agents", json=_bash_agent_payload())
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _create_pipeline(client: TestClient, agent_id: str) -> str:
    response = client.post(
        "/pipelines",
        json={
            "name": "Bash Policy Pipeline",
            "description": "",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [{"id": "e1", "source": "n1", "target": "__end__"}],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _wait_for_status(client: TestClient, run_id: str) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200, response.text
        status = str(response.json()["final_status"])
        if status != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish")


def _runtime_policy_audit_payloads(client: TestClient) -> list[dict[str, object]]:
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        rows = session.scalars(
            select(AuditLogORM)
            .where(AuditLogORM.event_type == "runtime_policy.denied")
            .order_by(AuditLogORM.created_at)
        ).all()
        return [dict(row.event_data or {}) for row in rows]


@contextmanager
def _client_with_config(config: EngineConfig) -> Iterator[TestClient]:
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c


def test_non_admin_dry_run_rejects_bash_by_default(
    engine_config_factory: Callable[..., EngineConfig],
) -> None:
    with _client_with_config(engine_config_factory()) as client:
        response = client.post(
            "/agents/dry-run",
            json={"draft": _bash_agent_payload(), "context": {}},
        )

    assert response.status_code == 403
    assert "bash runtime is disabled for non-admin users" in response.text
    payloads = _runtime_policy_audit_payloads(client)
    assert payloads == [
        {
            "surface": "agents.dry_run",
            "runtime_id": "bash",
            "reason": (
                "bash runtime is disabled for non-admin users. "
                "Set DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN=1 to opt in."
            ),
        }
    ]


def test_non_admin_run_rejects_bash_pipeline_by_default(
    engine_config_factory: Callable[..., EngineConfig],
) -> None:
    with _client_with_config(engine_config_factory()) as client:
        agent_id = _create_bash_agent(client)
        pipeline_id = _create_pipeline(client, agent_id)

        response = client.post("/runs", json={"pipeline_id": pipeline_id, "initial_state": {}})

    assert response.status_code == 403
    assert "bash runtime is disabled for non-admin users" in response.text
    payloads = _runtime_policy_audit_payloads(client)
    assert payloads == [
        {
            "surface": "runs.trigger",
            "pipeline_id": pipeline_id,
            "pipeline_version": 1,
            "reason": (
                "bash runtime is disabled for non-admin users. "
                "Set DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN=1 to opt in."
            ),
        }
    ]


def test_explicit_config_allows_non_admin_bash_run(
    engine_config_factory: Callable[..., EngineConfig],
) -> None:
    config = engine_config_factory(allow_bash_runtime_for_non_admin=True)
    with _client_with_config(config) as client:
        agent_id = _create_bash_agent(client)
        pipeline_id = _create_pipeline(client, agent_id)

        response = client.post("/runs", json={"pipeline_id": pipeline_id, "initial_state": {}})
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        final_status = _wait_for_status(client, run_id)

    assert final_status == "success"

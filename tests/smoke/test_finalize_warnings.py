"""Smoke test: finalize_warnings in state_delta land in extensions without blocking.

A python-func node returning ``finalize_warnings`` in its ``state_delta``
must have those warnings routed into ``state.extensions.finalize_warnings``
(via ``_route_extensions``) and the run must complete successfully — i.e.
warnings are informational and do not fail the pipeline.

Mirrors the Phase 2 smoke-test pattern in ``test_smoke_phase2.py``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

from .conftest import replace_adapter, wait_for_status

# ---------------------------------------------------------------------------
# Stub adapter — emits finalize_warnings in state_delta
# ---------------------------------------------------------------------------

_WARNINGS = [
    "stale open question: scripts/fetch_yahoo_news.py already exists",
    "cicd references non-existent tests/standalone/ directory",
]


class FinalizeWithWarningsStub:
    """python-func stub that returns finalize_warnings in state_delta."""

    id = "python-func"
    display_name = "Finalize Warnings Stub"
    kind: RuntimeKind = "api"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        return RuntimeResult(
            success=True,
            output="finalize: clean with warnings",
            duration_ms=80,
            structured={
                "state_delta": {
                    "finalize_warnings": _WARNINGS,
                },
                "audit": {"agent": "finalize-stub"},
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_single_node_pipeline(client: TestClient) -> str:
    """Create a minimal 1-node pipeline whose only node emits warnings."""
    resp = client.post(
        "/agents",
        json={
            "name": "Finalize Warnings Node",
            "role": "post_check",
            "runtime_id": "python-func",
            "prompt_template": "<agent_prompt><role>finalize</role></agent_prompt>",
        },
    )
    assert resp.status_code == 201, resp.text
    agent_id = str(resp.json()["id"])

    resp = client.post(
        "/pipelines",
        json={
            "name": "Finalize Warnings Pipeline",
            "description": "1-node pipeline for finalize_warnings routing",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "finalize",
            "nodes": [
                {"id": "finalize", "agent_id": agent_id, "position": {"x": 0, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": "finalize", "target": "__end__"},
            ],
            "defaults": {"max_attempts": 1, "budget_limit_usd": 1.0},
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def warnings_client() -> Iterator[TestClient]:
    """TestClient with a python-func stub that emits finalize_warnings."""
    tmp = tempfile.mkdtemp(prefix="dap-finalize-warnings-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="smoke-secret",
    )
    app = create_app(config)
    with authed_test_client(app) as c:
        registry: RuntimeRegistry = app.state.runtime_registry
        replace_adapter(registry, FinalizeWithWarningsStub())
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_finalize_warnings_routed_to_extensions(warnings_client: TestClient) -> None:
    """finalize_warnings in state_delta must appear in extensions after execution."""
    client = warnings_client
    pipeline_id = _create_single_node_pipeline(client)

    triggered = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {"repo": "test/repo", "branch": "main"},
        },
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    assert completed["final_status"] == "success"

    # Verify warnings landed in extensions via state history
    history = client.get(f"/runs/{run_id}/state/history").json()
    assert len(history) >= 1

    last_snap = history[-1]
    state = last_snap["state"]
    extensions = state.get("extensions", {})
    assert extensions.get("finalize_warnings") == _WARNINGS


def test_finalize_warnings_do_not_block_run(warnings_client: TestClient) -> None:
    """A run with finalize_warnings must complete with status=success."""
    client = warnings_client
    pipeline_id = _create_single_node_pipeline(client)

    triggered = client.post(
        "/runs",
        json={
            "pipeline_id": pipeline_id,
            "initial_state": {"repo": "test/repo", "branch": "main"},
        },
    ).json()
    run_id = triggered["id"]

    completed = wait_for_status(client, run_id, {"success", "failed"})
    # Warnings are informational — they must not cause failure
    assert completed["final_status"] == "success"

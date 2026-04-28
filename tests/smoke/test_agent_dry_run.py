"""Tests for ``POST /agents/dry-run`` (#103).

Uses the ``bash`` adapter (always available, free) so the dry-run path
is exercised end-to-end — prompt build, runtime execute, output schema
check, response shape — without burning LLM tokens.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory(prefix="dap-dryrun-test-") as tmp:
        config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
        app = create_app(config)
        with TestClient(app) as c:
            yield c


@pytest.fixture
def low_cap_client() -> Iterator[TestClient]:
    """Engine with a tight ``dry_run_budget_usd`` for cap-violation tests."""
    with tempfile.TemporaryDirectory(prefix="dap-dryrun-cap-") as tmp:
        config = EngineConfig(
            db_path=str(Path(tmp) / "state.db"),
            dry_run_budget_usd=0.05,
        )
        app = create_app(config)
        with TestClient(app) as c:
            yield c


def _bash_draft(**overrides: Any) -> dict[str, Any]:
    """A draft using the bash runtime with a deterministic command."""
    payload: dict[str, Any] = {
        "name": "Dry Run Probe",
        "role": "test_author",
        "runtime_id": "bash",
        "runtime_config": {"command": "echo hello-from-dry-run"},
        # bash adapter ignores prompt_xml when runtime_config.command is set,
        # but build_prompt still needs a renderable template.
        "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
        "input_schema": [],
        "output_schema": [],
        "constraints": [],
        "budget_limit_usd": None,
        "timeout_ms": 5_000,
    }
    payload.update(overrides)
    return payload


def _agent_create_payload(**overrides: Any) -> dict[str, Any]:
    """Same shape as a bash draft but with no Pydantic-only fields."""
    return _bash_draft(**overrides)


# ---------------------------------------------------------------------------
# Happy path — saved agent
# ---------------------------------------------------------------------------


def test_dry_run_with_saved_agent_returns_runtime_result(client: TestClient) -> None:
    created = client.post("/agents", json=_agent_create_payload()).json()

    response = client.post(
        "/agents/dry-run",
        json={"agent_id": created["id"], "context": {}},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["rendered_xml"].startswith("<agent_prompt>")
    assert body["runtime_result"]["success"] is True
    assert "hello-from-dry-run" in body["runtime_result"]["output"]
    assert body["runtime_result"]["structured"]["exit_code"] == 0
    # No output_schema declared → not checked.
    assert body["output_schema_validation"]["checked"] is False
    assert body["output_schema_validation"]["valid"] is True


def test_dry_run_with_saved_agent_specific_version(client: TestClient) -> None:
    created = client.post("/agents", json=_agent_create_payload()).json()
    # Roll a new version with a different command
    client.put(
        f"/agents/{created['id']}",
        json=_agent_create_payload(
            runtime_config={"command": "echo version-two"},
        ),
    )

    response = client.post(
        "/agents/dry-run",
        json={
            "agent_id": created["id"],
            "agent_version": 1,
            "context": {},
        },
    )
    assert response.status_code == 200
    assert "hello-from-dry-run" in response.json()["runtime_result"]["output"]


# ---------------------------------------------------------------------------
# Happy path — inline draft (no agent persisted)
# ---------------------------------------------------------------------------


def test_dry_run_with_draft_does_not_persist(client: TestClient) -> None:
    response = client.post(
        "/agents/dry-run",
        json={"draft": _bash_draft(), "context": {}},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["runtime_result"]["success"] is True
    assert "hello-from-dry-run" in body["runtime_result"]["output"]

    # No agent should have been created.
    listing = client.get("/agents").json()
    assert listing["total"] == 0


# ---------------------------------------------------------------------------
# Mutual exclusion of agent_id vs draft
# ---------------------------------------------------------------------------


def test_dry_run_rejects_both_sources(client: TestClient) -> None:
    created = client.post("/agents", json=_agent_create_payload()).json()
    response = client.post(
        "/agents/dry-run",
        json={
            "agent_id": created["id"],
            "draft": _bash_draft(),
            "context": {},
        },
    )
    assert response.status_code == 422
    assert "exactly one" in str(response.json()["detail"]).lower()


def test_dry_run_rejects_neither_source(client: TestClient) -> None:
    response = client.post("/agents/dry-run", json={"context": {}})
    assert response.status_code == 422
    assert "exactly one" in str(response.json()["detail"]).lower()


# ---------------------------------------------------------------------------
# 404 / 422 paths
# ---------------------------------------------------------------------------


def test_dry_run_404_unknown_agent(client: TestClient) -> None:
    response = client.post(
        "/agents/dry-run",
        json={"agent_id": "missing-id", "context": {}},
    )
    assert response.status_code == 404


def test_dry_run_unknown_runtime_id_returns_422(client: TestClient) -> None:
    response = client.post(
        "/agents/dry-run",
        json={"draft": _bash_draft(runtime_id="ghost-runtime"), "context": {}},
    )
    assert response.status_code == 422
    assert "ghost-runtime" in str(response.json()["detail"])


def test_dry_run_draft_validates_input_schema(client: TestClient) -> None:
    response = client.post(
        "/agents/dry-run",
        json={
            "draft": _bash_draft(input_schema=["definitely_not_a_field"]),
            "context": {},
        },
    )
    assert response.status_code == 422
    assert "definitely_not_a_field" in str(response.json()["detail"])


def test_dry_run_prompt_render_error_returns_422(client: TestClient) -> None:
    """Undefined template variable → 422 from build_prompt."""
    response = client.post(
        "/agents/dry-run",
        json={
            "draft": _bash_draft(
                prompt_template="<agent_prompt>{{ missing_var }}</agent_prompt>",
            ),
            "context": {},
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Budget cap
# ---------------------------------------------------------------------------


def test_dry_run_rejects_budget_above_engine_cap(low_cap_client: TestClient) -> None:
    """Engine cap = $0.05; agent declares $1.00 → refuse before invocation."""
    response = low_cap_client.post(
        "/agents/dry-run",
        json={
            "draft": _bash_draft(budget_limit_usd=1.00),
            "context": {},
        },
    )
    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "exceeds the dry-run cap" in detail


def test_dry_run_accepts_budget_within_cap(low_cap_client: TestClient) -> None:
    response = low_cap_client.post(
        "/agents/dry-run",
        json={
            "draft": _bash_draft(budget_limit_usd=0.01),
            "context": {},
        },
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Output schema validation (soft check)
# ---------------------------------------------------------------------------


def test_dry_run_output_schema_missing_fields_warns(client: TestClient) -> None:
    """bash structured payload doesn't include PipelineState keys, so a
    declared output_schema surfaces them all as missing — but the call
    still succeeds (soft check, not fatal)."""
    response = client.post(
        "/agents/dry-run",
        json={
            "draft": _bash_draft(output_schema=["test_files", "tests_generated"]),
            "context": {},
        },
    )
    assert response.status_code == 200

    check = response.json()["output_schema_validation"]
    assert check["checked"] is True
    assert check["valid"] is False
    assert set(check["missing_fields"]) == {"test_files", "tests_generated"}


def test_dry_run_output_schema_empty_skips_check(client: TestClient) -> None:
    response = client.post(
        "/agents/dry-run",
        json={"draft": _bash_draft(output_schema=[]), "context": {}},
    )
    assert response.status_code == 200
    check = response.json()["output_schema_validation"]
    assert check["checked"] is False
    assert check["valid"] is True


# ---------------------------------------------------------------------------
# Side-effect isolation
# ---------------------------------------------------------------------------


def test_dry_run_does_not_create_run_row(client: TestClient) -> None:
    response = client.post(
        "/agents/dry-run",
        json={"draft": _bash_draft(), "context": {}},
    )
    assert response.status_code == 200

    runs = client.get("/runs").json()
    assert runs["total"] == 0


def test_dry_run_working_directory_isolated(client: TestClient) -> None:
    """The temp working_directory must not leak: bash writes a file there
    and the dir should be gone by the time the response returns."""
    response = client.post(
        "/agents/dry-run",
        json={
            "draft": _bash_draft(
                runtime_config={
                    "command": "pwd && echo > marker.txt && ls",
                },
            ),
            "context": {},
        },
    )
    assert response.status_code == 200

    output = response.json()["runtime_result"]["output"]
    # Capture the cwd path the subprocess saw, then verify it's gone.
    workdir_line = output.strip().splitlines()[0]
    assert workdir_line.startswith("/")
    # marker.txt was created inside the temp dir
    assert "marker.txt" in output
    # ... and the temp dir should already be cleaned up
    assert not Path(workdir_line).exists()

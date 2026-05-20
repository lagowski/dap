"""CRUD tests for /agents endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from dap_engine.persistence.models import AgentVersionORM
from fastapi.testclient import TestClient


@pytest.fixture
def client(authed_client: TestClient) -> TestClient:
    """Authed TestClient shim — see ``tests/smoke/conftest.py::authed_client``.

    Kept as a local alias because most test bodies in this file already
    take a ``client: TestClient`` parameter; renaming them all would
    bloat the X1 diff without changing behaviour.
    """
    return authed_client


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Test Author",
        "role": "test_author",
        "runtime_id": "claude-code",
        "runtime_config": {"model": "claude-sonnet-4-6", "max_turns": 10},
        "prompt_template": "<agent_prompt><role>test_author</role></agent_prompt>",
        "input_schema": ["available_issues"],
        "output_schema": ["test_files", "tests_generated"],
        "constraints": ["no_implementation"],
        "budget_limit_usd": 2.0,
        "timeout_ms": 30000,
    }
    payload.update(overrides)
    return payload


def _update_payload(**overrides: Any) -> dict[str, Any]:
    """Update body — no role (role is immutable identity)."""
    payload = _create_payload(**overrides)
    payload.pop("role", None)
    return payload


def test_create_agent_returns_v1(client: TestClient) -> None:
    response = client.post("/agents", json=_create_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Test Author"
    assert body["role"] == "test_author"
    assert body["version"] == 1
    assert body["is_active"] is True
    assert body["runtime_id"] == "claude-code"
    assert body.get("id")
    assert "created_at" in body
    assert "updated_at" in body


def test_create_agent_validation(client: TestClient) -> None:
    response = client.post("/agents", json={"name": ""})
    assert response.status_code == 422


def test_create_agent_extra_field_rejected(client: TestClient) -> None:
    payload = _create_payload(unknown_field="bad")
    response = client.post("/agents", json=payload)
    assert response.status_code == 422


def test_get_agent_404(client: TestClient) -> None:
    response = client.get("/agents/nonexistent-id")
    assert response.status_code == 404


def test_get_agent_returns_current_version(client: TestClient) -> None:
    created = client.post("/agents", json=_create_payload()).json()
    agent_id = created["id"]

    response = client.get(f"/agents/{agent_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == agent_id
    assert body["version"] == 1


def test_update_agent_creates_new_version(client: TestClient) -> None:
    created = client.post("/agents", json=_create_payload()).json()
    agent_id = created["id"]

    update_payload = _update_payload(
        name="Test Author v2",
        runtime_config={"model": "claude-opus-4-6", "max_turns": 20},
    )
    response = client.put(f"/agents/{agent_id}", json=update_payload)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == agent_id
    assert body["version"] == 2
    assert body["name"] == "Test Author v2"
    assert body["runtime_config"]["model"] == "claude-opus-4-6"


def test_update_agent_404(client: TestClient) -> None:
    response = client.put("/agents/nope", json=_update_payload())
    assert response.status_code == 404


def test_list_agent_versions(client: TestClient) -> None:
    created = client.post("/agents", json=_create_payload()).json()
    agent_id = created["id"]
    client.put(f"/agents/{agent_id}", json=_update_payload(name="v2"))
    client.put(f"/agents/{agent_id}", json=_update_payload(name="v3"))

    response = client.get(f"/agents/{agent_id}/versions")
    assert response.status_code == 200
    versions = response.json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[0]["name"] == "Test Author"  # original
    assert versions[2]["name"] == "v3"


def test_get_specific_version(client: TestClient) -> None:
    created = client.post("/agents", json=_create_payload(name="initial")).json()
    agent_id = created["id"]
    client.put(f"/agents/{agent_id}", json=_update_payload(name="updated"))

    response = client.get(f"/agents/{agent_id}/versions/1")
    assert response.status_code == 200
    assert response.json()["name"] == "initial"

    response = client.get(f"/agents/{agent_id}/versions/2")
    assert response.status_code == 200
    assert response.json()["name"] == "updated"

    response = client.get(f"/agents/{agent_id}/versions/99")
    assert response.status_code == 404


def test_archive_agent(client: TestClient) -> None:
    created = client.post("/agents", json=_create_payload()).json()
    agent_id = created["id"]

    response = client.delete(f"/agents/{agent_id}")
    assert response.status_code == 204

    # Default list excludes archived
    listing = client.get("/agents").json()
    assert all(a["id"] != agent_id for a in listing["items"])

    # archived=true includes it
    listing_with_archived = client.get("/agents?archived=true").json()
    ids = [a["id"] for a in listing_with_archived["items"]]
    assert agent_id in ids


def test_archive_404(client: TestClient) -> None:
    response = client.delete("/agents/nope")
    assert response.status_code == 404


def test_update_archived_404(client: TestClient) -> None:
    created = client.post("/agents", json=_create_payload()).json()
    agent_id = created["id"]
    client.delete(f"/agents/{agent_id}")

    response = client.put(f"/agents/{agent_id}", json=_update_payload())
    assert response.status_code == 404


def test_list_pagination_and_role_filter(client: TestClient) -> None:
    # Seed 5 agents: 3 test_author, 2 implementer
    for i in range(3):
        client.post("/agents", json=_create_payload(name=f"ta-{i}", role="test_author"))
    for i in range(2):
        client.post("/agents", json=_create_payload(name=f"impl-{i}", role="implementer"))

    listing = client.get("/agents").json()
    assert listing["total"] == 5
    assert len(listing["items"]) == 5

    only_test_author = client.get("/agents?role=test_author").json()
    assert only_test_author["total"] == 3
    assert all(a["role"] == "test_author" for a in only_test_author["items"])

    paginated = client.get("/agents?limit=2&offset=0").json()
    assert paginated["limit"] == 2
    assert len(paginated["items"]) == 2
    assert paginated["total"] == 5


def test_pagination_validation(client: TestClient) -> None:
    response = client.get("/agents?limit=0")
    assert response.status_code == 422
    response = client.get("/agents?offset=-1")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Per-agent input/output schemas (#58, v0.5)
# ---------------------------------------------------------------------------


def test_create_agent_persists_field_list_schemas(client: TestClient) -> None:
    """Schemas round-trip as ``list[str]`` with the field names intact."""
    payload = _create_payload(
        input_schema=["available_issues", "max_attempts"],
        output_schema=["test_files", "tests_generated", "test_generation_errors"],
    )
    body = client.post("/agents", json=payload).json()
    assert body["input_schema"] == ["available_issues", "max_attempts"]
    assert body["output_schema"] == [
        "test_files",
        "tests_generated",
        "test_generation_errors",
    ]


def test_create_agent_accepts_extension_schema_fields(client: TestClient) -> None:
    payload = _create_payload(
        input_schema=["selected_issue_ids", "issue_number", "extensions.pr_url"],
        output_schema=["files_changed", "__audit"],
    )
    response = client.post("/agents", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["input_schema"] == ["selected_issue_ids", "issue_number", "extensions.pr_url"]
    assert body["output_schema"] == ["files_changed", "__audit"]


def test_create_agent_rejects_malformed_field_in_input_schema(client: TestClient) -> None:
    payload = _create_payload(input_schema=["selected_issue_ids", "extensions."])
    response = client.post("/agents", json=payload)
    assert response.status_code == 422
    body = response.json()
    detail = str(body["detail"])
    assert "extensions." in detail
    assert "unsupported field" in detail


def test_create_agent_rejects_malformed_field_in_output_schema(client: TestClient) -> None:
    payload = _create_payload(output_schema=["repo.url"])
    response = client.post("/agents", json=payload)
    assert response.status_code == 422
    assert "repo.url" in str(response.json()["detail"])


def test_get_agent_sanitizes_malformed_legacy_schema_refs(client: TestClient) -> None:
    created = client.post("/agents", json=_create_payload()).json()
    agent_id = created["id"]

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        version = (
            session.query(AgentVersionORM)
            .filter(AgentVersionORM.agent_id == agent_id, AgentVersionORM.version == 1)
            .one()
        )
        version.input_schema = ["available_issues", "repo.url", "available_issues", 42]
        version.output_schema = ["files_changed", "extensions.", "__audit"]
        session.commit()

    response = client.get(f"/agents/{agent_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["input_schema"] == ["available_issues"]
    assert body["output_schema"] == ["files_changed", "__audit"]


def test_create_agent_rejects_duplicate_field_names(client: TestClient) -> None:
    payload = _create_payload(output_schema=["test_files", "test_files"])
    response = client.post("/agents", json=payload)
    assert response.status_code == 422
    assert "duplicate" in str(response.json()["detail"]).lower()


def test_update_agent_rejects_unknown_field(client: TestClient) -> None:
    created = client.post("/agents", json=_create_payload()).json()
    agent_id = created["id"]
    response = client.put(
        f"/agents/{agent_id}",
        json=_update_payload(output_schema=["extensions."]),
    )
    assert response.status_code == 422
    assert "extensions." in str(response.json()["detail"])


def test_create_agent_coerces_legacy_dict_schema_to_empty_list(
    client: TestClient,
) -> None:
    """Older clients still sending ``{}`` get a silent coercion to ``[]``.

    We don't accept a *non-empty* dict — those carried no real contract
    (placeholder JSON-Schema-ish blobs), so emptying them on the way in
    is the safe migration. Removed in v0.6 once clients are updated.
    """
    payload = _create_payload(input_schema={}, output_schema={})
    response = client.post("/agents", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["input_schema"] == []
    assert body["output_schema"] == []


def test_default_schemas_are_empty_lists(client: TestClient) -> None:
    """Omitting the fields entirely also gives ``[]`` (not ``{}``)."""
    payload = _create_payload()
    payload.pop("input_schema", None)
    payload.pop("output_schema", None)
    body = client.post("/agents", json=payload).json()
    assert body["input_schema"] == []
    assert body["output_schema"] == []

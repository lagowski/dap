"""Integration test for POST /agents/{id}/render-preview."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    tmp = tempfile.mkdtemp(prefix="dap-render-preview-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"))
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _create_agent(client: TestClient, template: str) -> str:
    response = client.post(
        "/agents",
        json={
            "name": "Test Author",
            "role": "test_author",
            "runtime_id": "claude-code",
            "prompt_template": template,
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_render_preview_success(client: TestClient) -> None:
    agent_id = _create_agent(
        client,
        template="<agent_prompt><role>{{ role }}</role></agent_prompt>",
    )

    response = client.post(
        f"/agents/{agent_id}/render-preview",
        json={"context": {"role": "test_author"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert "<role>test_author</role>" in body["rendered_xml"]
    assert body["errors"] == []


def test_render_preview_undefined_var_returns_422(client: TestClient) -> None:
    agent_id = _create_agent(
        client,
        template="<agent_prompt><role>{{ missing }}</role></agent_prompt>",
    )

    response = client.post(
        f"/agents/{agent_id}/render-preview",
        json={"context": {}},
    )
    assert response.status_code == 422


def test_render_preview_invalid_xml_returns_200_with_errors(client: TestClient) -> None:
    """Rendering succeeds but XML is malformed — return 200 with valid=False."""
    agent_id = _create_agent(
        client,
        template="<agent_prompt><unclosed></agent_prompt>",
    )

    response = client.post(
        f"/agents/{agent_id}/render-preview",
        json={"context": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"]


def test_render_preview_404_unknown_agent(client: TestClient) -> None:
    response = client.post(
        "/agents/nope/render-preview",
        json={"context": {}},
    )
    assert response.status_code == 404


def test_render_preview_specific_version(client: TestClient) -> None:
    agent_id = _create_agent(
        client,
        template="<agent_prompt><v>1</v></agent_prompt>",
    )
    # Update creates v2 with different template
    response = client.put(
        f"/agents/{agent_id}",
        json={
            "runtime_id": "claude-code",
            "prompt_template": "<agent_prompt><v>2</v></agent_prompt>",
        },
    )
    assert response.status_code == 200

    # Default → current (v2)
    current = client.post(f"/agents/{agent_id}/render-preview", json={"context": {}}).json()
    assert "<v>2</v>" in current["rendered_xml"]

    # Specific → v1
    v1 = client.post(
        f"/agents/{agent_id}/render-preview?version=1",
        json={"context": {}},
    ).json()
    assert "<v>1</v>" in v1["rendered_xml"]


def test_render_preview_extra_field_rejected(client: TestClient) -> None:
    agent_id = _create_agent(client, template="<agent_prompt/>")
    response = client.post(
        f"/agents/{agent_id}/render-preview",
        json={"context": {}, "unknown": "field"},
    )
    assert response.status_code == 422

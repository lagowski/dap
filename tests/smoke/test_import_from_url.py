"""Tests for ``POST /pipelines/import-from-url`` (#385).

Iter 1 of the private template registry. Covers:

- happy path against a mocked URL with a valid bundle body
- SSRF guard: hostname not on allow-list → 422
- HTTPS-only enforcement (with the localhost dev exception)
- empty allow-list (default) → 422 with operator-friendly message
- bearer token: when ``DAP_TEMPLATE_REGISTRY_AUTH_TOKEN`` is set the
  outbound GET carries the matching ``Authorization`` header
- network failures (404 / timeout / non-JSON / oversized response)
  map to clean 502 / 413 responses
- audit-log entry written on successful import
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.smoke._auth import authed_test_client

# Hostname we'll pretend is "trusted" throughout the suite. Matching
# the literal in the engine's allow-list is what makes the URL valid.
_TRUSTED_HOST = "raw.githubusercontent.com"
_TRUSTED_URL = f"https://{_TRUSTED_HOST}/owner/repo/main/example.pipeline-bundle.json"


def _make_app(
    *,
    allowed_hosts: list[str] | None = None,
    auth_token: str | None = None,
) -> tuple[Any, EngineConfig]:
    """Build a fresh EngineConfig + FastAPI app for one test.

    Returns the app and the config so tests can mutate config-driven
    behaviour without restarting fixtures.
    """
    tmp = tempfile.mkdtemp(prefix="dap-import-url-")
    config = EngineConfig(
        db_path=str(Path(tmp) / "state.db"),
        auth_jwt_secret="smoke-secret",
        template_registry_allowed_hosts=allowed_hosts or [],
        template_registry_auth_token=auth_token,
    )
    return create_app(config), config


@pytest.fixture
def client_with_registry() -> Iterator[TestClient]:
    """Standard client where the allow-list contains _TRUSTED_HOST."""
    app, _ = _make_app(allowed_hosts=[_TRUSTED_HOST])
    with authed_test_client(app) as c:
        yield c


@pytest.fixture
def client_without_registry() -> Iterator[TestClient]:
    """Client with allow-list empty — exercises the opt-in disable path."""
    app, _ = _make_app(allowed_hosts=[])
    with authed_test_client(app) as c:
        yield c


def _bundle_body(agent_id: str = "tpl_a") -> dict[str, Any]:
    """Minimal but valid ``bundle-export/1`` JSON."""
    return {
        "schema_version": "pipeline-export/1",
        "pipeline": {
            "name": "Registry Test Pipeline",
            "description": "Imported via URL for the test suite",
            "schema_version": "langgraph/1.0",
            "state_schema_ref": "PipelineState.v1",
            "entry_point": "n1",
            "nodes": [{"id": "n1", "agent_id": agent_id, "position": {"x": 0, "y": 0}}],
            "edges": [
                {"id": "e1", "source": "__start__", "target": "n1"},
                {"id": "e2", "source": "n1", "target": "__end__"},
            ],
            "defaults": {
                "max_attempts": 3,
                "budget_limit_usd": 5.0,
                "approval_required_nodes": [],
            },
        },
        "bundled_agents": {
            agent_id: {
                "name": "Test Agent",
                "role": "task_selector",
                "runtime_id": "api-call",
                "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
            }
        },
    }


def _patch_httpx_stream(
    *,
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    content_length_header: str | None = None,
    stream_raises: Exception | None = None,
) -> Any:
    """Build a ``patch()`` context for ``httpx.Client`` matching the
    endpoint's streaming pattern.

    The endpoint uses:

        with httpx.Client(timeout=...) as client:
            with client.stream("GET", url, headers=...) as response:
                response.status_code
                response.headers["content-length"]  # optional
                for chunk in response.iter_bytes(chunk_size=...):
                    ...

    So we need to mock two layers of context managers. ``stream_raises``
    surfaces an exception out of ``client.stream(...)`` — used for the
    timeout test.
    """
    if raw_body is not None:
        body = raw_body
    elif json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
    else:
        body = b""

    response_mock = MagicMock()
    response_mock.status_code = status_code
    headers = {}
    if content_length_header is not None:
        headers["content-length"] = content_length_header
    response_mock.headers = headers
    # Default: yield the whole body as one chunk. Tests that need
    # finer-grained chunking can mutate iter_bytes after construction.
    response_mock.iter_bytes.return_value = iter([body])

    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=response_mock)
    stream_cm.__exit__ = MagicMock(return_value=False)

    inner_client = MagicMock()
    if stream_raises is not None:
        inner_client.stream = MagicMock(side_effect=stream_raises)
    else:
        inner_client.stream = MagicMock(return_value=stream_cm)

    outer_cm = MagicMock()
    outer_cm.__enter__ = MagicMock(return_value=inner_client)
    outer_cm.__exit__ = MagicMock(return_value=False)

    return patch(
        "dap_engine.api.pipeline_bundles.httpx.Client",
        return_value=outer_cm,
    ), inner_client.stream


# --------------------------------------------------------------------- #
# 1. Happy path
# --------------------------------------------------------------------- #


def test_import_from_url_happy_path(client_with_registry: TestClient) -> None:
    """Trusted URL serves a valid bundle → pipeline created, audit row written."""
    patcher, _ = _patch_httpx_stream(json_body=_bundle_body())
    with patcher:
        response = client_with_registry.post(
            "/pipelines/import-from-url",
            json={"url": _TRUSTED_URL},
        )

    assert response.status_code == 201, response.text
    pipeline = response.json()
    assert pipeline["name"] == "Registry Test Pipeline"
    assert pipeline["id"]
    # Caller follows up to read the pipeline back through the standard path.
    follow = client_with_registry.get(f"/pipelines/{pipeline['id']}")
    assert follow.status_code == 200


# --------------------------------------------------------------------- #
# 2. SSRF guard
# --------------------------------------------------------------------- #


def test_import_from_url_blocks_unlisted_host(client_with_registry: TestClient) -> None:
    """A URL on a non-allowed-list host returns 422 before any network call."""
    response = client_with_registry.post(
        "/pipelines/import-from-url",
        json={"url": "https://attacker.example.com/evil.pipeline-bundle.json"},
    )
    assert response.status_code == 422
    assert "allow-list" in response.text.lower()


def test_import_from_url_blocks_loopback_bypass(client_with_registry: TestClient) -> None:
    """``0.0.0.0`` and ``169.254.169.254`` are blocked even if accidentally allow-listed."""
    app, _ = _make_app(allowed_hosts=["0.0.0.0", "169.254.169.254"])
    with authed_test_client(app) as c:
        r1 = c.post("/pipelines/import-from-url", json={"url": "http://0.0.0.0/x.json"})
        assert r1.status_code == 422
        assert "ssrf" in r1.text.lower()
        r2 = c.post(
            "/pipelines/import-from-url",
            json={"url": "http://169.254.169.254/latest/meta-data/"},
        )
        assert r2.status_code == 422
        assert "ssrf" in r2.text.lower()


def test_import_from_url_rejects_userinfo_in_url(
    client_with_registry: TestClient,
) -> None:
    """``https://user:pass@host/...`` is refused before any network call.

    Credentials in the URL would otherwise leak into the audit log
    + error responses. Operators authenticate via the engine-side
    ``DAP_TEMPLATE_REGISTRY_AUTH_TOKEN`` env var.
    """
    response = client_with_registry.post(
        "/pipelines/import-from-url",
        json={"url": f"https://user:secret@{_TRUSTED_HOST}/owner/repo/main/x.json"},
    )
    assert response.status_code == 422
    body = response.text.lower()
    assert "userinfo" in body
    # And the leaked secret must NOT appear in the response.
    assert "secret" not in response.text


def test_import_from_url_rejects_non_https_for_remote(
    client_with_registry: TestClient,
) -> None:
    """Plain http:// is only allowed for localhost (dev). Remote hosts must be https."""
    response = client_with_registry.post(
        "/pipelines/import-from-url",
        json={"url": f"http://{_TRUSTED_HOST}/owner/repo/main/x.json"},
    )
    assert response.status_code == 422
    assert "https" in response.text.lower()


# --------------------------------------------------------------------- #
# 3. Opt-in feature gate
# --------------------------------------------------------------------- #


def test_import_from_url_disabled_when_allow_list_empty(
    client_without_registry: TestClient,
) -> None:
    """Default config has empty allow-list → endpoint refuses every URL."""
    response = client_without_registry.post(
        "/pipelines/import-from-url",
        json={"url": _TRUSTED_URL},
    )
    assert response.status_code == 422
    assert "DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS" in response.text


# --------------------------------------------------------------------- #
# 4. Bearer token forwarding
# --------------------------------------------------------------------- #


def test_import_from_url_attaches_bearer_when_configured() -> None:
    """When the engine has a registry token, the streamed GET carries
    the Authorization header."""
    app, _ = _make_app(
        allowed_hosts=[_TRUSTED_HOST],
        auth_token="ghp_test_token_value",
    )
    patcher, stream_mock = _patch_httpx_stream(json_body=_bundle_body())
    with authed_test_client(app) as c, patcher:
        response = c.post(
            "/pipelines/import-from-url",
            json={"url": _TRUSTED_URL},
        )

    assert response.status_code == 201, response.text
    # The mock recorded our stream("GET", ...) call; check the header.
    call_kwargs = stream_mock.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer ghp_test_token_value"


# --------------------------------------------------------------------- #
# 5. Network / payload failure modes
# --------------------------------------------------------------------- #


def test_import_from_url_maps_404_to_502(client_with_registry: TestClient) -> None:
    """Origin returns 404 → engine returns 502 with the upstream code."""
    patcher, _ = _patch_httpx_stream(status_code=404)
    with patcher:
        response = client_with_registry.post(
            "/pipelines/import-from-url",
            json={"url": _TRUSTED_URL},
        )
    assert response.status_code == 502
    assert "404" in response.text


def test_import_from_url_maps_timeout_to_502(client_with_registry: TestClient) -> None:
    """``httpx.TimeoutException`` becomes a 502 with a clear message."""
    patcher, _ = _patch_httpx_stream(stream_raises=httpx.TimeoutException("simulated"))
    with patcher:
        response = client_with_registry.post(
            "/pipelines/import-from-url",
            json={"url": _TRUSTED_URL},
        )
    assert response.status_code == 502
    assert "timeout" in response.text.lower()


def test_import_from_url_rejects_malformed_json(
    client_with_registry: TestClient,
) -> None:
    """Non-JSON body → 422 (the inner bundle parser rejects it)."""
    patcher, _ = _patch_httpx_stream(raw_body=b"not even JSON")
    with patcher:
        response = client_with_registry.post(
            "/pipelines/import-from-url",
            json={"url": _TRUSTED_URL},
        )
    assert response.status_code == 422
    assert "bundle JSON failed validation" in response.text


def test_import_from_url_rejects_oversized_response(
    client_with_registry: TestClient,
) -> None:
    """Streaming aborts when accumulated chunks exceed the cap."""
    # 11 MB body, no Content-Length header → the chunk-accumulating
    # loop catches the overflow.
    big_body = b"\x00" * (11 * 1024 * 1024)
    patcher, _ = _patch_httpx_stream(raw_body=big_body)
    with patcher:
        response = client_with_registry.post(
            "/pipelines/import-from-url",
            json={"url": _TRUSTED_URL},
        )
    assert response.status_code == 413
    assert "exceeds" in response.text.lower()


def test_import_from_url_rejects_content_length_over_cap(
    client_with_registry: TestClient,
) -> None:
    """When Content-Length is advertised over the cap, abort before
    consuming any body bytes."""
    # Body itself is tiny, but the header lies — we should refuse
    # without even attempting to read the body.
    patcher, _ = _patch_httpx_stream(
        json_body=_bundle_body(),
        content_length_header=str(50 * 1024 * 1024),  # 50 MB advertised
    )
    with patcher:
        response = client_with_registry.post(
            "/pipelines/import-from-url",
            json={"url": _TRUSTED_URL},
        )
    assert response.status_code == 413
    assert "content-length" in response.text.lower()


# --------------------------------------------------------------------- #
# 6. Audit log entry
# --------------------------------------------------------------------- #


def test_import_from_url_writes_audit_entry() -> None:
    """Successful import lands a ``pipeline.imported_from_url`` audit row.

    The HTTP audit endpoint is admin-only (returns 404 for non-admins
    as an anti-enumeration measure). We query the audit_log table via
    SQL directly instead of promoting the test user — keeps the test
    focused on "did the audit write happen?" rather than coupling it
    to admin-promotion plumbing.
    """
    import sqlite3

    app, cfg = _make_app(allowed_hosts=[_TRUSTED_HOST])
    patcher, _ = _patch_httpx_stream(json_body=_bundle_body())
    url_with_query = f"{_TRUSTED_URL}?token=do-not-log#frag"

    with authed_test_client(app) as c, patcher:
        response = c.post(
            "/pipelines/import-from-url",
            json={"url": url_with_query},
        )
        assert response.status_code == 201
        pipeline_id = response.json()["id"]

    # Read audit_log directly via SQL. The engine writes JSON-encoded
    # event_data so we json.loads() it to verify the URL+pipeline_id
    # round-tripped intact.
    with sqlite3.connect(cfg.db_path) as cx:
        rows = cx.execute(
            "SELECT event_data FROM audit_log WHERE event_type = ?",
            ("pipeline.imported_from_url",),
        ).fetchall()

    assert rows, "no pipeline.imported_from_url audit entry written"
    matching = [
        data
        for (raw,) in rows
        for data in [json.loads(raw)]
        if data.get("url") == _TRUSTED_URL and data.get("pipeline_id") == pipeline_id
    ]
    assert matching, f"audit entry missing url/pipeline_id; rows={rows}"
    assert "do-not-log" not in json.dumps(rows)


# Suppress unused-import warnings — ``Session`` is referenced only by
# the docstring examples; pinning it keeps mypy honest if a test grows
# into a fixture-driven flow later.
_ = Session

"""The cortex project-bootstrap bundle imports and builds a sound graph (#797).

``examples/pipelines/project-bootstrap.pipeline-bundle.json`` is the vendored copy
of cortex ``cortex/dap_bundles/project-bootstrap.pipeline-bundle.json`` (cortex
epic #749, lagowski/cortex-project#777) with ``_comment`` and
``install_instructions`` stripped. It is a 13-node pipeline with five human gates.

These tests are a DRY verification: the bundle is imported through the real API,
its edges are routed through the engine's own edge chooser, and one gated run
is started with the ``python-func`` backend faked so it never imports cortex or
calls GitHub/k8s. The run must pause before ``gate_params``.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.api import run_callable_preflight
from dap_engine.app import EngineConfig, create_app
from dap_engine.execution.runner import _edge_chooser
from dap_runtimes import RuntimeRegistry
from dap_types import OutputCallback, RuntimeResult, RuntimeTask
from dap_types.pipeline import Pipeline
from dap_types.state import PipelineState
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "pipelines"
    / "project-bootstrap.pipeline-bundle.json"
)
GATES = ["gate_params", "gate_scope", "gate_scaffold", "gate_provision", "gate_decompose"]
NODES = [
    "validate_params",
    "gate_params",
    "scope_mvp",
    "gate_scope",
    "scaffold_repo",
    "gate_scaffold",
    "wire_gate",
    "provision_db",
    "gate_provision",
    "register_project",
    "decompose_issues",
    "gate_decompose",
    "finalize",
]
END = "__end__"
RUN_INPUTS = {
    "project_slug": "demo-app",
    "repo_layout": "single",
    "stack": "python",
    "db_choice": "postgres",
    "arena_verdict": "go",
}
POLL_TIMEOUT_S = 10.0


@pytest.fixture
def client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory(prefix="dap-project-bootstrap-") as tmp:
        app = create_app(
            EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="smoke-secret")
        )
        with authed_test_client(app) as c:
            yield c


def _import(client: TestClient) -> dict[str, Any]:
    response = client.post("/pipelines/import", json=json.loads(BUNDLE.read_text()))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _pipeline(client: TestClient) -> Pipeline:
    imported = _import(client)
    fetched = client.get(f"/pipelines/{imported['id']}")
    assert fetched.status_code == 200, fetched.text
    return Pipeline.model_validate(fetched.json())


def _route(pipeline: Pipeline, source: str, state: dict[str, Any]) -> str:
    edges = [e for e in pipeline.edges if e.source == source]
    assert edges, f"{source} has no outgoing edge"
    chooser, _ = _edge_chooser(edges)
    # Node returns that are not PipelineState fields land in ``extensions``.
    full = {"run_id": "r", "repo": "", "branch": "", "extensions": state}
    return str(chooser(PipelineState.model_validate(full)))


def test_cortex_min_dap_version_is_refused(client: TestClient) -> None:
    """Why the vendored copy lowers ``min_dap_version``: cortex's ``0.8.0`` is refused."""
    bundle = json.loads(BUNDLE.read_text())
    assert "_comment" not in bundle
    assert "install_instructions" not in bundle
    bundle["min_dap_version"] = "0.8.0"

    response = client.post("/pipelines/import", json=bundle)

    assert response.status_code == 422, response.text
    assert "Bundle requires DAP >= 0.8.0" in response.text


def test_project_bootstrap_bundle_imports_with_five_interrupt_gates(
    client: TestClient,
) -> None:
    pipeline = _pipeline(client)

    assert [n.id for n in pipeline.nodes] == NODES
    assert pipeline.entry_point == "validate_params"
    assert list(pipeline.defaults.approval_required_nodes) == GATES

    node_ids = set(NODES)
    for edge in pipeline.edges:
        assert edge.source in node_ids, edge
        assert edge.target in node_ids | {END}, edge
    # No orphan: every node is reachable from the entry point and has a way out.
    reachable, frontier = {pipeline.entry_point}, [pipeline.entry_point]
    while frontier:
        source = frontier.pop()
        for edge in pipeline.edges:
            if edge.source == source and edge.target != END and edge.target not in reachable:
                reachable.add(edge.target)
                frontier.append(edge.target)
    assert reachable == node_ids
    assert {e.source for e in pipeline.edges} == node_ids


@pytest.mark.parametrize(
    ("gate", "approved_target"),
    [
        ("gate_params", "scope_mvp"),
        ("gate_scope", "scaffold_repo"),
        ("gate_provision", "register_project"),
        ("gate_decompose", "decompose_issues"),
    ],
)
def test_approval_flags_route_each_gate(
    client: TestClient, gate: str, approved_target: str
) -> None:
    pipeline = _pipeline(client)
    flag = f"{gate}_approved"

    assert _route(pipeline, gate, {flag: True}) == approved_target
    assert _route(pipeline, gate, {flag: False}) == END


def test_gate_scaffold_is_a_pass_through(client: TestClient) -> None:
    pipeline = _pipeline(client)

    assert _route(pipeline, "gate_scaffold", {}) == "wire_gate"
    assert _route(pipeline, "gate_scaffold", {"gate_scaffold_approved": False}) == "wire_gate"


def test_decompose_loop_reaches_its_gate_before_the_flag_exists(client: TestClient) -> None:
    """First pass: ``gate_decompose_approved`` is unset, so the run must stop at the gate.

    A missing field compares False in DAP, so an ``== false`` edge never matches
    an absent flag and the run would end silently instead of pausing. The
    vendored bundle routes on ``not (flag == true)`` instead.
    """
    pipeline = _pipeline(client)

    assert _route(pipeline, "decompose_issues", {}) == "gate_decompose"
    assert _route(pipeline, "decompose_issues", {"gate_decompose_approved": False}) == (
        "gate_decompose"
    )
    # Second pass, after approval: the issues are filed, then finalize.
    assert _route(pipeline, "decompose_issues", {"gate_decompose_approved": True}) == "finalize"


@pytest.fixture
def no_cortex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let ``POST /runs`` pass its callable preflight without cortex installed."""

    def resolve(_path: str) -> tuple[Any, None]:
        return (lambda **_: {}), None

    monkeypatch.setattr(run_callable_preflight, "resolve_callable", resolve)


@pytest.mark.usefixtures("no_cortex")
def test_run_inputs_belong_in_extensions(client: TestClient) -> None:
    """DAP's state is fixed: top-level bootstrap inputs are rejected, not dropped."""
    imported = _import(client)
    response = client.post(
        "/runs", json={"pipeline_id": imported["id"], "initial_state": RUN_INPUTS}
    )

    assert response.status_code == 422, response.text
    assert "Invalid initial_state" in response.text


@pytest.mark.usefixtures("no_cortex")
def test_dry_run_pauses_before_gate_params(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real graph build + interrupt_before, with python-func faked (no cortex, no network)."""
    calls: list[str] = []

    async def execute(task: RuntimeTask, on_output: OutputCallback | None = None) -> RuntimeResult:
        calls.append(str(task.runtime_config.get("callable_path")))
        return RuntimeResult(success=True, output="ok", duration_ms=1)

    registry: RuntimeRegistry = client.app.state.runtime_registry  # type: ignore[attr-defined]
    monkeypatch.setattr(registry.get("python-func"), "execute", execute)

    imported = _import(client)
    started = client.post(
        "/runs",
        json={
            "pipeline_id": imported["id"],
            "initial_state": {"extensions": RUN_INPUTS},
        },
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]

    deadline = time.monotonic() + POLL_TIMEOUT_S
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = client.get(f"/runs/{run_id}").json()
        if run["final_status"] != "running":
            break
        time.sleep(0.05)

    assert run["final_status"] == "paused", run
    assert calls == ["cortex.pipelines.project_bootstrap.nodes:_validate_params"]

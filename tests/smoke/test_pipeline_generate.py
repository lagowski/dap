"""``/pipelines/generate``: the dashboard prompt box's engine side (cortex#1774 S5).

The generator is a stand-in Python script driven by the prompt text, so each
test exercises the real subprocess contract (argv, exit code, stdout JSON) and
the real import path, with no LLM.
"""

from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from dap_engine.app import EngineConfig, create_app
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

_AGENT: dict[str, Any] = {
    "name": "Generated Step",
    "role": "task_selector",
    "runtime_id": "api-call",
    "runtime_config": {"provider": "anthropic", "model_id": "claude-haiku-4-5"},
    "prompt_template": "<agent_prompt><role>x</role></agent_prompt>",
    "input_schema": [],
    "output_schema": [],
    "constraints": [],
    "budget_limit_usd": None,
    "timeout_ms": 60_000,
}

BUNDLE: dict[str, Any] = {
    "schema_version": "pipeline-export/2",
    "_comment": "made by the fake generator",
    "pipeline": {
        "name": "Generated Triage",
        "description": "from a prompt",
        "schema_version": "langgraph/1.0",
        "state_schema_ref": "PipelineState.v1",
        "entry_point": "n1",
        "nodes": [{"id": "n1", "agent_id": "gen_step", "position": {"x": 0, "y": 0}}],
        "edges": [
            {"id": "e1", "source": "__start__", "target": "n1"},
            {"id": "e2", "source": "n1", "target": "__end__"},
        ],
        "defaults": {"max_attempts": 3, "budget_limit_usd": 5.0, "approval_required_nodes": []},
    },
    "bundled_agents": {"gen_step": _AGENT},
}

# The prompt picks the behaviour; argv[-1] must be the prompt (the contract).
_SCRIPT = textwrap.dedent(
    """
    import json, sys, time
    prompt = sys.argv[-1]
    bundle = json.loads(sys.argv[1])
    if prompt == "refuse":
        print("no valid spec after 2 attempts", file=sys.stderr); sys.exit(2)
    if prompt == "crash":
        print("Traceback: boom", file=sys.stderr); sys.exit(1)
    if prompt == "noise":
        print("✓ package written"); sys.exit(0)
    if prompt == "nobundle":
        print(json.dumps({"name": "x"})); sys.exit(0)
    if prompt == "badbundle":
        print(json.dumps({"bundle": {"schema_version": "pipeline-export/9"}})); sys.exit(0)
    if prompt == "slow":
        time.sleep(5)
    print("progress goes to stderr", file=sys.stderr)
    print(json.dumps({"name": "generated-triage", "nodes": 1, "bundle": bundle, "echo": prompt}))
    """
)


MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    script = tmp_path / "fake_generator.py"
    script.write_text(_SCRIPT)
    stack: list[Any] = []

    def _make(command: list[str] | None = None, timeout: float = 30.0) -> TestClient:
        if command is None:
            command = [sys.executable, str(script), json.dumps(BUNDLE)]
        config = EngineConfig(
            db_path=str(tmp_path / f"state{len(stack)}.db"),
            auth_jwt_secret="smoke-test-secret-32-chars-padding",
            pipeline_generator_command=command or None,
            pipeline_generator_timeout_seconds=timeout,
        )
        cm = authed_test_client(create_app(config))
        stack.append(cm)
        return cm.__enter__()

    yield _make
    for cm in reversed(stack):
        cm.__exit__(None, None, None)


def test_status_reports_enabled_only_when_configured(make_client: MakeClient) -> None:
    assert make_client().get("/pipelines/generate").json() == {"enabled": True}
    assert make_client(command=[]).get("/pipelines/generate").json() == {"enabled": False}


def test_unconfigured_engine_answers_503(make_client: MakeClient) -> None:
    res = make_client(command=[]).post("/pipelines/generate", json={"prompt": "triage email"})
    assert res.status_code == 503
    assert "DAP_PIPELINE_GENERATOR_COMMAND" in res.json()["detail"]


def test_generated_bundle_round_trips_through_import(make_client: MakeClient) -> None:
    client = make_client()
    res = client.post("/pipelines/generate", json={"prompt": "triage support email"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "generated-triage" and body["nodes"] == 1
    assert body["bundle"] == BUNDLE  # lossless: _comment and all

    imported = client.post("/pipelines/import", json=body["bundle"])
    assert imported.status_code == 201, imported.text
    assert imported.json()["name"] == "Generated Triage"


def test_prompt_is_passed_as_one_argv_element_without_a_shell(
    make_client: MakeClient, tmp_path: Path
) -> None:
    marker = tmp_path / "pwned"
    prompt = f"x; touch {marker} $(touch {marker})"
    client = make_client()
    res = client.post("/pipelines/generate", json={"prompt": prompt})
    assert res.status_code == 200, res.text
    assert not marker.exists()


@pytest.mark.parametrize(
    ("prompt", "status", "needle"),
    [
        ("refuse", 422, "no valid spec"),
        ("crash", 502, "exit 1"),
        ("noise", 502, "not JSON"),
        ("nobundle", 502, "no 'bundle'"),
        ("badbundle", 502, "invalid bundle"),
    ],
)
def test_generator_failures_map_to_clear_errors(
    make_client: MakeClient, prompt: str, status: int, needle: str
) -> None:
    res = make_client().post("/pipelines/generate", json={"prompt": prompt})
    assert res.status_code == status, res.text
    assert needle in res.json()["detail"]


def test_timeout_answers_504(make_client: MakeClient) -> None:
    res = make_client(timeout=0.5).post("/pipelines/generate", json={"prompt": "slow"})
    assert res.status_code == 504


def test_missing_command_answers_502(make_client: MakeClient, tmp_path: Path) -> None:
    res = make_client(command=[str(tmp_path / "no-such-generator")]).post(
        "/pipelines/generate", json={"prompt": "x"}
    )
    assert res.status_code == 502 and "could not start" in res.json()["detail"]


@pytest.mark.parametrize("prompt", ["", "x" * 4001])
def test_prompt_length_is_bounded(make_client: MakeClient, prompt: str) -> None:
    assert make_client().post("/pipelines/generate", json={"prompt": prompt}).status_code == 422


def test_generation_requires_login(tmp_path: Path) -> None:
    config = EngineConfig(
        db_path=str(tmp_path / "anon.db"),
        auth_jwt_secret="smoke-test-secret-32-chars-padding",
        pipeline_generator_command=["true"],
    )
    with TestClient(create_app(config)) as anon:
        assert anon.post("/pipelines/generate", json={"prompt": "x"}).status_code == 401
        assert anon.get("/pipelines/generate").status_code == 401

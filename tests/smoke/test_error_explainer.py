"""Deterministic error explainer (#691, slice 1)."""

from __future__ import annotations

import pytest
from dap_engine.diagnostics.error_explainer import explain_node_error


def test_empty_error_is_unrecognized() -> None:
    exp = explain_node_error(None)
    assert exp.recognized is False
    assert "no error" in exp.cause.lower()
    exp2 = explain_node_error("   ")
    assert exp2.recognized is False


def test_claude_bridge_gate() -> None:
    err = (
        "python-func: 'cortex.nodes.x:run' raised BackendError: ... "
        "extensions.claude_bridge_enabled was not set — refusing to merge. "
        "Set extensions.claude_bridge_enabled=true to opt in, or "
        "extensions.skip_claude_bridge_review=true to bypass."
    )
    exp = explain_node_error(err)
    assert exp.recognized is True
    targets = {a.target for a in exp.actions}
    assert "claude_bridge_enabled" in targets
    assert "skip_claude_bridge_review" in targets
    assert all(a.kind == "set_extension" for a in exp.actions)


def test_unimportable_callable() -> None:
    err = (
        "python-func: cannot import 'cortex.nodes.coder:run' "
        "(ModuleNotFoundError: No module named 'cortex')"
    )
    exp = explain_node_error(err, runtime_id="python-func")
    assert exp.recognized is True
    assert "isn't installed" in exp.cause or "can't import" in exp.cause
    assert any("install" in a.text.lower() for a in exp.actions)


def test_callable_path_malformed() -> None:
    err = "python-func: callable_path must use 'module.path:func_name' format (got 'foo')"
    exp = explain_node_error(err)
    assert exp.recognized is True
    assert any(a.kind == "edit_agent" for a in exp.actions)


def test_out_of_credits() -> None:
    err = "openai error: insufficient_quota — your credit balance is too low"
    exp = explain_node_error(err)
    assert exp.recognized is True
    assert "credit" in exp.cause.lower() or "quota" in exp.cause.lower()


def test_rate_limit() -> None:
    err = "Error 429: rate limit exceeded, please slow down"
    exp = explain_node_error(err)
    assert exp.recognized is True
    assert any(a.kind == "retry" for a in exp.actions)


def test_missing_api_key_extracts_env_name() -> None:
    err = "401 Unauthorized: invalid api key. Set GEMINI_API_KEY and retry."
    exp = explain_node_error(err)
    assert exp.recognized is True
    add_env = [a for a in exp.actions if a.kind == "add_env"]
    assert add_env
    assert add_env[0].target == "GEMINI_API_KEY"


def test_missing_api_key_prefers_underscored_var_over_bare_word() -> None:
    # "API" appears before the real var; the underscored token must win (#716 review).
    err = "Invalid API key — set ANTHROPIC_API_KEY and retry."
    exp = explain_node_error(err)
    add_env = [a for a in exp.actions if a.kind == "add_env"]
    assert add_env
    assert add_env[0].target == "ANTHROPIC_API_KEY"


def test_missing_api_key_skips_generic_underscored_token() -> None:
    # "API_KEY" in prose is generic (all parts generic) — the real var wins (#718 review).
    err = "the API_KEY format is wrong; unauthorized. Set OPENAI_API_KEY."
    exp = explain_node_error(err)
    add_env = [a for a in exp.actions if a.kind == "add_env"]
    assert add_env
    assert add_env[0].target == "OPENAI_API_KEY"


def test_contract_mismatch_extracts_field() -> None:
    err = "agent output did not match output_schema: required field 'test_files' missing"
    exp = explain_node_error(err)
    assert exp.recognized is True
    assert "test_files" in exp.cause


def test_timeout() -> None:
    exp = explain_node_error("python-func: 'x:run' timed out after 30000ms")
    assert exp.recognized is True
    assert any("timeout_ms" in a.text for a in exp.actions)


def test_budget() -> None:
    exp = explain_node_error("run aborted: budget limit exceeded ($5.00)")
    assert exp.recognized is True
    assert any(a.kind == "raise_budget" for a in exp.actions)


def test_unrecognized_falls_back_with_doc_link() -> None:
    exp = explain_node_error("Segmentation fault (core dumped) at 0xdeadbeef")
    assert exp.recognized is False
    assert exp.source == "deterministic"
    assert exp.docs  # generic fallback points at troubleshooting docs
    assert any("AI" in a.text or "LLM" in a.text for a in exp.actions)


# ---- HTTP endpoint -------------------------------------------------------

import tempfile  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from dap_engine.app import EngineConfig, create_app  # noqa: E402
from dap_engine.persistence.models import NodeExecutionLogORM, UserORM  # noqa: E402
from dap_engine.persistence.run_models import RunORM  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from tests.smoke._auth import DEFAULT_TEST_EMAIL, authed_test_client  # noqa: E402


@pytest.fixture
def http() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    tmp = tempfile.mkdtemp(prefix="dap-explain-")
    config = EngineConfig(db_path=str(Path(tmp) / "state.db"), auth_jwt_secret="test-secret")
    app = create_app(config)
    with authed_test_client(app) as c:
        yield c, app.state.session_factory


def _seed_failed_node(factory: sessionmaker[Session], *, error_message: str) -> tuple[str, str]:
    with factory() as s:
        user = (
            s.scalars(
                select(UserORM).where(UserORM.email == DEFAULT_TEST_EMAIL)  # type: ignore[arg-type]
            )
            .unique()
            .one()
        )
        uid = user.id
        now = datetime.now(UTC)
        run_id = "run-explain-1"
        s.add(
            RunORM(
                id=run_id,
                user_id=uid,
                project_id=None,
                pipeline_id="pipe-1",
                pipeline_version=1,
                trigger_source="test",
                initial_state={},
                current_node=None,
                node_statuses={},
                final_status="failed",
                started_at=now,
                tokens_used=0,
                cost_usd=0.0,
                created_at=now,
                updated_at=now,
            )
        )
        s.flush()
        s.add(
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
                status="failed",
                error_message=error_message,
            )
        )
        s.commit()
        return run_id, "n1"


def test_explain_endpoint_returns_recognized_explanation(
    http: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = http
    run_id, node_id = _seed_failed_node(
        factory,
        error_message="401 Unauthorized: invalid api key. Set OPENAI_API_KEY.",
    )
    resp = client.get(f"/runs/{run_id}/nodes/{node_id}/explain")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recognized"] is True
    assert body["source"] == "deterministic"
    assert any(a.get("target") == "OPENAI_API_KEY" for a in body["actions"])


def test_explain_endpoint_404_for_unknown_node(
    http: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = http
    assert client.get("/runs/nope/nodes/nope/explain").status_code == 404


# ---- LLM fallback (#691 slice 2) ------------------------------------------


class _FakeLLMAdapter:
    def __init__(self, output: str) -> None:
        self.output = output
        self.last_task = None

    async def execute(self, task, on_output=None):  # type: ignore[no-untyped-def]
        from dap_types import RuntimeResult

        self.last_task = task
        return RuntimeResult(success=True, output=self.output)


@pytest.mark.asyncio
async def test_explain_error_llm_no_provider_returns_none() -> None:
    from dap_engine.diagnostics.error_explainer import explain_error_llm

    assert await explain_error_llm("boom", runtime_id="bash", env={}) is None


@pytest.mark.asyncio
async def test_explain_error_llm_uses_provider() -> None:
    from dap_engine.diagnostics.error_explainer import explain_error_llm

    fake = _FakeLLMAdapter("It failed because the package is missing. Install it.")
    exp = await explain_error_llm(
        "weird unrecognised error",
        runtime_id="bash",
        env={"ANTHROPIC_API_KEY": "sk-secret-zzz"},
        adapter=fake,
    )
    assert exp is not None
    assert exp.source == "llm"
    assert exp.recognized is True
    assert "package is missing" in exp.cause
    # the error text is in the prompt for grounding; the key value never is.
    assert "weird unrecognised error" in fake.last_task.prompt_xml  # type: ignore[union-attr]
    assert "sk-secret-zzz" not in fake.last_task.runtime_config["system_prompt"]  # type: ignore[union-attr]


def test_explain_endpoint_ai_falls_back_to_deterministic_without_provider(
    http: tuple[TestClient, sessionmaker[Session]],
) -> None:
    # With ?ai=1 but no provider configured (CI), the endpoint returns the
    # deterministic explanation rather than erroring.
    client, factory = http
    run_id, node_id = _seed_failed_node(factory, error_message="Segfault 0xdeadbeef (unrecognised)")
    resp = client.get(f"/runs/{run_id}/nodes/{node_id}/explain?ai=1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "deterministic"

"""Tests for the codex (OpenAI Codex CLI) runtime adapter.

The CLI subprocess is mocked — real CLI calls would burn tokens.
Live tests behind ``DAP_E2E_CODEX=1`` (separate file, not in CI).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from dap_runtimes import CodexAdapter
from dap_types import RuntimeTask

from .conftest import build_subprocess_mock

_PATCH_PATH = "dap_runtimes.adapters._cli_base.asyncio.create_subprocess_exec"
_WHICH_PATH = "dap_runtimes.adapters._cli_base.shutil.which"


@pytest.fixture
def with_api_key() -> Iterator[None]:
    original = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "sk-test-fake"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original


def _task(
    *,
    model_id: str = "gpt-5-codex",
    binary_path: str | None = None,
    extra_args: Any = None,
    timeout_ms: int = 60_000,
    env: dict[str, str] | None = None,
    project_env_vars: dict[str, str] | None = None,
) -> RuntimeTask:
    runtime_config: dict[str, Any] = {"model_id": model_id}
    if binary_path is not None:
        runtime_config["binary_path"] = binary_path
    if extra_args is not None:
        runtime_config["extra_args"] = extra_args
    if env is not None:
        runtime_config["env"] = env
    return RuntimeTask(
        execution_id="exec-1",
        prompt_xml="<agent_prompt><role>impl</role><task>do it</task></agent_prompt>",
        working_directory="/tmp",
        timeout_ms=timeout_ms,
        runtime_config=runtime_config,
        project_env_vars=project_env_vars or {},
    )


def _success_payload(
    text: str = "ok",
    *,
    input_tokens: int = 100,
    output_tokens: int = 200,
    finish_reason: str = "stop",
    text_key: str = "output_text",
) -> bytes:
    return json.dumps(
        {
            text_key: text,
            "finish_reason": finish_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }
    ).encode("utf-8")


# _build_subprocess_mock moved to tests/smoke/conftest.py — shared across
# claude_code / codex / gemini_cli tests (audit refactor #7).


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


async def test_healthcheck_missing_binary(with_api_key: None) -> None:
    adapter = CodexAdapter()
    with patch(_WHICH_PATH, return_value=None):
        health = await adapter.healthcheck()
    assert health.available is False
    assert health.missing is not None
    assert any("codex" in m for m in health.missing)


async def test_healthcheck_missing_api_key() -> None:
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        adapter = CodexAdapter()
        with patch(_WHICH_PATH, return_value="/usr/local/bin/codex"):
            health = await adapter.healthcheck()
        assert health.available is False
        assert health.missing is not None
        assert any("OPENAI_API_KEY" in m for m in health.missing)
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved


async def test_healthcheck_available(with_api_key: None) -> None:
    adapter = CodexAdapter()
    proc = build_subprocess_mock(stdout=b"codex 0.5.0\n")
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        health = await adapter.healthcheck()
    assert health.available is True
    assert health.version is not None
    assert "codex" in health.version


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_missing_model_id_returns_error(with_api_key: None) -> None:
    adapter = CodexAdapter()
    task = _task()
    task.runtime_config["model_id"] = ""
    result = await adapter.execute(task)
    assert result.success is False
    assert any("model_id" in e for e in result.errors)


async def test_missing_api_key_returns_error() -> None:
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        adapter = CodexAdapter()
        result = await adapter.execute(_task())
        assert result.success is False
        assert any("OPENAI_API_KEY" in e for e in result.errors)
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved


async def test_binary_not_found_returns_error(with_api_key: None) -> None:
    adapter = CodexAdapter()
    with patch(_WHICH_PATH, return_value=None):
        result = await adapter.execute(_task())
    assert result.success is False
    assert any("Codex binary not found" in e for e in result.errors)


async def test_extra_args_must_be_list_of_strings(with_api_key: None) -> None:
    adapter = CodexAdapter()
    with patch(_WHICH_PATH, return_value="/usr/local/bin/codex"):
        result = await adapter.execute(_task(extra_args="not-a-list"))
    assert result.success is False
    assert any("extra_args" in e for e in result.errors)


async def test_extra_args_with_non_string_element(with_api_key: None) -> None:
    adapter = CodexAdapter()
    with patch(_WHICH_PATH, return_value="/usr/local/bin/codex"):
        result = await adapter.execute(_task(extra_args=["--ok", 42]))
    assert result.success is False
    assert any("extra_args" in e for e in result.errors)


async def test_binary_path_must_be_string(with_api_key: None) -> None:
    adapter = CodexAdapter()
    task = _task()
    task.runtime_config["binary_path"] = 123
    result = await adapter.execute(task)
    assert result.success is False
    assert any("binary_path" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Successful execute
# ---------------------------------------------------------------------------


async def test_execute_success_extracts_output_and_usage(
    with_api_key: None,
) -> None:
    adapter = CodexAdapter()
    proc = build_subprocess_mock(
        stdout=_success_payload(text="hi", input_tokens=200, output_tokens=80)
    )
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    assert result.output == "hi"
    assert result.tokens_used == 280  # 200 input + 80 output
    # No cost from the CLI — api-call provider has the pricing tables.
    assert result.cost_usd is None
    assert result.structured is not None
    assert result.structured["provider"] == "codex"
    assert result.structured["stop_reason"] == "stop"
    assert result.structured["timed_out"] is False

    # Verify CLI invocation shape
    argv = create_mock.call_args.args
    assert argv[0].endswith("codex") or argv[0] == "codex"
    assert "exec" in argv
    assert "--json" in argv
    assert "--model" in argv
    assert "gpt-5-codex" in argv


async def test_execute_passes_prompt_via_stdin(with_api_key: None) -> None:
    adapter = CodexAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        await adapter.execute(_task())

    # Prompt is now fed via the streamed stdin write (#662), not communicate().
    sent = proc.stdin.write.call_args.args[0].decode("utf-8")
    assert sent.startswith("<agent_prompt>")


async def test_execute_with_extra_args(with_api_key: None) -> None:
    adapter = CodexAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(_task(extra_args=["--sandbox", "workspace-write"]))

    argv = create_mock.call_args.args
    assert "--sandbox" in argv
    assert "workspace-write" in argv


async def test_camelcase_token_keys_are_supported(with_api_key: None) -> None:
    """Some codex CLI builds emit camelCase keys instead of snake_case."""
    adapter = CodexAdapter()
    payload = json.dumps(
        {
            "output_text": "hi",
            "usage": {
                "promptTokens": 50,
                "completionTokens": 40,
            },
            "finishReason": "stop",
        }
    ).encode("utf-8")
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    assert result.tokens_used == 90
    assert result.structured is not None
    assert result.structured["stop_reason"] == "stop"


async def test_alternate_text_keys_are_supported(with_api_key: None) -> None:
    """Codex CLI shape drift: ``result`` instead of ``output_text``."""
    adapter = CodexAdapter()
    payload = json.dumps(
        {
            "result": "fallback-text",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    ).encode("utf-8")
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    assert result.output == "fallback-text"


async def test_per_agent_env_overrides_project_env(
    with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#65 layering: runtime_config.env (highest) > project_env_vars > engine env."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    adapter = CodexAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(
            _task(
                project_env_vars={"DAP_LAYER_PROBE": "project"},
                env={"DAP_LAYER_PROBE": "agent"},
            )
        )

    sent_env = create_mock.call_args.kwargs["env"]
    assert sent_env["DAP_LAYER_PROBE"] == "agent"


async def test_project_env_overrides_engine_env(
    with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    adapter = CodexAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(_task(project_env_vars={"DAP_LAYER_PROBE": "project"}))

    sent_env = create_mock.call_args.kwargs["env"]
    assert sent_env["DAP_LAYER_PROBE"] == "project"


async def test_invalid_runtime_config_env_returns_failed(
    with_api_key: None,
) -> None:
    adapter = CodexAdapter()
    with patch(_WHICH_PATH, return_value="/usr/local/bin/codex"):
        result = await adapter.execute(_task(env={"OK": 123}))  # type: ignore[dict-item]
    assert result.success is False
    assert any("runtime_config.env" in e for e in result.errors)


async def test_payload_preserved_for_debug(with_api_key: None) -> None:
    """Whole CLI payload should round-trip into structured for shape-drift debug."""
    adapter = CodexAdapter()
    proc = build_subprocess_mock(stdout=_success_payload(text="ping"))
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.structured is not None
    assert "payload" in result.structured
    assert result.structured["payload"]["output_text"] == "ping"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_non_zero_exit_marks_failure(with_api_key: None) -> None:
    adapter = CodexAdapter()
    proc = build_subprocess_mock(
        returncode=1,
        stdout=b"",
        stderr=b"401 unauthorized\nbad key",
    )
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("exited with code 1" in e for e in result.errors)
    assert result.structured is not None
    assert result.structured["exit_code"] == 1
    assert "unauthorized" in result.structured["stderr"]


async def test_unparseable_json_returns_descriptive_error(
    with_api_key: None,
) -> None:
    adapter = CodexAdapter()
    proc = build_subprocess_mock(stdout=b"not json", returncode=0)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("not parse" in e.lower() for e in result.errors)


async def test_non_object_payload_returns_descriptive_error(
    with_api_key: None,
) -> None:
    """Valid JSON that isn't an object (e.g. an array) shouldn't crash the adapter."""
    adapter = CodexAdapter()
    proc = build_subprocess_mock(stdout=b"[1, 2, 3]")
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("expected object" in e for e in result.errors)


async def test_non_dict_usage_returns_descriptive_error(
    with_api_key: None,
) -> None:
    """A valid result dict with `usage` of the wrong shape — graceful failure."""
    adapter = CodexAdapter()
    payload = json.dumps({"output_text": "ok", "usage": "nope"}).encode("utf-8")
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("usage" in e for e in result.errors)


async def test_non_numeric_token_count_skipped_gracefully(
    with_api_key: None,
) -> None:
    """Pre-#214 a non-numeric token field crashed the entire run; now it skips
    the bad field, falls back to 0 for that key, and counts the others normally.
    Trades hard-fail for "succeed with imperfect telemetry" — see #214 rationale.
    """
    adapter = CodexAdapter()
    payload = json.dumps(
        {
            "output_text": "ok",
            "usage": {"input_tokens": "not-a-number", "output_tokens": 50},
        }
    ).encode("utf-8")
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    # bad input_tokens skipped → 0; output_tokens counted
    assert result.tokens_used == 50


async def test_missing_usage_defaults_to_zero_tokens(with_api_key: None) -> None:
    """Older CLI builds may omit `usage` entirely — adapter shouldn't crash."""
    adapter = CodexAdapter()
    payload = json.dumps({"output_text": "ok"}).encode("utf-8")
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    assert result.tokens_used == 0


async def test_timeout_kills_long_running_command(with_api_key: None) -> None:
    adapter = CodexAdapter()
    # communicate raises TimeoutError when wait_for fires
    proc = build_subprocess_mock(side_effect=TimeoutError())
    proc.returncode = None  # still running

    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task(timeout_ms=200))

    assert result.success is False
    assert any("timed out" in e.lower() for e in result.errors)
    assert result.structured is not None
    assert result.structured["timed_out"] is True


async def test_cancellation_kills_subprocess(with_api_key: None) -> None:
    adapter = CodexAdapter()

    # Subprocess whose stdout read hangs forever (#662); cancel the outer task.
    proc = build_subprocess_mock(hang=True, pid=99999)
    proc.returncode = None

    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/codex"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        exec_task = asyncio.create_task(adapter.execute(_task()))
        await asyncio.sleep(0.05)
        exec_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await exec_task

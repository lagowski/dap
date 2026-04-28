"""Tests for the gemini-cli runtime adapter.

The CLI subprocess is mocked — real CLI calls would burn quota.
Live tests behind ``DAP_E2E_GEMINI_CLI=1`` (separate file, not in CI).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dap_runtimes import GeminiCliAdapter
from dap_types import RuntimeTask

_PATCH_PATH = "dap_runtimes.adapters.gemini_cli.asyncio.create_subprocess_exec"
_WHICH_PATH = "dap_runtimes.adapters.gemini_cli.shutil.which"


@pytest.fixture
def with_api_key() -> Iterator[None]:
    """Set GEMINI_API_KEY for the test; clear GOOGLE_API_KEY too."""
    saved_gemini = os.environ.get("GEMINI_API_KEY")
    saved_google = os.environ.get("GOOGLE_API_KEY")
    os.environ["GEMINI_API_KEY"] = "gem-test-fake"
    os.environ.pop("GOOGLE_API_KEY", None)
    try:
        yield
    finally:
        if saved_gemini is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = saved_gemini
        if saved_google is not None:
            os.environ["GOOGLE_API_KEY"] = saved_google


@pytest.fixture
def with_google_api_key() -> Iterator[None]:
    """Set GOOGLE_API_KEY only — verify the fallback env var works."""
    saved_gemini = os.environ.pop("GEMINI_API_KEY", None)
    saved_google = os.environ.get("GOOGLE_API_KEY")
    os.environ["GOOGLE_API_KEY"] = "google-test-fake"
    try:
        yield
    finally:
        if saved_gemini is not None:
            os.environ["GEMINI_API_KEY"] = saved_gemini
        if saved_google is None:
            os.environ.pop("GOOGLE_API_KEY", None)
        else:
            os.environ["GOOGLE_API_KEY"] = saved_google


def _task(
    *,
    model_id: str = "gemini-3.0-pro",
    binary_path: str | None = None,
    thinking_budget: Any = None,
    timeout_ms: int = 60_000,
    env: dict[str, str] | None = None,
    project_env_vars: dict[str, str] | None = None,
) -> RuntimeTask:
    runtime_config: dict[str, Any] = {"model_id": model_id}
    if binary_path is not None:
        runtime_config["binary_path"] = binary_path
    if thinking_budget is not None:
        runtime_config["thinking_budget"] = thinking_budget
    if env is not None:
        runtime_config["env"] = env
    return RuntimeTask(
        execution_id="exec-1",
        prompt_xml="<agent_prompt><role>r</role><task>t</task></agent_prompt>",
        working_directory="/tmp",
        timeout_ms=timeout_ms,
        runtime_config=runtime_config,
        project_env_vars=project_env_vars or {},
    )


def _success_payload(
    text: str = "ok",
    *,
    prompt_token_count: int = 100,
    candidates_token_count: int = 50,
    cached_token_count: int = 0,
    finish_reason: str = "STOP",
) -> bytes:
    return json.dumps(
        {
            "response": text,
            "usage_metadata": {
                "prompt_token_count": prompt_token_count,
                "candidates_token_count": candidates_token_count,
                "cached_content_token_count": cached_token_count,
            },
            "finish_reason": finish_reason,
        }
    ).encode("utf-8")


def _build_subprocess_mock(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
    side_effect: Exception | None = None,
) -> MagicMock:
    process = MagicMock()
    process.returncode = returncode
    process.pid = 54321
    if side_effect is not None:
        process.communicate = AsyncMock(side_effect=side_effect)
    else:
        process.communicate = AsyncMock(return_value=(stdout, stderr))
    process.wait = AsyncMock(return_value=returncode)
    process.kill = MagicMock()
    return process


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


async def test_healthcheck_missing_binary(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    with patch(_WHICH_PATH, return_value=None):
        health = await adapter.healthcheck()
    assert health.available is False
    assert any("gemini" in m for m in (health.missing or []))


async def test_healthcheck_missing_api_keys() -> None:
    saved_gemini = os.environ.pop("GEMINI_API_KEY", None)
    saved_google = os.environ.pop("GOOGLE_API_KEY", None)
    try:
        adapter = GeminiCliAdapter()
        with patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"):
            health = await adapter.healthcheck()
        assert health.available is False
        assert health.missing is not None
        assert any("GEMINI_API_KEY" in m for m in health.missing)
    finally:
        if saved_gemini is not None:
            os.environ["GEMINI_API_KEY"] = saved_gemini
        if saved_google is not None:
            os.environ["GOOGLE_API_KEY"] = saved_google


async def test_healthcheck_accepts_google_api_key_fallback(
    with_google_api_key: None,
) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(stdout=b"gemini 0.10.0\n")
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        health = await adapter.healthcheck()
    assert health.available is True


async def test_healthcheck_available(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(stdout=b"gemini 0.10.0\n")
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        health = await adapter.healthcheck()
    assert health.available is True
    assert health.version is not None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_missing_model_id_returns_error(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    task = _task()
    task.runtime_config["model_id"] = ""
    result = await adapter.execute(task)
    assert result.success is False
    assert any("model_id" in e for e in result.errors)


async def test_missing_api_key_returns_error() -> None:
    saved_gemini = os.environ.pop("GEMINI_API_KEY", None)
    saved_google = os.environ.pop("GOOGLE_API_KEY", None)
    try:
        adapter = GeminiCliAdapter()
        result = await adapter.execute(_task())
        assert result.success is False
        assert any("GEMINI_API_KEY" in e for e in result.errors)
    finally:
        if saved_gemini is not None:
            os.environ["GEMINI_API_KEY"] = saved_gemini
        if saved_google is not None:
            os.environ["GOOGLE_API_KEY"] = saved_google


async def test_invalid_thinking_budget_returns_error(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    result = await adapter.execute(_task(thinking_budget=-5))
    assert result.success is False
    assert any("thinking_budget" in e for e in result.errors)


async def test_binary_not_found_returns_error(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    with patch(_WHICH_PATH, return_value=None):
        result = await adapter.execute(_task())
    assert result.success is False
    assert any("Gemini binary not found" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Successful execute
# ---------------------------------------------------------------------------


async def test_execute_success_extracts_response_and_usage(
    with_api_key: None,
) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(
        stdout=_success_payload(text="hi", prompt_token_count=200, candidates_token_count=80)
    )
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    assert result.output == "hi"
    assert result.tokens_used == 280  # 200 + 80 + 0 cache
    # No cost from the CLI — api-call provider has the pricing tables.
    assert result.cost_usd is None
    assert result.structured is not None
    assert result.structured["provider"] == "gemini-cli"
    assert result.structured["stop_reason"] == "STOP"

    # Verify CLI invocation
    argv = create_mock.call_args.args
    assert argv[0].endswith("gemini") or argv[0] == "gemini"
    assert "-m" in argv
    assert "gemini-3.0-pro" in argv
    assert "-o" in argv
    assert "json" in argv


async def test_execute_passes_prompt_via_stdin(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        await adapter.execute(_task())

    sent = proc.communicate.call_args.kwargs["input"].decode("utf-8")
    assert sent.startswith("<agent_prompt>")


async def test_execute_with_thinking_budget(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(_task(thinking_budget=8192))

    argv = create_mock.call_args.args
    assert "--thinking-budget" in argv
    assert "8192" in argv


async def test_per_agent_env_overrides_project_env(
    with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#65 layering: runtime_config.env (highest) > project_env_vars > engine env."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
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
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(_task(project_env_vars={"DAP_LAYER_PROBE": "project"}))

    sent_env = create_mock.call_args.kwargs["env"]
    assert sent_env["DAP_LAYER_PROBE"] == "project"


async def test_invalid_runtime_config_env_returns_failed(
    with_api_key: None,
) -> None:
    adapter = GeminiCliAdapter()
    with patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"):
        result = await adapter.execute(_task(env={"OK": 123}))  # type: ignore[dict-item]
    assert result.success is False
    assert any("runtime_config.env" in e for e in result.errors)


async def test_camelcase_token_keys_are_supported(with_api_key: None) -> None:
    """Some gemini-cli builds emit camelCase keys instead of snake_case."""
    adapter = GeminiCliAdapter()
    payload = json.dumps(
        {
            "response": "hi",
            "usage_metadata": {
                "promptTokenCount": 50,
                "candidatesTokenCount": 40,
            },
            "finishReason": "STOP",
        }
    ).encode("utf-8")
    proc = _build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    assert result.tokens_used == 90
    assert result.structured is not None
    assert result.structured["stop_reason"] == "STOP"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_non_zero_exit_marks_failure(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(
        returncode=2,
        stdout=b"",
        stderr=b"Auth failed",
    )
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("exited with code 2" in e for e in result.errors)


async def test_unparseable_json_returns_descriptive_error(
    with_api_key: None,
) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(stdout=b"not json", returncode=0)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("not parse" in e.lower() for e in result.errors)


async def test_non_object_payload_returns_descriptive_error(
    with_api_key: None,
) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(stdout=b'"plain string"')
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("expected object" in e for e in result.errors)


async def test_non_dict_usage_returns_descriptive_error(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    payload = json.dumps({"response": "ok", "usage_metadata": "bad"}).encode("utf-8")
    proc = _build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("usage" in e for e in result.errors)


async def test_non_numeric_token_count_returns_descriptive_error(
    with_api_key: None,
) -> None:
    adapter = GeminiCliAdapter()
    payload = json.dumps(
        {
            "response": "ok",
            "usage_metadata": {
                "prompt_token_count": "not-a-number",
                "candidates_token_count": 50,
            },
        }
    ).encode("utf-8")
    proc = _build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("token metadata" in e for e in result.errors)


async def test_timeout_kills_long_running_command(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    proc = _build_subprocess_mock(side_effect=TimeoutError())
    proc.returncode = None

    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task(timeout_ms=200))

    assert result.success is False
    assert any("timed out" in e.lower() for e in result.errors)
    assert result.structured is not None
    assert result.structured["timed_out"] is True


async def test_cancellation_kills_subprocess(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()

    async def hang(*_args: Any, **_kwargs: Any) -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        return (b"", b"")

    proc = MagicMock()
    proc.returncode = None
    proc.pid = 99999
    proc.communicate = hang
    proc.wait = AsyncMock(return_value=-9)
    proc.kill = MagicMock()

    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        exec_task = asyncio.create_task(adapter.execute(_task()))
        await asyncio.sleep(0.05)
        exec_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await exec_task

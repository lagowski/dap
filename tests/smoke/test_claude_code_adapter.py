"""Tests for the claude-code (Claude Code CLI) runtime adapter.

The CLI subprocess is mocked — real CLI calls would burn tokens. To
opt into a live test run set ``DAP_E2E_CLAUDE_CODE=1`` and run pytest
with ``-k cli_real`` (a separate file we don't ship by default).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dap_runtimes import ClaudeCodeAdapter
from dap_types import RuntimeTask

from .conftest import build_subprocess_mock

_PATCH_PATH = "dap_runtimes.adapters._cli_base.asyncio.create_subprocess_exec"
_WHICH_PATH = "dap_runtimes.adapters._cli_base.shutil.which"


@pytest.fixture
def with_api_key() -> Iterator[None]:
    original = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-fake"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = original


def _task(
    *,
    model_id: str = "claude-opus-4-7",
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
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    total_cost_usd: float = 0.0125,
    session_id: str = "sess-1",
    num_turns: int = 3,
) -> bytes:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": text,
            "session_id": session_id,
            "num_turns": num_turns,
            "duration_ms": 1500,
            "duration_api_ms": 1200,
            "is_error": False,
            "total_cost_usd": total_cost_usd,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "service_tier": "standard",
            },
        }
    ).encode("utf-8")


# _build_subprocess_mock moved to tests/smoke/conftest.py — shared across
# claude_code / codex / gemini_cli tests (audit refactor #7).


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


async def test_healthcheck_missing_binary(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    with patch(_WHICH_PATH, return_value=None):
        health = await adapter.healthcheck()
    assert health.available is False
    assert health.missing is not None
    assert any("claude" in m for m in health.missing)


async def test_healthcheck_available_without_api_key() -> None:
    """Probe must succeed when the binary exists but no env key is set —
    Claude Code can use a stored OAuth session (Pro/Max plans)."""
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        adapter = ClaudeCodeAdapter()
        proc = build_subprocess_mock(stdout=b"claude 1.0.45\n")
        with (
            patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
            patch(_PATCH_PATH, AsyncMock(return_value=proc)),
        ):
            health = await adapter.healthcheck()
        assert health.available is True
        assert health.version is not None
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


async def test_healthcheck_available(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(stdout=b"claude 1.0.45\n")
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        health = await adapter.healthcheck()
    assert health.available is True
    assert health.version is not None
    assert "claude" in health.version


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_missing_model_id_returns_error(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    task = _task()
    task.runtime_config["model_id"] = ""
    result = await adapter.execute(task)
    assert result.success is False
    assert any("model_id" in e for e in result.errors)


async def test_binary_not_found_returns_error(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    with patch(_WHICH_PATH, return_value=None):
        result = await adapter.execute(_task())
    assert result.success is False
    assert any("Claude binary not found" in e for e in result.errors)


async def test_extra_args_must_be_list_of_strings(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    with patch(_WHICH_PATH, return_value="/usr/local/bin/claude"):
        result = await adapter.execute(_task(extra_args="not-a-list"))
    assert result.success is False
    assert any("extra_args" in e for e in result.errors)


async def test_extra_args_with_non_string_element(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    with patch(_WHICH_PATH, return_value="/usr/local/bin/claude"):
        result = await adapter.execute(_task(extra_args=["--ok", 42]))
    assert result.success is False
    assert any("extra_args" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Successful execute
# ---------------------------------------------------------------------------


async def test_execute_success_extracts_result_and_usage(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(stdout=_success_payload(text="hi"))
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    assert result.output == "hi"
    assert result.tokens_used == 300  # 100 input + 200 output
    assert result.cost_usd == pytest.approx(0.0125)
    assert result.structured is not None
    assert result.structured["provider"] == "claude-code"
    assert result.structured["session_id"] == "sess-1"
    assert result.structured["num_turns"] == 3
    assert result.structured["timed_out"] is False

    # Verify CLI invocation shape
    argv = create_mock.call_args.args
    assert argv[0].endswith("claude") or argv[0] == "claude"
    assert "--print" in argv
    assert "--output-format" in argv
    assert "json" in argv
    assert "--model" in argv
    assert "claude-opus-4-7" in argv


async def test_execute_passes_prompt_via_stdin(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        await adapter.execute(_task())

    call_kwargs = proc.communicate.call_args.kwargs
    assert call_kwargs["input"].decode("utf-8").startswith("<agent_prompt>")


async def test_execute_with_extra_args(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(_task(extra_args=["--allowed-tools", "Read,Edit,Bash"]))

    argv = create_mock.call_args.args
    assert "--allowed-tools" in argv
    assert "Read,Edit,Bash" in argv


async def test_per_agent_env_overrides_project_env(
    with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three-layer env (#65): per-agent runtime_config.env wins over project, project wins over engine."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
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
    """Project env_vars must beat the engine process env (#65 layer 2 vs 1)."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(_task(project_env_vars={"DAP_LAYER_PROBE": "project"}))

    sent_env = create_mock.call_args.kwargs["env"]
    assert sent_env["DAP_LAYER_PROBE"] == "project"


async def test_invalid_runtime_config_env_returns_failed(
    with_api_key: None,
) -> None:
    """``runtime_config.env`` must be a dict[str, str] — anything else fails the call."""
    adapter = ClaudeCodeAdapter()
    with patch(_WHICH_PATH, return_value="/usr/local/bin/claude"):
        result = await adapter.execute(_task(env={"OK": 123}))  # type: ignore[dict-item]
    assert result.success is False
    assert any("runtime_config.env" in e for e in result.errors)


async def test_execute_with_cache_tokens(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(
        stdout=_success_payload(
            input_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=1000,
            output_tokens=100,
        )
    )
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.tokens_used == 50 + 200 + 1000 + 100
    assert result.structured is not None
    assert result.structured["usage"]["cache_read_input_tokens"] == 1000


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_non_zero_exit_marks_failure(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(
        returncode=1,
        stdout=b"",
        stderr=b"Some error from CLI\nstack trace line",
    )
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("exited with code 1" in e for e in result.errors)
    assert result.structured is not None
    assert result.structured["exit_code"] == 1
    assert "stack trace" in result.structured["stderr"]


async def test_unparseable_json_returns_descriptive_error(
    with_api_key: None,
) -> None:
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(stdout=b"not json", returncode=0)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("not parse" in e.lower() for e in result.errors)


async def test_non_object_payload_returns_descriptive_error(
    with_api_key: None,
) -> None:
    """Valid JSON that isn't an object (e.g. an array) shouldn't crash the adapter."""
    adapter = ClaudeCodeAdapter()
    proc = build_subprocess_mock(stdout=b"[1, 2, 3]")
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("expected object" in e for e in result.errors)


async def test_non_dict_usage_returns_descriptive_error(
    with_api_key: None,
) -> None:
    """A valid result dict with `usage` of the wrong shape — graceful failure."""
    adapter = ClaudeCodeAdapter()
    payload = json.dumps(
        {"type": "result", "is_error": False, "result": "ok", "usage": "nope"}
    ).encode("utf-8")
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("usage" in e for e in result.errors)


async def test_non_numeric_token_count_returns_descriptive_error(
    with_api_key: None,
) -> None:
    """`usage.input_tokens` returning a non-numeric — graceful failure."""
    adapter = ClaudeCodeAdapter()
    payload = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": "ok",
            "usage": {"input_tokens": "not-a-number", "output_tokens": 50},
        }
    ).encode("utf-8")
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("token metadata" in e for e in result.errors)


async def test_is_error_flag_marks_failure(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    payload = json.dumps(
        {
            "type": "result",
            "is_error": True,
            "result": "rate-limited or whatever",
        }
    ).encode("utf-8")
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is False
    assert any("rate-limited" in e for e in result.errors)


async def test_timeout_kills_long_running_command(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()
    # communicate raises TimeoutError when wait_for fires
    proc = build_subprocess_mock(side_effect=TimeoutError())
    proc.returncode = None  # still running

    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task(timeout_ms=200))

    assert result.success is False
    assert any("timed out" in e.lower() for e in result.errors)
    assert result.structured is not None
    assert result.structured["timed_out"] is True


async def test_cancellation_kills_subprocess(with_api_key: None) -> None:
    adapter = ClaudeCodeAdapter()

    # Build a subprocess whose communicate hangs forever — we cancel
    # the outer task to verify the adapter cleans up.
    async def hang(*_args: Any, **_kwargs: Any) -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        return (b"", b"")  # unreachable; satisfies the type-checker

    proc = MagicMock()
    proc.returncode = None
    proc.pid = 99999
    proc.communicate = hang
    proc.wait = AsyncMock(return_value=-9)
    proc.kill = MagicMock()

    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/claude"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        exec_task = asyncio.create_task(adapter.execute(_task()))
        await asyncio.sleep(0.05)
        exec_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await exec_task

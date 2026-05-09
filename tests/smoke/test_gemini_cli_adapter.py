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

from .conftest import build_subprocess_mock

_PATCH_PATH = "dap_runtimes.adapters._cli_base.asyncio.create_subprocess_exec"
_WHICH_PATH = "dap_runtimes.adapters._cli_base.shutil.which"


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


def _task(
    *,
    model_id: str = "gemini-3.0-pro",
    binary_path: str | None = None,
    thinking_budget: Any = None,
    extra_args: Any = None,
    timeout_ms: int = 60_000,
    env: dict[str, str] | None = None,
    project_env_vars: dict[str, str] | None = None,
) -> RuntimeTask:
    runtime_config: dict[str, Any] = {"model_id": model_id}
    if binary_path is not None:
        runtime_config["binary_path"] = binary_path
    if thinking_budget is not None:
        runtime_config["thinking_budget"] = thinking_budget
    if extra_args is not None:
        runtime_config["extra_args"] = extra_args
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


# _build_subprocess_mock moved to tests/smoke/conftest.py — shared across
# claude_code / codex / gemini_cli tests (audit refactor #7).


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


async def test_healthcheck_missing_binary(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    with patch(_WHICH_PATH, return_value=None):
        health = await adapter.healthcheck()
    assert health.available is False
    assert any("gemini" in m for m in (health.missing or []))


async def test_healthcheck_available_without_api_keys() -> None:
    """Probe must succeed when the binary exists but no env key is set —
    Gemini CLI can use a stored OAuth session (Gemini Advanced)."""
    saved_gemini = os.environ.pop("GEMINI_API_KEY", None)
    saved_google = os.environ.pop("GOOGLE_API_KEY", None)
    try:
        adapter = GeminiCliAdapter()
        proc = build_subprocess_mock(stdout=b"gemini 0.10.0\n")
        with (
            patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
            patch(_PATCH_PATH, AsyncMock(return_value=proc)),
        ):
            health = await adapter.healthcheck()
        assert health.available is True
        assert health.version is not None
    finally:
        if saved_gemini is not None:
            os.environ["GEMINI_API_KEY"] = saved_gemini
        if saved_google is not None:
            os.environ["GOOGLE_API_KEY"] = saved_google


async def test_healthcheck_available(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    proc = build_subprocess_mock(stdout=b"gemini 0.10.0\n")
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
    proc = build_subprocess_mock(
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
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        await adapter.execute(_task())

    sent = proc.communicate.call_args.kwargs["input"].decode("utf-8")
    assert sent.startswith("<agent_prompt>")


async def test_execute_with_thinking_budget(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(_task(thinking_budget=8192))

    argv = create_mock.call_args.args
    assert "--thinking-budget" in argv
    assert "8192" in argv


async def test_execute_passes_extra_args_to_argv(with_api_key: None) -> None:
    """runtime_config.extra_args is forwarded to the gemini argv (#213).

    Mirrors the equivalent claude_code / codex tests so the same agent template
    can be reused across all three CLI providers without silently dropping flags.
    """
    adapter = GeminiCliAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)) as create_mock,
    ):
        await adapter.execute(_task(extra_args=["--sandbox=docker", "--allow-tools", "fs"]))

    argv = create_mock.call_args.args
    assert "--sandbox=docker" in argv
    assert "--allow-tools" in argv
    assert "fs" in argv


async def test_execute_extra_args_must_be_list_of_strings(with_api_key: None) -> None:
    """Non-list / non-string extra_args is rejected with a descriptive error (#213)."""
    adapter = GeminiCliAdapter()
    with patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"):
        # Non-list
        result = await adapter.execute(_task(extra_args="--not-a-list"))
        assert result.success is False
        assert any("must be a list of strings" in e for e in result.errors)

        # List with non-string element
        result = await adapter.execute(_task(extra_args=["--ok", 42]))
        assert result.success is False
        assert any("got element of type int" in e for e in result.errors)


async def test_per_agent_env_overrides_project_env(
    with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#65 layering: runtime_config.env (highest) > project_env_vars > engine env."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    adapter = GeminiCliAdapter()
    proc = build_subprocess_mock(stdout=_success_payload())
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
    proc = build_subprocess_mock(stdout=_success_payload())
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
    proc = build_subprocess_mock(stdout=payload)
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
    proc = build_subprocess_mock(
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
    proc = build_subprocess_mock(stdout=b"not json", returncode=0)
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
    proc = build_subprocess_mock(stdout=b'"plain string"')
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
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
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
    proc = build_subprocess_mock(stdout=payload)
    with (
        patch(_WHICH_PATH, return_value="/usr/local/bin/gemini"),
        patch(_PATCH_PATH, AsyncMock(return_value=proc)),
    ):
        result = await adapter.execute(_task())

    assert result.success is True
    # bad prompt_token_count skipped → 0; candidates_token_count counted
    assert result.tokens_used == 50


async def test_timeout_kills_long_running_command(with_api_key: None) -> None:
    adapter = GeminiCliAdapter()
    proc = build_subprocess_mock(side_effect=TimeoutError())
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


# ---------------------------------------------------------------------------
# _first_int helper — robustness against varied CLI usage shapes (#214)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"tokens": 12}, 12),  # plain int
        ({"tokens": "12"}, 12),  # stringified int
        ({"tokens": "12.0"}, 12),  # stringified float — pre-#214 raised ValueError
        ({"tokens": 12.7}, 12),  # raw float, truncates to int
        ({"tokens": True}, 0),  # bool explicitly skipped
        ({"tokens": False}, 0),  # bool explicitly skipped
        ({"tokens": None}, 0),  # None skipped, fallback to default
        ({"tokens": "abc"}, 0),  # unparseable, skipped, fallback to default
        ({"tokens": "inf"}, 0),  # int(float("inf")) → OverflowError, skipped
        ({"tokens": "1e309"}, 0),  # exponent overflows float→int, skipped
        ({}, 0),  # missing, fallback to default
    ],
)
def test_first_int_handles_varied_shapes(payload: dict[str, Any], expected: int) -> None:
    from dap_runtimes.adapters.gemini_cli import _first_int

    assert _first_int(payload, ("tokens",)) == expected


def test_first_int_codex_and_gemini_share_behavior() -> None:
    """Both adapters' _first_int must agree on edge cases (#214)."""
    from dap_runtimes.adapters.codex import _first_int as codex_first_int
    from dap_runtimes.adapters.gemini_cli import _first_int as gemini_first_int

    cases: list[tuple[dict[str, Any], tuple[str, ...]]] = [
        ({"x": "12.0"}, ("x",)),
        ({"x": True}, ("x",)),
        ({"x": "abc"}, ("x",)),
        ({}, ("x",)),
    ]
    for payload, keys in cases:
        assert codex_first_int(payload, keys) == gemini_first_int(payload, keys)

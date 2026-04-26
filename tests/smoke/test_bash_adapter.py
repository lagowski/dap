"""Tests for the bash runtime adapter — actually executes shell commands."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from dap_runtimes import BashAdapter
from dap_types import RuntimeTask


@pytest.fixture
def adapter() -> BashAdapter:
    return BashAdapter()


def _task(
    *,
    command: str | None = None,
    prompt_xml: str = "",
    cwd: str | None = None,
    timeout_ms: int = 5000,
    env: dict[str, str] | None = None,
) -> RuntimeTask:
    config: dict[str, object] = {}
    if command is not None:
        config["command"] = command
    if env is not None:
        config["env"] = env
    return RuntimeTask(
        execution_id="exec-1",
        prompt_xml=prompt_xml,
        working_directory=cwd or "/tmp",
        timeout_ms=timeout_ms,
        runtime_config=config,
    )


@pytest.mark.asyncio
async def test_healthcheck_reports_shell(adapter: BashAdapter) -> None:
    health = await adapter.healthcheck()
    assert health.available is True
    assert health.version is not None
    assert health.version.endswith("bash") or health.version.endswith("sh")


@pytest.mark.asyncio
async def test_execute_success(adapter: BashAdapter) -> None:
    result = await adapter.execute(_task(command="echo hello"))
    assert result.success is True
    assert result.output.strip() == "hello"
    assert result.structured is not None
    assert result.structured["exit_code"] == 0
    assert result.structured["timed_out"] is False
    assert result.errors == []


@pytest.mark.asyncio
async def test_execute_nonzero_exit_marks_failure(adapter: BashAdapter) -> None:
    result = await adapter.execute(_task(command="false"))
    assert result.success is False
    assert result.structured is not None
    assert result.structured["exit_code"] != 0
    assert len(result.errors) == 1


@pytest.mark.asyncio
async def test_execute_captures_stderr(adapter: BashAdapter) -> None:
    result = await adapter.execute(
        _task(command="echo stdout-line; echo stderr-line >&2; exit 7")
    )
    assert result.success is False
    assert "stdout-line" in result.output
    assert result.structured is not None
    assert result.structured["exit_code"] == 7
    assert "stderr-line" in result.structured["stderr"]


@pytest.mark.asyncio
async def test_command_extracted_from_prompt_xml(adapter: BashAdapter) -> None:
    """When runtime_config.command is missing, fall back to <command> tag."""
    prompt = "<agent_prompt><command>echo from-xml</command></agent_prompt>"
    result = await adapter.execute(_task(prompt_xml=prompt))
    assert result.success is True
    assert result.output.strip() == "from-xml"


@pytest.mark.asyncio
async def test_runtime_config_command_wins_over_xml(adapter: BashAdapter) -> None:
    prompt = "<agent_prompt><command>echo wrong</command></agent_prompt>"
    result = await adapter.execute(_task(command="echo right", prompt_xml=prompt))
    assert result.output.strip() == "right"


@pytest.mark.asyncio
async def test_no_command_returns_descriptive_error(adapter: BashAdapter) -> None:
    result = await adapter.execute(_task(prompt_xml="<agent_prompt></agent_prompt>"))
    assert result.success is False
    assert any("command" in err.lower() for err in result.errors)


@pytest.mark.asyncio
async def test_timeout_kills_long_running_command(adapter: BashAdapter) -> None:
    result = await adapter.execute(_task(command="sleep 5", timeout_ms=200))
    assert result.success is False
    assert any("timed out" in err.lower() for err in result.errors)
    assert result.structured is not None
    assert result.structured["timed_out"] is True


@pytest.mark.asyncio
async def test_working_directory_is_honored(adapter: BashAdapter) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "marker.txt"
        marker.write_text("ok")
        result = await adapter.execute(_task(command="cat marker.txt", cwd=tmp))
        assert result.success is True
        assert result.output.strip() == "ok"


@pytest.mark.asyncio
async def test_extra_env_is_passed_to_command(adapter: BashAdapter) -> None:
    result = await adapter.execute(
        _task(command="echo $DAP_TEST_VAR", env={"DAP_TEST_VAR": "injected"})
    )
    assert result.success is True
    assert result.output.strip() == "injected"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only test")
async def test_pytest_invocation_is_a_realistic_use_case(
    adapter: BashAdapter,
) -> None:
    """End-to-end-ish: bash adapter could run a real test command."""
    result = await adapter.execute(_task(command="python -c 'print(2+2)'"))
    assert result.success is True
    assert result.output.strip() == "4"

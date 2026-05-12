"""Tests for the bash runtime adapter — actually executes shell commands."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from dap_runtimes import BashAdapter
from dap_types import RuntimeTask


@pytest.fixture
def adapter() -> BashAdapter:
    return BashAdapter()


@pytest.fixture(autouse=True)
def _hermetic_shell_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip DAP_BASH_SHELL so tests don't depend on the developer's shell setup."""
    monkeypatch.delenv("DAP_BASH_SHELL", raising=False)


def _task(
    *,
    command: str | None = None,
    prompt_xml: str = "",
    cwd: str | None = None,
    timeout_ms: int = 5000,
    env: dict[str, str] | None = None,
    project_env_vars: dict[str, str] | None = None,
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
        project_env_vars=project_env_vars or {},
    )


@pytest.mark.asyncio
async def test_healthcheck_reports_default_shell(adapter: BashAdapter) -> None:
    """With DAP_BASH_SHELL unset, healthcheck reports the compile-time default."""
    health = await adapter.healthcheck()
    assert health.available is True
    assert health.version == "/bin/bash"


@pytest.mark.asyncio
async def test_healthcheck_honors_env_override(
    adapter: BashAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DAP_BASH_SHELL", "/bin/sh")
    health = await adapter.healthcheck()
    assert health.available is True
    assert health.version == "/bin/sh"


@pytest.mark.asyncio
async def test_healthcheck_missing_shell_reports_unavailable(
    adapter: BashAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DAP_BASH_SHELL", "/no/such/shell-binary")
    health = await adapter.healthcheck()
    assert health.available is False
    assert health.missing is not None
    assert any("/no/such/shell-binary" in m for m in health.missing)


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
    result = await adapter.execute(_task(command="echo stdout-line; echo stderr-line >&2; exit 7"))
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
async def test_project_env_overlay_visible_in_subprocess(
    adapter: BashAdapter,
) -> None:
    """Project env_vars (#65) must reach the subprocess on top of engine env."""
    result = await adapter.execute(
        _task(
            command="echo $DAP_PROJECT_VAR",
            project_env_vars={"DAP_PROJECT_VAR": "from-project"},
        )
    )
    assert result.success is True
    assert result.output.strip() == "from-project"


@pytest.mark.asyncio
async def test_project_env_overrides_engine_env(
    adapter: BashAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Engine env is the base layer — project env_vars must win on conflict."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    result = await adapter.execute(
        _task(
            command="echo $DAP_LAYER_PROBE",
            project_env_vars={"DAP_LAYER_PROBE": "project"},
        )
    )
    assert result.success is True
    assert result.output.strip() == "project"


@pytest.mark.asyncio
async def test_per_agent_env_beats_project_env(adapter: BashAdapter) -> None:
    """Per-agent runtime_config.env is the highest layer — must override project env."""
    result = await adapter.execute(
        _task(
            command="echo $DAP_LAYER_PROBE",
            project_env_vars={"DAP_LAYER_PROBE": "project"},
            env={"DAP_LAYER_PROBE": "agent"},
        )
    )
    assert result.success is True
    assert result.output.strip() == "agent"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only test")
async def test_pytest_invocation_is_a_realistic_use_case(
    adapter: BashAdapter,
) -> None:
    """End-to-end-ish: bash adapter could run a real test command."""
    result = await adapter.execute(_task(command="python -c 'print(2+2)'"))
    assert result.success is True
    assert result.output.strip() == "4"


@pytest.mark.asyncio
async def test_execute_uses_env_var_shell_when_runtime_config_omits_it(
    adapter: BashAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DAP_BASH_SHELL", "/bin/sh")
    result = await adapter.execute(_task(command="echo via-env"))
    assert result.success is True
    assert result.structured is not None
    assert result.structured["shell"] == "/bin/sh"


@pytest.mark.asyncio
async def test_runtime_config_shell_overrides_env_var(
    adapter: BashAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DAP_BASH_SHELL", "/bin/sh")
    task = _task(command="echo from-config")
    task.runtime_config["shell"] = "/bin/bash"
    result = await adapter.execute(task)
    assert result.success is True
    assert result.structured is not None
    assert result.structured["shell"] == "/bin/bash"


@pytest.mark.asyncio
async def test_invalid_env_dict_returns_descriptive_error(
    adapter: BashAdapter,
) -> None:
    task = _task(command="echo hi")
    task.runtime_config["env"] = "not-a-dict"
    result = await adapter.execute(task)
    assert result.success is False
    assert any("env" in err for err in result.errors)


@pytest.mark.asyncio
async def test_invalid_env_entry_types_return_descriptive_error(
    adapter: BashAdapter,
) -> None:
    task = _task(command="echo hi")
    task.runtime_config["env"] = {"OK": 123}
    result = await adapter.execute(task)
    assert result.success is False
    assert any("env" in err and "str" in err for err in result.errors)


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(__import__("os"), "setsid"), reason="POSIX-only")
async def test_cancellation_kills_subprocess(adapter: BashAdapter) -> None:
    """Engine-level cancellation should not leak the subprocess."""
    task = _task(command="sleep 30", timeout_ms=60_000)
    exec_task = asyncio.create_task(adapter.execute(task))
    await asyncio.sleep(0.1)
    exec_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await exec_task


@pytest.mark.asyncio
async def test_structured_shape_is_consistent_across_outcomes(
    adapter: BashAdapter,
) -> None:
    """Every result.structured carries the documented keys so downstream nodes can branch."""
    expected_keys = {"command", "shell", "exit_code", "stderr", "timed_out"}

    success = await adapter.execute(_task(command="echo x"))
    assert success.structured is not None
    assert set(success.structured.keys()) == expected_keys

    failure = await adapter.execute(_task(command="false"))
    assert failure.structured is not None
    assert set(failure.structured.keys()) == expected_keys

    timeout = await adapter.execute(_task(command="sleep 5", timeout_ms=200))
    assert timeout.structured is not None
    assert set(timeout.structured.keys()) == expected_keys
    assert timeout.structured["timed_out"] is True

    missing_cmd = await adapter.execute(_task())
    assert missing_cmd.structured is not None
    assert set(missing_cmd.structured.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Bundle regression tests — dap#223
# ---------------------------------------------------------------------------


def _load_bundle(filename: str) -> dict[str, Any]:
    """Load a Cortex DAP bundle JSON from the packages/cortex tree."""
    import json

    bundle_path = (
        Path(__file__).parent.parent.parent / "packages/cortex/src/cortex/dap_bundles" / filename
    )
    return cast(dict[str, Any], json.loads(bundle_path.read_text()))


def _git_branch_agent(bundle: dict[str, Any]) -> dict[str, Any] | None:
    """Return the git-branch bundled agent dict, or None if not present."""
    for agent_id, agent in bundle.get("bundled_agents", {}).items():
        if "git_branch" in agent_id or "Git Branch" in agent.get("name", ""):
            return cast(dict[str, Any], agent)
    return None


def test_phase2_bundle_git_branch_omits_explicit_shell() -> None:
    """cortex-phase2 bundle must not hard-code shell in git-branch runtime_config.

    Hard-coding 'shell': '/bin/bash' bypasses the BashAdapter fallback chain
    (DAP_BASH_SHELL env var → /bin/bash default), causing 'Shell not found'
    on hosts without /bin/bash (#223).
    """
    bundle = _load_bundle("cortex-phase2.pipeline-bundle.json")
    agent = _git_branch_agent(bundle)
    assert agent is not None, "cx_p2_git_branch agent not found in phase2 bundle"
    rc = agent.get("runtime_config", {})
    assert "shell" not in rc, f"git-branch runtime_config must not contain 'shell' key; got: {rc}"


def test_full_bundle_git_branch_omits_explicit_shell() -> None:
    """cortex-full bundle must not hard-code shell in git-branch runtime_config."""
    bundle = _load_bundle("cortex-full.pipeline-bundle.json")
    agent = _git_branch_agent(bundle)
    assert agent is not None, "cx_p2_git_branch agent not found in full bundle"
    rc = agent.get("runtime_config", {})
    assert "shell" not in rc, f"git-branch runtime_config must not contain 'shell' key; got: {rc}"


def test_full_bundle_git_branch_command_uses_and_chaining() -> None:
    """git-branch prompt template must use && not ; so cd failure aborts the chain."""
    import re

    bundle = _load_bundle("cortex-full.pipeline-bundle.json")
    agent = _git_branch_agent(bundle)
    assert agent is not None, "cx_p2_git_branch agent not found in full bundle"
    pt = agent.get("prompt_template", "")
    m = re.search(r"<command>(.*?)</command>", pt, re.DOTALL)
    assert m is not None, "git-branch prompt_template has no <command> block"
    cmd = m.group(1)
    assert ";" not in cmd, (
        "git-branch command uses ';' chaining — use '&&' so cd failure aborts "
        f"subsequent git commands. command: {cmd!r}"
    )

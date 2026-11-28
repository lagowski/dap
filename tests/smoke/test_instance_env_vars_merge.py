"""Runtime merge tests for instance env vars (#388).

Two layers:

1. **Unit** — ``merge_subprocess_env`` honours ``instance_env_vars`` and
   applies the four-layer order:
   ``os.environ → instance → project → runtime_config.env`` (rightmost
   wins on conflict).
2. **Integration** — the bash adapter actually sees instance env vars
   in the spawned subprocess via ``RuntimeTask.instance_env_vars``.
"""

from __future__ import annotations

import sys

import pytest
from dap_runtimes import BashAdapter
from dap_runtimes.adapters._subprocess_env import (
    compute_env_overlay,
    merge_subprocess_env,
)
from dap_types import RuntimeTask

# ---------------------------------------------------------------------------
# Unit: merge_subprocess_env directly
# ---------------------------------------------------------------------------


def test_merge_includes_instance_env_vars() -> None:
    env, err = merge_subprocess_env(
        instance_env_vars={"GH_TOKEN_CODE": "from-instance"},
        project_env_vars={},
        runtime_config={},
    )
    assert err is None
    assert env["GH_TOKEN_CODE"] == "from-instance"


def test_instance_env_overrides_engine_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Layer 2 wins over layer 1 — instance vars shadow engine env."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    env, err = merge_subprocess_env(
        instance_env_vars={"DAP_LAYER_PROBE": "instance"},
        project_env_vars={},
        runtime_config={},
    )
    assert err is None
    assert env["DAP_LAYER_PROBE"] == "instance"


def test_project_env_overrides_instance_env() -> None:
    """Layer 3 (project) wins over layer 2 (instance)."""
    env, err = merge_subprocess_env(
        instance_env_vars={"GH_TOKEN_CODE": "from-instance"},
        project_env_vars={"GH_TOKEN_CODE": "from-project"},
        runtime_config={},
    )
    assert err is None
    assert env["GH_TOKEN_CODE"] == "from-project"


def test_runtime_env_overrides_instance_env() -> None:
    """Layer 4 (per-agent) wins over layer 2 (instance)."""
    env, err = merge_subprocess_env(
        instance_env_vars={"GH_TOKEN_CODE": "from-instance"},
        project_env_vars={},
        runtime_config={"env": {"GH_TOKEN_CODE": "from-agent"}},
    )
    assert err is None
    assert env["GH_TOKEN_CODE"] == "from-agent"


def test_full_four_layer_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same key set on all four layers — rightmost wins."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    env, err = merge_subprocess_env(
        instance_env_vars={"DAP_LAYER_PROBE": "instance"},
        project_env_vars={"DAP_LAYER_PROBE": "project"},
        runtime_config={"env": {"DAP_LAYER_PROBE": "agent"}},
    )
    assert err is None
    assert env["DAP_LAYER_PROBE"] == "agent"


def test_instance_env_vars_default_to_empty() -> None:
    """Backward-compat — call sites that don't yet pass ``instance_env_vars``
    must keep working."""
    env, err = merge_subprocess_env(
        project_env_vars={"FOO": "bar"},
        runtime_config={},
    )
    assert err is None
    assert env["FOO"] == "bar"


# ---------------------------------------------------------------------------
# Integration: bash adapter sees instance env vars in subprocess
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> BashAdapter:
    return BashAdapter()


@pytest.fixture(autouse=True)
def _hermetic_shell_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAP_BASH_SHELL", raising=False)


def _task(
    *,
    command: str,
    instance_env_vars: dict[str, str] | None = None,
    project_env_vars: dict[str, str] | None = None,
    runtime_env: dict[str, str] | None = None,
) -> RuntimeTask:
    config: dict[str, object] = {"command": command}
    if runtime_env is not None:
        config["env"] = runtime_env
    return RuntimeTask(
        execution_id="exec-instance-env",
        prompt_xml="",
        working_directory="/tmp",
        timeout_ms=5000,
        runtime_config=config,
        instance_env_vars=instance_env_vars or {},
        project_env_vars=project_env_vars or {},
    )


@pytest.mark.asyncio
async def test_instance_env_var_visible_in_subprocess(adapter: BashAdapter) -> None:
    """Instance env vars must reach the spawned shell — that's the whole
    point of issue #388."""
    result = await adapter.execute(
        _task(
            command="echo $GH_TOKEN_CODE",
            instance_env_vars={"GH_TOKEN_CODE": "from-instance"},
        )
    )
    assert result.success, result.errors
    assert "from-instance" in result.output


@pytest.mark.asyncio
async def test_project_env_overrides_instance_env_in_subprocess(
    adapter: BashAdapter,
) -> None:
    result = await adapter.execute(
        _task(
            command="echo $GH_TOKEN_CODE",
            instance_env_vars={"GH_TOKEN_CODE": "from-instance"},
            project_env_vars={"GH_TOKEN_CODE": "from-project"},
        )
    )
    assert result.success, result.errors
    assert "from-project" in result.output
    assert "from-instance" not in result.output


@pytest.mark.asyncio
async def test_agent_env_overrides_instance_env_in_subprocess(
    adapter: BashAdapter,
) -> None:
    result = await adapter.execute(
        _task(
            command="echo $GH_TOKEN_CODE",
            instance_env_vars={"GH_TOKEN_CODE": "from-instance"},
            runtime_env={"GH_TOKEN_CODE": "from-agent"},
        )
    )
    assert result.success, result.errors
    assert "from-agent" in result.output


@pytest.mark.asyncio
async def test_instance_env_overrides_engine_env_in_subprocess(
    adapter: BashAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Engine inherits the process env — instance vars must shadow it."""
    monkeypatch.setenv("DAP_LAYER_PROBE", "engine")
    result = await adapter.execute(
        _task(
            command="echo $DAP_LAYER_PROBE",
            instance_env_vars={"DAP_LAYER_PROBE": "instance"},
        )
    )
    assert result.success, result.errors
    assert "instance" in result.output


# ---------------------------------------------------------------------------
# Unit: compute_env_overlay — the layered overlay WITHOUT os.environ.
#
# Used by the in-process python-func adapter (#65/#388 follow-up), which
# patches os.environ around the callable instead of copying it into a
# subprocess. ``compute_env_overlay`` is the shared core that
# ``merge_subprocess_env`` layers on top of ``os.environ.copy()``.
# ---------------------------------------------------------------------------


def test_overlay_layers_instance_then_project_then_runtime() -> None:
    overlay, err = compute_env_overlay(
        instance_env_vars={"A": "i", "B": "i", "C": "i"},
        project_env_vars={"B": "p", "C": "p"},
        runtime_config={"env": {"C": "r"}},
    )
    assert err is None
    assert overlay == {"A": "i", "B": "p", "C": "r"}


def test_overlay_excludes_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overlay is ONLY the layered vars — never the ambient process env.

    This is the contract the in-process adapter relies on: it must know
    exactly which keys to restore afterwards, so os.environ must not leak in.
    """
    monkeypatch.setenv("DAP_OVERLAY_AMBIENT_PROBE", "ambient")
    overlay, err = compute_env_overlay(
        instance_env_vars={"GH_TOKEN_CODE": "from-instance"},
        project_env_vars={},
        runtime_config={},
    )
    assert err is None
    assert overlay == {"GH_TOKEN_CODE": "from-instance"}
    assert "DAP_OVERLAY_AMBIENT_PROBE" not in overlay


def test_overlay_empty_when_no_layers() -> None:
    overlay, err = compute_env_overlay(
        instance_env_vars=None,
        project_env_vars={},
        runtime_config={},
    )
    assert err is None
    assert overlay == {}


def test_overlay_rejects_malformed_runtime_env() -> None:
    overlay, err = compute_env_overlay(
        instance_env_vars={},
        project_env_vars={},
        runtime_config={"env": "not-a-dict"},
    )
    assert err is not None
    assert "dict[str, str]" in err
    # Overlay-so-far is still a dict (callers check ``err`` before using it).
    assert isinstance(overlay, dict)


def test_overlay_rejects_non_string_runtime_env_values() -> None:
    overlay, err = compute_env_overlay(
        instance_env_vars={},
        project_env_vars={},
        runtime_config={"env": {"GH_TOKEN": 123}},
    )
    assert err is not None
    assert "dict[str, str]" in err
    assert isinstance(overlay, dict)


def test_merge_subprocess_env_still_includes_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: refactoring merge onto compute_env_overlay must keep the
    full process env in the subprocess result (only the in-process path drops it).
    """
    monkeypatch.setenv("DAP_MERGE_AMBIENT_PROBE", "ambient")
    env, err = merge_subprocess_env(
        instance_env_vars={"GH_TOKEN_CODE": "from-instance"},
        project_env_vars={},
        runtime_config={},
    )
    assert err is None
    assert env["DAP_MERGE_AMBIENT_PROBE"] == "ambient"
    assert env["GH_TOKEN_CODE"] == "from-instance"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

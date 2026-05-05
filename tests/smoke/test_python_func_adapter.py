"""Tests for the python-func runtime adapter."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from dap_runtimes import PythonFuncAdapter
from dap_types import RuntimeTask


@pytest.fixture
def adapter() -> PythonFuncAdapter:
    return PythonFuncAdapter()


def _task(
    *,
    callable_path: str | None = None,
    prompt_xml: str = "<prompt>hello</prompt>",
    timeout_ms: int | None = 5000,
    pass_prompt: bool | None = None,
    pass_context: bool | None = None,
) -> RuntimeTask:
    config: dict[str, object] = {}
    if callable_path is not None:
        config["callable_path"] = callable_path
    if pass_prompt is not None:
        config["pass_prompt"] = pass_prompt
    if pass_context is not None:
        config["pass_context"] = pass_context
    return RuntimeTask(
        execution_id="exec-test",
        prompt_xml=prompt_xml,
        working_directory="/tmp",
        timeout_ms=timeout_ms,
        runtime_config=config,
        project_env_vars={},
    )


# ---------------------------------------------------------------------------
# healthcheck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthcheck_always_available(adapter: PythonFuncAdapter) -> None:
    health = await adapter.healthcheck()
    assert health.available is True
    assert health.version is not None  # Python version string


# ---------------------------------------------------------------------------
# missing / malformed callable_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_callable_path_returns_error(adapter: PythonFuncAdapter) -> None:
    result = await adapter.execute(_task())
    assert result.success is False
    assert any("callable_path" in err for err in result.errors)


@pytest.mark.asyncio
async def test_callable_path_without_separator_returns_error(adapter: PythonFuncAdapter) -> None:
    # No colon AND no dot — ambiguous, rejected.
    result = await adapter.execute(_task(callable_path="nodotnocolon"))
    assert result.success is False
    assert any("callable_path" in err for err in result.errors)


@pytest.mark.asyncio
async def test_callable_path_dot_notation(adapter: PythonFuncAdapter) -> None:
    # "os.path.join" resolves module="os.path", func="join".
    # os.path.join returns a str, not a dict — adapter fails on return type.
    result = await adapter.execute(_task(callable_path="os.path.join"))
    assert result.success is False
    assert any("dict" in err for err in result.errors)


@pytest.mark.asyncio
async def test_bad_import_raises_runtime_error(adapter: PythonFuncAdapter) -> None:
    """ImportError for a nonexistent module raises RuntimeError with descriptive message."""
    with pytest.raises(RuntimeError, match="python-func: cannot import"):
        await adapter.execute(_task(callable_path="no_such_package.no_such_module:run"))


@pytest.mark.asyncio
async def test_missing_attribute_returns_error(adapter: PythonFuncAdapter) -> None:
    result = await adapter.execute(_task(callable_path="os.path:no_such_func_xyz"))
    assert result.success is False
    assert any("no attribute" in err for err in result.errors)


@pytest.mark.asyncio
async def test_non_callable_attribute_returns_error(adapter: PythonFuncAdapter) -> None:
    # os.sep is a string, not callable
    result = await adapter.execute(_task(callable_path="os:sep"))
    assert result.success is False
    assert any("not callable" in err for err in result.errors)


# ---------------------------------------------------------------------------
# async callable — happy path
# ---------------------------------------------------------------------------


async def _async_echo(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {"echo": state.get("prompt_xml", ""), "from_config": config.get("extra_key")}


@pytest.mark.asyncio
async def test_async_callable_happy_path(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Async callable is awaited and its return dict becomes state_delta."""
    # Inject a trivial async function via a temporary module attribute.
    import sys
    import types

    mod = types.ModuleType("_dap_test_async_mod")
    mod.run = _async_echo  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_async_mod", mod)

    result = await adapter.execute(
        _task(callable_path="_dap_test_async_mod:run", prompt_xml="<p>hi</p>")
    )
    assert result.success is True
    assert result.structured is not None
    assert result.structured["state_delta"]["echo"] == "<p>hi</p>"
    assert result.errors == []


# ---------------------------------------------------------------------------
# sync callable — happy path
# ---------------------------------------------------------------------------


def _sync_double(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {"doubled": 42}


@pytest.mark.asyncio
async def test_sync_callable_happy_path(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sync callable is run via run_in_executor and its result is captured correctly."""
    import sys
    import types

    mod = types.ModuleType("_dap_test_sync_mod")
    mod.run = _sync_double  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_sync_mod", mod)

    result = await adapter.execute(_task(callable_path="_dap_test_sync_mod:run"))
    assert result.success is True
    assert result.structured is not None
    assert result.structured["state_delta"]["doubled"] == 42


# ---------------------------------------------------------------------------
# __audit extraction
# ---------------------------------------------------------------------------


async def _with_audit(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_field": "hello",
        "__audit": {"tokens_used": 100, "cost_usd": 0.002, "custom": "value"},
    }


@pytest.mark.asyncio
async def test_audit_key_extracted_not_in_state_delta(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    mod = types.ModuleType("_dap_test_audit_mod")
    mod.run = _with_audit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_audit_mod", mod)

    result = await adapter.execute(_task(callable_path="_dap_test_audit_mod:run"))
    assert result.success is True
    assert result.structured is not None
    # __audit must NOT appear in state_delta
    assert "__audit" not in result.structured["state_delta"]
    assert result.structured["audit"]["tokens_used"] == 100
    assert result.structured["audit"]["cost_usd"] == 0.002
    assert result.tokens_used == 100
    assert result.cost_usd == pytest.approx(0.002)


# ---------------------------------------------------------------------------
# pass_prompt / pass_context toggles
# ---------------------------------------------------------------------------


async def _capture_state(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {"captured_keys": sorted(state.keys())}


@pytest.mark.asyncio
async def test_pass_prompt_true_injects_prompt_xml(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    mod = types.ModuleType("_dap_test_prompt_mod")
    mod.run = _capture_state  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_prompt_mod", mod)

    result = await adapter.execute(
        _task(callable_path="_dap_test_prompt_mod:run", pass_prompt=True)
    )
    assert result.success is True
    assert result.structured is not None
    assert "prompt_xml" in result.structured["state_delta"]["captured_keys"]


@pytest.mark.asyncio
async def test_pass_prompt_false_omits_prompt_xml(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    mod = types.ModuleType("_dap_test_noprompt_mod")
    mod.run = _capture_state  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_noprompt_mod", mod)

    result = await adapter.execute(
        _task(callable_path="_dap_test_noprompt_mod:run", pass_prompt=False)
    )
    assert result.success is True
    assert result.structured is not None
    assert "prompt_xml" not in result.structured["state_delta"]["captured_keys"]


# ---------------------------------------------------------------------------
# timeout=None runs without limit
@pytest.mark.asyncio
async def test_timeout_none_runs_without_limit(adapter: PythonFuncAdapter) -> None:
    import sys, types
    mod = types.ModuleType("_dap_test_notimeout_mod")
    mod.run = lambda state, config: {"done": True}  # type: ignore[attr-defined]
    sys.modules["_dap_test_notimeout_mod"] = mod
    try:
        result = await adapter.execute(_task(callable_path="_dap_test_notimeout_mod:run", timeout_ms=None))
        assert result.success
    finally:
        del sys.modules["_dap_test_notimeout_mod"]


# timeout
# ---------------------------------------------------------------------------


async def _slow_func(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(30)
    return {}


@pytest.mark.asyncio
async def test_timeout_returns_failure(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    mod = types.ModuleType("_dap_test_slow_mod")
    mod.run = _slow_func  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_slow_mod", mod)

    result = await adapter.execute(
        _task(callable_path="_dap_test_slow_mod:run", timeout_ms=100)
    )
    assert result.success is False
    assert any("timed out" in err for err in result.errors)


# ---------------------------------------------------------------------------
# callable raises an exception
# ---------------------------------------------------------------------------


async def _raises(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    raise ValueError("boom from user code")


@pytest.mark.asyncio
async def test_callable_exception_returns_failure(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    mod = types.ModuleType("_dap_test_exc_mod")
    mod.run = _raises  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_exc_mod", mod)

    result = await adapter.execute(_task(callable_path="_dap_test_exc_mod:run"))
    assert result.success is False
    assert any("ValueError" in err and "boom" in err for err in result.errors)


# ---------------------------------------------------------------------------
# structured shape is always consistent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_shape_consistent(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    async def _ok(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return {"x": 1}

    mod = types.ModuleType("_dap_test_shape_mod")
    mod.run = _ok  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_shape_mod", mod)

    expected_keys = {"state_delta", "audit"}

    success = await adapter.execute(_task(callable_path="_dap_test_shape_mod:run"))
    assert success.structured is not None
    assert set(success.structured.keys()) == expected_keys

    failure = await adapter.execute(_task())  # missing callable_path
    assert failure.structured is not None
    assert set(failure.structured.keys()) == expected_keys


# ---------------------------------------------------------------------------
# __pause sentinel extraction
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# registry integration
# ---------------------------------------------------------------------------


def test_registered_in_default_registry() -> None:
    from dap_runtimes import create_default_registry

    registry = create_default_registry()
    assert registry.has("python-func")
    adapter = registry.get("python-func")
    assert adapter.id == "python-func"

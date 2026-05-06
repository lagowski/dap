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
    pipeline_state: dict[str, Any] | None = None,
) -> RuntimeTask:
    config: dict[str, object] = {}
    if callable_path is not None:
        config["callable_path"] = callable_path
    if pass_prompt is not None:
        config["pass_prompt"] = pass_prompt
    if pass_context is not None:
        config["pass_context"] = pass_context
    if pipeline_state is not None:
        # Mirrors what node_executor injects (#165) — full pipeline state
        # snapshot keyed under "__pipeline_state".
        config["__pipeline_state"] = pipeline_state
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
async def test_callable_path_without_colon_returns_error(adapter: PythonFuncAdapter) -> None:
    """callable_path must use 'module:attr' form — no colon = configuration error (#192)."""
    result = await adapter.execute(_task(callable_path="nodotnocolon"))
    assert result.success is False
    assert any("module.path:func_name" in err for err in result.errors)


@pytest.mark.asyncio
async def test_dot_only_callable_path_rejected(adapter: PythonFuncAdapter) -> None:
    """Dot-only notation (no ':') is rejected after #192 — the fallback worked only
    when every intermediate segment was importable as a module, which is fragile.
    """
    result = await adapter.execute(_task(callable_path="os.path.join"))
    assert result.success is False
    assert any("module.path:func_name" in err for err in result.errors)


@pytest.mark.asyncio
async def test_callable_path_with_colon_resolves(adapter: PythonFuncAdapter) -> None:
    """Explicit 'module.path:attr' form is the only accepted shape post-#192."""
    # os.path.join returns a str, not a dict — adapter resolves it then fails on return type.
    result = await adapter.execute(_task(callable_path="os.path:join"))
    assert result.success is False
    assert any("dict" in err for err in result.errors)


@pytest.mark.parametrize(
    "bad_path",
    [
        ":run",  # empty module
        "pkg:",  # empty attr
        ":",  # both empty
        "  :run",  # whitespace-only module after strip
        "pkg:  ",  # whitespace-only attr after strip
        "  :  ",  # both whitespace
    ],
)
@pytest.mark.asyncio
async def test_callable_path_empty_segments_rejected(
    adapter: PythonFuncAdapter, bad_path: str
) -> None:
    """Both sides of the ':' separator must be non-empty after stripping (#192 follow-up).

    Without this check, e.g. \":run\" fell through to importlib.import_module(\"\")
    producing a generic 'cannot import' message instead of pointing at the shape error.
    """
    result = await adapter.execute(_task(callable_path=bad_path))
    assert result.success is False
    assert any("non-empty" in err for err in result.errors)


@pytest.mark.asyncio
async def test_bad_import_returns_failed_result(adapter: PythonFuncAdapter) -> None:
    """ImportError for a nonexistent module returns a structured _failed result, not a raise (#191).

    Matches sibling adapters (bash/http/codex) — configuration errors surface as
    failed RuntimeResult so the engine error path stays uniform.
    """
    result = await adapter.execute(_task(callable_path="no_such_package.no_such_module:run"))
    assert result.success is False
    assert any("cannot import" in err for err in result.errors)
    # Structured-failure shape: empty state_delta + audit, no tokens/cost
    assert result.structured == {"state_delta": {}, "audit": {}}
    assert result.tokens_used is None
    assert result.cost_usd is None
    assert result.output == ""


@pytest.mark.asyncio
async def test_module_top_level_raise_returns_failed_result(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-ImportError raised during module import (e.g. RuntimeError at top level)
    is also caught and returned as _failed, matching sibling-adapter behavior (#191).
    """
    # importlib raises whatever the module's top-level code raises. Simulate by
    # registering a finder that raises RuntimeError on import.
    import importlib.abc
    import importlib.machinery
    import sys

    class _RaisingLoader(importlib.abc.Loader):
        def create_module(self, spec):  # type: ignore[no-untyped-def]
            return None

        def exec_module(self, module):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom from module top level")

    class _RaisingFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):  # type: ignore[no-untyped-def]
            if name == "_dap_test_raise_mod":
                return importlib.machinery.ModuleSpec(name, _RaisingLoader())
            return None

    finder = _RaisingFinder()
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])

    result = await adapter.execute(_task(callable_path="_dap_test_raise_mod:run"))
    assert result.success is False
    assert any("RuntimeError" in err and "boom" in err for err in result.errors)
    assert result.structured == {"state_delta": {}, "audit": {}}


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
# __pipeline_state injection — the runtime_config key node_executor uses to
# forward the full PipelineState snapshot so python-func callables can read
# top-level fields and extensions without a separate state fetch (#165, #186).
# ---------------------------------------------------------------------------


async def _return_state(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Echo the state dict the adapter passed in — used to inspect injection."""
    # Drop prompt_xml (covered by pass_prompt tests) so assertions focus on
    # the __pipeline_state-derived keys.
    return {"received_state": {k: v for k, v in state.items() if k != "prompt_xml"}}


@pytest.mark.asyncio
async def test_pipeline_state_injected_into_callable_state(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """runtime_config['__pipeline_state'] becomes the seed of the callable's state arg (#186).

    node_executor injects the full PipelineState snapshot under this key
    (apps/engine/src/dap_engine/execution/node_executor.py:145). The adapter
    is expected to forward those fields into the user callable's ``state``
    argument so the callable can resolve top-level + extensions without a
    separate DB fetch — that's the contract that #165 introduced.
    """
    import sys
    import types

    mod = types.ModuleType("_dap_test_pipeline_state_mod")
    mod.run = _return_state  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_pipeline_state_mod", mod)

    snapshot = {
        "run_id": "abc-123",
        "repo": "rafeekpro/dap",
        "branch": "develop",
        "extensions": {"review_approved": True, "review_attempts": 2},
    }
    result = await adapter.execute(
        _task(
            callable_path="_dap_test_pipeline_state_mod:run",
            pipeline_state=snapshot,
            pass_prompt=False,
        )
    )

    assert result.success is True
    assert result.structured is not None
    received = result.structured["state_delta"]["received_state"]
    assert received["run_id"] == "abc-123"
    assert received["repo"] == "rafeekpro/dap"
    assert received["branch"] == "develop"
    assert received["extensions"] == {"review_approved": True, "review_attempts": 2}


@pytest.mark.asyncio
async def test_pipeline_state_missing_yields_empty_seed(
    adapter: PythonFuncAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No '__pipeline_state' in runtime_config → callable's state is empty (#186).

    Backward-compat path: pre-#165 callers that don't inject the snapshot
    must still run without error; the callable simply sees an empty state
    seed (plus prompt_xml when pass_prompt=True).
    """
    import sys
    import types

    mod = types.ModuleType("_dap_test_no_pipeline_state_mod")
    mod.run = _return_state  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_dap_test_no_pipeline_state_mod", mod)

    result = await adapter.execute(
        _task(callable_path="_dap_test_no_pipeline_state_mod:run", pass_prompt=False)
    )
    assert result.success is True
    assert result.structured is not None
    assert result.structured["state_delta"]["received_state"] == {}


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

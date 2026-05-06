"""Python callable runtime adapter — executes an arbitrary Python function as a DAP agent.

Use cases: agent logic that is inherently code — conditional prompt construction,
complex output parsing, GitHub API calls woven together with LLM invocations.
Avoids the need to rewrite Python-native pipelines as Jinja/XML templates.

Function signature (what DAP expects):

    async def run(state: dict, config: dict) -> dict:
        ...
        return {
            "my_output_field": result,
            "__audit": {"tokens_used": 42, "cost_usd": 0.001},
        }

Sync ``def run(state, config)`` is also supported — the adapter wraps it in
``loop.run_in_executor(None, ...)`` so the event loop is not blocked.

The ``__audit`` key is reserved: it is extracted from the return dict and
routed to ``RuntimeResult.structured["audit"]`` (and drives ``tokens_used`` /
``cost_usd`` on the result). It is NOT merged into ``state_delta``.

``runtime_config``:

    callable_path  str   required  "module.path:func_name", resolved at call time.
    pass_prompt    bool  optional  Pass prompt_xml in state["prompt_xml"]. Default True.
    pass_context   bool  optional  Pass task.context dict in state["context"]. Default False.

Entrypoint resolution happens at invocation time (not startup) — allows
installing the package without restarting the engine.

Security model (v0.1): same trust level as ``bash`` — the callable runs with
the engine's process privileges. No sandboxing, no import restrictions, no
network confinement. The Python package must be installed in the engine's venv.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import platform
import time
from typing import Any

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.python_func")

MS_PER_SECOND: int = 1000


class PythonFuncAdapter(BaseAdapter):
    """Executes an arbitrary Python callable (sync or async) as a DAP agent."""

    id = "python-func"
    display_name = "Python Function"
    kind: RuntimeKind = "shell"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True, version=platform.python_version())

    async def execute(self, task: RuntimeTask) -> RuntimeResult:  # noqa: PLR0911,PLR0912,PLR0915
        config = task.runtime_config

        callable_path = config.get("callable_path")
        if not callable_path or not isinstance(callable_path, str):
            return _failed(
                "python-func: runtime_config.callable_path is required "
                "(format: 'package.module:func_name')",
                duration_ms=0,
            )

        if ":" in callable_path:
            module_path, func_name = callable_path.rsplit(":", 1)
        elif "." in callable_path:
            # Dot-notation fallback: "a.b.c" → module "a.b", func "c"
            module_path, func_name = callable_path.rsplit(".", 1)
        else:
            return _failed(
                f"python-func: callable_path must be 'module.path:func_name' "
                f"or 'module.path.func_name', got {callable_path!r}",
                duration_ms=0,
            )

        # Resolve at invocation time — allows package installs without engine restart.
        # Any import-time failure (ImportError, SyntaxError, raises in module
        # top-level code) is a configuration error — return _failed to match
        # sibling adapters (bash/http/codex). BaseException (KeyboardInterrupt,
        # SystemExit, GeneratorExit) still propagates. #191
        try:
            mod = importlib.import_module(module_path)
        except Exception as exc:
            return _failed(
                f"python-func: cannot import '{callable_path}' "
                f"({type(exc).__name__}: {exc})",
                duration_ms=0,
            )

        func = getattr(mod, func_name, _MISSING)
        if func is _MISSING:
            return _failed(
                f"python-func: module '{module_path}' has no attribute '{func_name}'",
                duration_ms=0,
            )

        if not callable(func):
            return _failed(
                f"python-func: '{callable_path}' is not callable",
                duration_ms=0,
            )

        pass_prompt = bool(config.get("pass_prompt", True))
        pass_context = bool(config.get("pass_context", False))

        # Seed state with the full PipelineState snapshot injected by node_executor
        # so python-func callables that use cortex.adapters.pipeline_state can resolve
        # all fields (top-level and extensions) without a separate DB fetch.
        pipeline_snapshot: dict[str, Any] = config.get("__pipeline_state") or {}
        state: dict[str, Any] = dict(pipeline_snapshot)
        if pass_prompt:
            state["prompt_xml"] = task.prompt_xml
        if pass_context and task.context is not None:
            state["context"] = task.context.model_dump()

        start = time.monotonic()
        # timeout_ms=None means no timeout (run until completion).
        timeout_seconds = (
            max(task.timeout_ms, 1) / MS_PER_SECOND if task.timeout_ms is not None else None
        )

        try:
            if asyncio.iscoroutinefunction(func):
                awaitable: Any = func(state, config)
            else:
                loop = asyncio.get_running_loop()
                awaitable = loop.run_in_executor(None, func, state, config)

            if timeout_seconds is not None:
                result: Any = await asyncio.wait_for(awaitable, timeout=timeout_seconds)
            else:
                result = await awaitable

        except TimeoutError:
            return _failed(
                f"python-func: '{callable_path}' timed out after {task.timeout_ms}ms",
                duration_ms=_elapsed_ms(start),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _failed(
                f"python-func: '{callable_path}' raised {type(exc).__name__}: {exc}",
                duration_ms=_elapsed_ms(start),
            )

        if not isinstance(result, dict):
            return _failed(
                f"python-func: '{callable_path}' must return a dict, got {type(result).__name__}",
                duration_ms=_elapsed_ms(start),
            )

        duration_ms = _elapsed_ms(start)
        audit: dict[str, Any] = result.pop("__audit", {}) or {}

        tokens_used: int | None = None
        cost_usd: float | None = None
        if isinstance(audit, dict):
            with contextlib.suppress(TypeError, ValueError):
                if (v := audit.get("tokens_used")) is not None:
                    tokens_used = int(v)
            with contextlib.suppress(TypeError, ValueError):
                if (v := audit.get("cost_usd")) is not None:
                    cost_usd = float(v)

        return RuntimeResult(
            success=True,
            output=str(result),
            duration_ms=duration_ms,
            errors=[],
            structured={"state_delta": result, "audit": audit},
            tokens_used=tokens_used,
            cost_usd=cost_usd,
        )


# Sentinel — distinct from None so getattr can distinguish "attr missing" from "attr is None".
_MISSING: object = object()


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * MS_PER_SECOND)


def _failed(message: str, *, duration_ms: int) -> RuntimeResult:
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=duration_ms,
        errors=[message],
        structured={"state_delta": {}, "audit": {}},
    )

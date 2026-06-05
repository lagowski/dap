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
import concurrent.futures
import contextlib
import importlib
import json
import logging
import os
import platform
import time
from collections.abc import Iterator
from typing import Any

from dap_types import HealthStatus, OutputCallback, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters._subprocess_env import compute_env_overlay
from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.python_func")

MS_PER_SECOND: int = 1000

# python-func callables run IN the engine process and read configuration
# from ``os.environ`` (e.g. cortex resolves GitHub tokens via pydantic
# settings). To honour the #65/#388 env-layering contract for in-process
# nodes we patch ``os.environ`` with the instance/project/runtime overlay
# around the callable. ``os.environ`` is process-global, so concurrent runs
# (which may carry DIFFERENT project tokens) must not interleave their
# patches — this lock serialises overlay-bearing python-func execution.
# Nodes with no overlay skip the lock entirely and keep full concurrency.
_ENV_OVERLAY_LOCK = asyncio.Lock()


# Dedicated thread pool for sync ``python-func`` callables (#621).
#
# Previously, sync callables ran via ``loop.run_in_executor(None, ...)``,
# which routes work through asyncio's *default* executor. FastAPI also
# routes its sync ``def`` route handlers (including ``/health``) through
# that same default executor. When a long-running in-process node held a
# default-executor slot for minutes, ``/health`` queued behind it and
# external probes (k8s liveness/readiness, monitors, dashboards) saw the
# engine as down even though the DB was healthy. The cfd#134 run
# ``67771e2e-...`` reproduced this with a ~196s ``coder`` node holding
# ``/health`` unresponsive for the full duration.
#
# This pool is python-func-only — sync node callables run here, sync
# FastAPI handlers continue to use the default pool. They never compete.
#
# Worker count: configurable via ``DAP_PYTHON_FUNC_MAX_WORKERS``
# (default 10). 10 is enough for typical cortex parallelism (5-8
# concurrent nodes) without sprawling thread counts. Operators on
# higher-throughput hosts can raise it; on memory-constrained pods
# they can lower it. Setting it to 1 falls back to fully-serialised
# behaviour, useful for deterministic debugging.
_PYTHON_FUNC_MAX_WORKERS = max(1, int(os.environ.get("DAP_PYTHON_FUNC_MAX_WORKERS", "10")))
_NODE_EXECUTOR: concurrent.futures.ThreadPoolExecutor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_PYTHON_FUNC_MAX_WORKERS,
    thread_name_prefix="dap-python-func",
)


@contextlib.contextmanager
def _patched_environ(overlay: dict[str, str]) -> Iterator[None]:
    """Apply *overlay* to ``os.environ``, restoring prior values on exit.

    Keys absent before are removed afterwards; pre-existing keys are
    restored to their original value. Restoration runs in ``finally`` so a
    raising / timing-out callable never leaks the overlay into the engine env.
    """
    saved: dict[str, str | None] = {}
    try:
        for key, value in overlay.items():
            saved[key] = os.environ.get(key)
            os.environ[key] = value
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


class PythonFuncAdapter(BaseAdapter):
    """Executes an arbitrary Python callable (sync or async) as a DAP agent."""

    id = "python-func"
    display_name = "Python Function"
    kind: RuntimeKind = "shell"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(available=True, version=platform.python_version())

    async def execute(  # noqa: PLR0911,PLR0912,PLR0915
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult:
        # on_output ignored: python-func runs an in-process callable, not a
        # subprocess. Streaming is wired only for subprocess adapters in
        # Phase 3b-2a (#662).
        del on_output
        config = task.runtime_config

        callable_path = config.get("callable_path")
        if not callable_path or not isinstance(callable_path, str):
            return _failed(
                "python-func: runtime_config.callable_path is required "
                "(format: 'package.module:func_name')",
                duration_ms=0,
            )

        # Require explicit `module:attr` separator. Dot-notation was previously
        # accepted as a fallback but only worked when every intermediate segment
        # was importable as a module — e.g. `os.path.join` worked because
        # `os.path` is a module, but `pkg.helpers.run` failed confusingly when
        # `helpers` was a function. Always require `:` so the boundary between
        # module and attribute is unambiguous. #192
        if ":" not in callable_path:
            return _failed(
                f"python-func: callable_path must use 'module.path:func_name' "
                f"format (got {callable_path!r}). Dot-only notation was removed "
                f"in #192 — use ':' to separate module from attribute.",
                duration_ms=0,
            )
        module_path, func_name = callable_path.rsplit(":", 1)
        module_path = module_path.strip()
        func_name = func_name.strip()
        if not module_path or not func_name:
            return _failed(
                f"python-func: callable_path must have non-empty module and attr "
                f"separated by ':' (got {callable_path!r}).",
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
                f"python-func: cannot import '{callable_path}' ({type(exc).__name__}: {exc})",
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

        # Env-layering contract (#65/#388) for in-process nodes: instance <
        # project < per-agent runtime_config.env. Validate before timing so a
        # malformed override fails the same way the subprocess adapters do.
        env_overlay, overlay_error = compute_env_overlay(
            task.project_env_vars,
            config,
            instance_env_vars=task.instance_env_vars,
        )
        if overlay_error is not None:
            return _failed(f"python-func: {overlay_error}", duration_ms=0)

        start = time.monotonic()
        # timeout_ms=None means no timeout (run until completion).
        timeout_seconds = (
            max(task.timeout_ms, 1) / MS_PER_SECOND if task.timeout_ms is not None else None
        )

        async def _invoke() -> Any:
            if asyncio.iscoroutinefunction(func):
                awaitable: Any = func(state, config)
            else:
                # Use the python-func-dedicated executor (#621) so a
                # long sync node doesn't starve FastAPI's default pool
                # and queue ``/health`` behind it.
                loop = asyncio.get_running_loop()
                awaitable = loop.run_in_executor(_NODE_EXECUTOR, func, state, config)
            if timeout_seconds is not None:
                return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
            return await awaitable

        try:
            if env_overlay:
                # Hold the lock across the whole callable: the overlay lives in
                # the process-global os.environ, so concurrent overlay-bearing
                # nodes must not interleave (correctness over throughput).
                async with _ENV_OVERLAY_LOCK:
                    with _patched_environ(env_overlay):
                        result: Any = await _invoke()
            else:
                result = await _invoke()

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
        # Extract __audit without mutating the caller's dict — they may have
        # retained the reference (e.g. cached it) and pop() would silently
        # delete the key from their copy. Build a fresh state_delta instead.
        audit_raw: Any = result.get("__audit", {})
        audit: dict[str, Any] = audit_raw if isinstance(audit_raw, dict) else {}
        state_delta: dict[str, Any] = {k: v for k, v in result.items() if k != "__audit"}

        tokens_used: int | None = None
        cost_usd: float | None = None
        with contextlib.suppress(TypeError, ValueError):
            if (v := audit.get("tokens_used")) is not None:
                tokens_used = int(v)
        with contextlib.suppress(TypeError, ValueError):
            if (v := audit.get("cost_usd")) is not None:
                cost_usd = float(v)

        return RuntimeResult(
            success=True,
            # Stringify state_delta (the user's return minus __audit), not the
            # raw result — keeps __audit metadata out of stdout/execution logs
            # the same way the original pop()-mutating code did.
            output=json.dumps(state_delta, default=str),
            duration_ms=duration_ms,
            errors=[],
            structured={"state_delta": state_delta, "audit": audit},
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

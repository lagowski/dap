"""Fail-fast preflight for ``python-func`` callables (#710).

A ``python-func`` node resolves its ``callable_path`` lazily, at the moment
the node fires. When the target package isn't installed (e.g. ``dap-cortex``
missing) the failure surfaces *mid-run* as a cryptic failed node — the run
already started, burned earlier nodes, and looks like a pipeline bug.

This module resolves every ``python-func`` node's callable *before* a run
starts (mirroring ``pipeline_runtime_policy_error``), so an unresolvable
callable is a clean 422 at the trigger boundary instead. The resolution uses
the exact same :func:`dap_runtimes.adapters.python_func.resolve_callable` the
adapter uses at run time, so "passes preflight" and "resolves at run time"
mean the same thing. Resolution is per-request, preserving install-without-
restart: a package installed after the engine started resolves on the next
trigger.

``inspect_pipeline_callables`` returns the full per-node result so a future
read-only introspection endpoint can reuse it; ``pipeline_callable_preflight_error``
is the trigger-time convenience that returns the first failure as a string.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dap_runtimes import PythonFuncAdapter, RuntimeRegistry
from dap_runtimes.adapters.python_func import resolve_callable
from sqlalchemy import select
from sqlalchemy.orm import Session

from dap_engine.execution.backend_profiles import resolve_backend_profile
from dap_engine.persistence.models import AgentORM, AgentVersionORM, PipelineVersionORM

logger = logging.getLogger("dap.engine.api.run_callable_preflight")

_PYTHON_FUNC = "python-func"


@dataclass(frozen=True)
class CallableCheck:
    """Resolution result for one ``python-func`` node."""

    node_id: str
    agent_id: str
    callable_path: str | None
    resolvable: bool
    error: str | None


def inspect_pipeline_callables(
    session: Session,
    pipeline_version: PipelineVersionORM,
    registry: RuntimeRegistry,
) -> list[CallableCheck]:
    """Resolve every ``python-func`` node's callable; report per node.

    Non-``python-func`` nodes are skipped (they don't carry a callable). The
    effective runtime is resolved through the backend profile, matching what
    the executor will actually run.

    The preflight only predicts the **real** :class:`PythonFuncAdapter`'s
    resolution. If the registry has a different adapter bound to
    ``python-func`` (a test stub that ignores ``callable_path``), there is
    nothing to predict — return no checks rather than reject a run the stub
    would happily execute.
    """
    if not (
        registry.has(_PYTHON_FUNC) and isinstance(registry.get(_PYTHON_FUNC), PythonFuncAdapter)
    ):
        return []

    agent_ids: set[str] = set()
    for node in pipeline_version.nodes:
        if isinstance(node, dict):
            agent_id = node.get("agent_id")
            if isinstance(agent_id, str):
                agent_ids.add(agent_id)
    if not agent_ids:
        return []

    rows = session.execute(
        select(AgentORM, AgentVersionORM)
        .join(
            AgentVersionORM,
            (AgentVersionORM.agent_id == AgentORM.id)
            & (AgentVersionORM.version == AgentORM.current_version),
        )
        .where(AgentORM.id.in_(agent_ids))
    ).all()
    rows_by_agent_id = {agent.id: (agent, version) for agent, version in rows}

    checks: list[CallableCheck] = []
    for node in pipeline_version.nodes:
        if not isinstance(node, dict):
            continue
        agent_id = node.get("agent_id")
        if not isinstance(agent_id, str):
            continue
        pair = rows_by_agent_id.get(agent_id)
        if pair is None:
            continue
        agent, version = pair
        node_id = node.get("id")
        resolved = resolve_backend_profile(
            backend_profiles=pipeline_version.backend_profiles,
            node_id=node_id if isinstance(node_id, str) else "",
            agent_id=agent.id,
            agent_name=agent.name,
            agent_role=agent.role,
            base_runtime_id=version.runtime_id,
            base_runtime_config=version.runtime_config,
        )
        if resolved.runtime_id != _PYTHON_FUNC:
            continue
        callable_path = resolved.runtime_config.get("callable_path")
        _func, error = resolve_callable(callable_path)
        checks.append(
            CallableCheck(
                node_id=node_id if isinstance(node_id, str) else "",
                agent_id=agent.id,
                callable_path=callable_path if isinstance(callable_path, str) else None,
                resolvable=error is None,
                error=error,
            )
        )
    return checks


def pipeline_callable_preflight_error(
    session: Session,
    pipeline_version: PipelineVersionORM,
    registry: RuntimeRegistry,
) -> str | None:
    """Return a message for the first unresolvable ``python-func`` node, else None."""
    for check in inspect_pipeline_callables(session, pipeline_version, registry):
        if not check.resolvable:
            return (
                f"Node '{check.node_id}' cannot run: {check.error} "
                f"(install the package providing this callable, then retry)."
            )
    return None

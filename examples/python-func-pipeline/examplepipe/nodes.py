"""Pipeline nodes — the whole ``python-func`` contract in a few lines.

A DAP ``python-func`` node is just a function::

    async def fn(state: dict, config: dict) -> dict

- ``state``  — the live ``PipelineState``. Non-engine-native fields live under
  ``state["extensions"]`` (the same convention Cortex uses) so edge conditions
  can read them via ``extensions.<key>``.
- ``config`` — the node's ``runtime_config`` extras, passed straight through.
  One generic function can serve many nodes, each with a different ``config``.
- returns — a **dict of state updates** the engine merges back into the run.

Nodes should be pure where possible (no external writes) so the engine can
retry them idempotently; push side effects into their own downstream node.
"""

from __future__ import annotations

from typing import Any


async def greet(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Read a name from ``extensions`` (or ``config``), write a greeting."""
    ext = state.get("extensions") or {}
    name = ext.get("name") or config.get("default_name") or "world"
    return {"extensions": {"greeting": f"Hello, {name}!"}}


async def shout(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Uppercase the greeting produced by the upstream node."""
    ext = state.get("extensions") or {}
    greeting = str(ext.get("greeting") or "")
    return {"extensions": {"greeting_loud": greeting.upper()}}

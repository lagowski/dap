"""The python-func pipeline example actually imports, runs, and wires up.

Proves the example in ``examples/python-func-pipeline/`` is real (not a
file-existence stub): the node functions execute and return state updates, the
gate is a no-op, and every ``callable_path`` in the bundle resolves to an
importable function — exactly the ``module:function`` import DAP performs.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "python-func-pipeline"
BUNDLE_PATH = EXAMPLE_DIR / "example.pipeline-bundle.json"


@pytest.fixture(autouse=True)
def _example_on_path() -> Iterator[None]:
    """Put the example package on ``sys.path`` (what the editable install does)."""
    sys.path.insert(0, str(EXAMPLE_DIR))
    try:
        yield
    finally:
        sys.path.remove(str(EXAMPLE_DIR))
        for name in [m for m in sys.modules if m == "examplepipe" or m.startswith("examplepipe.")]:
            del sys.modules[name]


def _resolve(callable_path: str) -> Callable[..., Any]:
    """Resolve a ``module:function`` path the same way DAP's python-func adapter does."""
    module_name, function_name = callable_path.split(":")
    fn: Callable[..., Any] = getattr(importlib.import_module(module_name), function_name)
    return fn


def test_greet_node_writes_a_greeting() -> None:
    greet = _resolve("examplepipe.nodes:greet")
    result = asyncio.run(greet({"extensions": {"name": "DAP"}}, {}))
    assert result == {"extensions": {"greeting": "Hello, DAP!"}}


def test_greet_falls_back_to_config_then_default() -> None:
    greet = _resolve("examplepipe.nodes:greet")
    assert (
        asyncio.run(greet({}, {"default_name": "ops"}))["extensions"]["greeting"] == "Hello, ops!"
    )
    assert asyncio.run(greet({}, {}))["extensions"]["greeting"] == "Hello, world!"


def test_shout_uppercases_the_upstream_greeting() -> None:
    shout = _resolve("examplepipe.nodes:shout")
    result = asyncio.run(shout({"extensions": {"greeting": "Hello, DAP!"}}, {}))
    assert result == {"extensions": {"greeting_loud": "HELLO, DAP!"}}


def test_gate_is_a_noop() -> None:
    noop = _resolve("examplepipe.gates:noop")
    assert noop({"extensions": {"x": 1}}, {}) == {}


def test_bundle_references_resolve_to_importable_callables() -> None:
    bundle = json.loads(BUNDLE_PATH.read_text())
    agents = bundle["bundled_agents"]

    # Every graph node points at a bundled agent.
    for node in bundle["pipeline"]["nodes"]:
        assert node["agent_id"] in agents, f"node {node['id']} → missing agent {node['agent_id']}"

    # Every python-func agent's callable_path imports and is callable.
    for agent_id, agent in agents.items():
        if agent["runtime_id"] != "python-func":
            continue
        fn = _resolve(agent["runtime_config"]["callable_path"])
        assert callable(fn), f"{agent_id}: {agent['runtime_config']['callable_path']} not callable"

    # The gate is in the pause list — that's where the human approval happens.
    assert "gate" in bundle["pipeline"]["defaults"]["approval_required_nodes"]

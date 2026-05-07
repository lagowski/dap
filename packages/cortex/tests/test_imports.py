"""Verify all cortex callable_path references resolve without import errors.

Same pattern as test_dap_bundles_import.py — ensures the engine can
import every node.run referenced in the pipeline bundles.
"""

import importlib


def _import(dotted: str) -> object:
    module_path, _, attr = dotted.rpartition(":")
    mod = importlib.import_module(module_path)
    return getattr(mod, attr)


# All callable_path values referenced in the 4 bundles
CALLABLES = [
    # Phase 1 — enrichment nodes
    "cortex.nodes.complexity_gate:run",
    "cortex.nodes.mockup:run",
    "cortex.nodes.specify:run",
    "cortex.nodes.cicd:run",
    "cortex.nodes.dispatcher:run",
    "cortex.nodes.finalize:run",
    # Phase 1 — dap_steps (callable_path values from bundles)
    "cortex.dap_steps.enrichment_write:run_side_effects",
    "cortex.dap_steps.human_gate:noop",
    # Phase 2 — execution nodes
    "cortex.nodes.coder:run",
    "cortex.nodes.designer:run",
    "cortex.nodes.documenter:run",
    "cortex.nodes.code_reviewer:run",
    # Phase 3 — merge nodes
    "cortex.nodes.tester:run",
    "cortex.nodes.pr_creator:run",
    "cortex.nodes.reviewer:run",
    "cortex.nodes.pr_merger:run",
]


def test_all_callables_importable() -> None:
    for path in CALLABLES:
        obj = _import(path)
        assert callable(obj), f"{path} is not callable"


def test_state_importable() -> None:
    from cortex.state import CortexState  # noqa: F401


def test_adapters_importable() -> None:
    from cortex.adapters.pipeline_state import (  # noqa: F401
        cortex_to_dap,
        dap_to_cortex,
        preserve_extensions,
    )


def test_config_importable() -> None:
    from cortex.config.settings import load_agent_configs, load_settings  # noqa: F401


def test_backends_importable() -> None:
    from cortex.backends.registry import create_backend  # noqa: F401

"""Smoke test: import each node module without error.

Does NOT execute node logic — just verifies the module loads cleanly
and `run` is a callable (coroutine function).
"""

import importlib
import inspect

NODE_MODULES = [
    "cortex.nodes.complexity_gate",
    "cortex.nodes.mockup",
    "cortex.nodes.specify",
    "cortex.nodes.cicd",
    "cortex.nodes.dispatcher",
    "cortex.nodes.finalize",
    "cortex.nodes.coder",
    "cortex.nodes.designer",
    "cortex.nodes.documenter",
    "cortex.nodes.code_reviewer",
    "cortex.nodes.tester",
    "cortex.nodes.pr_creator",
    "cortex.nodes.reviewer",
    "cortex.nodes.pr_merger",
    "cortex.nodes.retry",
]

DAP_STEP_MODULES = [
    "cortex.dap_steps.human_gate",
    "cortex.dap_steps.enrichment_write",
    "cortex.dap_steps.execution_write",
    "cortex.dap_steps.git_ops",
    "cortex.dap_steps.pr_write",
]


def test_node_modules_import_cleanly() -> None:
    for module_path in NODE_MODULES:
        mod = importlib.import_module(module_path)
        assert hasattr(mod, "run"), f"{module_path} missing run()"
        assert callable(mod.run), f"{module_path}.run is not callable"


def test_dap_step_modules_import_cleanly() -> None:
    # dap_steps use different callable names depending on their role
    entry_points = {
        "cortex.dap_steps.human_gate": "noop",
        "cortex.dap_steps.enrichment_write": "run_side_effects",
        "cortex.dap_steps.execution_write": "run_side_effects",
        "cortex.dap_steps.git_ops": "run_side_effects",
        "cortex.dap_steps.pr_write": "run_side_effects",
    }
    for module_path in DAP_STEP_MODULES:
        mod = importlib.import_module(module_path)
        attr = entry_points[module_path]
        assert hasattr(mod, attr), f"{module_path} missing {attr}()"
        assert callable(getattr(mod, attr)), f"{module_path}.{attr} is not callable"


def test_node_run_are_coroutines() -> None:
    for module_path in NODE_MODULES:
        mod = importlib.import_module(module_path)
        assert inspect.iscoroutinefunction(mod.run), f"{module_path}.run must be async"


def test_enrichment_write_run_side_effects_is_async() -> None:
    from cortex.dap_steps.enrichment_write import run_side_effects

    assert inspect.iscoroutinefunction(run_side_effects)


def test_bundles_present() -> None:
    import importlib.resources

    bundle_names = [
        "cortex-phase1.pipeline-bundle.json",
        "cortex-phase2.pipeline-bundle.json",
        "cortex-phase3.pipeline-bundle.json",
        "cortex-full.pipeline-bundle.json",
    ]
    for name in bundle_names:
        path = importlib.resources.files("cortex.dap_bundles") / name
        assert path.is_file(), f"Bundle not found: {name}"

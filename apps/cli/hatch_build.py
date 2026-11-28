"""Carry the ``dap demo`` example into the dap-cli wheel as ``dap_cli/_demo`` (#1042).

From a checkout the example lives at ``examples/python-func-pipeline``, outside
this project, so the wheel force-includes it from there. A wheel built from the
sdist finds it already under ``src/dap_cli/_demo`` (the sdist copies it in) and
needs nothing extra. A plain ``force-include`` can't express "if present": a
missing source is a hard build error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# hatchling exists only in the build environment, not in the dev venv.
from hatchling.builders.hooks.plugin.interface import (  # type: ignore[import-not-found]
    BuildHookInterface,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "python-func-pipeline"


class DemoAssetsHook(BuildHookInterface):  # type: ignore[misc]
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if (Path(self.root) / "src" / "dap_cli" / "_demo").is_dir() or not EXAMPLE.is_dir():
            return
        force = build_data["force_include"]
        force[str(EXAMPLE / "example.pipeline-bundle.json")] = (
            "dap_cli/_demo/example.pipeline-bundle.json"
        )
        for module in sorted((EXAMPLE / "examplepipe").glob("*.py")):
            force[str(module)] = f"dap_cli/_demo/examplepipe/{module.name}"

"""Prompt → pipeline bundle, via an operator-configured generator (cortex#1774 S5).

The engine does not design pipelines itself. An operator opts in by setting
``DAP_PIPELINE_GENERATOR_COMMAND`` (``ServerConfig.pipeline_generator_command``)
to a command such as ``cortex pipeline new --json --out <dir>``. The contract:

* the engine appends the user's prompt as the LAST argv element (never a shell);
* exit 0 → stdout is ONE JSON object with a ``bundle`` key holding a
  ``pipeline-export/1|2`` envelope (optional ``name`` / ``nodes``);
* exit 2 → the generator refused (bad prompt, invalid design); stderr says why;
* anything else is a generator failure.

The endpoint only returns the bundle; importing it into the canvas is the
existing ``POST /pipelines/import``, so every import rule (DAG validation,
``min_dap_version``, bundled agents) still applies to generated pipelines.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dap_engine.api.schemas import PipelineImportRequest

logger = logging.getLogger("dap.engine.pipeline_generator")

PROMPT_MAX_CHARS = 4000
_STDERR_TAIL_CHARS = 2000


class GeneratePipelineRequest(BaseModel):
    """Body of ``POST /pipelines/generate``."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        min_length=1,
        max_length=PROMPT_MAX_CHARS,
        description="What the pipeline should do, in plain words.",
    )


class GeneratedPipeline(BaseModel):
    """Response of ``POST /pipelines/generate``: a bundle ready for ``/pipelines/import``."""

    name: str
    nodes: int
    bundle: dict[str, Any] = Field(
        description="pipeline-export envelope; POST it to /pipelines/import as-is."
    )


class PipelineGeneratorStatus(BaseModel):
    """Response of ``GET /pipelines/generate``: is the prompt box usable here."""

    enabled: bool


class PipelineGeneratorError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _tail(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= _STDERR_TAIL_CHARS else "…" + text[-_STDERR_TAIL_CHARS:]


def run_generator(command: list[str] | None, prompt: str, timeout_s: float) -> GeneratedPipeline:
    """Run the generator and return its validated bundle; raise with an HTTP status."""
    if not command:
        raise PipelineGeneratorError(
            503,
            "pipeline generator not configured on this engine "
            "(set DAP_PIPELINE_GENERATOR_COMMAND)",
        )
    prompt = prompt.strip()
    if not prompt:
        raise PipelineGeneratorError(422, "prompt is empty")
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell; command is operator config
            [*command, prompt],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineGeneratorError(504, f"generator timed out after {timeout_s:g}s") from exc
    except OSError as exc:
        raise PipelineGeneratorError(502, f"generator could not start: {exc}") from exc

    if proc.returncode == 2:
        raise PipelineGeneratorError(422, f"generator refused: {_tail(proc.stderr)}")
    if proc.returncode != 0:
        raise PipelineGeneratorError(
            502, f"generator failed (exit {proc.returncode}): {_tail(proc.stderr)}"
        )
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineGeneratorError(502, f"generator stdout is not JSON: {exc}") from exc
    if not isinstance(out, dict) or not isinstance(out.get("bundle"), dict):
        raise PipelineGeneratorError(502, "generator stdout has no 'bundle' object")
    bundle = out["bundle"]
    try:
        parsed = PipelineImportRequest.model_validate(bundle)
    except ValidationError as exc:
        raise PipelineGeneratorError(
            502, f"generator returned an invalid bundle: {exc.error_count()} error(s): {exc}"
        ) from exc
    name = str(out.get("name") or parsed.pipeline.name)
    nodes = out.get("nodes")
    if not isinstance(nodes, int):
        nodes = len(parsed.pipeline.nodes)
    logger.info("generated pipeline %r (%d nodes)", name, nodes)
    return GeneratedPipeline(name=name, nodes=nodes, bundle=bundle)

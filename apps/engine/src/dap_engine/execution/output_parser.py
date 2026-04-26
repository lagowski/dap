"""Per-role output parsing — turns raw adapter text into a state diff.

The api-call adapter returns raw LLM text in ``RuntimeResult.output``.
For deterministic pipelines we want each agent role to write a *known
subset* of ``PipelineState`` fields — task_selector writes
``selected_issue_ids``, test_author writes ``test_files``, etc.

This module:
1. Pulls the JSON payload out of the LLM's text (XML wrapper or markdown
   fence — whichever the prompt template requested).
2. Validates it against the role's declared schema (``role_output_model``).
3. Returns a ``ParseResult`` with the parsed fields and any errors so the
   caller can decide whether to log + continue, or fail the node.

Roles without a declared schema are not parsed here — the runner falls
back to permissive ``RuntimeResult.structured`` merging for those.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from dap_types import role_output_model
from pydantic import ValidationError

logger = logging.getLogger("dap.engine.execution.output_parser")

# <output>{...}</output> — the canonical wrapper recommended by the
# stock prompt templates. Uses non-greedy + DOTALL so multi-line JSON works.
OUTPUT_TAG_PATTERN = re.compile(
    r"<output>\s*(.*?)\s*</output>",
    re.DOTALL,
)

# ```json\n{...}\n``` — accepted as a fallback because LLMs reach for it
# when not given an explicit format instruction.
JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*\n(.*?)\n```",
    re.DOTALL,
)


@dataclass
class ParseResult:
    """Outcome of parsing one node's raw output for state-merge candidates."""

    success: bool
    parsed: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    # When the role has no declared schema we skip parsing entirely;
    # the caller distinguishes this from a real failure to decide whether
    # to log a warning.
    skipped: bool = False


def parse_node_output(role: str, output: str) -> ParseResult:
    """Extract a state-diff dict from ``output`` according to ``role``'s schema.

    Returns ``ParseResult(skipped=True)`` for roles without a declared
    schema — the caller should fall back to permissive merging.
    """
    validator = role_output_model(role)
    if validator is None:
        return ParseResult(success=True, skipped=True)

    raw = _extract_json_block(output)
    if raw is None:
        return ParseResult(
            success=False,
            errors=[
                "No JSON payload found in output. Expected <output>{...}</output>, "
                "a ```json fence, or a bare {...} JSON object."
            ],
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ParseResult(
            success=False,
            errors=[f"Output is not valid JSON: {exc.msg} (pos={exc.pos})"],
        )

    if not isinstance(payload, dict):
        return ParseResult(
            success=False,
            errors=[f"Output JSON must be an object; got {type(payload).__name__}"],
        )

    try:
        validated = validator.model_validate(payload)
    except ValidationError as exc:
        return ParseResult(
            success=False,
            errors=[_format_validation_error(role, e) for e in exc.errors()],
        )

    # Drop the None defaults so we only carry fields the agent actually
    # wrote — keeps node snapshots minimal and avoids clobbering state
    # with explicit nulls.
    parsed = {k: v for k, v in validated.model_dump().items() if v is not None}
    return ParseResult(success=True, parsed=parsed)


def _extract_json_block(text: str) -> str | None:
    """Pull the first JSON payload from text. Prefers <output> over markdown fences."""
    if not text:
        return None

    tag_match = OUTPUT_TAG_PATTERN.search(text)
    if tag_match is not None:
        candidate = tag_match.group(1).strip()
        if candidate:
            return candidate

    fence_match = JSON_FENCE_PATTERN.search(text)
    if fence_match is not None:
        candidate = fence_match.group(1).strip()
        if candidate:
            return candidate

    # Last resort: maybe the whole text is JSON (some templates ask for that).
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    return None


def _format_validation_error(role: str, error: Any) -> str:
    """Pretty-print a single Pydantic validation error for the role schema.

    `error` is a Pydantic v2 ErrorDetails (TypedDict-ish); typed as Any to
    avoid coupling to a private import.
    """
    loc = ".".join(str(p) for p in error.get("loc", ())) or "<root>"
    msg = error.get("msg", "validation error")
    err_type = error.get("type", "")
    suffix = f" [{err_type}]" if err_type else ""
    return f"{role}.{loc}: {msg}{suffix}"

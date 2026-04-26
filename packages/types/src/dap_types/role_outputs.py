"""Per-role contracts for what an agent's output is allowed to write.

Each well-known agent role declares the subset of `PipelineState` fields its
output may touch. The engine uses this to:

1. Build a Pydantic validator that enforces the field allow-list and the
   types from `PipelineState` itself (no second source of truth).
2. Reject unknown keys with a descriptive error so a buggy prompt template
   doesn't silently drop fields.

Roles not listed here are treated as "custom" — no enforcement, the engine
permissively merges any keys whose names match `PipelineState` fields.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from dap_types.state import PipelineState

# Allow-list of state fields each well-known role may write. Add a role here
# AND wire its prompt template; the engine handles the rest automatically.
ROLE_FIELDS: dict[str, frozenset[str]] = {
    "task_selector": frozenset({"selected_issue_ids"}),
    "test_author": frozenset(
        {
            "tests_generated",
            "test_files",
            "test_generation_errors",
        }
    ),
    "implementer": frozenset(
        {
            "modified_files",
            "implementation_notes",
        }
    ),
    "verifier": frozenset(
        {
            "verification_status",
            "verification_reason",
            "tests_passed",
            "last_test_output",
        }
    ),
}


@cache
def role_output_model(role: str) -> type[BaseModel] | None:
    """Build (and cache) a Pydantic model that validates a role's output.

    Returns ``None`` for roles without a declared field allow-list — callers
    should fall back to permissive merging in that case.

    The dynamic model:
    - has only the fields named in ``ROLE_FIELDS[role]``,
    - copies their type annotations from ``PipelineState`` (single source
      of truth for types),
    - makes every field optional (a node may write a subset),
    - rejects unknown keys via ``extra="forbid"``.
    """
    fields = ROLE_FIELDS.get(role)
    if fields is None:
        return None

    pipeline_fields = PipelineState.model_fields
    model_fields: dict[str, Any] = {}
    for name in fields:
        info = pipeline_fields[name]
        annotation = info.annotation
        if annotation is None:
            # Shouldn't happen — every PipelineState field is annotated —
            # but guard so create_model gets a usable type.
            annotation = Any
        # All output fields are optional — nodes may write a subset.
        model_fields[name] = (annotation | None, None)

    return create_model(
        f"{role.title().replace('_', '')}Output",
        __config__=ConfigDict(extra="forbid"),
        **model_fields,
    )

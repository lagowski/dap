"""Per-agent / per-role contracts for what an agent's output may write.

Two layers of contract:

1. **Per-agent** (preferred, v0.5+) — ``Agent.output_schema`` declares the
   exact subset of ``PipelineState`` fields the agent writes. Resolved
   via ``agent_output_model(agent)``.
2. **Per-role** (legacy) — ``ROLE_FIELDS`` ships hardcoded contracts for
   well-known role names. Used as a fallback when ``output_schema`` is
   empty so existing pipelines keep working without a migration.

Both layers build a dynamic Pydantic model that:

- exposes only the declared fields,
- copies their type annotations from ``PipelineState`` (single source of
  truth — no parallel type table to keep in sync),
- treats every field as optional (a node may write a subset),
- rejects unknown keys via ``extra="forbid"``.

Custom roles with no declared schema are not enforced — the engine
falls back to permissive ``RuntimeResult.structured`` merging for those.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, create_model

from dap_types.state import PipelineState

if TYPE_CHECKING:
    from dap_types.agent import Agent

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

    Legacy entry point — callers with an ``Agent`` instance should
    prefer :func:`agent_output_model`, which honours per-agent
    ``output_schema`` overrides.

    Returns ``None`` for roles without a declared field allow-list — callers
    should fall back to permissive merging in that case.
    """
    fields = ROLE_FIELDS.get(role)
    if fields is None:
        return None
    return _build_model_from_field_subset(fields, name=f"{role.title().replace('_', '')}Output")


def agent_output_model(agent: Agent) -> type[BaseModel] | None:
    """Resolve the output validator for ``agent``.

    Convenience wrapper around :func:`resolve_output_validator` for
    callers holding a Pydantic ``Agent``. The model is named after the
    agent + version so cached classes don't collide across agents that
    happen to declare the same field subset.
    """
    if agent.output_schema:
        return _build_model_from_field_subset(
            tuple(agent.output_schema),
            name=f"Agent_{agent.id}_v{agent.version}_Output",
        )
    return role_output_model(agent.role)


def resolve_output_validator(
    role: str,
    output_schema: list[str] | tuple[str, ...],
) -> type[BaseModel] | None:
    """Resolve a validator from raw ``role`` + ``output_schema`` scalars.

    Same resolution order as :func:`agent_output_model`, but takes the
    contract as two arguments so engine callers holding an ORM split
    (``AgentORM`` + ``AgentVersionORM``) don't need to construct a
    Pydantic ``Agent`` just to parse output.

    1. ``output_schema`` non-empty → per-agent validator (cached on the
       sorted tuple of fields so two agents with the same subset reuse
       one compiled class).
    2. Otherwise falls back to ``ROLE_FIELDS[role]``.
    3. Returns ``None`` for custom roles with no declared schema.
    """
    if output_schema:
        fields = tuple(output_schema)
        cache_name = f"FieldSubsetOutput[{','.join(sorted(fields))}]"
        return _build_model_from_field_subset(fields, name=cache_name)
    return role_output_model(role)


@cache
def _build_model_from_field_subset(
    fields: tuple[str, ...] | frozenset[str],
    *,
    name: str,
) -> type[BaseModel]:
    """Compile a Pydantic model exposing exactly ``fields`` from PipelineState.

    The resulting model:
    - has only the fields named in ``fields``,
    - copies their type annotations from ``PipelineState`` (single source
      of truth for types),
    - makes every field optional (a node may write a subset),
    - rejects unknown keys via ``extra="forbid"``.

    Cached on ``(fields, name)`` — repeated lookups for the same agent /
    role return the same compiled class so equality checks behave.
    """
    pipeline_fields = PipelineState.model_fields
    model_fields: dict[str, Any] = {}
    for field_name in fields:
        info = pipeline_fields[field_name]
        annotation = info.annotation
        if annotation is None:
            # Shouldn't happen — every PipelineState field is annotated —
            # but guard so create_model gets a usable type.
            annotation = Any
        # All output fields are optional — nodes may write a subset.
        model_fields[field_name] = (annotation | None, None)

    return create_model(
        name,
        __config__=ConfigDict(extra="forbid"),
        **model_fields,
    )

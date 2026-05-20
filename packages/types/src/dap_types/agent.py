from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dap_types.state import PipelineState

AgentRole = str  # "task_selector" | "test_author" | "implementer" | "verifier" | ... | custom
AgentSchemaFieldKind = Literal["pipeline_state", "extension"]

_EXTENSION_FIELD_RE = re.compile(r"^[_A-Za-z][_A-Za-z0-9]*$")
_EXTENSION_PREFIX = "extensions."


def coerce_legacy_field_list(value: Any) -> Any:
    """Normalise legacy dict-shaped schemas to ``list[str]``.

    Older Agent rows persisted ``input_schema`` / ``output_schema`` as
    ``dict[str, Any]`` (placeholder JSON-Schema-ish blobs). v0.5 narrows
    the contract to a list of PipelineState field names. We coerce
    ``dict``/``None`` to ``[]`` here so existing rows survive a read-back
    without a database migration. The shim is removed in v0.6.

    Anything else (lists, primitives, …) is returned untouched so
    Pydantic's ``list[str]`` annotation does its own type checking and
    rejects non-string elements with the standard validation error
    instead of a silent ``str(item)`` coercion.
    """
    if value is None or isinstance(value, dict):
        # ``{}`` and any non-empty placeholder dict (legacy JSON-Schema-ish
        # blob) collapse to ``[]`` — there's no real contract to recover.
        return []
    return value


def classify_schema_field_ref(field: str) -> AgentSchemaFieldKind:
    """Classify an agent schema reference as PipelineState or extension state.

    ``input_schema`` / ``output_schema`` remain ``list[str]`` on the wire for
    OpenAPI compatibility. A value that exactly matches ``PipelineState`` is a
    top-level state field. Any other valid identifier is pipeline-defined extra
    state stored under ``PipelineState.extensions``. ``extensions.foo`` is also
    accepted as an explicit extension reference; persisted Cortex/DAP v2 bundles
    that used bare extension names remain readable.
    """
    known = set(PipelineState.model_fields.keys())
    if field in known:
        return "pipeline_state"

    extension_name = field.removeprefix(_EXTENSION_PREFIX)
    if (
        not extension_name
        or "." in extension_name
        or not _EXTENSION_FIELD_RE.fullmatch(extension_name)
    ):
        known_fields = ", ".join(sorted(known))
        msg = (
            "input_schema/output_schema reference unsupported field "
            f"'{field}'. Use a PipelineState field ({known_fields}) or an "
            "extension field identifier such as 'issue_number' or "
            "'extensions.issue_number'."
        )
        raise ValueError(msg)

    return "extension"


def validate_field_list(fields: list[str]) -> list[str]:
    """Validate agent schema references and reject duplicate entries."""
    if not fields:
        return fields
    duplicates = sorted({f for f in fields if fields.count(f) > 1})
    if duplicates:
        msg = f"input_schema/output_schema contain duplicate field(s): {', '.join(duplicates)}"
        raise ValueError(msg)
    for field in fields:
        classify_schema_field_ref(field)
    return fields


class Agent(BaseModel):
    """Wersjonowana definicja agenta — runtime + config + prompt template."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: AgentRole
    version: int

    runtime_id: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)

    prompt_template: str

    input_schema: list[str] = Field(default_factory=list)
    output_schema: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    budget_limit_usd: float | None = None
    timeout_ms: int = 60_000

    created_at: datetime
    updated_at: datetime
    is_active: bool = True

    # Populated only on list responses; None on detail / create / update /
    # version endpoints, which don't compute usage. Treat None as "unknown",
    # not "zero".
    used_in_pipelines: int | None = None

    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def _coerce_legacy_dict_schema(cls, value: Any) -> Any:
        return coerce_legacy_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return validate_field_list(value)

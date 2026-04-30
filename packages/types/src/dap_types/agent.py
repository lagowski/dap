from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dap_types.state import PipelineState

AgentRole = str  # "task_selector" | "test_author" | "implementer" | "verifier" | ... | custom


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


def validate_field_list(fields: list[str]) -> list[str]:
    """Reject any name that doesn't exist in ``PipelineState.model_fields``."""
    if not fields:
        return fields
    known = set(PipelineState.model_fields.keys())
    unknown = sorted({f for f in fields if f not in known})
    if unknown:
        msg = (
            "input_schema/output_schema reference unknown PipelineState "
            f"field(s): {', '.join(unknown)}. Known fields: {', '.join(sorted(known))}"
        )
        raise ValueError(msg)
    duplicates = sorted({f for f in fields if fields.count(f) > 1})
    if duplicates:
        msg = f"input_schema/output_schema contain duplicate field(s): {', '.join(duplicates)}"
        raise ValueError(msg)
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

    used_in_pipelines: int = 0

    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def _coerce_legacy_dict_schema(cls, value: Any) -> Any:
        return coerce_legacy_field_list(value)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _check_known_fields(cls, value: list[str]) -> list[str]:
        return validate_field_list(value)

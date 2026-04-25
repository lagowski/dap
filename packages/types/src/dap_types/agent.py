from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

AgentRole = str  # "task_selector" | "test_author" | "implementer" | "verifier" | ... | custom


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

    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)

    budget_limit_usd: float | None = None
    timeout_ms: int = 60_000

    created_at: datetime
    updated_at: datetime
    is_active: bool = True

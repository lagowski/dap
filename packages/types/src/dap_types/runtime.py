from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

RuntimeKind = Literal["cli", "api", "shell", "http"]


class RuntimeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_fields: dict[str, Any] = Field(default_factory=dict)


class RuntimeTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    prompt_xml: str
    working_directory: str
    allowed_files: list[str] | None = None
    allowed_tools: list[str] | None = None
    timeout_ms: int | None = 60_000
    budget_usd: float | None = None
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    context: RuntimeContext = Field(default_factory=RuntimeContext)
    # Project-scoped env overlay (#65). Subprocess-spawning adapters
    # layer these onto the inherited engine env, *before* per-agent
    # ``runtime_config.env`` is applied — so agent overrides still win,
    # project env still beats engine env, engine env is the base.
    # Empty dict for ad-hoc runs and api-call style adapters that
    # don't spawn subprocesses.
    project_env_vars: dict[str, str] = Field(default_factory=dict)


class RuntimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    output: str
    structured: dict[str, Any] | None = None
    files_changed: list[str] = Field(default_factory=list)
    tokens_used: int | None = None
    cost_usd: float | None = None
    duration_ms: int = 0
    errors: list[str] = Field(default_factory=list)
    pause_requested: bool = False


class HealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    version: str | None = None
    missing: list[str] | None = None


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Protocol dla runtime adapterów — implementujemy w packages/runtimes."""

    id: str
    display_name: str
    kind: RuntimeKind

    async def healthcheck(self) -> HealthStatus: ...

    async def execute(self, task: RuntimeTask) -> RuntimeResult: ...

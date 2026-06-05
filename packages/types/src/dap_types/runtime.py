from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

RuntimeKind = Literal["cli", "api", "shell", "http"]

# A synchronous sink invoked with each incremental stdout chunk an adapter
# produces while a node executes (#662, Phase 3b-2a). The adapter calls it
# best-effort: a raising sink is logged and swallowed, never failing the run,
# and the callback never affects the returned ``RuntimeResult.output``. Only
# subprocess-spawning CLI adapters stream through it today; the engine wires a
# chunk-persisting callback in Phase 3b-2b.
OutputCallback = Callable[[str], None]


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
    # Instance-scoped env overlay (#388). Shared key/value store
    # configured by the operator via ``/settings/admin/env-vars``.
    # Decrypted at run start and threaded through every task. Layered
    # between engine env (base) and project env vars, so project
    # overrides still win on conflict, but every project picks up
    # operator-set defaults (e.g. GitHub tokens) automatically without
    # per-project configuration.
    instance_env_vars: dict[str, str] = Field(default_factory=dict)
    # Project-scoped env overlay (#65). Subprocess-spawning adapters
    # layer these onto the inherited engine env, *after* the instance
    # overlay and *before* per-agent ``runtime_config.env`` is applied
    # — so agent overrides still win, project env beats instance env,
    # instance env beats engine env. Empty dict for ad-hoc runs and
    # api-call style adapters that don't spawn subprocesses.
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

    async def execute(
        self,
        task: RuntimeTask,
        on_output: OutputCallback | None = None,
    ) -> RuntimeResult: ...

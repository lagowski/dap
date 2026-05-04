from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dap_types.state import FinalStatus, PipelineState

NodeStatus = Literal["pending", "running", "success", "failed", "skipped"]
TriggerSource = Literal["dashboard", "cli", "api"]


class Run(BaseModel):
    """Instancja wykonania pipeline'u — immutable FK do wersji pipeline'u."""

    model_config = ConfigDict(extra="forbid")

    id: str

    # Owning project (v0.6). ``None`` for ad-hoc / legacy runs that
    # were triggered directly via ``POST /runs`` without a project
    # association. Populated automatically by
    # ``POST /projects/{id}/run/{kind}`` (#66).
    project_id: str | None = None

    pipeline_id: str
    pipeline_version: int

    trigger_source: TriggerSource
    initial_state: PipelineState

    current_node: str | None = None
    node_statuses: dict[str, NodeStatus] = Field(default_factory=dict)

    final_status: FinalStatus = "running"
    started_at: datetime
    ended_at: datetime | None = None

    tokens_used: int = 0
    cost_usd: float = 0.0


class NodeExecutionLog(BaseModel):
    """Log wykonania pojedynczego node'a w runie."""

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    node_id: str
    agent_id: str
    runtime_id: str

    started_at: datetime
    ended_at: datetime | None = None

    prompt_xml: str
    stdout: str = ""
    stderr: str = ""
    output_json: dict[str, Any] | None = None

    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0

    status: NodeStatus
    error_message: str | None = None
    extra_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Adapter-supplied audit metadata, populated from ``result.structured['audit']`` "
            "when a python-func node returns ``__audit`` in its output dict. "
            "Shape is intentionally open — the engine does not validate or interpret keys. "
            "Common keys written by python-func adapters: "
            "``github_user`` (str), ``section`` (str), "
            "``content_before`` / ``content_after`` (str, truncated diffs), "
            "``operation`` (str, e.g. 'create_branch' / 'push' / 'create_pr'), "
            "``commit_sha`` (str), ``tokens_used`` (int), ``cost_usd`` (float). "
            "Consumers (API, dashboard) should treat every key as optional. "
            "``None`` for CLI/LLM adapter nodes that do not produce audit data."
        ),
    )

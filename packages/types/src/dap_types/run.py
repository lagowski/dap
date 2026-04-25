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

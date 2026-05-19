from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BatchRunStatus = Literal["running", "success", "failed", "aborted"]


class BatchRunResult(BaseModel):
    """Outcome for one issue within a batch run."""

    model_config = ConfigDict(extra="forbid")

    issue_number: int
    run_id: str | None = None
    status: str  # mirrors Run.final_status; "skipped" when stop_on_failure cut the batch short


class BatchRun(BaseModel):
    """A batch of sequential pipeline executions, one per issue number."""

    model_config = ConfigDict(extra="forbid")

    id: str
    pipeline_id: str
    pipeline_version: int | None = None
    project_id: str | None = None

    issue_numbers: list[int]
    stop_on_failure: bool = True
    auto_approve: bool = False

    current_index: int = 0
    status: BatchRunStatus = "running"
    results: list[BatchRunResult] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime

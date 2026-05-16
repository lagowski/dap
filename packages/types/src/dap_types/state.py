from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VerificationStatus = Literal["pending", "approved", "rejected"]
FinalStatus = Literal["running", "success", "failed", "aborted", "paused"]


class PipelineState(BaseModel):
    """Jawny, typowany stan pipeline'u — single source of truth."""

    model_config = ConfigDict(extra="forbid")

    # ---- METADATA / CONTROL ----
    run_id: str
    repo: str
    branch: str
    commit_sha: str | None = None
    description: str | None = None

    # ---- TASK SELECTION ----
    available_issues: list[dict[str, Any]] = Field(default_factory=list)
    selected_issue_ids: list[int] = Field(default_factory=list)

    # ---- TEST GENERATION ----
    tests_generated: bool = False
    test_files: list[str] = Field(default_factory=list)
    test_generation_errors: list[str] = Field(default_factory=list)

    # ---- EXECUTION LOOP ----
    max_attempts: int = 3
    attempt: int = 0
    tests_passed: bool = False
    last_test_output: str = ""

    # ---- IMPLEMENTATION ----
    modified_files: list[str] = Field(default_factory=list)
    implementation_notes: str | None = None

    # ---- VERIFICATION ----
    verification_status: VerificationStatus = "pending"
    verification_reason: str | None = None

    # ---- FINAL OUTPUT ----
    final_status: FinalStatus = "running"

    # ---- EXTENSIONS ----
    extensions: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-pipeline extra state. Keys are pipeline-defined. "
            "Values must be JSON-serializable. "
            "Reserved keys (engine-defined): "
            "``auto_approve`` (bool, #389) — when True, the runner "
            "skips every ``interrupt_before`` approval gate so the run "
            "executes end-to-end without pausing. Operator-only flag, "
            "intentionally named to mirror Claude Code's "
            "``--dangerously-skip-permissions``."
        ),
    )


class StateSnapshot(BaseModel):
    """Snapshot stanu po wykonaniu konkretnego node'a."""

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    node_id: str
    timestamp: datetime
    state: PipelineState

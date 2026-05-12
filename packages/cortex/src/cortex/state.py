"""Cortex workflow state schema.

Defines the TypedDict that flows through every node in the graph.
Each node reads what it needs and writes its outputs.
"""

from __future__ import annotations

from typing import Annotated, TypedDict


def _files_changed_reducer(a: list[str] | None, b: list[str] | None) -> list[str]:
    """Reducer for state.files_changed.

    - ``None`` from any source is treated as a reset signal: returns ``[]``.
    - Normal lists are accumulated via concatenation (``operator.add``).
    This lets ``retry_prepare`` emit ``None`` to clear the stale list before
    the next coder round, while execution agents accumulate across the chain.
    """
    if b is None:
        return []
    return (a or []) + b


class CortexState(TypedDict):
    """State that flows through the Cortex pipeline.

    Each field is optional (except issue_url) because different nodes
    populate different fields as the workflow progresses.
    """

    # --- Input (set at workflow start) ---
    issue_url: str
    repo: str
    issue_number: int
    issue_title: str
    issue_body: str

    # --- Triage output ---
    classification: str  # feature | bug | refactor | docs | chore
    complexity: str  # trivial | small | medium | large | epic
    target_files: list[str]

    # --- Phase 0 complexity gate (#104) ---
    # complexity_score (1-10) is set by the complexity_gate node before
    # mockup runs. complexity_blocked is True when the score is at or above
    # complexity_threshold AND the operator didn't pass --force. force_run
    # is the operator's explicit override; carried in state so the gate can
    # see it.
    complexity_score: int
    complexity_blocked: bool
    complexity_threshold: int
    force_run: bool

    # --- Issue comments (fetched once, available to all nodes) ---
    issue_comments: list[dict[str, str]]  # [{author, body, created_at}]

    # --- Enrichment output ---
    # Each assignment has {task, agent, priority} (str) plus optional per-task
    # budget fields max_turns/timeout_sec (int) added by the dispatcher (#49).
    task_assignments: list[dict[str, str | int]]
    issue_ready: bool  # set by finalize node after review

    # --- Decompose output ---
    sub_tasks: list[dict[str, str]]  # [{title, description, deps}]
    implementation_plan: str

    # --- Plan output ---
    file_changes: list[dict[str, str]]  # [{path, action, description}]
    tests_to_write: list[dict[str, str]]  # [{path, covers}]

    # --- Implement output ---
    branch_name: str  # last execution agent's branch (goes into the PR)
    coder_branch_name: str  # coder's branch specifically — stable across retries (#161)
    commits: list[dict[str, str]]  # [{sha, message}]
    files_changed: Annotated[list[str], _files_changed_reducer]

    # --- Test output ---
    tests_passed: bool
    test_output: str
    # Structured pytest results (#89) — deterministic gate driven by tester.
    # Schema: {passed: int, failed: int, errors: int, failed_tests: list[str]}.
    tester_result: dict
    # Bounded retry budget when tester reports failures (#89). When tester
    # fails and retries remain, the graph routes back to coder with the
    # failed tests prepended to coder's prompt. After max_retries attempts,
    # the loop exits and Phase 3 continues to its human gate.
    pr_merger_retry_count: int
    max_retries: int

    # --- Code review output (Phase 2.5, #152) ---
    review_findings: list[str]  # populated by code_reviewer; empty = approved
    # Findings the operator has explicitly acknowledged by approving a gate.
    # code_reviewer and reviewer skip re-blocking on any finding in this list
    # so that a human gate approval acts as a permanent override (#244).
    acknowledged_findings: list[str]

    # --- Review output ---
    pr_number: int
    pr_url: str
    review_approved: bool

    # --- Deploy output ---
    merged: bool
    merge_sha: str

    # --- Control flow ---
    current_phase: str
    human_feedback: str  # feedback from human at approval gates
    retry_count: int
    error: str  # last error message if any

    # --- Audit trail ---
    run_id: str  # cortex_runs UUID, set at pipeline start
    decisions: list[dict[str, str]]  # [{node, action, reasoning, backend, model, timestamp}]

    # --- Plugin: docker (#178) ---
    docker_image_tag: str  # set by docker_pusher
    docker_build_log: str  # set by docker_validator

    # --- Project scoping (#213) ---
    project_id: str  # cortex_projects UUID for multi-project isolation

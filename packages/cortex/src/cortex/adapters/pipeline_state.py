"""CortexState ↔ DAP PipelineState compatibility adapter.

Bridges the gap between:
- ``CortexState`` (TypedDict, Cortex-specific fields)
- DAP's ``PipelineState`` (Pydantic, generic fields + ``extensions`` dict)

Nodes that use this adapter work identically whether called from:
- ``cortex/graph.py`` (native Cortex run) — state is already CortexState-shaped
- DAP's ``python-func`` runtime — state arrives as a PipelineState-shaped dict

Usage in a node::

    from cortex.adapters.pipeline_state import from_pipeline_state, to_pipeline_state

    async def run(state: dict, config: dict) -> dict:
        cortex_state = from_pipeline_state(state)
        # ... existing logic using cortex_state ...
        result = {**cortex_state, "some_output": value}
        return to_pipeline_state(result)

Field mapping
-------------
Direct (same key, same meaning):
    repo, commit_sha, tests_passed, last_test_output, final_status

Renamed:
    branch_name  →  branch   (and back)
    files_changed → modified_files  (and back)

Into extensions (Cortex-specific, no DAP equivalent):
    issue_url, issue_number, issue_title, issue_body,
    issue_comments, run_id, thread_id, current_phase,
    approved, rejection_feedback, human_feedback,
    task_assignments, issue_ready, sub_tasks, implementation_plan,
    file_changes, tests_to_write, coder_branch_name, commits,
    tester_result, pr_merger_retry_count, max_retries,
    review_findings, pr_number, pr_url, review_approved,
    merged, merge_sha, classification, complexity, target_files,
    complexity_score, complexity_blocked, complexity_threshold,
    force_run, retry_count, error, decisions,
    docker_image_tag, docker_build_log, project_id,
    github_user_per_agent,
"""

from __future__ import annotations

from typing import Any

# Fields that map 1-to-1 between CortexState and PipelineState (same key name).
_DIRECT_FIELDS: tuple[str, ...] = (
    "repo",
    "commit_sha",
    "tests_passed",
    "last_test_output",
    "final_status",
)

# Fields whose key name differs between the two state shapes.
# Format: (cortex_key, pipeline_key)
_RENAMED_FIELDS: tuple[tuple[str, str], ...] = (
    ("branch_name", "branch"),
    ("files_changed", "modified_files"),
)

# CortexState fields that have no direct PipelineState equivalent.
# They are packed into PipelineState["extensions"] during to_pipeline_state()
# and unpacked from there during from_pipeline_state().
_EXTENSION_FIELDS: tuple[str, ...] = (
    "issue_url",
    "issue_number",
    "issue_title",
    "issue_body",
    "issue_comments",
    "run_id",
    "thread_id",
    "current_phase",
    "approved",
    "rejection_feedback",
    "human_feedback",
    "task_assignments",
    "issue_ready",
    "sub_tasks",
    "implementation_plan",
    "file_changes",
    "tests_to_write",
    "coder_branch_name",
    "commits",
    "tester_result",
    "pr_merger_retry_count",
    "max_retries",
    "review_findings",
    "review_attempts",
    "review_output",
    "pr_number",
    "pr_url",
    "review_approved",
    "merged",
    "merge_sha",
    "classification",
    "complexity",
    "target_files",
    "complexity_score",
    "complexity_blocked",
    "complexity_threshold",
    "force_run",
    "retry_count",
    "error",
    "decisions",
    "docker_image_tag",
    "docker_build_log",
    "project_id",
    "github_user_per_agent",
    # __audit carries per-node token/cost metadata — must survive round-trip.
    "__audit",
    # _full_response_content carries full LLM response for finalize contradiction check.
    "_full_response_content",
)


def to_pipeline_state(cortex_state: dict[str, Any]) -> dict[str, Any]:
    """Convert a CortexState dict to a DAP PipelineState-compatible dict.

    Absent keys are omitted (not set to ``None``) so the caller can merge the
    result into an existing PipelineState without clobbering unrelated fields.

    Args:
        cortex_state: A dict with CortexState-shaped keys (may be partial).

    Returns:
        A dict with PipelineState-shaped top-level keys plus an ``extensions``
        sub-dict carrying Cortex-specific fields.
    """
    pipeline: dict[str, Any] = {}

    # Direct 1-to-1 fields
    for key in _DIRECT_FIELDS:
        if key in cortex_state:
            pipeline[key] = cortex_state[key]

    # Renamed fields: cortex key → pipeline key
    for cortex_key, pipeline_key in _RENAMED_FIELDS:
        if cortex_key in cortex_state:
            pipeline[pipeline_key] = cortex_state[cortex_key]

    # Extension fields: pack into extensions sub-dict
    extensions: dict[str, Any] = {}
    for key in _EXTENSION_FIELDS:
        if key in cortex_state:
            extensions[key] = cortex_state[key]

    # Merge with any existing extensions already in the incoming state so we
    # don't clobber DAP-side extension keys Cortex doesn't know about.
    existing_extensions = cortex_state.get("extensions", {})
    merged_extensions = {**existing_extensions, **extensions} if existing_extensions else extensions

    if merged_extensions:
        pipeline["extensions"] = merged_extensions

    return pipeline


def from_pipeline_state(pipeline_state: dict[str, Any]) -> dict[str, Any]:
    """Convert a DAP PipelineState dict back to a CortexState-shaped dict.

    Produces a plain ``dict`` (not a ``CortexState`` instance) so it is safe
    to pass into LangGraph nodes which accept ``dict`` inputs.

    When ``pipeline_state`` is *already* CortexState-shaped (native Cortex run
    where ``branch_name`` is present instead of ``branch``), the function
    returns it unchanged so nodes work transparently in both runtimes.

    Args:
        pipeline_state: A dict with either PipelineState-shaped or
            CortexState-shaped keys.

    Returns:
        A dict with CortexState-shaped keys.
    """
    # Fast path: already CortexState-shaped (native Cortex run).
    # Heuristic: if none of the PipelineState-specific keys are present
    # (branch, modified_files, extensions with known cortex fields),
    # the state hasn't been converted — pass through as-is.
    _has_pipeline_keys = (
        "branch" in pipeline_state
        or "modified_files" in pipeline_state
        or "extensions" in pipeline_state  # any dict with extensions key is PipelineState
        or any(
            k in pipeline_state.get("extensions", {})
            for k in _EXTENSION_FIELDS[:3]  # check a few sentinel extension keys
        )
    )
    if not _has_pipeline_keys:
        return dict(pipeline_state)

    cortex: dict[str, Any] = {}

    # Direct 1-to-1 fields
    for key in _DIRECT_FIELDS:
        if key in pipeline_state:
            cortex[key] = pipeline_state[key]

    # Renamed fields: pipeline key → cortex key
    for cortex_key, pipeline_key in _RENAMED_FIELDS:
        if pipeline_key in pipeline_state:
            cortex[cortex_key] = pipeline_state[pipeline_key]

    # Extension fields: unpack from extensions sub-dict
    extensions = pipeline_state.get("extensions", {})
    for key in _EXTENSION_FIELDS:
        if key in extensions:
            cortex[key] = extensions[key]

    # Fallback: when CLI sets repo only in extensions (not top-level PipelineState),
    # promote it so nodes find cortex_state["repo"] and resolve the workspace path.
    if not cortex.get("repo") and extensions.get("repo"):
        cortex["repo"] = extensions["repo"]

    return cortex


# Aliases matching the issue #225 spec naming convention.
cortex_to_dap = to_pipeline_state
dap_to_cortex = from_pipeline_state


def preserve_extensions(delta: dict, original_extensions: dict) -> dict:
    """Prevent LangGraph dict-replace from clobbering accumulated extensions.

    When a node returns ``cortex_to_dap(result)``, LangGraph replaces the
    entire ``extensions`` dict with the node's partial output, losing fields
    written by earlier nodes (issue_number, task_assignments, etc.).

    Call this on the delta before returning: it merges *original_extensions*
    (captured from the input state before conversion) with the new node output,
    giving the new output priority while preserving any pre-existing fields.

    Usage in a node::

        original_extensions = dict(state.get("extensions") or {})
        state = dap_to_cortex(state)
        ...
        return preserve_extensions(cortex_to_dap(result), original_extensions)

    See cortex-project#303 (code_reviewer), cortex-project#327 (Phase 1 nodes).
    """
    merged = {**original_extensions, **delta.get("extensions", {})}
    delta["extensions"] = merged
    return delta

"""Finalize node — review the fully enriched issue for completeness.

Validates all sections are filled, test criteria are meaningful,
and task assignments match agent capabilities. Sets issue_ready
flag for the human approval gate.

After the LLM review, cross-checks Target Files from the Technical
Specification against the workspace on disk and flags contradictions
(missing paths, stale line references). Issue #96.

Backend: claude_cli. Token: ISSUES.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cortex.config.settings import load_settings
from cortex.init.profile import repo_clone_path
from cortex.nodes.base import _llm_call
from cortex.templates import get_issue_section, parse_target_files
from cortex.tools.github import read_issue

__all__ = ["run"]


def validate_target_paths(
    paths: list[tuple[str, int | None]],
    workspace_root: Path,
) -> list[dict]:
    """Check Target File paths against the workspace on disk.

    Returns a list of contradiction dicts with keys: path, line, reason.
    """
    contradictions: list[dict] = []
    for file_path, line_num in paths:
        full = workspace_root / file_path
        if not full.exists():
            contradictions.append({
                "path": file_path,
                "line": line_num,
                "reason": "file does not exist on disk",
            })
        elif line_num is not None:
            try:
                line_count = len(full.read_text().splitlines())
            except OSError:
                continue
            if line_num > line_count:
                contradictions.append({
                    "path": file_path,
                    "line": line_num,
                    "reason": (
                        f"line {line_num} exceeds file length ({line_count} lines)"
                    ),
                })
    return contradictions


def _format_contradictions(contradictions: list[dict]) -> str:
    """Build a markdown ### Contradictions block."""
    lines = ["### Contradictions"]
    for c in contradictions:
        ref = f"`{c['path']}:{c['line']}`" if c["line"] else f"`{c['path']}`"
        lines.append(f"- {ref} — {c['reason']}")
    return "\n".join(lines)


async def run(state: dict, config: dict) -> dict:
    """Review enriched issue and set readiness flag.

    Pure: calls _llm_call only — no GitHub writes (update_issue_body),
    no audit DB calls (log_decision). Side effects handled by dap_steps.
    """
    from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions, preserve_extensions
    original_extensions = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)
    section_content, audit = _llm_call(state, "finalize", "Review Notes")
    from cortex.dap_steps.enrichment_write import run_side_effects
    side = await run_side_effects(state, audit, "finalize", "Review Notes")
    reasoning = section_content[:200].lower()
    full_response = section_content

    # Cross-check Target Files against workspace (issue #96).
    # Appends to full_response for downstream consumers; does NOT write to
    # GitHub (that write moves to dap_steps/enrichment_write.py).
    workspace = repo_clone_path(state["repo"])
    if workspace.exists():
        settings = load_settings()
        token = settings.get_github_token("issues")
        issue_data = read_issue.invoke({
            "repo": state["repo"],
            "issue_number": state["issue_number"],
            "token": token,
        })
        issue_body = issue_data.get("body", "")
        spec_section = get_issue_section(issue_body, "Technical Specification")
        target_files = parse_target_files(spec_section)

        if target_files:
            contradictions = validate_target_paths(target_files, workspace)
            if contradictions:
                block = _format_contradictions(contradictions)
                full_response = full_response + "\n\n" + block

    result: dict = {
        **side,
        "__audit": audit,
        "_full_response_content": full_response,
        "issue_ready": "ready" in reasoning and "needs_work" not in reasoning,
        "decisions": state.get("decisions", []) + [{
            "node": "finalize",
            "action": "enriched Review Notes",
            "reasoning": section_content[:200],
            "backend": audit["backend"],
            "model": audit["model"],
            "tokens": f"{audit['input_tokens']}in/{audit['output_tokens']}out",
            "timestamp": datetime.now(UTC).isoformat(),
        }],
    }

    # Clear consumed operator feedback (#108). Phase 1 agents have just had
    # their chance to address it; leaving it set would re-inject it into
    # Phase 2 prompts and any subsequent re-run.
    if state.get("human_feedback"):
        result["human_feedback"] = ""

    return preserve_extensions(cortex_to_dap(result), original_extensions)

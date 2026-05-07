"""Mockup node — structure raw issues into the standard template.

First enrichment agent. Reads the raw GitHub issue, injects the
canonical template, and asks the LLM to fill the Description section.
Other sections stay as-is for downstream agents.

Backend: ollama/gemma4. Token: ISSUES.
"""

from __future__ import annotations

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.nodes.base import _llm_call
from cortex.templates import load_template, parse_issue_sections

__all__ = ["run"]


async def run(state: dict, config: dict) -> dict:
    """Fill the issue template from a raw GitHub issue.

    Pure: calls _llm_call only — no GitHub writes, no audit DB calls.
    Side effects (update_issue_body, log_decision, etc.) are handled by
    cortex/dap_steps/enrichment_write.py in DAP mode, or by the backwards-compat
    enrichment_step() bridge in non-DAP cortex run.

    Accepts both CortexState-shaped and PipelineState-shaped input (adapter).
    """
    original_extensions = dict(state.get("extensions") or {})
    cortex_state = dap_to_cortex(state)
    cortex_state = _ensure_template_in_state(cortex_state)
    _section_content, audit = _llm_call(cortex_state, "mockup", "Description")
    from cortex.dap_steps.enrichment_write import run_side_effects

    side = await run_side_effects(cortex_state, audit, "mockup", "Description")
    return preserve_extensions(cortex_to_dap({**side, "__audit": audit}), original_extensions)


def _ensure_template_in_state(state: dict) -> dict:
    """Pre-fill the issue body with the template if no sections exist yet.

    Mutates a shallow copy: if the GitHub issue has no recognisable sections
    (raw 2-line bug report), prepend the template skeleton so subsequent
    section parsing works. If the issue already has sections, leave it alone.

    The actual ``update_issue_body`` happens inside enrichment_step — this
    helper just makes sure the template is present in the state seed used by
    that call's prompt building.
    """
    body = state.get("issue_body", "")
    if parse_issue_sections(body):
        # Issue already has at least one ## header — assume template exists
        return state

    template = load_template()
    new_state = dict(state)
    # Preserve the raw issue text under a "Raw Issue" preamble so the mockup
    # agent has the original to draw from.
    if body.strip():
        new_state["issue_body"] = f"<!-- Raw issue from human:\n{body}\n-->\n\n{template}"
    else:
        new_state["issue_body"] = template
    return new_state

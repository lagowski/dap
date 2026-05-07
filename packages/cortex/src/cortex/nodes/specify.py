"""Specify node — add technical specification to the issue.

Reads the project codebase and fills the Technical Specification
section with target files, dependencies, architecture impact,
and build/test/deploy commands.

Backend: claude_cli. Token: READ.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.dap_steps.enrichment_write import run_side_effects
from cortex.nodes.base import _llm_call

__all__ = ["run"]


async def run(state: dict, config: dict) -> dict:
    """Fill the Technical Specification section.

    Accepts both CortexState-shaped and PipelineState-shaped input (adapter).
    """
    original_extensions = dict(state.get("extensions") or {})
    cortex_state = dap_to_cortex(state)
    section_content, audit = _llm_call(cortex_state, "specify", "Technical Specification")
    side = await run_side_effects(cortex_state, audit, "specify", "Technical Specification")
    return preserve_extensions(
        cortex_to_dap(
            {
                **side,
                "__audit": audit,
                "decisions": [
                    *cortex_state.get("decisions", []),
                    {
                        "node": "specify",
                        "action": "enriched Technical Specification"
                        if "error" not in side
                        else "FAILED to enrich Technical Specification",
                        "reasoning": section_content[:200],
                        "backend": audit["backend"],
                        "model": audit["model"],
                        "tokens": f"{audit['input_tokens']}in/{audit['output_tokens']}out",
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                ],
            }
        ),
        original_extensions,
    )

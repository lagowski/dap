"""CI/CD node — define test criteria using Red-Green-Refactor.

Reads the Technical Specification and creates specific, testable
criteria: RED tests that must fail, GREEN tests that must pass,
and REFACTOR tasks for cleanup.

Backend: ollama/gemma4. Token: ISSUES.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cortex.dap_steps.enrichment_write import run_side_effects
from cortex.nodes.base import _llm_call

__all__ = ["run"]


async def run(state: dict, config: dict) -> dict:
    """Fill the Test Criteria section."""
    from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions, preserve_extensions
    original_extensions = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)
    section_content, audit = _llm_call(state, "cicd", "Test Criteria")
    side = await run_side_effects(state, audit, "cicd", "Test Criteria")
    return preserve_extensions(cortex_to_dap({
        **side,
        "__audit": audit,
        "decisions": state.get("decisions", []) + [{
            "node": "cicd",
            "action": "enriched Test Criteria" if "error" not in side else "FAILED to enrich Test Criteria",
            "reasoning": section_content[:200],
            "backend": audit["backend"],
            "model": audit["model"],
            "tokens": f"{audit['input_tokens']}in/{audit['output_tokens']}out",
            "timestamp": datetime.now(UTC).isoformat(),
        }],
    }), original_extensions)

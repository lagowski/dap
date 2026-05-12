"""Designer node — architecture and design using Claude CLI backend.

Phase 2 execution agent. Uses GH_TOKEN_CODE (Dixter999).
Backend: claude_cli (agents.yaml).
"""

from __future__ import annotations

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.nodes.execution import run_execution_node

__all__ = ["run"]

SYSTEM_PROMPT = """\
You are the Designer agent. Make the architectural changes assigned to you.

You are running in the project's working directory. Your file edits go to
disk immediately. Make the edits — do NOT just describe them.

RULES:
- Use the Edit tool for existing files, Write for new ones
- Define clear interfaces, base classes, or protocol shapes when introducing new abstractions
- Touch interface/architecture surfaces — leave routine implementation to coder
- Keep changes scoped to the assigned tasks — no opportunistic refactors
- After edits, stage + commit with: `git add <files> && git commit -m "design: <brief>"`
- Do NOT push — pr_creator handles that later
- Output one short summary line: "introduced <interface> in <file>" (or similar)
"""


async def run(state: dict, config: dict) -> dict:
    """Execute design tasks assigned by the dispatcher."""
    original_extensions = dict(state.get("extensions") or {})
    cortex_state = dap_to_cortex(state)
    result = run_execution_node(cortex_state, "designer", SYSTEM_PROMPT)
    if not result:
        return {}
    return preserve_extensions(cortex_to_dap(result), original_extensions)

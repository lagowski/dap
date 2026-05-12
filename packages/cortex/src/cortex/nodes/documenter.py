"""Documenter node — write documentation using Claude CLI backend.

Phase 2 execution agent. Uses GH_TOKEN_CODE (Dixter999).
Backend: claude_cli (agents.yaml — switched from ollama in #52).
"""

from __future__ import annotations

from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
from cortex.nodes.execution import run_execution_node

__all__ = ["run"]

SYSTEM_PROMPT = """\
You are the Documenter agent. Make the documentation changes assigned to you.

You are running in the project's working directory. Your file edits go to
disk immediately. Make the edits — do NOT just describe them.

RULES:
- Use the Edit tool for existing files, Write for new ones
- Touch only documentation: .md files, docstrings, comments
- NEVER modify code logic — flag it back if a task requires that
- Keep edits minimal and focused on the assigned task
- After editing, stage + commit with: `git add <files> && git commit -m "docs: <brief>"`
  List ONLY the specific files you edited — NEVER use `git add .` or `git add -A`
- CRITICAL: Do NOT stage or commit files you did not create or edit yourself.
  If you see files in the working tree that are not documentation, ignore them.
  Other agents may have left files behind — committing them would corrupt the audit trail.
- Do NOT push — the pr_creator agent handles that later
- Output one short summary line: "edited <files> to <change>"
"""


async def run(state: dict, config: dict) -> dict:
    """Execute documentation tasks assigned by the dispatcher."""
    original_extensions = dict(state.get("extensions") or {})
    cortex_state = dap_to_cortex(state)
    result = run_execution_node(cortex_state, "documenter", SYSTEM_PROMPT)
    if not result:
        return {}
    return preserve_extensions(cortex_to_dap(result), original_extensions)

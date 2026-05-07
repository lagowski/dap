"""Coder node — write code changes.

Phase 2 execution agent. Uses GH_TOKEN_CODE (Dixter999).
"""

from __future__ import annotations

from cortex.adapters.pipeline_state import dap_to_cortex, cortex_to_dap, preserve_extensions
from cortex.nodes.execution import run_execution_node

__all__ = ["run"]

SYSTEM_PROMPT = """\
You are the Coder agent. Make the code changes assigned to you.

You are running in the project's working directory. Your file edits go to
disk immediately. Make the edits — do NOT just describe them.

RULES:
- Use the Edit tool for existing files, Write for new ones
- Follow existing project conventions you observe in nearby files
- Add or update tests alongside implementation in the same commit
- Keep changes scoped to the assigned tasks — no drive-by refactors
- After edits, stage + commit with: `git add <files> && git commit -m "feat/fix: <brief>"`
- Do NOT push — pr_creator handles that later
- Output one short summary line per task: "edited <files> to <change>"

RULES — git history (append-only):
- Operators may push intermediate commits between agent rounds (test
  patches, hotfixes). Rewriting history under those commits is silent
  data loss. Layer your commits on top of HEAD; never rewrite the branch.
- FORBIDDEN: `git push --force` / `git push -f` / `git push +<refspec>`
- FORBIDDEN: `git reset --hard <ref>` to anything other than current HEAD
- FORBIDDEN: `git rebase` and `git rebase -i`
- FORBIDDEN: `git commit --amend`
- FORBIDDEN: `git filter-branch` and `git filter-repo`
- If you need to undo a local change, use `git restore` / `git checkout --`
  on individual files, not history-rewriting commands.
"""


async def run(state: dict, config: dict) -> dict:
    """Execute coding tasks assigned by the dispatcher.

    Accepts both CortexState-shaped and PipelineState-shaped input (adapter).
    """
    original_extensions = dict(state.get("extensions") or {})
    cortex_state = dap_to_cortex(state)
    result = run_execution_node(cortex_state, "coder", SYSTEM_PROMPT)
    if not result:
        return {}
    return preserve_extensions(cortex_to_dap(result), original_extensions)

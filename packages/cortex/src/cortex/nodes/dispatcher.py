"""Dispatcher node — assign tasks to execution agents.

Analyzes the enriched issue and assigns each sub-task to a specific
execution agent based on task type and agent capabilities. Writes
an assignment table to the issue and outputs structured assignments
to state for Phase 2 routing.

Backend: ollama/gemma4. Token: ISSUES.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Literal

from cortex.config.settings import get_agent_backend_config

logger = logging.getLogger(__name__)

__all__ = ["run"]

# Agents that can legally appear in a dispatcher task table.
_EXECUTION_AGENTS = frozenset({"coder", "designer", "documenter", "specialist"})

# Match backtick-quoted file path tokens in a task description, e.g. `cortex/cli.py`
# or `tests/test_state.py`. The path must contain a "/" or end with a ".ext"
# extension — otherwise plain words like `coder` would count as a file.
_PATH_RE = re.compile(r"`([^`\s]+(?:/[^`\s]+|\.[A-Za-z0-9]+))`")


def _count_target_files(task_description: str) -> int:
    """Count distinct file path mentions in a task description.

    Looks for backtick-quoted paths that contain a "/" or end in a ".ext"
    extension. De-duplicates so a path mentioned twice still counts once.
    """
    return len(set(_PATH_RE.findall(task_description)))


# Match backtick-wrapped code spans. Order matters in the alternation:
# double-backtick (``...``) is tried first so spans like `` `int | None` ``
# (markdown's "render literal backticks" form) aren't truncated by the
# single-backtick branch.
#
# Double-backtick spans MUST allow inner backticks (that's the whole point of
# the form), so the inner class is `[^\n]` not `[^`]`. Non-greedy `+?` keeps
# adjacent double-backtick spans on one line from merging into a single match.
# Single-backtick spans forbid inner backticks (markdown spec) and stay on
# one line.
_CODE_SPAN_RE = re.compile(r"``[^\n]+?``|`[^`\n]+?`")


def _split_row_respecting_code_spans(line: str) -> list[str]:
    """Split a markdown table row on ``|``, preserving pipes inside code spans.

    A naive ``line.split("|")`` mangles rows where the LLM put a Python type
    union or regex pipe inside backticks — e.g. ``| ... `dict[str, str | int]`
    ... | coder | high |`` — because the inner ``|`` is treated as a column
    boundary. The result: the agent column receives a slice of the task and
    the row is silently lost at execution time (#107).

    Strategy: replace each backtick-wrapped span with an opaque placeholder
    that contains no ``|``, split on ``|``, then restore the spans into the
    cells they ended up in. Empty cells are dropped (matches prior behavior:
    leading/trailing pipes in markdown rows produce empty boundary cells).

    Returns the list of trimmed, non-empty cells in row order.
    """
    spans: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        # \x00 is invalid in markdown source, so this collides with nothing
        # the LLM would have written. Wrapping in markers keeps adjacent
        # placeholders disambiguated for the restore step.
        return f"\x00CSPAN{len(spans) - 1}\x00"

    masked = _CODE_SPAN_RE.sub(_stash, line)
    raw_cells = [c.strip() for c in masked.split("|") if c.strip()]

    def _restore(cell: str) -> str:
        # Replace placeholders back to original spans. Iterating each known
        # span by index is O(n*m) but n is tiny (a row's spans), so simpler
        # than running another regex sub.
        for i, original in enumerate(spans):
            cell = cell.replace(f"\x00CSPAN{i}\x00", original)
        return cell

    return [_restore(c) for c in raw_cells]


def _classify_budget_tier(file_count: int) -> Literal["small", "medium", "large"]:
    """Classify a task's budget tier from how many files it touches.

    1 file (or none) → small, 2-4 files → medium, 5+ files → large.
    Boundaries live in one place so they're unit-testable in isolation.
    """
    if file_count <= 1:
        return "small"
    if file_count <= 4:
        return "medium"
    return "large"


def _parse_assignments(
    content: str,
    config_lookup: Callable[[str], dict] = get_agent_backend_config,
) -> list[dict[str, str | int]]:
    """Parse markdown table rows into task assignment dicts.

    Expects rows like: ``| Task description | agent_name | priority |``.

    Each emitted assignment also carries ``max_turns`` and ``timeout_sec``
    derived from how many files the task description references (#49).
    The values are looked up in the agent's ``budget_tiers`` block. If the
    agent has no ``budget_tiers`` (or is unknown), the static top-level
    ``max_turns``/``timeout_sec`` from the agent config are used as fallback.
    """
    assignments: list[dict[str, str | int]] = []
    for line in content.split("\n"):
        # Match table rows (skip header and separator lines). Split via the
        # code-span-aware helper so pipes inside `` `int | None` ``-style
        # spans don't fragment the row (#107).
        cells = _split_row_respecting_code_spans(line)
        if len(cells) >= 3 and not re.match(r"^[-:]+$", cells[1]):
            task, agent, *rest = cells
            # Skip header row
            if agent.lower() in ("agent", "assigned to"):
                continue
            agent_lower = agent.lower()
            # #123: skip rows whose parsed agent column isn't a known execution
            # agent — guards against multi-line code spans or prose fragments
            # bleeding into the agent column and corrupting task_assignments.
            if agent_lower not in _EXECUTION_AGENTS:
                logger.warning(
                    "dispatcher: skipping row with unknown agent %r (task=%r)",
                    agent_lower,
                    task[:60],
                )
                continue
            assignment: dict[str, str | int] = {
                "task": task,
                "agent": agent_lower,
                "priority": rest[0] if rest else "medium",
            }
            budget = _budget_for(task, agent_lower, config_lookup)
            if budget is not None:
                max_turns, timeout_sec = budget
                assignment["max_turns"] = max_turns
                assignment["timeout_sec"] = timeout_sec
            assignments.append(assignment)
    return assignments


def _budget_for(
    task_description: str,
    agent_name: str,
    config_lookup: Callable[[str], dict],
) -> tuple[int, int] | None:
    """Resolve (max_turns, timeout_sec) for one task from the agent config.

    Returns None when the lookup fails entirely (unknown agent, no config).
    Falls back to top-level ``max_turns``/``timeout_sec`` when the agent has
    no ``budget_tiers`` block.
    """
    try:
        agent_config = config_lookup(agent_name) or {}
    except Exception:
        return None
    tier = _classify_budget_tier(_count_target_files(task_description))
    tiers = agent_config.get("budget_tiers") or {}
    tier_values = tiers.get(tier) if isinstance(tiers, dict) else None
    if (
        isinstance(tier_values, dict)
        and "max_turns" in tier_values
        and "timeout_sec" in tier_values
    ):
        return int(tier_values["max_turns"]), int(tier_values["timeout_sec"])
    # Fallback: use the agent's top-level static values, if present
    static_max = agent_config.get("max_turns")
    static_timeout = agent_config.get("timeout_sec")
    if isinstance(static_max, int) and isinstance(static_timeout, int):
        return static_max, static_timeout
    return None


async def run(state: dict, config: dict) -> dict:
    """Fill the Task Assignment section and output task_assignments to state.

    Pure: calls _llm_call only — no GitHub writes, no audit DB calls.
    Parses the full LLM response from audit["content_after"] so rows past
    the 200-char reasoning truncation are not silently dropped (#70).
    """
    from datetime import UTC, datetime

    from cortex.adapters.pipeline_state import cortex_to_dap, dap_to_cortex, preserve_extensions
    from cortex.nodes.base import _llm_call

    original_extensions = dict(state.get("extensions") or {})
    state = dap_to_cortex(state)
    section_content, audit = _llm_call(state, "dispatcher", "Task Assignment")
    assignments = _parse_assignments(section_content)

    from cortex.dap_steps.enrichment_write import run_side_effects

    side = await run_side_effects(state, audit, "dispatcher", "Task Assignment")
    return preserve_extensions(
        cortex_to_dap(
            {
                **side,
                "__audit": audit,
                "task_assignments": assignments,
                "decisions": [
                    *state.get("decisions", []),
                    {
                        "node": "dispatcher",
                        "action": "enriched Task Assignment",
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

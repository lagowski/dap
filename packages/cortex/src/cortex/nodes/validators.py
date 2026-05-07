"""Per-agent response validators.

LLM responses can include preamble ("Here is my analysis...") or miss
required sections. Validators run between the LLM call and the issue
update so we don't write garbage to GitHub.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Outcome of validating an LLM response."""

    valid: bool
    reason: str = ""
    cleaned: str = ""  # Response with preamble stripped


# Patterns that indicate preamble / meta-commentary the LLM added before
# the actual content. We strip lines matching any of these from the start
# until we hit a real markdown section or content line.
_PREAMBLE_PREFIXES = (
    "here is",
    "here's",
    "now i have",
    "now i'll",
    "let me",
    "i will",
    "i'll",
    "i've",
    "first,",
    "okay,",
    "alright,",
    "sure,",
    "based on",
    "good —",
    "good,",
)


def strip_preamble(text: str) -> str:
    """Remove leading preamble lines from an LLM response.

    Walks down from the start dropping any line that looks like meta-commentary
    until we hit a markdown header (``#``/``-``/``|``/``*`` start), a code
    fence, or text that doesn't match a known preamble pattern.
    """
    if not text or not text.strip():
        return ""

    lines = text.split("\n")
    drop_until = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            drop_until = i + 1
            continue
        # Real content markers — stop dropping
        if stripped.startswith(("#", "-", "*", "|", "```", "**", ">")):
            drop_until = i
            break
        # Looks like preamble?
        lower = stripped.lower()
        if any(lower.startswith(p) for p in _PREAMBLE_PREFIXES):
            drop_until = i + 1
            continue
        # Non-preamble text — stop here
        drop_until = i
        break
    else:
        # Loop completed without break — entire text was preamble
        return ""

    return "\n".join(lines[drop_until:]).strip()


def _validate_mockup(text: str) -> ValidationResult:
    lower = text.lower()
    if "description" not in lower:
        return ValidationResult(False, "Missing Description section")
    if "acceptance" not in lower:
        return ValidationResult(False, "Missing Acceptance Criteria section")
    return ValidationResult(True, cleaned=text)


def _validate_specify(text: str) -> ValidationResult:
    lower = text.lower()
    required = ("target files", "dependencies", "architecture impact", "commands")
    missing = [name for name in required if name not in lower]
    if missing:
        return ValidationResult(False, f"Missing sections: {', '.join(missing)}")
    return ValidationResult(True, cleaned=text)


def _validate_cicd(text: str) -> ValidationResult:
    lower = text.lower()
    required = ("red", "green", "refactor")
    missing = [name for name in required if name not in lower]
    if missing:
        return ValidationResult(
            False, f"Missing RED/GREEN/REFACTOR sections: {', '.join(missing)}"
        )
    return ValidationResult(True, cleaned=text)


# Canonical set of Phase 2 execution agents that the dispatcher may assign to.
# Source of truth: cortex/config/agents.yaml Phase 2 keys wired in graph.py.
# Update here if new execution agents are added to the graph.
_DISPATCHER_VALID_AGENTS: set[str] = {"coder", "designer", "documenter"}


def _validate_dispatcher(text: str) -> ValidationResult:
    """Validate a dispatcher task table.

    Catches the gemma4 repetition failure mode from issue #45:
    - 80 identical rows generated until max_tokens runs out
    - Rows with an empty Agent column

    Also validates agent names against the canonical execution agent set
    (issue #50) to catch LLM hallucinations like "developer" or "tester".

    Returns invalid when ≥3 identical data rows, any data row has
    an empty Agent column, or any agent name is not in the valid set.
    """
    # Collect all markdown table rows (including header + separator)
    table_rows = [
        line for line in text.split("\n")
        if line.strip().startswith("|") and line.count("|") >= 3
    ]
    if len(table_rows) < 2:
        return ValidationResult(False, "Missing markdown task table")

    # Drop header + separator (first two rows) to inspect data rows
    data_rows = [
        r for r in table_rows[2:]
        if not all(c.strip() in ("", "-", ":") for c in r.split("|"))
    ]

    # Check for the empty-Agent column failure mode.
    # Row format: | Task | Agent | Priority |
    # split("|") → ['', ' Task ', ' Agent ', ' Priority ', '']
    # cells[1] = task, cells[2] = agent, cells[3] = priority
    for row in data_rows:
        cells = [c.strip() for c in row.split("|")]
        # Row must have ≥4 split parts (3 columns + 2 edge empties)
        if len(cells) >= 4 and not cells[2]:
            return ValidationResult(
                False, "Row has empty Agent column — dispatcher must assign each task"
            )

    # Check agent names against the canonical valid set (issue #50)
    for row in data_rows:
        cells = [c.strip() for c in row.split("|")]
        if len(cells) >= 4:
            agent = cells[2].lower()
            if agent and agent not in _DISPATCHER_VALID_AGENTS:
                return ValidationResult(
                    False,
                    f"Unknown agent '{agent}' — valid agents: "
                    f"{', '.join(sorted(_DISPATCHER_VALID_AGENTS))}",
                )

    # Check for the repetition failure mode (≥3 identical data rows)
    if len(data_rows) >= 3:
        from collections import Counter
        counts = Counter(r.strip() for r in data_rows)
        most_common, count = counts.most_common(1)[0]
        if count >= 3:
            return ValidationResult(
                False, f"Detected {count} duplicate rows — looks like a repetition loop"
            )

    return ValidationResult(True, cleaned=text)


def _validate_finalize(text: str) -> ValidationResult:
    if not re.search(r"\b(READY|NEEDS_WORK)\b", text):
        return ValidationResult(False, "Missing verdict (READY or NEEDS_WORK)")
    return ValidationResult(True, cleaned=text)


_AGENT_VALIDATORS = {
    "mockup": _validate_mockup,
    "specify": _validate_specify,
    "cicd": _validate_cicd,
    "dispatcher": _validate_dispatcher,
    "finalize": _validate_finalize,
}


def validate_response(
    agent_name: str,
    section_name: str,
    response: str,
) -> ValidationResult:
    """Validate an LLM response for a given agent and section.

    Strips preamble first, then runs the agent-specific validator.
    Falls back to a generic non-empty check for unknown agents.
    """
    cleaned = strip_preamble(response)
    if not cleaned:
        return ValidationResult(False, "Empty response (or only preamble)")

    validator = _AGENT_VALIDATORS.get(agent_name)
    if validator is None:
        # Generic: just check non-empty
        return ValidationResult(True, cleaned=cleaned)

    result = validator(cleaned)
    if result.valid:
        result.cleaned = cleaned
    return result


__all__ = [
    "ValidationResult",
    "_DISPATCHER_VALID_AGENTS",
    "strip_preamble",
    "validate_response",
]

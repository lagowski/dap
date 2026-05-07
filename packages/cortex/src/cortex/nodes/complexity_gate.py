"""Phase 0 complexity gate — halts the pipeline before mockup if an issue
is too large to deliver in one pass (#104).

Mirrors #89's design philosophy: deterministic gate, no LLM judgment in
the routing decision. The complexity rating is computed once from the
raw issue body; the action is mechanical.

The rating function below is a **heuristic placeholder**. #102's
experiment showed local LLMs aren't reliable enough to drive this gate
on their own. Until a better evaluator lands, we score complexity from
deterministic signals: issue body size, hot-word matches that suggest
breadth ("every backend", "all tables", "migration"), bullet-point
density, and code-block count. Crude but transparent and replaceable —
swap ``_rate_complexity`` with a real evaluator when one becomes
available, no graph or state changes needed.

Operator override: pass ``--force`` to ``cortex run`` to bypass the gate
when the operator disagrees with the rating.
"""

from __future__ import annotations

from datetime import UTC, datetime

DEFAULT_COMPLEXITY_THRESHOLD = 8

__all__ = ["run"]

# Hot words that suggest the change spans many surfaces. Each match adds 1
# to the score. Order doesn't matter; matches are case-insensitive.
_BREADTH_HOT_WORDS = (
    "every backend",
    "every agent",
    "every table",
    "all tables",
    "all backends",
    "all agents",
    "across all",
    "across every",
    "multi-tenant",
    "migration",
    "migrate from",
    "rewrite",
    "refactor across",
    "epic:",
)


def _rate_complexity(issue_body: str) -> int:
    """Return a 1-10 complexity rating from the raw issue body.

    Heuristic placeholder — see module docstring. The score combines:

    1. Body size (longer body → larger scope, usually).
    2. Breadth hot-word matches (one point each for "every backend",
       "all tables", "migration", etc.).
    3. Bullet-point density (>=20 bullets implies many sub-tasks).
    4. Acceptance-criteria density (>5 checkbox lines "- [ ]" implies
       many concrete deliverables — strong epic signal).
    5. Code-block count (>=4 fenced blocks implies multiple
       architectural fragments).

    Capped at 10 so individual signals can't run away.
    """
    if not issue_body:
        return 1
    body_lower = issue_body.lower()
    score = 1

    char_count = len(issue_body)
    if char_count > 5000:
        score += 3
    elif char_count > 2000:
        score += 2
    elif char_count > 800:
        score += 1

    for word in _BREADTH_HOT_WORDS:
        if word in body_lower:
            score += 1

    bullets = sum(1 for line in issue_body.split("\n") if line.lstrip().startswith("-"))
    if bullets >= 20:  # was > 20 — boundary miss caught by #175 gate test
        score += 1

    # Acceptance-criteria checkboxes: "- [ ]" lines are concrete deliverables.
    # More than 5 is a strong signal the issue spans multiple work streams.
    ac_checkboxes = sum(
        1
        for line in issue_body.split("\n")
        if line.lstrip().startswith("- [ ]") or line.lstrip().startswith("- [x]")
    )
    if ac_checkboxes > 5:
        score += 1

    # Each fenced block is opened and closed by ```; count pairs.
    fenced_block_pairs = issue_body.count("```") // 2
    if fenced_block_pairs >= 4:
        score += 1

    return min(score, 10)


def _format_recommendation(issue_number: int, score: int, threshold: int) -> str:
    """Build the copy-pasteable split recommendation (#104).

    Concrete commands the operator can run to break the issue down. The
    message is rendered by the CLI's ``run`` command when complexity_blocked
    is set on the returned state, so the operator sees it immediately.
    """
    return (
        f"\n✗ Issue #{issue_number} rated {score}/10 (≥{threshold}). "
        f"Pipeline halted before mockup.\n\n"
        f"This issue is too complex for a single pipeline run. "
        f"To split it automatically:\n\n"
        f"  cortex split <issue-url>\n\n"
        f"This runs the built-in split pipeline which decomposes the issue "
        f"into smaller child issues on GitHub. After splitting, run cortex "
        f"on each child issue. If any child is still rated ≥{threshold}/10, "
        f"repeat the split on that child (recursive split).\n\n"
        f"To override and run anyway: cortex run --force "
        f"<issue-url>\n"
    )


async def run(state: dict, config: dict) -> dict:
    """Phase 0: rate complexity, decide whether to route to mockup or halt.

    Sets ``complexity_score``, ``complexity_blocked``, and (when blocked)
    ``error`` containing the recommended split message. The graph's
    conditional edge inspects ``complexity_blocked`` and routes to END
    instead of mockup.
    """
    issue_body = state.get("issue_body", "") or ""
    threshold = int(
        state.get("complexity_threshold", DEFAULT_COMPLEXITY_THRESHOLD)
        or DEFAULT_COMPLEXITY_THRESHOLD
    )
    force = bool(state.get("force_run", False))

    score = _rate_complexity(issue_body)
    blocked = score >= threshold and not force

    decision_action = "blocked" if blocked else ("forced_pass" if score >= threshold else "passed")
    reasoning_bits = [f"score={score}/10", f"threshold={threshold}"]
    if force and score >= threshold:
        reasoning_bits.append("force=True (operator override)")
    reasoning = " ".join(reasoning_bits)

    decisions = [
        *state.get("decisions", []),
        {
            "node": "complexity_gate",
            "action": decision_action,
            "reasoning": reasoning,
            "backend": "",
            "model": "",
            "tokens": "",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    ]

    result: dict = {
        "complexity_score": score,
        "complexity_blocked": blocked,
        "current_phase": "complexity_gate",
        "decisions": decisions,
    }
    if blocked:
        result["error"] = _format_recommendation(
            state.get("issue_number", 0),
            score,
            threshold,
        )
    return result

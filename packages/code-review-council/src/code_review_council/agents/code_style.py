"""CodeStyleAgent — naming, readability, dead/duplicated code, lint smells.

Spike (issue #633): a line-by-line "Code Style" specialist for the
review council. It owns subjective-but-cheap quality signals that the
auto-formatter (``ruff format``) does NOT fix on its own: unclear
names, dead or duplicated code, tangled readability, and lint smells a
human would flag in review.

Deliberately NOT in scope: correctness, security, perf, DB, frontend —
those are other specialists' lanes. Keeping this agent narrow is the
same anti-hallucination lever every other agent relies on.

Severity is held conservative on purpose. Style opinions at HIGH/MEDIUM
would drown the council's real bug findings, so the prompt pins this
agent to LOW / NIT. The arbiter's low-confidence drop spares NIT, so a
LOW-confidence NIT still surfaces — which is exactly the granularity a
style reviewer wants.

Opt-in: this agent is registered here but is NOT in the default roster
unless ``COUNCIL_ENABLE_CODE_STYLE`` is set (see
``council.default_agents``). The spike ships disabled so the existing
council behaviour is unchanged until a maintainer enables it for live
evaluation.
"""

from __future__ import annotations

from typing import ClassVar

from code_review_council.agents.base import BaseAgent


class CodeStyleAgent(BaseAgent):
    name: ClassVar[str] = "Code Style"
    scope: ClassVar[str] = (
        "naming clarity, readability, dead/duplicated code, and the "
        "formatting / lint smells an auto-formatter would NOT catch. "
        "Cite every finding with a precise file:line. Style only — never "
        "correctness, security, performance, DB, or frontend concerns."
    )
    focus_areas: ClassVar[list[str]] = [
        "Naming clarity: single-letter or cryptic identifiers outside a "
        "tight loop; a name that lies about its type/contents (``users`` "
        "holding one user); inconsistent casing vs. the file's existing "
        "convention. Cite file:line.",
        "Dead code: unreachable branch after an unconditional return; an "
        "unused local / import the formatter left behind; a parameter "
        "threaded through but never read. Cite file:line.",
        "Duplication: a copy-pasted block that drifts from its sibling; "
        "the same literal repeated where a named constant would read "
        "better. Flag once, point at both file:line locations.",
        "Readability: a deeply nested conditional that an early-return "
        "would flatten; a multi-clause boolean that begs for a named "
        "predicate; a comment that contradicts the code beneath it. "
        "Cite file:line.",
        "Lint smells the auto-formatter won't fix: a bare ``# noqa`` with "
        "no rule code; a ``TODO`` with no ticket reference; a magic "
        "number where the codebase uses named constants nearby. Cite "
        "file:line.",
        "Severity discipline: this lane is LOW/NIT only. A style "
        "observation is NEVER HIGH or CRITICAL — if it feels like a "
        "blocker it belongs to Correctness/Security, not here, so drop "
        "it. Quality over quantity: a clean diff yields an empty report.",
    ]

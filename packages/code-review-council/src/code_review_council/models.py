"""Pydantic models for the Code Review Council.

Three shapes matter:

- :class:`Finding` — a single observation produced by an agent.
- :class:`AgentReport` — what an agent returns (findings + a brief
  summary + a ``review_complete`` flag).
- :class:`ReviewVerdict` — the final consolidated output the Council
  hands back to the caller; what gets posted to the PR.
- :class:`ProjectContext` — injected into every agent's prompt so
  out-of-scope concerns never enter the candidate pool.

Severity / confidence vocabulary is documented in the Field
descriptions so the LLM (which sees the JSON schema) knows what each
value means without a separate prompt round.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectContext(BaseModel):
    """Per-project scope guard. Injected into every agent prompt.

    Out-of-scope items are the main anti-hallucination lever: if the
    project doesn't use i18n / CSP / RTL, the agent is told explicitly
    "don't produce findings for these concerns" and produces fewer
    speculative observations.
    """

    model_config = ConfigDict(frozen=True)

    stack: list[str] = Field(
        description=(
            "Short bullets describing the runtime stack — e.g. "
            "'Next.js 14 App Router', 'Python 3.13 / FastAPI'. "
            "Used to prime the reviewer on the actual technologies."
        ),
    )
    in_scope: list[str] = Field(
        default_factory=list,
        description=(
            "Concerns the reviewer SHOULD flag when found — e.g. "
            "'auth', 'data correctness', 'real perf'."
        ),
    )
    out_of_scope: list[str] = Field(
        default_factory=list,
        description=(
            "Concerns the reviewer MUST NOT flag — e.g. 'i18n', "
            "'CSP nonces', 'AAA accessibility'. The agent's prompt "
            "names each one explicitly so the model knows to skip."
        ),
    )
    notes: str = Field(
        default="",
        description=(
            "Free-text notes about house conventions. "
            "Brief, no Markdown — the model sees this in its system "
            "instruction. Example: 'shadcn <Button> defaults are "
            "fine, admin endpoints return 404 not 403'."
        ),
    )


class Finding(BaseModel):
    """A single review observation.

    The schema is rigid because vague findings are useless: every field
    is required at the LLM layer (the Pydantic-generated JSON schema is
    fed to Gemini as ``response_json_schema``). The post-processor
    further filters LOW-confidence findings unless their severity is
    already NIT.
    """

    severity: str = Field(
        description=(
            "One of 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NIT'. "
            "CRITICAL = data loss, security hole, broken contract. "
            "HIGH = real bug or design flaw that should block merge. "
            "MEDIUM = correctness concern or missing edge case. "
            "LOW = code-quality / maintainability issue. "
            "NIT = style or naming opinion."
        ),
    )
    confidence: str = Field(
        description=(
            "One of 'HIGH', 'MEDIUM', 'LOW'. "
            "HIGH = the issue is concretely present at the cited "
            "file:line, verifiable from the diff alone. "
            "MEDIUM = likely an issue, supported by the surrounding "
            "code but requires a small inferential leap. "
            "LOW = hypothetical — 'what if a user did X' / "
            "'in scenario Y'. LOW-confidence findings are auto-dropped "
            "by the post-processor unless severity is also NIT."
        ),
    )
    file: str = Field(
        description=(
            "Path to the file the finding applies to, relative to repo "
            "root. Mandatory — a finding without a file path is "
            "actionable for nobody."
        ),
    )
    line: int | None = Field(
        default=None,
        description=(
            "Line number in the new (post-diff) version of the file. "
            "Omit only when the issue is genuinely cross-file."
        ),
    )
    issue: str = Field(
        description=(
            "Concrete description of the problem. State what's wrong, "
            "not what was changed. One-to-three sentences."
        ),
    )
    suggestion: str = Field(
        description=(
            "Concrete remediation: what to do to fix the issue. Code "
            "snippet welcome but not required. One-to-three sentences."
        ),
    )
    evidence: str = Field(
        default="",
        description=(
            "Verbatim quote of the problematic code from the diff. "
            "Grounds the claim — a hallucinated line number becomes "
            "obvious to the human reader. Use '—' for cross-file "
            "findings where no single line applies."
        ),
    )
    # Stamped by the Council orchestrator after the agent returns.
    # Not part of the LLM-facing schema (so the model doesn't have to
    # populate it) but useful in the rendered review body.
    agent: str = Field(
        default="",
        description=(
            "Name of the agent that produced this finding. Populated "
            "by the Council, not the LLM. Visible in the rendered "
            "review body to attribute concerns to their specialist."
        ),
    )


class AgentReport(BaseModel):
    """What a single agent returns to the Council.

    Each agent runs in its own narrow scope and produces this report.
    The Council collects all reports, feeds them to the
    :class:`~code_review_council.arbiter.FinalArbiter`, and the
    arbiter produces the final :class:`ReviewVerdict`.
    """

    summary: str = Field(
        description=(
            "One-to-two sentence summary of what this agent observed "
            "WITHIN ITS SCOPE. Empty/placeholder text is fine when the "
            "agent has nothing to flag — strict reviewers don't pad."
        ),
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description=(
            "List of findings within this agent's scope. **Empty list "
            "is permitted** — quality > quantity. Sort CRITICAL → NIT."
        ),
    )
    review_complete: bool = Field(
        default=False,
        description=(
            "True when this agent reports it has nothing more "
            "actionable to add (within its scope). The Council "
            "aggregates this signal across all agents to decide "
            "whether the overall verdict is 'complete'."
        ),
    )


class ReviewVerdict(BaseModel):
    """Final consolidated output of a Council review.

    Built by the FinalArbiter from one-or-more :class:`AgentReport`
    instances. The verdict shape is identical to the legacy
    single-agent shape so the workflow's posting code didn't need a
    rewrite — only the upstream production of it.
    """

    verdict: str = Field(
        description=(
            "One of 'request_changes', 'comment'. "
            "'request_changes' iff at least one CRITICAL finding or "
            "a HIGH-severity finding with non-LOW confidence survives "
            "the arbiter's filter. Otherwise 'comment'. Strict mode "
            "never produces 'approve'."
        ),
    )
    summary: str = Field(
        description=(
            "Plain-prose take on the change. Avoid flattery; lead with "
            "the most important observation."
        ),
    )
    findings: list[Finding] = Field(
        default_factory=list,
    )
    review_complete: bool = Field(
        default=False,
        description=(
            "True when every contributing agent reported "
            "``review_complete`` and the arbiter agrees no further "
            "actionable items exist."
        ),
    )
    # Internal-only — handy for the rendered body's scoreboard.
    per_agent: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Map of agent name → kept-findings count. Populated by "
            "the Council so the renderer can show a per-agent "
            "breakdown without re-walking the findings list."
        ),
    )

    def to_legacy_dict(self) -> dict[str, Any]:
        """Render in the same shape the pre-Council script used.

        Lets the workflow's post-processor / renderer stay unchanged
        during the migration. Drop once the renderer reads
        ``ReviewVerdict`` directly.
        """
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "findings": [f.model_dump() for f in self.findings],
            "review_complete": self.review_complete,
        }

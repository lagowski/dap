"""Common scaffolding shared by every specialized review agent.

The pattern: each concrete agent declares its ``name`` and ``scope``
(human-readable description of what it owns), and a ``focus_areas``
list (concrete bullets fed into the prompt). The base class composes
the system instruction from those + the ``ProjectContext``, so
adding a new agent is mostly choosing a scope and listing focus
areas — no prompt boilerplate.
"""

from __future__ import annotations

from typing import ClassVar

from code_review_council.models import AgentReport, Finding, ProjectContext
from code_review_council.providers.base import BaseProvider


class BaseAgent:
    """Concrete subclasses must set ``name``, ``scope``, ``focus_areas``.

    The system instruction follows the same template across all
    agents — narrow scope, anti-hallucination guards, structured
    output — so subclass overrides should be rare. When you need a
    fundamentally different prompt (e.g. agent that needs file context
    beyond the diff), override ``build_system_instruction``.
    """

    # Override in subclasses. ``ClassVar`` annotation tells ruff /
    # mypy these are class-level constants, not instance fields.
    name: ClassVar[str] = ""
    scope: ClassVar[str] = ""
    focus_areas: ClassVar[list[str]] = []

    def __init__(self, provider: BaseProvider, context: ProjectContext) -> None:
        if not self.name or not self.scope:
            raise NotImplementedError(
                "Subclass must set both ``name`` and ``scope`` class attrs.",
            )
        self._provider = provider
        self._context = context

    def build_system_instruction(self) -> str:
        """Compose the prompt for this agent + project.

        Kept as a method (not a constant) so subclasses can append
        scope-specific addenda without copying the whole template.
        """
        focus_block = "\n".join(f"- {item}" for item in self.focus_areas) or (
            "- (no further focus bullets declared)"
        )
        in_scope = ", ".join(self._context.in_scope) or "(unspecified)"
        out_of_scope = ", ".join(self._context.out_of_scope) or "(none)"
        stack = ", ".join(self._context.stack) or "(unspecified)"

        return (
            f"You are the **{self.name}** reviewer in a multi-agent review "
            f"council. Your SOLE focus: {self.scope}. Other agents handle "
            "other concerns — do NOT produce findings outside your scope.\n\n"
            "Project context:\n"
            f"- Stack: {stack}\n"
            f"- In scope (for the council overall): {in_scope}\n"
            f"- Out of scope — DROP findings on these topics: {out_of_scope}\n"
            f"- House notes: {self._context.notes or '(none)'}\n\n"
            "Your specific focus areas:\n"
            f"{focus_block}\n\n"
            "Rules:\n"
            "1. ONLY findings in YOUR scope. If you notice a problem outside "
            "your scope, ignore it — another agent will (or won't) flag it.\n"
            "2. Every finding has ``severity``, ``confidence``, ``file``, "
            "``issue``, ``suggestion``, and ``evidence`` (quote of the "
            "problematic code).\n"
            "3. Severity is honest. CRITICAL = data loss / security hole / "
            "broken contract. HIGH = real bug that blocks merge. MEDIUM = "
            "correctness concern. LOW = quality. NIT = style/naming.\n"
            "4. Confidence is honest. HIGH = verified in this diff. "
            "MEDIUM = likely. LOW = hypothetical — those will be filtered "
            "out unless severity is NIT, so don't pad with hypotheticals.\n"
            "5. Empty findings IS the correct answer when nothing in your "
            "scope is wrong. Quality > quantity.\n"
            "6. Don't open the summary with 'looks good' or any flattery. "
            "Lead with the most important observation in your scope.\n"
            "7. When your scope holds no remaining actionable items, set "
            "``review_complete: true``.\n\n"
            "Output the structured AgentReport JSON schema you've been given."
        )

    def build_user_content(self, *, pr_title: str, pr_body: str, diff: str) -> str:
        """Compose the per-call user payload.

        Same shape every agent uses — the difference between agents is
        the system instruction, not the input.
        """
        # Cap the PR body so a long template description doesn't crowd
        # the diff. 4k chars ≈ ~1.5k tokens — plenty for a summary.
        body_clipped = (pr_body or "")[:4000]
        return (
            f"PR title: {pr_title}\n\n"
            f"PR description:\n{body_clipped}\n\n"
            f"Diff:\n```diff\n{diff}\n```"
        )

    def review(self, *, pr_title: str, pr_body: str, diff: str) -> AgentReport:
        """Run the agent and return its report.

        Findings get stamped with ``agent=self.name`` so the arbiter can
        attribute concerns in the final rendered body.
        """
        system = self.build_system_instruction()
        user = self.build_user_content(pr_title=pr_title, pr_body=pr_body, diff=diff)
        report = self._provider.run_structured(
            system_instruction=system,
            user_content=user,
            response_schema=AgentReport,
        )
        # Stamp the source agent — saves the arbiter from threading
        # this in by hand and makes per-agent counts cheap to compute.
        stamped: list[Finding] = []
        for f in report.findings:
            stamped.append(f.model_copy(update={"agent": self.name}))
        return report.model_copy(update={"findings": stamped})

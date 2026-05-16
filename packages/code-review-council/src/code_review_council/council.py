"""Council orchestrator — run agents in sequence, arbitrate findings.

MVP runs agents sequentially. For two agents this is the right
trade-off: simpler control flow, easier debugging, and the latency
cost (2x one Gemini call) stays well inside CI's 5-minute timeout.
Async parallelism via ``asyncio.gather`` is a follow-up once we have
4+ agents.
"""

from __future__ import annotations

from code_review_council.agents.base import BaseAgent
from code_review_council.agents.correctness import CorrectnessAgent
from code_review_council.agents.security import SecurityAgent
from code_review_council.arbiter import FinalArbiter
from code_review_council.models import AgentReport, ProjectContext, ReviewVerdict
from code_review_council.providers.base import BaseProvider


def default_agents(provider: BaseProvider, context: ProjectContext) -> list[BaseAgent]:
    """Construct the MVP roster.

    Lives as a function (not a class constant) so callers can swap in
    different agent sets — e.g. a smaller council for nit-only PRs.
    """
    return [
        SecurityAgent(provider, context),
        CorrectnessAgent(provider, context),
    ]


class Council:
    """Top-level entry point. Build with a provider + context, call review()."""

    def __init__(
        self,
        *,
        provider: BaseProvider,
        context: ProjectContext,
        agents: list[BaseAgent] | None = None,
    ) -> None:
        self._provider = provider
        self._context = context
        self._agents = agents if agents is not None else default_agents(provider, context)
        self._arbiter = FinalArbiter(context)

    @property
    def agents(self) -> list[BaseAgent]:
        """Read-only view — handy for the renderer's per-agent labels."""
        return list(self._agents)

    def review(
        self,
        *,
        diff: str,
        pr_title: str = "",
        pr_body: str = "",
    ) -> ReviewVerdict:
        """Run every agent against the diff and arbitrate the findings.

        Errors from one agent bubble up — we'd rather surface a partial
        failure than silently lose half the review (the workflow has a
        check_run-failure path for that case).
        """
        reports: list[AgentReport] = []
        for agent in self._agents:
            report = agent.review(pr_title=pr_title, pr_body=pr_body, diff=diff)
            reports.append(report)
        return self._arbiter.arbitrate(reports)

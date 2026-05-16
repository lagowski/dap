"""Council orchestrator — run specialist agents in parallel, then arbitrate.

V2 (post-OpenRouter):
- **Parallel execution** via :func:`asyncio.gather` + per-agent
  :func:`asyncio.to_thread`. Each provider stays sync (Gemini SDK is
  blocking, ``httpx.Client`` is blocking) but we release the GIL on
  the HTTP wait, so 5 agents complete in ~max(per_agent_time)
  instead of ~sum.
- **Skip honoring**: agents that report ``should_run(diff) == False``
  are skipped entirely — no LLM call. A FrontendAgent on a pure-
  backend diff produces no report at all (not even an empty one)
  so the arbiter doesn't count it against ``review_complete``.
- **Expanded default roster**: 5 agents (Security / Correctness /
  Database / Performance / Frontend). The skip logic keeps cost
  bounded — for a typical backend-only PR only 3 of 5 actually run.

For callers that want sequential exec (debugging, deterministic
ordering in tests), pass ``parallel=False`` to ``review()``.
"""

from __future__ import annotations

import asyncio

from code_review_council.agents.base import BaseAgent
from code_review_council.agents.correctness import CorrectnessAgent
from code_review_council.agents.database import DatabaseAgent
from code_review_council.agents.frontend import FrontendAgent
from code_review_council.agents.performance import PerformanceAgent
from code_review_council.agents.security import SecurityAgent
from code_review_council.arbiter import FinalArbiter
from code_review_council.models import AgentReport, ProjectContext, ReviewVerdict
from code_review_council.providers.base import BaseProvider


def default_agents(provider: BaseProvider, context: ProjectContext) -> list[BaseAgent]:
    """Construct the V2 roster.

    Order matters for *sequential* mode (debug runs) and for tie-break
    in the arbiter's dedup pass — earlier agents win on equal-severity
    duplicates. Security first so its high-severity findings land
    before Correctness's framing of the same line.
    """
    return [
        SecurityAgent(provider, context),
        CorrectnessAgent(provider, context),
        DatabaseAgent(provider, context),
        PerformanceAgent(provider, context),
        FrontendAgent(provider, context),
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
        """Read-only view — useful for the workflow's pre-flight logging."""
        return list(self._agents)

    def review(
        self,
        *,
        diff: str,
        pr_title: str = "",
        pr_body: str = "",
        parallel: bool = True,
    ) -> ReviewVerdict:
        """Run every (non-skipped) agent against the diff and arbitrate.

        - ``parallel=True`` (default): use ``asyncio.gather`` +
          ``asyncio.to_thread`` so all surviving agents run
          concurrently. Wall-clock ≈ max(per-agent), not sum.
        - ``parallel=False``: run sequentially in the order agents
          were registered. Useful when debugging a single misbehaving
          agent (cleaner stack traces, no thread interleaving).

        Returns the arbitrated verdict — see
        :class:`code_review_council.arbiter.FinalArbiter` for the
        post-processing rules (low-confidence drop, dedup,
        out-of-scope guard).
        """
        active = [a for a in self._agents if type(a).should_run(diff)]
        if not active:
            # Nothing to review by anyone. Return an empty verdict
            # the workflow can post as a clean check_run.
            return self._arbiter.arbitrate([])

        if parallel:
            reports = asyncio.run(self._gather(active, pr_title, pr_body, diff))
        else:
            reports = [
                agent.review(pr_title=pr_title, pr_body=pr_body, diff=diff) for agent in active
            ]
        return self._arbiter.arbitrate(reports)

    async def _gather(
        self,
        agents: list[BaseAgent],
        pr_title: str,
        pr_body: str,
        diff: str,
    ) -> list[AgentReport]:
        """Fan out each agent's review onto a thread + await all.

        ``asyncio.to_thread`` lets us parallelize sync provider clients
        (Gemini SDK, httpx) without rewriting them as async — the GIL
        releases during the HTTP I/O wait, so concurrency is real.
        """
        tasks = [
            asyncio.to_thread(
                agent.review,
                pr_title=pr_title,
                pr_body=pr_body,
                diff=diff,
            )
            for agent in agents
        ]
        results = await asyncio.gather(*tasks)
        # ``asyncio.gather`` preserves order, so ``results`` aligns
        # with ``agents`` — useful when debugging which slot misbehaved.
        return list(results)

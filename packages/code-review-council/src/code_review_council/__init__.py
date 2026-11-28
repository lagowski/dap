"""Multi-agent code review with anti-hallucination filtering.

See ``README.md`` for the architecture. Public entry points:

- :class:`Council` — main orchestrator
- :class:`ProjectContext` — scope-guard config
- :class:`ReviewVerdict` — what review() returns
- :class:`Finding` — single review observation
- :class:`AgentReport` — what each agent produces
- :class:`FinalArbiter` — the dedup / filter pass (exposed for tests)

Agents and providers are importable via their subpackages
(``code_review_council.agents.SecurityAgent``,
``code_review_council.providers.GeminiProvider``) — they're not
re-exported here to keep the top-level namespace small.
"""

from code_review_council.arbiter import FinalArbiter
from code_review_council.council import Council, default_agents
from code_review_council.models import (
    AgentReport,
    Finding,
    ProjectContext,
    ReviewVerdict,
)

__all__ = [
    "AgentReport",
    "Council",
    "FinalArbiter",
    "Finding",
    "ProjectContext",
    "ReviewVerdict",
    "default_agents",
]

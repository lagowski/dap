"""Specialized review agents.

Each agent has a narrow scope (security, correctness, etc.) and is
prompted to ONLY flag findings within that scope. Narrowness is the
core anti-hallucination mechanism: an agent told it owns "auth and
injection" never reaches for "what about i18n" because i18n is
explicitly out of its lane.
"""

from code_review_council.agents.base import BaseAgent
from code_review_council.agents.correctness import CorrectnessAgent
from code_review_council.agents.database import DatabaseAgent
from code_review_council.agents.frontend import FrontendAgent
from code_review_council.agents.performance import PerformanceAgent
from code_review_council.agents.security import SecurityAgent

__all__ = [
    "BaseAgent",
    "CorrectnessAgent",
    "DatabaseAgent",
    "FrontendAgent",
    "PerformanceAgent",
    "SecurityAgent",
]

"""LLM provider adapters.

Each provider implements the :class:`BaseProvider` protocol so the
Council orchestrator stays provider-agnostic. MVP ships GeminiProvider
only; Claude / OpenAI adapters are mechanical follow-ups (matched
SDK shapes, same Pydantic-schema-driven structured output).
"""

from code_review_council.providers.base import BaseProvider
from code_review_council.providers.gemini import GeminiProvider

__all__ = ["BaseProvider", "GeminiProvider"]

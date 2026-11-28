"""LLM provider adapters.

Each provider implements the :class:`BaseProvider` protocol so the
Council orchestrator stays provider-agnostic.

- :class:`GeminiProvider` — Google AI Studio direct (free tier).
- :class:`OpenRouterProvider` — unified gateway to Claude / Gemini /
  DeepSeek / GPT, used when we want a model family Gemini-direct
  can't reach.

Picking between them is a workflow-level decision (env var) — no agent
code touches provider specifics. Speecher's experience showed that
multi-model OpenRouter catches a class of bugs Gemini-only misses;
keeping both adapters lets DAP A/B between them without rewrites.
"""

from code_review_council.providers.base import BaseProvider
from code_review_council.providers.gemini import GeminiProvider
from code_review_council.providers.openrouter import OpenRouterProvider

__all__ = ["BaseProvider", "GeminiProvider", "OpenRouterProvider"]

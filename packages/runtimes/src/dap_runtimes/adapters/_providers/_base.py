"""Shared types for provider modules.

Lives in its own module so provider implementations can import from it
without going through ``_providers/__init__.py`` (which itself imports
the provider modules to build the registry — creates a circular
import smell otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TOKENS_PER_MILLION = 1_000_000


@dataclass
class ProviderResult:
    """Internal: provider-specific call result before normalisation.

    The api-call adapter maps this to a public ``RuntimeResult``. Cache
    token fields default to zero for providers that don't expose them.
    """

    output_text: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = None
    model: str = ""
    stop_reason: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Raised by a provider's ``call()`` on API/network/auth failures.

    The api-call adapter catches this and surfaces ``str(error)`` in
    the ``RuntimeResult.errors`` list.
    """


def calculate_cost_usd(
    *,
    model_id: str,
    pricing: dict[str, tuple[float, float]],
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_multiplier: float = 1.0,
    cache_read_multiplier: float = 1.0,
) -> float | None:
    """Return USD cost for token usage, or ``None`` for unknown models.

    Provider modules own their pricing tables, but the arithmetic is
    shared so cache-aware providers and simple input/output providers
    cannot drift in how they normalize "USD per 1M tokens".
    """
    rates = pricing.get(model_id)
    if rates is None:
        return None
    input_rate, output_rate = rates
    input_cost = input_tokens * input_rate
    cache_creation_cost = cache_creation_tokens * input_rate * cache_write_multiplier
    cache_read_cost = cache_read_tokens * input_rate * cache_read_multiplier
    output_cost = output_tokens * output_rate
    return (input_cost + cache_creation_cost + cache_read_cost + output_cost) / TOKENS_PER_MILLION

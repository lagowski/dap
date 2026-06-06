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
_HTTP_TOO_MANY_REQUESTS = 429


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

    ``category`` (#692) classifies the failure so callers and the UI can
    react to quota / credit exhaustion distinctly from a generic backend
    error. One of:

    - ``"out_of_credits"`` — quota / credit exhausted; will NOT recover on
      retry (top up or switch provider/runtime).
    - ``"rate_limit"`` — transient throttling; may recover on retry.
    - ``None`` — anything else (auth, bad request, network, ...).
    """

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        self.category = category


def classify_provider_failure(
    text: str,
    *,
    status_code: int | None = None,
) -> str | None:
    """Best-effort category for a provider failure (#692).

    Keyed on the error *text* (plus an optional HTTP status) so it works
    across SDKs without depending on provider-specific exception fields.
    Returns ``"out_of_credits"``, ``"rate_limit"``, or ``None``.

    Order matters: out-of-credits is checked first because providers often
    deliver it *as* a 429 (e.g. OpenAI ``insufficient_quota``), and we must
    not mislabel a non-recoverable billing failure as a transient one.
    """
    haystack = (text or "").lower()
    out_of_credits_markers = (
        "insufficient_quota",
        "insufficient quota",
        "credit balance",
        "billing_hard_limit",
        "billing hard limit",
        "out of credits",
        "exceeded your current quota",
        "plan and billing",
    )
    if any(marker in haystack for marker in out_of_credits_markers):
        return "out_of_credits"
    if (
        status_code == _HTTP_TOO_MANY_REQUESTS
        or "rate limit" in haystack
        or "too many requests" in haystack
        or "resource_exhausted" in haystack
        or "resource exhausted" in haystack
    ):
        return "rate_limit"
    return None


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

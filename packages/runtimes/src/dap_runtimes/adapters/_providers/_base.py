"""Shared types for provider modules.

Lives in its own module so provider implementations can import from it
without going through ``_providers/__init__.py`` (which itself imports
the provider modules to build the registry — creates a circular
import smell otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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

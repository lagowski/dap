"""Backend registry — creates the right backend from agent config.

Each agent node declares its backend type and config in agents.yaml.
This module turns that config into a live Backend instance.

``dap-runtimes`` (``packages/runtimes`` in rafeekpro/dap) is listed as a
dependency and its ``RuntimeAdapter`` Protocol / ``RuntimeTask`` / ``RuntimeResult``
types are re-exported here for callers that need them (#222). Full migration
to DAP adapters (replacing the Cortex-native implementations below) is
tracked in #223–#226 — the async interface mismatch requires node-level
changes that are out of scope here.

Optional ``fallback:`` entries wrap the primary in a :class:`FallbackBackend`
that retries on the configured fallbacks when the primary ``invoke()``
raises. Set ``CORTEX_DISABLE_FALLBACKS=1`` to skip wrapping entirely
(issue #37).
"""

from __future__ import annotations

import logging
import os

# DAP types — re-exported for callers migrating to the DAP interface (#222).
# Full async migration of node callers is tracked in #223–#226.
from dap_types import RuntimeAdapter, RuntimeResult, RuntimeTask  # noqa: F401

from cortex.backends.api import APIBackend
from cortex.backends.base import (
    Backend,
    BackendError,  # noqa: F401
    BackendTimeoutError,  # noqa: F401
    BackendType,
    BackendUnavailableError,  # noqa: F401
    LLMRequest,
    LLMResponse,
)
from cortex.backends.claude_cli import ClaudeCLIBackend
from cortex.backends.ollama import OllamaBackend

logger = logging.getLogger(__name__)


def create_backend(config: dict) -> Backend:
    """Create a backend from a config dict.

    Config format (from agents.yaml):
        backend: ollama
        model: gemma3:27b
        # ... backend-specific options

    Args:
        config: Dict with 'backend' key and backend-specific options.
            Any ``fallback:`` key is ignored here — only the primary is
            built. Use :func:`create_backend_with_fallback` for chain semantics.

    Returns:
        Configured Backend instance.

    Raises:
        ValueError: If backend type is unknown.
    """
    backend_type: str = config.get("backend", "")

    if backend_type == "ollama":
        return OllamaBackend(
            base_url=config.get("base_url", "http://localhost:11434"),
            model=config.get("model", "gemma3:27b"),
        )

    if backend_type == "claude_cli":
        return ClaudeCLIBackend(
            command=config.get("command", "claude"),
            model=config.get("model", ""),
            cwd=config.get("cwd", ""),
            max_turns=config.get("max_turns", 0),
            timeout_sec=config.get("timeout_sec", 300),
            instructions_file=config.get("instructions_file", ""),
            extra_args=config.get("extra_args", []),
            env=config.get("env", {}),
            allowed_tools=config.get("allowed_tools", []),
        )

    if backend_type == "api":
        return APIBackend(
            provider=config.get("provider", "deepseek"),
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url", ""),
            model=config.get("model", ""),
        )

    raise ValueError(
        f"Unknown backend type: '{backend_type}'. Must be one of: ollama, claude_cli, api"
    )


class FallbackBackend(Backend):
    """Wraps a primary backend with an ordered list of fallbacks.

    On ``invoke()``, tries the primary first. If it raises, walks the
    fallback list in order until one succeeds. The successful fallback's
    response is returned with ``(fallback)`` appended to ``response.backend``
    so the audit trail can distinguish primary vs fallback runs.

    If every backend (primary + all fallbacks) raises, the original
    primary error is re-raised — that's almost always the most actionable
    diagnostic.
    """

    backend_type: BackendType = "claude_cli"  # placeholder; not meaningful for the wrapper

    def __init__(self, primary: Backend, fallbacks: list[Backend]):
        self.primary = primary
        self.fallbacks = list(fallbacks)

    def invoke(self, request: LLMRequest) -> LLMResponse:
        try:
            return self.primary.invoke(request)
        except Exception as primary_err:
            primary_label = getattr(self.primary, "backend_type", type(self.primary).__name__)
            logger.warning(
                "Primary backend %s failed: %s — trying %d fallback(s)",
                primary_label,
                primary_err,
                len(self.fallbacks),
            )
            for i, fb in enumerate(self.fallbacks):
                try:
                    response = fb.invoke(request)
                except Exception as fb_err:
                    fb_label = getattr(fb, "backend_type", type(fb).__name__)
                    logger.warning(
                        "Fallback %d (%s) failed: %s",
                        i,
                        fb_label,
                        fb_err,
                    )
                    continue
                # Mark the response so audit can distinguish fallback usage
                response.backend = f"{response.backend} (fallback)"
                return response
            # All fallbacks exhausted — re-raise the primary error
            raise primary_err

    def health_check(self) -> bool:
        # Healthy if the primary OR any fallback is reachable.
        if self.primary.health_check():
            return True
        return any(fb.health_check() for fb in self.fallbacks)


def create_backend_with_fallback(config: dict) -> Backend:
    """Create the primary backend, optionally wrapped with fallbacks.

    Reads the ``fallback:`` key from ``config`` (a list of backend configs).
    If present and non-empty AND ``CORTEX_DISABLE_FALLBACKS`` is not set,
    returns a :class:`FallbackBackend` wrapping the primary with the chain.
    Otherwise returns the primary alone.

    The env var ``CORTEX_DISABLE_FALLBACKS=1`` is the kill switch — useful
    for cost-bound runs or when the fallback API has a problem the user
    wants to surface rather than mask.
    """
    primary = create_backend(config)

    if os.environ.get("CORTEX_DISABLE_FALLBACKS") == "1":
        return primary

    fallback_configs = config.get("fallback") or []
    if not fallback_configs:
        return primary

    fallbacks: list[Backend] = []
    for fb_config in fallback_configs:
        try:
            fallbacks.append(create_backend(fb_config))
        except Exception as e:
            # A misconfigured fallback shouldn't break the primary path.
            logger.warning("Skipping malformed fallback config %r: %s", fb_config, e)

    if not fallbacks:
        return primary

    return FallbackBackend(primary, fallbacks)

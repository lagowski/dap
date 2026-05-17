"""Direct LLM API adapter — multi-provider dispatcher.

Selects the SDK call path via ``runtime_config.provider`` (defaults to
``anthropic`` for backward compatibility). Each provider lives in its own
module under ``_providers/`` with a small, identical interface — adding
a new provider doesn't require any change here.

Supported providers (v0.4):

- ``anthropic`` — Anthropic SDK (Claude 4.x family).
- ``openai`` — OpenAI SDK with default base URL (GPT-5, o-series).
- ``glm`` — Z.AI GLM via OpenAI SDK, hardcoded base_url +
  ``GLM_API_KEY`` (first-class registration, #115).
- ``openrouter`` — OpenRouter multi-model gateway via OpenAI SDK,
  hardcoded base_url + ``OPENROUTER_API_KEY`` + DAP identification
  headers (first-class registration, #449). ``model_id`` is a
  slash-namespaced id like ``anthropic/claude-3.5-sonnet``.
- ``openai-compat`` — OpenAI SDK with a custom ``base_url`` and
  ``api_key_env``. Covers Together, llama.cpp servers, internal
  proxies, and any other Chat Completions-compatible endpoint
  that doesn't have a first-class registration above.
- ``gemini`` — Google Gen AI SDK (Gemini 2.x / 3.x).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Final

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters._providers import (
    PROVIDER_REGISTRY,
    ProviderError,
    ProviderResult,
    get_provider,
    list_provider_ids,
)
from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.api_call")

DEFAULT_PROVIDER: Final = "anthropic"


class ApiCallAdapter(BaseAdapter):
    """Direct LLM API adapter — single-shot, no tools, no streaming.

    The agent's ``runtime_config.provider`` selects the SDK call path.
    Each provider module owns its own pricing table + token extraction;
    this adapter only stitches things together for the engine.
    """

    id = "api-call"
    display_name = "Direct LLM API call (SDK)"
    kind: RuntimeKind = "api"

    async def healthcheck(self) -> HealthStatus:
        """Available if at least one provider's env var is set.

        Reads only the registry metadata — does NOT import any SDK,
        so calling healthcheck stays fast and doesn't trigger lazy
        loading of all providers' SDKs.
        """
        missing: list[str] = []
        configured: list[str] = []

        for info in PROVIDER_REGISTRY.values():
            # openai-compat keys live per-agent; nothing to check here.
            if info.default_env_var is None:
                continue
            if os.environ.get(info.default_env_var):
                configured.append(info.id)
            else:
                missing.append(f"{info.default_env_var} ({info.display_name})")

        if not configured:
            return HealthStatus(available=False, missing=missing)
        return HealthStatus(
            available=True,
            version=", ".join(configured),
            missing=missing if missing else None,
        )

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        config = task.runtime_config
        provider_id = config.get("provider", DEFAULT_PROVIDER)

        provider = get_provider(provider_id)
        if provider is None:
            return _failed(
                start=time.monotonic(),
                message=(
                    f"Unknown provider '{provider_id}'. Supported: {sorted(list_provider_ids())}"
                ),
            )

        validation_error = provider.validate_config(config)
        if validation_error is not None:
            return _failed(start=time.monotonic(), message=validation_error)

        start = time.monotonic()
        try:
            result: ProviderResult = await provider.call(provider_id, config, task.prompt_xml)
        except ProviderError as exc:
            logger.warning("api-call provider=%s failed: %s", provider_id, exc)
            return _failed(start=start, message=str(exc))
        except asyncio.CancelledError:
            # Cooperative cancellation must propagate so the runner can mark
            # the run aborted.
            raise
        except Exception as exc:
            # Provider didn't wrap an unexpected error into ProviderError —
            # SDK bug, lazy-import failure, network blip outside the typed
            # surface. Match the family-wide _failed contract (#211) so a
            # single SDK exception doesn't crash the worker.
            logger.exception("api-call provider=%s unexpected error", provider_id)
            return _failed(
                start=start,
                message=f"unexpected {type(exc).__name__}: {exc}",
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        total_tokens = (
            result.input_tokens
            + result.output_tokens
            + result.cache_creation_tokens
            + result.cache_read_tokens
        )

        return RuntimeResult(
            success=True,
            output=result.output_text,
            tokens_used=total_tokens,
            cost_usd=result.cost_usd,
            duration_ms=duration_ms,
            errors=[],
            structured={
                "provider": provider_id,
                "model": result.model,
                "stop_reason": result.stop_reason,
                **result.raw_metadata,
            },
        )


def _failed(*, start: float, message: str) -> RuntimeResult:
    """Build a failed RuntimeResult with elapsed-since-start duration."""
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=int((time.monotonic() - start) * 1000),
        errors=[message],
    )


__all__ = ["ApiCallAdapter"]

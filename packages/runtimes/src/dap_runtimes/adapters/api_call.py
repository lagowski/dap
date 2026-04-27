"""Direct LLM API adapter — multi-provider dispatcher.

Selects the SDK call path via ``runtime_config.provider`` (defaults to
``anthropic`` for backward compatibility). Each provider lives in its own
module under ``_providers/`` with a small, identical interface — adding
a new provider doesn't require any change here.

Supported providers (v0.4):

- ``anthropic`` — Anthropic SDK (Claude 4.x family).
- ``openai`` — OpenAI SDK with default base URL (GPT-5, o-series).
- ``openai-compat`` — OpenAI SDK with a custom ``base_url`` and
  ``api_key_env``. Covers GLM (z.ai), Together, OpenRouter, llama.cpp
  servers and any other Chat Completions-compatible endpoint.
- ``gemini`` — Google Gen AI SDK (Gemini 2.x / 3.x).
"""

from __future__ import annotations

import logging
import time
from typing import Final

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters._providers import (
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
        """Available if at least one provider is fully configured.

        Per-provider state is reported as ``missing`` for each provider
        that's not configured — gives the operator a clear list of
        env vars to set.
        """
        missing: list[str] = []
        versions: list[str] = []
        any_available = False

        for provider_id in list_provider_ids():
            provider = get_provider(provider_id)
            if provider is None:
                continue
            # Avoid double-reporting openai/openai-compat (same module).
            if provider_id == "openai-compat":
                continue
            available, version, provider_missing = provider.healthcheck()
            if available:
                any_available = True
                if version is not None:
                    versions.append(version)
            elif provider_missing is not None:
                missing.extend(provider_missing)

        if not any_available:
            return HealthStatus(available=False, missing=missing)
        return HealthStatus(
            available=True,
            version=", ".join(versions) if versions else None,
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

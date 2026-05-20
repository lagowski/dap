"""Anthropic provider for the api-call adapter — uses the official SDK."""

from __future__ import annotations

import os
from typing import Any, Final

import anthropic
from anthropic import AsyncAnthropic

from dap_runtimes.adapters._providers._base import (
    ProviderError,
    ProviderResult,
    calculate_cost_usd,
)

ID: Final = "anthropic"
DEFAULT_ENV_VAR: Final = "ANTHROPIC_API_KEY"

# USD per 1M tokens (input, output). Cached prices; update when public
# pricing changes. Cache writes cost 1.25x of input rate; cache reads 0.1x.
PRICING: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

CACHE_WRITE_MULTIPLIER: Final = 1.25
CACHE_READ_MULTIPLIER: Final = 0.10
DEFAULT_USER_MESSAGE: Final = "Execute the task as specified in the system instructions."
VALID_EFFORT: Final = frozenset({"low", "medium", "high", "xhigh", "max"})


def env_var_for(_config: dict[str, Any]) -> str:
    """Anthropic always reads ANTHROPIC_API_KEY."""
    return DEFAULT_ENV_VAR


def validate_config(config: dict[str, Any]) -> str | None:
    """Per-call validation. Returns an error message or None when OK."""
    model_id = config.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return "runtime_config.model_id is required (e.g. 'claude-haiku-4-5')"

    max_tokens = config.get("max_tokens", 4096)
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        return "runtime_config.max_tokens must be a positive int"

    effort = config.get("effort")
    if effort is not None and effort not in VALID_EFFORT:
        return f"runtime_config.effort must be one of {sorted(VALID_EFFORT)}, got '{effort}'"

    if not os.environ.get(DEFAULT_ENV_VAR):
        return f"{DEFAULT_ENV_VAR} env var not set"

    return None


def healthcheck() -> tuple[bool, str | None, list[str] | None]:
    """Adapter-level: do we have what we need to call Anthropic?"""
    if not os.environ.get(DEFAULT_ENV_VAR):
        return (False, None, [f"{DEFAULT_ENV_VAR} env var (Anthropic SDK)"])
    return (True, f"anthropic-sdk {anthropic.__version__}", None)


async def call(
    _provider_id: str,
    config: dict[str, Any],
    prompt_xml: str,
) -> ProviderResult:
    """Single-shot Anthropic call. Returns ProviderResult; raises ProviderError."""
    request_kwargs = _build_request_kwargs(config, prompt_xml)

    client = AsyncAnthropic()
    try:
        response = await client.messages.create(**request_kwargs)
    except anthropic.AuthenticationError as exc:
        raise ProviderError(f"Authentication failed: {exc.message}") from exc
    except anthropic.PermissionDeniedError as exc:
        raise ProviderError(f"Permission denied: {exc.message}") from exc
    except anthropic.NotFoundError as exc:
        raise ProviderError(f"Model not found: {exc.message}") from exc
    except anthropic.BadRequestError as exc:
        raise ProviderError(f"Bad request: {exc.message}") from exc
    except anthropic.RateLimitError as exc:
        raise ProviderError(f"Rate limited: {exc.message}") from exc
    except anthropic.APITimeoutError as exc:
        raise ProviderError(f"API timeout: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise ProviderError(f"Connection error: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise ProviderError(f"API error {exc.status_code}: {exc.message}") from exc
    finally:
        await client.close()

    text_parts: list[str] = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
    output_text = "".join(text_parts)

    usage = response.usage
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    cost = _calculate_cost(
        model_id=config["model_id"],
        input_tokens=input_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        output_tokens=output_tokens,
    )

    return ProviderResult(
        output_text=output_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        cost_usd=cost,
        model=response.model,
        stop_reason=response.stop_reason,
        raw_metadata={
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            },
        },
    )


def _build_request_kwargs(config: dict[str, Any], prompt_xml: str) -> dict[str, Any]:
    user_system_prompt = config.get("system_prompt")
    if isinstance(user_system_prompt, str) and user_system_prompt:
        system_text = f"{user_system_prompt}\n\n{prompt_xml}"
    else:
        system_text = prompt_xml

    kwargs: dict[str, Any] = {
        "model": config["model_id"],
        "max_tokens": config.get("max_tokens", 4096),
        "system": system_text,
        "messages": [{"role": "user", "content": DEFAULT_USER_MESSAGE}],
    }
    if config.get("prompt_cache"):
        kwargs["cache_control"] = {"type": "ephemeral"}
    if config.get("enable_thinking"):
        kwargs["thinking"] = {"type": "adaptive"}
    if config.get("effort") is not None:
        kwargs["output_config"] = {"effort": config["effort"]}
    return kwargs


def _calculate_cost(
    model_id: str,
    input_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
) -> float | None:
    """USD cost from token counts. None for unknown models."""
    return calculate_cost_usd(
        model_id=model_id,
        pricing=PRICING,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_multiplier=CACHE_WRITE_MULTIPLIER,
        cache_read_multiplier=CACHE_READ_MULTIPLIER,
    )

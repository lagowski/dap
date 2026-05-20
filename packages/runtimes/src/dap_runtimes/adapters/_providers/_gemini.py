"""Gemini provider — Google Gen AI SDK."""

from __future__ import annotations

import os
from typing import Any, Final

from google import genai
from google.genai import errors as genai_errors

from dap_runtimes.adapters._providers._base import (
    ProviderError,
    ProviderResult,
    calculate_cost_usd,
)

ID: Final = "gemini"
DEFAULT_ENV_VAR: Final = "GEMINI_API_KEY"

# USD per 1M tokens (input, output). Update as pricing changes.
PRICING: Final[dict[str, tuple[float, float]]] = {
    "gemini-3.0-pro": (1.25, 10.00),
    "gemini-3.0-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-exp": (0.10, 0.40),
}


def env_var_for(_config: dict[str, Any]) -> str:
    """Gemini reads GEMINI_API_KEY (the SDK also accepts GOOGLE_API_KEY)."""
    return DEFAULT_ENV_VAR


def validate_config(config: dict[str, Any]) -> str | None:
    """Per-call validation. Returns an error message or None when OK."""
    model_id = config.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return "runtime_config.model_id is required (e.g. 'gemini-3.0-flash')"

    max_tokens = config.get("max_tokens", 4096)
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        return "runtime_config.max_tokens must be a positive int"

    if not os.environ.get(DEFAULT_ENV_VAR):
        return f"{DEFAULT_ENV_VAR} env var not set"

    return None


def healthcheck() -> tuple[bool, str | None, list[str] | None]:
    """Adapter-level: do we have what we need to call Gemini?"""
    if not os.environ.get(DEFAULT_ENV_VAR):
        return (False, None, [f"{DEFAULT_ENV_VAR} env var (Google Gen AI SDK)"])
    return (True, f"google-genai {genai.__version__}", None)


async def call(
    _provider_id: str,
    config: dict[str, Any],
    prompt_xml: str,
) -> ProviderResult:
    """Single-shot Gemini call. Returns ProviderResult; raises ProviderError."""
    client = genai.Client()
    request_kwargs = _build_request_kwargs(config, prompt_xml)

    try:
        response = await client.aio.models.generate_content(**request_kwargs)
    except genai_errors.APIError as exc:
        # google-genai raises a single APIError class with a status code on it.
        status = getattr(exc, "code", None)
        raise ProviderError(f"Gemini API error {status}: {exc}") from exc
    except Exception as exc:
        # Network / unexpected — surface generically.
        raise ProviderError(f"Gemini call failed: {exc}") from exc

    output_text = response.text or ""

    usage = response.usage_metadata
    input_tokens = getattr(usage, "prompt_token_count", 0) or 0
    output_tokens = getattr(usage, "candidates_token_count", 0) or 0
    cache_read = getattr(usage, "cached_content_token_count", 0) or 0

    cost = _calculate_cost(config["model_id"], input_tokens, output_tokens)

    return ProviderResult(
        output_text=output_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cost_usd=cost,
        model=config["model_id"],
        stop_reason=_extract_finish_reason(response),
        raw_metadata={
            "usage": {
                "prompt_token_count": input_tokens,
                "candidates_token_count": output_tokens,
                "cached_content_token_count": cache_read,
            },
        },
    )


def _build_request_kwargs(config: dict[str, Any], prompt_xml: str) -> dict[str, Any]:
    """Map per-call config to the Gen AI SDK request shape.

    Prompt XML goes in as the system instruction; a small user message
    triggers the actual completion. Mirrors the Anthropic / OpenAI shape,
    including the optional ``runtime_config.system_prompt`` that gets
    prepended to the XML so users can layer extra context.
    """
    user_system_prompt = config.get("system_prompt")
    if isinstance(user_system_prompt, str) and user_system_prompt:
        system_instruction = f"{user_system_prompt}\n\n{prompt_xml}"
    else:
        system_instruction = prompt_xml

    config_block: dict[str, Any] = {
        "system_instruction": system_instruction,
        "max_output_tokens": config.get("max_tokens", 4096),
    }
    if config.get("temperature") is not None:
        config_block["temperature"] = config["temperature"]
    if config.get("thinking_budget") is not None:
        config_block["thinking_config"] = {
            "thinking_budget": config["thinking_budget"],
        }

    return {
        "model": config["model_id"],
        "contents": "Execute the task as specified in the system instructions.",
        "config": config_block,
    }


def _extract_finish_reason(response: Any) -> str | None:
    """Lift the first candidate's finish reason out of the response."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    return str(reason) if reason is not None else None


def _calculate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """USD cost from token counts. None for unknown models."""
    return calculate_cost_usd(
        model_id=model_id,
        pricing=PRICING,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

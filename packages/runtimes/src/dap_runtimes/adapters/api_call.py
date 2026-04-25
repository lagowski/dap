"""Direct LLM API adapter — calls Anthropic SDK with rendered XML prompt.

In F3 only `provider: "anthropic"` is supported. OpenAI / Google providers
arrive in F9 alongside CLI adapters.

The rendered XML (output of dap_prompt_dsl) is sent as the `system` message —
it contains role/task/constraints/output-spec which are instructions. The
single user message is "Execute the task as specified."
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Final

import anthropic
from anthropic import AsyncAnthropic
from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.api_call")

ANTHROPIC_API_KEY_ENV: Final = "ANTHROPIC_API_KEY"
ANTHROPIC_PROVIDER: Final = "anthropic"

# USD per 1M tokens (input, output). Pricing as of 2026-04-25 (cached from
# claude-api skill). Cache writes cost 1.25x of input rate; cache reads 0.1x.
ANTHROPIC_PRICING: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

CACHE_WRITE_MULTIPLIER: Final = 1.25
CACHE_READ_MULTIPLIER: Final = 0.10
TOKENS_PER_MILLION: Final = 1_000_000

DEFAULT_USER_MESSAGE: Final = "Execute the task as specified in the system instructions."

VALID_EFFORT: Final = frozenset({"low", "medium", "high", "xhigh", "max"})


def _calculate_cost(
    model_id: str,
    input_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
) -> float | None:
    """Compute USD cost from token counts. Returns None for unknown models."""
    pricing = ANTHROPIC_PRICING.get(model_id)
    if pricing is None:
        return None
    input_rate, output_rate = pricing

    input_cost = input_tokens * input_rate
    cache_creation_cost = cache_creation_tokens * input_rate * CACHE_WRITE_MULTIPLIER
    cache_read_cost = cache_read_tokens * input_rate * CACHE_READ_MULTIPLIER
    output_cost = output_tokens * output_rate

    total_micro = input_cost + cache_creation_cost + cache_read_cost + output_cost
    return total_micro / TOKENS_PER_MILLION


class ApiCallAdapter(BaseAdapter):
    """Direct Anthropic API adapter — single-shot, no tools, no streaming."""

    id = "api-call"
    display_name = "Direct LLM API call (SDK)"
    kind: RuntimeKind = "api"

    async def healthcheck(self) -> HealthStatus:
        if not os.environ.get(ANTHROPIC_API_KEY_ENV):
            return HealthStatus(
                available=False,
                missing=[f"{ANTHROPIC_API_KEY_ENV} env var (Anthropic SDK)"],
            )
        return HealthStatus(available=True, version=f"anthropic-sdk {anthropic.__version__}")

    async def execute(self, task: RuntimeTask) -> RuntimeResult:  # noqa: PLR0911
        # Many returns: each anthropic exception class maps to a distinct
        # error message; collapsing into a dispatch dict obscures the mapping.
        validation_error = _validate_config(task.runtime_config)
        if validation_error is not None:
            return validation_error

        request_kwargs = _build_request_kwargs(task)

        # --- Invoke SDK ---
        start = time.monotonic()
        client = AsyncAnthropic()
        try:
            response = await client.messages.create(**request_kwargs)
        except anthropic.AuthenticationError as exc:
            return _error_result(start, f"Authentication failed: {exc.message}")
        except anthropic.PermissionDeniedError as exc:
            return _error_result(start, f"Permission denied: {exc.message}")
        except anthropic.NotFoundError as exc:
            return _error_result(start, f"Model not found: {exc.message}")
        except anthropic.BadRequestError as exc:
            return _error_result(start, f"Bad request: {exc.message}")
        except anthropic.RateLimitError as exc:
            return _error_result(start, f"Rate limited: {exc.message}")
        except anthropic.APITimeoutError as exc:
            return _error_result(start, f"API timeout: {exc}")
        except anthropic.APIConnectionError as exc:
            return _error_result(start, f"Connection error: {exc}")
        except anthropic.APIStatusError as exc:
            return _error_result(start, f"API error {exc.status_code}: {exc.message}")
        finally:
            await client.close()

        duration_ms = int((time.monotonic() - start) * 1000)

        # --- Extract text from response ---
        text_parts: list[str] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
        output_text = "".join(text_parts)

        # --- Token usage + cost ---
        usage = response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        total_tokens = input_tokens + cache_creation + cache_read + output_tokens

        cost_usd = _calculate_cost(
            model_id=task.runtime_config["model_id"],
            input_tokens=input_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            output_tokens=output_tokens,
        )

        return RuntimeResult(
            success=True,
            output=output_text,
            tokens_used=total_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            errors=[],
            structured={
                "model": response.model,
                "stop_reason": response.stop_reason,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                },
            },
        )


def _validate_config(config: dict[str, Any]) -> RuntimeResult | None:
    """Return a failed RuntimeResult on invalid config, or None when OK."""

    def fail(msg: str) -> RuntimeResult:
        return RuntimeResult(success=False, output="", duration_ms=0, errors=[msg])

    provider = config.get("provider", ANTHROPIC_PROVIDER)
    if provider != ANTHROPIC_PROVIDER:
        return fail(
            f"Provider '{provider}' not supported in F3. "
            f"Only '{ANTHROPIC_PROVIDER}' is supported; openai/google arrive in F9."
        )

    if not os.environ.get(ANTHROPIC_API_KEY_ENV):
        return fail(f"{ANTHROPIC_API_KEY_ENV} env var not set")

    model_id = config.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return fail("runtime_config.model_id is required (e.g. 'claude-haiku-4-5')")

    max_tokens_value = config.get("max_tokens", 4096)
    if not isinstance(max_tokens_value, int) or max_tokens_value <= 0:
        return fail("runtime_config.max_tokens must be a positive int")

    effort = config.get("effort")
    if effort is not None and effort not in VALID_EFFORT:
        return fail(f"runtime_config.effort must be one of {sorted(VALID_EFFORT)}, got '{effort}'")

    return None


def _build_request_kwargs(task: RuntimeTask) -> dict[str, Any]:
    config = task.runtime_config

    user_system_prompt = config.get("system_prompt")
    if isinstance(user_system_prompt, str) and user_system_prompt:
        system_text = f"{user_system_prompt}\n\n{task.prompt_xml}"
    else:
        system_text = task.prompt_xml

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


def _error_result(start: float, message: str) -> RuntimeResult:
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=int((time.monotonic() - start) * 1000),
        errors=[message],
    )

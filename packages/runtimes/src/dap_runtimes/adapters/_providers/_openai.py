"""OpenAI provider — handles ``openai``, ``openai-compat``, and ``glm``.

The OpenAI Python SDK speaks the Chat Completions shape. For the
``openai`` provider we use the default base URL + ``OPENAI_API_KEY``.
For ``openai-compat`` (Together, OpenRouter, llama.cpp servers,
internal proxies, plus any unregistered z.ai-style endpoint) the
agent supplies ``base_url`` and an ``api_key_env`` naming the env
var that holds the key.

``glm`` is the first-class registration of Z.AI's GLM service (#115)
— same SDK shape, but the operator doesn't have to repeat the
``base_url`` + ``api_key_env`` pair on every agent. The constants
below pin them so a one-line ``provider: "glm"`` is enough.
"""

from __future__ import annotations

import os
from typing import Any, Final

import openai
from openai import AsyncOpenAI

from dap_runtimes.adapters._providers._base import ProviderError, ProviderResult

ID: Final = "openai"
ID_COMPAT: Final = "openai-compat"
ID_GLM: Final = "glm"
DEFAULT_ENV_VAR: Final = "OPENAI_API_KEY"
GLM_BASE_URL: Final = "https://api.z.ai/api/coding/paas/v4"
GLM_ENV_VAR: Final = "GLM_API_KEY"

# USD per 1M tokens (input, output). Update as pricing changes.
# Empty for ``openai-compat`` — we don't know third-party prices.
PRICING: Final[dict[str, tuple[float, float]]] = {
    "gpt-5": (2.50, 10.00),
    "gpt-5-codex": (3.00, 12.00),
    "gpt-5-mini": (0.25, 1.00),
    "gpt-5-nano": (0.05, 0.20),
    "o1": (15.00, 60.00),
    "o1-mini": (1.10, 4.40),
    "o3": (10.00, 40.00),
    "o3-mini": (1.10, 4.40),
}

TOKENS_PER_MILLION: Final = 1_000_000
DEFAULT_USER_MESSAGE: Final = "Execute the task as specified in the system instructions."


def env_var_for(config: dict[str, Any]) -> str:
    """Native OpenAI → OPENAI_API_KEY; GLM → GLM_API_KEY; compat → api_key_env.

    The split matters: ``_make_client`` only forwards ``api_key`` to the
    SDK constructor for the compat / glm paths; honoring ``api_key_env``
    for native OpenAI here would let validation pass while the actual
    call still relied on ``OPENAI_API_KEY`` — silently inconsistent.
    """
    provider = config.get("provider")
    if provider == ID_GLM:
        return GLM_ENV_VAR
    if provider == ID_COMPAT:
        custom = config.get("api_key_env")
        if isinstance(custom, str) and custom:
            return custom
    return DEFAULT_ENV_VAR


def validate_config(config: dict[str, Any]) -> str | None:
    """Per-call validation. Returns an error message or None when OK."""
    provider = config.get("provider", ID)

    model_id = config.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return "runtime_config.model_id is required"

    max_tokens = config.get("max_tokens", 4096)
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        return "runtime_config.max_tokens must be a positive int"

    if provider == ID_COMPAT:
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            return (
                "runtime_config.base_url is required for provider='openai-compat' "
                "(e.g. 'https://api.z.ai/api/coding/paas/v4')"
            )
        api_key_env = config.get("api_key_env")
        if not isinstance(api_key_env, str) or not api_key_env:
            return (
                "runtime_config.api_key_env is required for provider='openai-compat' "
                "(name of the env var holding the API key)"
            )

    env_var = env_var_for(config)
    if not os.environ.get(env_var):
        return f"{env_var} env var not set"

    return None


def healthcheck() -> tuple[bool, str | None, list[str] | None]:
    """Adapter-level: do we have what we need for native OpenAI?

    The compat path is per-agent (key + URL come from agent config), so
    healthcheck only reports the canonical case here.
    """
    if not os.environ.get(DEFAULT_ENV_VAR):
        return (False, None, [f"{DEFAULT_ENV_VAR} env var (OpenAI SDK)"])
    return (True, f"openai-sdk {openai.__version__}", None)


async def call(
    provider_id: str,
    config: dict[str, Any],
    prompt_xml: str,
) -> ProviderResult:
    """Chat Completions call. Returns ProviderResult; raises ProviderError."""
    client = _make_client(provider_id, config)
    request_kwargs = _build_request_kwargs(config, prompt_xml)

    try:
        response = await client.chat.completions.create(**request_kwargs)
    except openai.AuthenticationError as exc:
        raise ProviderError(f"Authentication failed: {exc}") from exc
    except openai.PermissionDeniedError as exc:
        raise ProviderError(f"Permission denied: {exc}") from exc
    except openai.NotFoundError as exc:
        raise ProviderError(f"Model not found: {exc}") from exc
    except openai.BadRequestError as exc:
        raise ProviderError(f"Bad request: {exc}") from exc
    except openai.RateLimitError as exc:
        raise ProviderError(f"Rate limited: {exc}") from exc
    except openai.APITimeoutError as exc:
        raise ProviderError(f"API timeout: {exc}") from exc
    except openai.APIConnectionError as exc:
        raise ProviderError(f"Connection error: {exc}") from exc
    except openai.APIStatusError as exc:
        raise ProviderError(f"API error {exc.status_code}: {exc}") from exc
    finally:
        await client.close()

    choice = response.choices[0]
    output_text = choice.message.content or ""

    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0

    # Native OpenAI has cached input tokens reported under
    # `prompt_tokens_details.cached_tokens`; compat servers usually don't.
    cache_read = 0
    if usage is not None:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cache_read = getattr(details, "cached_tokens", 0) or 0

    # Cost only for known native-OpenAI models. Compat → None.
    cost = None
    if provider_id == ID:
        cost = _calculate_cost(config["model_id"], input_tokens, output_tokens)

    return ProviderResult(
        output_text=output_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cost_usd=cost,
        model=response.model,
        stop_reason=choice.finish_reason,
        raw_metadata={
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "cached_tokens": cache_read,
            },
            "provider": provider_id,
        },
    )


def _make_client(provider_id: str, config: dict[str, Any]) -> AsyncOpenAI:
    """Build the SDK client. GLM + compat read a base_url + custom env var."""
    if provider_id == ID_GLM:
        return AsyncOpenAI(
            api_key=os.environ[GLM_ENV_VAR],
            base_url=GLM_BASE_URL,
        )
    if provider_id == ID_COMPAT:
        return AsyncOpenAI(
            api_key=os.environ[config["api_key_env"]],
            base_url=config["base_url"],
        )
    # Native — SDK reads OPENAI_API_KEY itself.
    return AsyncOpenAI()


def _build_request_kwargs(config: dict[str, Any], prompt_xml: str) -> dict[str, Any]:
    """Map per-call config to the Chat Completions request shape."""
    user_system_prompt = config.get("system_prompt")
    if isinstance(user_system_prompt, str) and user_system_prompt:
        system_text = f"{user_system_prompt}\n\n{prompt_xml}"
    else:
        system_text = prompt_xml

    kwargs: dict[str, Any] = {
        "model": config["model_id"],
        "max_tokens": config.get("max_tokens", 4096),
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": DEFAULT_USER_MESSAGE},
        ],
    }
    if config.get("temperature") is not None:
        kwargs["temperature"] = config["temperature"]
    return kwargs


def _calculate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """USD cost from token counts. None for unknown models."""
    pricing = PRICING.get(model_id)
    if pricing is None:
        return None
    input_rate, output_rate = pricing
    return (input_tokens * input_rate + output_tokens * output_rate) / TOKENS_PER_MILLION

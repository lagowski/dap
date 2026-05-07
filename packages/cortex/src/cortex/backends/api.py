"""Direct API backend — HTTP calls to any LLM provider.

For DeepSeek, OpenAI, Anthropic (with API key), or any OpenAI-compatible endpoint.
"""

from __future__ import annotations

import logging

import httpx

from cortex.backends.base import Backend, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

_PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}

# Default model per provider when none is specified
_PROVIDER_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    "deepseek": "deepseek-chat",
    "gemini": "gemini-2.0-flash",
}


class APIBackend(Backend):
    """Direct HTTP API backend for any OpenAI-compatible provider."""

    backend_type = "api"  # type: ignore[assignment]

    def __init__(
        self, provider: str = "deepseek", api_key: str = "", base_url: str = "", model: str = ""
    ):
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url or _PROVIDER_URLS.get(provider, "")
        self.model = model or _PROVIDER_DEFAULT_MODELS.get(provider, "")

    def invoke(self, request: LLMRequest) -> LLMResponse:
        if self.provider == "anthropic":
            return self._invoke_anthropic(request)
        if self.provider == "gemini":
            return self._invoke_gemini(request)
        return self._invoke_openai_compat(request)

    def _invoke_openai_compat(self, request: LLMRequest) -> LLMResponse:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                },
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice.get("message", {}).get("content", ""),
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            backend=f"api:{self.provider}",
            raw=data,
        )

    def _invoke_anthropic(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.user_prompt}],
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{self.base_url}/messages",
                json=payload,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=data.get("model", self.model),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            backend="api:anthropic",
            raw=data,
        )

    def _invoke_gemini(self, request: LLMRequest) -> LLMResponse:
        """Call Google's Gemini API.

        Gemini uses a different format: POST to /models/{model}:generateContent
        with `?key=API_KEY` query param.
        """
        contents = []
        if request.system_prompt:
            contents.append({"role": "user", "parts": [{"text": request.system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": request.user_prompt}]})

        url = f"{self.base_url}/models/{self.model}:generateContent"
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                url,
                params={"key": self.api_key},
                json={
                    "contents": contents,
                    "generationConfig": {
                        "temperature": request.temperature,
                        "maxOutputTokens": request.max_tokens,
                    },
                },
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        content = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            content = "".join(p.get("text", "") for p in parts)

        usage = data.get("usageMetadata", {})
        return LLMResponse(
            content=content,
            model=self.model,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            backend="api:gemini",
            raw=data,
        )

    def health_check(self) -> bool:
        if not self.api_key:
            return False
        if self.provider == "gemini":
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(
                        f"{self.base_url}/models",
                        params={"key": self.api_key},
                    )
                    return resp.status_code == 200
            except Exception:
                return False
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}
                )
                return resp.status_code in (200, 401)
        except Exception:
            return False

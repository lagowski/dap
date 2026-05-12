"""Ollama backend — local LLM via HTTP API.

For Gemma4, Llama, Mistral, etc. running on localhost.
Free, fast, private. Primary backend for issue pipeline and orchestration.
"""

from __future__ import annotations

import logging

import httpx

from cortex.backends.base import Backend, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class OllamaBackend(Backend):
    """Local LLM via Ollama HTTP API."""

    backend_type = "ollama"  # type: ignore[assignment]

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "gemma3:27b"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def invoke(self, request: LLMRequest) -> LLMResponse:
        """Call Ollama's /api/chat endpoint."""
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }

        with httpx.Client(timeout=300.0) as client:
            resp = client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("message", {}).get("content", "")
        eval_count = data.get("eval_count", 0)
        prompt_eval_count = data.get("prompt_eval_count", 0)

        return LLMResponse(
            content=content,
            model=data.get("model", self.model),
            input_tokens=prompt_eval_count,
            output_tokens=eval_count,
            cost_usd=0.0,  # Local = free
            backend="ollama",
            raw=data,
        )

    def health_check(self) -> bool:
        """Check if Ollama is running and model is available."""
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                tags = resp.json()
                models = [m.get("name", "") for m in tags.get("models", [])]
                # Check if our model (or its base name) is available
                base_model = self.model.split(":")[0]
                return any(base_model in m for m in models)
        except Exception as e:
            logger.warning("Ollama health check failed: %s", e)
            return False

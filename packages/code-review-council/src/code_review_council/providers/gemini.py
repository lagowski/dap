"""Google Gen AI (Gemini) provider implementation.

Uses the current ``google-genai`` SDK (not the deprecated
``google-generativeai``). Structured output via
``response_mime_type='application/json' + response_json_schema=...`` —
same pattern the previous single-agent script used.

Opts into ``ThinkingConfig(thinking_level=HIGH)`` for Gemini 3.x
models because the narrow-scope agent prompts benefit a lot from the
extra reasoning step. Older models silently reject the field, so the
flag is gated on the model name string.
"""

from __future__ import annotations

from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_TEMPERATURE = 0.2


class GeminiProvider:
    """Concrete provider that drives Google's Gemini models.

    Single-threaded: each call to ``run_structured`` is a blocking
    HTTP request to the Gemini API. The Council orchestrator runs
    agents sequentially today, so the simplicity is intentional;
    async batching is a follow-up when the council grows to 5+ agents.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.5-flash",
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiProvider requires a non-empty api_key")
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._temperature = temperature

    @property
    def name(self) -> str:
        """Used for logging / attribution in rendered review bodies."""
        return self._model

    def run_structured(
        self,
        *,
        system_instruction: str,
        user_content: str,
        response_schema: type[T],
    ) -> T:
        config_kwargs: dict[str, object] = {
            "system_instruction": system_instruction,
            "response_mime_type": "application/json",
            "response_json_schema": response_schema.model_json_schema(),
            "temperature": self._temperature,
        }
        # Gemini 3.x exposes ``thinking_config``. Older models 400 on it,
        # so we gate by model name. The HIGH level matters for strict
        # reviewer prompts — without it the agent skims the diff.
        if self._model.startswith("gemini-3"):
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.HIGH,
            )

        response = self._client.models.generate_content(
            model=self._model,
            contents=user_content,
            config=types.GenerateContentConfig(**config_kwargs),  # type: ignore[arg-type]
        )
        if not response.text:
            raise RuntimeError(
                f"Gemini ({self._model}) returned an empty response body",
            )
        return response_schema.model_validate_json(response.text)

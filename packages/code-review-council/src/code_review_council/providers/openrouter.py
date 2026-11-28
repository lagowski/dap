"""OpenRouter provider — one API surface for Claude / Gemini / DeepSeek / GPT.

Why OpenRouter alongside the Gemini-direct provider:

- Speecher's experience: a multi-model OpenRouter setup found bugs that
  Gemini alone missed on the same diffs. Different families of model
  catch different classes of issue, and the council benefits from
  swapping providers per agent (Security via Claude, Correctness via
  DeepSeek-V4-Pro, etc.) without rewriting any agent code.
- Single API key for every model the agent stack might want. No
  separate Anthropic / OpenAI keys, no SDK juggling.
- Cost: deepseek-v4-pro is ~$0.04-0.06 per PR review (5 agents +
  arbiter); claude-sonnet-4.6 around $0.10-0.20. AI Studio's free
  tier on Gemini is $0 but caps at ~250 PRs/day and quality plateaus.

The implementation is a thin httpx call to OpenRouter's OpenAI-
compatible Chat Completions endpoint. We ask for ``response_format:
{type: "json_object"}`` and embed the Pydantic-generated JSON schema
in the system prompt — every model on OpenRouter supports that shape
even when it doesn't support the stricter ``json_schema`` format.
Pydantic then validates the returned JSON, raising on shape drift.
"""

from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
DEFAULT_TEMPERATURE = 0.2
# Generous: a single agent's response is small (a few findings + a
# summary), but the input includes the full diff (up to 60k chars
# of context) so the round-trip can take 30-60s on slower models.
DEFAULT_TIMEOUT_S = 120.0
# OpenRouter recommends sending these so they can attribute traffic
# and surface helpful errors. Not required but considered polite.
APP_TITLE = "DAP Code Review Council"
APP_URL = "https://github.com/lagowski/dap"


class OpenRouterProvider:
    """Concrete provider driving any OpenRouter-hosted model.

    The model name is the OpenRouter identifier (``deepseek/deepseek-
    v4-pro``, ``anthropic/claude-sonnet-4.6``, ``openai/gpt-5.4``,
    etc.). Switching providers / model families is a one-string
    change in the workflow's env vars — no code touches the agents.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouterProvider requires a non-empty api_key")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._timeout_s = timeout_s

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
        # Embed the JSON schema in the system prompt — OpenRouter's
        # ``response_format: json_object`` guarantees valid JSON back
        # but does NOT enforce a schema. The model adheres to the
        # schema only because we tell it to in plain English. Pydantic
        # validation catches any drift.
        schema_text = json.dumps(
            response_schema.model_json_schema(),
            indent=2,
            ensure_ascii=False,
        )
        composed_system = (
            f"{system_instruction}\n\n"
            "Output: return ONLY a JSON object matching this schema. "
            "No prose, no Markdown fences, no leading commentary — "
            "just the JSON object.\n\n"
            f"```json\n{schema_text}\n```"
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": composed_system},
                {"role": "user", "content": user_content},
            ],
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # OpenRouter uses these to attribute traffic; they appear
            # on the dashboard's per-app analytics view.
            "HTTP-Referer": APP_URL,
            "X-Title": APP_TITLE,
        }

        with httpx.Client(timeout=self._timeout_s) as client:
            response = client.post(OPENROUTER_API_URL, json=payload, headers=headers)
            # Catch HTTP errors here so we can scrub the response body
            # before re-raising. Some OpenAI-compatible gateways echo
            # the request's Authorization header (or its prefix) into
            # error responses; an uncaught ``HTTPStatusError`` would
            # propagate the full body to the workflow's exception
            # handler, which writes it to a check_run summary visible
            # to anyone with PR-read access. Redact instead.
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"OpenRouter HTTP {response.status_code} (body redacted)",
                ) from exc
            # ``response.json()`` raises ``httpx.DecodingError`` (a
            # subclass of ``json.JSONDecodeError``) when a 200-OK body
            # isn't valid JSON. Same leak class as HTTPStatusError —
            # the default exception ``__str__`` may include the body
            # snippet that failed to parse. Redact identically.
            try:
                body = response.json()
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"OpenRouter HTTP {response.status_code} returned "
                    f"non-JSON body (content redacted)",
                ) from exc

        # OpenRouter follows the OpenAI shape: ``choices[0].message.content``.
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            # Don't include the raw body in the error message — same
            # concern as ``HTTPStatusError`` above. Show the top-level
            # keys so a debugger can still see whether OpenRouter sent
            # something OpenAI-shaped at all without dumping potentially
            # sensitive content.
            top_keys = sorted(body.keys()) if isinstance(body, dict) else type(body).__name__
            raise RuntimeError(
                f"OpenRouter response missing choices/message (top-level keys: {top_keys})",
            ) from exc

        if not content or not content.strip():
            raise RuntimeError(
                f"OpenRouter ({self._model}) returned an empty content body",
            )

        # ``json_object`` guarantees the body is JSON, but some models
        # still wrap it in a markdown fence — strip leading/trailing
        # fences defensively before Pydantic gets it.
        cleaned = content.strip()
        if cleaned.startswith("```"):
            # Drop the opening fence (with optional ``json`` tag) and the
            # closing one. Conservative — bail if structure looks wrong.
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        return response_schema.model_validate_json(cleaned)

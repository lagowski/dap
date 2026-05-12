"""Gemini CLI runtime adapter.

Spawns Google's ``gemini`` binary, pipes the rendered XML through stdin,
and parses the structured JSON response (output text + token usage).

Use cases vs the api-call (Gemini) provider:

| Need                                              | Use this     |
| ------------------------------------------------- | ------------ |
| Single-shot LLM call, no tools, deterministic    | ``api-call`` |
| Agentic CLI loop (tools, file edits, streaming)  | ``gemini-cli`` |

Process lifecycle is the same as ``claude-code`` and ``bash`` —
``start_new_session=True`` so a SIGKILL on timeout/cancel reaches the
whole process group; ``asyncio.shield(process.wait())`` reaps zombies.

Security model: same as ``bash`` (single-user, local-trust). The CLI
runs with the engine's privileges and reads ``GEMINI_API_KEY`` (or
``GOOGLE_API_KEY``) from the process environment itself.

CLI version drift: the response shape can shift between gemini-cli
releases. We extract token counts via fallbacks and tolerate missing
fields rather than crash; the raw payload is preserved in
``RuntimeResult.structured.payload`` so users can debug surprises.
"""

from __future__ import annotations

from typing import Any, Final

from dap_types import RuntimeResult

from dap_runtimes.adapters._cli_base import (
    SubprocessOutcome,
    _BaseCliAdapter,
    _first_int,
    _first_string,
)

# Field-name fallbacks for token counts — gemini-cli has shifted the
# JSON shape across releases. Both Pydantic-style snake_case (used by
# the SDK) and camelCase (some CLI builds) are checked.
_INPUT_TOKEN_KEYS: Final = ("prompt_token_count", "promptTokenCount", "input_tokens")
_OUTPUT_TOKEN_KEYS: Final = (
    "candidates_token_count",
    "candidatesTokenCount",
    "output_tokens",
)
_CACHE_TOKEN_KEYS: Final = (
    "cached_content_token_count",
    "cachedContentTokenCount",
    "cache_read_input_tokens",
)
_RESPONSE_TEXT_KEYS: Final = ("response", "text", "result", "output")


class GeminiCliAdapter(_BaseCliAdapter):
    """Runs the gemini CLI in JSON-output mode, captures structured result."""

    id = "gemini-cli"
    display_name = "Gemini CLI"

    default_binary = "gemini"
    provider_name = "gemini-cli"
    display_label = "Gemini"
    install_hint = "install Google Gemini CLI"

    def _validate_config(self, config: dict[str, Any]) -> str | None:
        # Auth deliberately not validated — gemini-cli accepts
        # GEMINI_API_KEY/GOOGLE_API_KEY *or* a stored OAuth session
        # (``gemini auth login``). Letting the CLI fail with its own
        # ``not authenticated`` message keeps both paths symmetric.
        model_id = config.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            return "runtime_config.model_id is required (e.g. 'gemini-3.0-pro')"

        binary_path = config.get("binary_path")
        if binary_path is not None and not isinstance(binary_path, str):
            return "runtime_config.binary_path must be a string"

        thinking_budget = config.get("thinking_budget")
        if thinking_budget is not None and (
            not isinstance(thinking_budget, int) or thinking_budget < 0
        ):
            return "runtime_config.thinking_budget must be a non-negative int"

        return None

    def _build_argv(
        self,
        binary: str,
        config: dict[str, Any],
        extra_args: list[str],
    ) -> list[str]:
        argv: list[str] = [
            binary,
            "-m",
            config["model_id"],
            "-o",
            "json",
        ]
        thinking_budget = config.get("thinking_budget")
        if isinstance(thinking_budget, int) and thinking_budget > 0:
            argv.extend(["--thinking-budget", str(thinking_budget)])
        argv.extend(extra_args)
        return argv

    def _parse_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
        outcome: SubprocessOutcome,
    ) -> RuntimeResult:
        output_text = _first_string(payload, _RESPONSE_TEXT_KEYS) or ""

        usage = payload.get("usage_metadata") or payload.get("usage") or {}
        if not isinstance(usage, dict):
            return self._failed(
                "Gemini CLI returned invalid token metadata: 'usage' must be an object",
                duration_ms=outcome.duration_ms,
                model_id=config["model_id"],
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )

        try:
            input_tokens = _first_int(usage, _INPUT_TOKEN_KEYS)
            output_tokens = _first_int(usage, _OUTPUT_TOKEN_KEYS)
            cache_read = _first_int(usage, _CACHE_TOKEN_KEYS)
        except (TypeError, ValueError) as exc:
            return self._failed(
                f"Gemini CLI returned invalid token metadata: {exc}",
                duration_ms=outcome.duration_ms,
                model_id=config["model_id"],
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )
        total_tokens = input_tokens + output_tokens + cache_read

        return RuntimeResult(
            success=True,
            output=output_text,
            tokens_used=total_tokens,
            cost_usd=None,  # CLI doesn't report it; api-call provider has pricing.
            duration_ms=outcome.duration_ms,
            errors=[],
            structured={
                "provider": "gemini-cli",
                "model": config["model_id"],
                "stop_reason": payload.get("finish_reason") or payload.get("finishReason"),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                },
                "exit_code": outcome.exit_code,
                "timed_out": False,
                # Whole payload preserved so users can debug shape drift
                # across gemini-cli releases.
                "payload": payload,
            },
        )


__all__: Final = ["GeminiCliAdapter"]

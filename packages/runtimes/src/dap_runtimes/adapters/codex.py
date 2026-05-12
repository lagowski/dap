"""OpenAI Codex CLI runtime adapter.

Spawns the ``codex`` binary in non-interactive mode (``exec``), pipes
the rendered XML through stdin, and parses the structured JSON
response. Mirrors the ``claude-code`` and ``gemini-cli`` adapters.

Use cases vs the api-call (OpenAI) provider:

| Need                                              | Use this    |
| ------------------------------------------------- | ----------- |
| Single-shot LLM call, no tools, deterministic    | ``api-call`` |
| Agentic loop with file edits, bash, MCP tools    | ``codex``   |

Process lifecycle is the same as ``bash`` — POSIX session group +
``asyncio.shield`` cleanup + ``CancelledError`` teardown — so
engine-level pause/abort doesn't leak processes.

Security model: same as ``bash`` (single-user, local-trust). The CLI
runs with the engine's privileges, can edit files in
``task.working_directory``, and reads ``OPENAI_API_KEY`` from the
process environment itself.

CLI version drift: OpenAI's Codex CLI has changed shape across
releases. We extract token counts via field-name fallbacks (snake_case
+ camelCase + alternative names) and tolerate missing fields rather
than crash; the raw payload lives in ``RuntimeResult.structured.payload``
so users can debug surprises.
"""

from __future__ import annotations

import os
from typing import Any, Final

from dap_types import HealthStatus, RuntimeResult

from dap_runtimes.adapters._cli_base import (
    SubprocessOutcome,
    _BaseCliAdapter,
    _first_int,
    _first_string,
)

ENV_API_KEY: Final = "OPENAI_API_KEY"

# Field-name fallbacks for token counts. Codex CLI's JSON shape has
# shifted between releases (snake_case from OpenAI's Python SDK,
# camelCase from older builds, "input_tokens" / "prompt_tokens"
# spelling variants).
_INPUT_TOKEN_KEYS: Final = ("input_tokens", "prompt_tokens", "promptTokens")
_OUTPUT_TOKEN_KEYS: Final = ("output_tokens", "completion_tokens", "completionTokens")
_RESPONSE_TEXT_KEYS: Final = ("output_text", "result", "response", "text", "output")


class CodexAdapter(_BaseCliAdapter):
    """Runs the codex CLI in JSON-output mode, captures structured result."""

    id = "codex"
    display_name = "OpenAI Codex CLI"

    default_binary = "codex"
    provider_name = "codex"
    display_label = "Codex"
    install_hint = "install: https://github.com/openai/codex"

    async def healthcheck(self) -> HealthStatus:
        # Codex requires OPENAI_API_KEY to do anything useful — surface
        # its absence in the healthcheck so operators aren't surprised
        # at execute() time. Defer binary lookup + version probe to the
        # base class.
        base = await super().healthcheck()
        if not base.available:
            return base
        if not os.environ.get(ENV_API_KEY):
            return HealthStatus(
                available=False,
                missing=[f"{ENV_API_KEY} env var (Codex CLI reads it directly)"],
            )
        return base

    def _validate_config(self, config: dict[str, Any]) -> str | None:
        model_id = config.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            return "runtime_config.model_id is required (e.g. 'gpt-5-codex')"

        binary_path = config.get("binary_path")
        if binary_path is not None and not isinstance(binary_path, str):
            return "runtime_config.binary_path must be a string"

        if not os.environ.get(ENV_API_KEY):
            return f"{ENV_API_KEY} env var not set (Codex CLI reads it directly)"

        return None

    def _build_argv(
        self,
        binary: str,
        config: dict[str, Any],
        extra_args: list[str],
    ) -> list[str]:
        return [
            binary,
            "exec",
            "--json",
            "--model",
            config["model_id"],
            *extra_args,
        ]

    def _parse_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
        outcome: SubprocessOutcome,
    ) -> RuntimeResult:
        output_text = _first_string(payload, _RESPONSE_TEXT_KEYS) or ""

        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            return self._failed(
                "Codex CLI returned invalid token metadata: 'usage' must be an object",
                duration_ms=outcome.duration_ms,
                model_id=config["model_id"],
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )

        try:
            input_tokens = _first_int(usage, _INPUT_TOKEN_KEYS)
            output_tokens = _first_int(usage, _OUTPUT_TOKEN_KEYS)
        except (TypeError, ValueError) as exc:
            return self._failed(
                f"Codex CLI returned invalid token metadata: {exc}",
                duration_ms=outcome.duration_ms,
                model_id=config["model_id"],
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )
        total_tokens = input_tokens + output_tokens

        return RuntimeResult(
            success=True,
            output=output_text,
            tokens_used=total_tokens,
            cost_usd=None,  # CLI doesn't report cost; api-call has pricing tables.
            duration_ms=outcome.duration_ms,
            errors=[],
            structured={
                "provider": "codex",
                "model": config["model_id"],
                "stop_reason": payload.get("finish_reason") or payload.get("finishReason"),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
                "exit_code": outcome.exit_code,
                "timed_out": False,
                # Whole payload preserved so users can debug shape drift
                # across codex CLI releases.
                "payload": payload,
            },
        )


__all__: Final = ["CodexAdapter"]

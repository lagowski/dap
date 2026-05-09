"""Claude Code CLI runtime adapter.

Spawns the ``claude`` binary in non-interactive mode (``--print``) with
the rendered XML piped to stdin and ``--output-format json`` so we can
parse a structured response (text + token usage + cost).

Use cases vs the api-call (Anthropic) provider:

| Need                                           | Use this    |
| ---------------------------------------------- | ----------- |
| Single-shot LLM call, no tools, deterministic | ``api-call`` |
| Agentic loop with file edits, bash, MCP tools | ``claude-code`` |

This adapter is 10-100x more expensive per task than ``api-call`` but
does work the SDK can't (real coding agent loop). Pipelines compose:
cheap ``api-call`` for selection / planning, ``claude-code`` for
implementation.

Process lifecycle is the same as the ``bash`` adapter — POSIX session
group + ``asyncio.shield`` cleanup + ``CancelledError`` teardown — so
engine-level pause/abort doesn't leak processes.

Security model: same as ``bash`` (single-user, local-trust). The CLI
runs with the engine's privileges, can edit files in
``task.working_directory``, and reads ``ANTHROPIC_API_KEY`` from the
process environment itself.
"""

from __future__ import annotations

from typing import Any, Final

from dap_types import RuntimeResult

from dap_runtimes.adapters._cli_base import (
    SubprocessOutcome,
    _BaseCliAdapter,
)


class ClaudeCodeAdapter(_BaseCliAdapter):
    """Runs the Claude Code CLI in print mode + JSON output, captures structured result."""

    id = "claude-code"
    display_name = "Claude Code CLI"

    default_binary = "claude"
    provider_name = "claude-code"
    display_label = "Claude"
    install_hint = "install: https://docs.claude.com/claude-code"

    def _validate_config(self, config: dict[str, Any]) -> str | None:
        # Auth deliberately not validated — Claude Code accepts either
        # ANTHROPIC_API_KEY in env *or* a stored OAuth session from
        # ``claude login`` (Pro/Max plans). Letting the CLI surface its
        # own ``not authenticated`` message keeps both paths symmetric.
        model_id = config.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            return "runtime_config.model_id is required (e.g. 'claude-opus-4-7')"

        binary_path = config.get("binary_path")
        if binary_path is not None and not isinstance(binary_path, str):
            return "runtime_config.binary_path must be a string"

        return None

    def _build_argv(
        self,
        binary: str,
        config: dict[str, Any],
        extra_args: list[str],
    ) -> list[str]:
        return [
            binary,
            "--print",
            "--output-format",
            "json",
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
        if payload.get("is_error") is True:
            return self._failed(
                f"Claude CLI reported an error: {payload.get('result') or 'unknown'}",
                duration_ms=outcome.duration_ms,
                model_id=config["model_id"],
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )

        output_text = payload.get("result") or ""

        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            return self._failed(
                "Claude CLI returned invalid token metadata: 'usage' must be an object",
                duration_ms=outcome.duration_ms,
                model_id=config["model_id"],
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )

        try:
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
            cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        except (TypeError, ValueError) as exc:
            return self._failed(
                f"Claude CLI returned invalid token metadata: {exc}",
                duration_ms=outcome.duration_ms,
                model_id=config["model_id"],
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )
        total_tokens = input_tokens + output_tokens + cache_creation + cache_read

        cost_raw = payload.get("total_cost_usd")
        cost: float | None = float(cost_raw) if isinstance(cost_raw, (int, float)) else None

        return RuntimeResult(
            success=True,
            output=output_text,
            tokens_used=total_tokens,
            cost_usd=cost,
            duration_ms=outcome.duration_ms,
            errors=[],
            structured={
                "provider": "claude-code",
                "model": config["model_id"],
                "session_id": payload.get("session_id"),
                "num_turns": payload.get("num_turns"),
                "stop_reason": payload.get("stop_reason"),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                },
                "exit_code": outcome.exit_code,
                "timed_out": False,
            },
        )


# Re-export so test patches at ``dap_runtimes.adapters.claude_code._BaseCliAdapter`` (if any)
# still resolve. Existing tests patch ``asyncio.create_subprocess_exec`` and
# ``shutil.which`` at the ``_cli_base`` module path.
__all__: Final = ["ClaudeCodeAdapter"]

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

import json
from typing import Any, Final

from dap_types import RuntimeResult

from dap_runtimes.adapters._cli_base import (
    SubprocessOutcome,
    _BaseCliAdapter,
)

# Marker keys placed on the decoded payload when stream-json yields no
# ``result`` event. ``--print`` mode treats this as a parse failure;
# subscription mode (#625) reads the raw stdout as the agent output.
_NO_RESULT_KEY: Final = "_no_result"
_RAW_STDOUT_KEY: Final = "_raw_stdout"


def _use_subscription(config: dict[str, Any]) -> bool:
    """Whether the run opts into subscription (non-``--print``) mode (#625).

    Defaults to ``False`` (the metered ``--print`` path) so existing
    behaviour is unchanged unless ``runtime_config.use_subscription`` is
    explicitly ``True``. Validation in ``_validate_config`` guarantees the
    value is a real ``bool`` by the time this runs.
    """
    return config.get("use_subscription") is True


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

        # ``use_subscription`` is optional and defaults to False. When set
        # it must be a real boolean — a stray "true"/1 would silently take
        # the wrong branch, so reject anything else up front. (#625)
        use_subscription = config.get("use_subscription")
        if use_subscription is not None and not isinstance(use_subscription, bool):
            return "runtime_config.use_subscription must be a boolean"

        return None

    def _build_argv(
        self,
        binary: str,
        config: dict[str, Any],
        extra_args: list[str],
    ) -> list[str]:
        if _use_subscription(config):
            # Subscription (Pro/Max) mode (#625): after 2026-06-15 Anthropic
            # moves ``--print`` programmatic usage onto a metered $200/month
            # Agent SDK credit. Dropping ``--print`` keeps runs on the
            # unmetered subscription pool instead. The prompt is still piped
            # via stdin (see ``_BaseCliAdapter._run_subprocess``) and stdin is
            # closed after the write, so the otherwise-interactive CLI reads
            # the prompt and exits rather than blocking for input.
            # ``--dangerously-skip-permissions`` auto-approves tool use so the
            # run never stalls on a permission prompt — matching the manual
            # ``echo prompt | claude --dangerously-skip-permissions`` pattern.
            return [
                binary,
                "--dangerously-skip-permissions",
                "--model",
                config["model_id"],
                *extra_args,
            ]
        return [
            binary,
            "--print",
            "--output-format",
            "stream-json",  # NDJSON — reliably includes usage per turn (#472)
            "--model",
            config["model_id"],
            *extra_args,
        ]

    def _decode_stdout(self, stdout: str) -> dict[str, Any]:
        """Find the ``result`` event in stream-json NDJSON output.

        ``stream-json`` emits one JSON object per line (assistant turns,
        tool calls, etc.). The final ``{"type": "result", ...}`` event
        carries the aggregated output, token usage, and cost. Non-result
        lines are skipped so stray malformed lines from tool output don't
        abort the parse.

        When no result event is found, returns a marker payload
        (``{_NO_RESULT_KEY: True, ...}``) rather than raising. ``--print``
        mode treats that as a failure (the structured response is missing);
        subscription mode (#625) expects it — without ``--print`` the CLI
        emits plain text, not stream-json — and reads the raw stdout as the
        agent output. The decode hook has no access to ``runtime_config``,
        so the print-vs-subscription decision lives in ``_parse_payload``.
        """
        result_event: dict[str, Any] | None = None
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result_event = event
        if result_event is None:
            return {_NO_RESULT_KEY: True, _RAW_STDOUT_KEY: stdout}
        return result_event

    def _parse_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
        outcome: SubprocessOutcome,
    ) -> RuntimeResult:
        if _use_subscription(config):
            return self._parse_subscription(payload, config, outcome)

        if payload.get(_NO_RESULT_KEY):
            return self._failed(
                "Could not parse Claude CLI output as JSON: "
                "No result event found in stream-json output",
                duration_ms=outcome.duration_ms,
                model_id=config["model_id"],
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )

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

    def _parse_subscription(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
        outcome: SubprocessOutcome,
    ) -> RuntimeResult:
        """Build a result for subscription (non-``--print``) mode (#625).

        Without ``--print`` the CLI emits plain text rather than
        stream-json, so there is no token usage or cost to extract — the
        run is billed against the flat-rate Pro/Max subscription, not
        metered per call. The agent output is the raw stdout. ``cost_usd``
        is ``None`` and ``tokens_used`` is left unset (0) to signal "not
        metered" rather than "zero cost".
        """
        # If a result event *did* appear (e.g. a future CLI build still
        # emits one without ``--print``), prefer its ``result`` text;
        # otherwise fall back to the raw stdout captured at decode time.
        if payload.get(_NO_RESULT_KEY):
            output_text = payload.get(_RAW_STDOUT_KEY) or ""
        else:
            output_text = payload.get("result") or ""

        return RuntimeResult(
            success=True,
            output=output_text,
            cost_usd=None,  # subscription mode is not metered per-call
            duration_ms=outcome.duration_ms,
            errors=[],
            structured={
                "provider": "claude-code",
                "model": config["model_id"],
                "subscription": True,
                "exit_code": outcome.exit_code,
                "timed_out": False,
            },
        )


# Re-export so test patches at ``dap_runtimes.adapters.claude_code._BaseCliAdapter`` (if any)
# still resolve. Existing tests patch ``asyncio.create_subprocess_exec`` and
# ``shutil.which`` at the ``_cli_base`` module path.
__all__: Final = ["ClaudeCodeAdapter"]

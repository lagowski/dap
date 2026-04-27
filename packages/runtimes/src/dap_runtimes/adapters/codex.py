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

import asyncio
import json
import logging
import os
import shutil
import signal
import time
from typing import Any, Final

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.codex")

DEFAULT_BINARY: Final = "codex"
ENV_API_KEY: Final = "OPENAI_API_KEY"
MS_PER_SECOND: Final = 1000

# Field-name fallbacks for token counts. Codex CLI's JSON shape has
# shifted between releases (snake_case from OpenAI's Python SDK,
# camelCase from older builds, "input_tokens" / "prompt_tokens"
# spelling variants).
_INPUT_TOKEN_KEYS: Final = ("input_tokens", "prompt_tokens", "promptTokens")
_OUTPUT_TOKEN_KEYS: Final = ("output_tokens", "completion_tokens", "completionTokens")
_RESPONSE_TEXT_KEYS: Final = ("output_text", "result", "response", "text", "output")


class CodexAdapter(BaseAdapter):
    """Runs the codex CLI in JSON-output mode, captures structured result."""

    id = "codex"
    display_name = "OpenAI Codex CLI"
    kind: RuntimeKind = "cli"

    async def healthcheck(self) -> HealthStatus:
        binary = _resolve_binary({})
        if shutil.which(binary) is None:
            return HealthStatus(
                available=False,
                missing=[f"{binary} binary on PATH (install: https://github.com/openai/codex)"],
            )
        if not os.environ.get(ENV_API_KEY):
            return HealthStatus(
                available=False,
                missing=[f"{ENV_API_KEY} env var (Codex CLI reads it directly)"],
            )
        version = await _read_cli_version(binary)
        return HealthStatus(available=True, version=version)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:  # noqa: PLR0911
        # Many returns: each guard maps to a distinct precondition failure
        # with its own error message.
        config = task.runtime_config

        validation_error = _validate_config(config)
        if validation_error is not None:
            return _failed(validation_error, duration_ms=0)

        binary = _resolve_binary(config)
        if shutil.which(binary) is None:
            return _failed(
                f"Codex binary not found: {binary}",
                duration_ms=0,
                model_id=config.get("model_id"),
            )

        extra_args, extra_args_error = _normalise_extra_args(config.get("extra_args"))
        if extra_args_error is not None:
            return _failed(extra_args_error, duration_ms=0)

        argv: list[str] = [
            binary,
            "exec",
            "--json",
            "--model",
            config["model_id"],
            *extra_args,
        ]

        cwd = task.working_directory or os.getcwd()
        timeout_seconds = max(task.timeout_ms, 1) / MS_PER_SECOND
        new_session = hasattr(os, "setsid")

        start = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=new_session,
            )
        except FileNotFoundError as exc:
            return _failed(
                f"Codex binary not found: {binary} ({exc})",
                duration_ms=_elapsed_ms(start),
                model_id=config["model_id"],
            )
        except OSError as exc:
            return _failed(
                f"Failed to start subprocess: {exc}",
                duration_ms=_elapsed_ms(start),
                model_id=config["model_id"],
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=task.prompt_xml.encode("utf-8")),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            await _kill_process_tree(process, new_session)
            return _failed(
                f"Command timed out after {task.timeout_ms}ms",
                duration_ms=_elapsed_ms(start),
                model_id=config["model_id"],
                timed_out=True,
            )
        except asyncio.CancelledError:
            await _kill_process_tree(process, new_session)
            raise

        duration_ms = _elapsed_ms(start)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = process.returncode if process.returncode is not None else -1

        if exit_code != 0:
            preview = stderr.strip().splitlines()[-5:]
            message = f"Codex CLI exited with code {exit_code}" + (
                ": " + " | ".join(preview) if preview else ""
            )
            return _failed(
                message,
                duration_ms=duration_ms,
                model_id=config["model_id"],
                exit_code=exit_code,
                stderr=stderr,
            )

        # Parse structured JSON response.
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return _failed(
                f"Could not parse Codex CLI output as JSON: {exc.msg}",
                duration_ms=duration_ms,
                model_id=config["model_id"],
                exit_code=exit_code,
                stderr=stderr,
            )

        if not isinstance(payload, dict):
            return _failed(
                "Codex CLI returned unexpected JSON shape: "
                f"expected object, got {type(payload).__name__}",
                duration_ms=duration_ms,
                model_id=config["model_id"],
                exit_code=exit_code,
                stderr=stderr,
            )

        output_text = _first_string(payload, _RESPONSE_TEXT_KEYS) or ""

        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            return _failed(
                "Codex CLI returned invalid token metadata: 'usage' must be an object",
                duration_ms=duration_ms,
                model_id=config["model_id"],
                exit_code=exit_code,
                stderr=stderr,
            )

        try:
            input_tokens = _first_int(usage, _INPUT_TOKEN_KEYS)
            output_tokens = _first_int(usage, _OUTPUT_TOKEN_KEYS)
        except (TypeError, ValueError) as exc:
            return _failed(
                f"Codex CLI returned invalid token metadata: {exc}",
                duration_ms=duration_ms,
                model_id=config["model_id"],
                exit_code=exit_code,
                stderr=stderr,
            )
        total_tokens = input_tokens + output_tokens

        return RuntimeResult(
            success=True,
            output=output_text,
            tokens_used=total_tokens,
            cost_usd=None,  # CLI doesn't report cost; api-call has pricing tables.
            duration_ms=duration_ms,
            errors=[],
            structured={
                "provider": "codex",
                "model": config["model_id"],
                "stop_reason": payload.get("finish_reason") or payload.get("finishReason"),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
                "exit_code": exit_code,
                "timed_out": False,
                # Whole payload preserved so users can debug shape drift
                # across codex CLI releases.
                "payload": payload,
            },
        )


def _resolve_binary(config: dict[str, Any]) -> str:
    """Pick the binary path: runtime_config.binary_path → DEFAULT_BINARY."""
    explicit = config.get("binary_path")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    return DEFAULT_BINARY


def _validate_config(config: dict[str, Any]) -> str | None:
    """Per-call validation. Returns an error message or None when OK."""
    model_id = config.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return "runtime_config.model_id is required (e.g. 'gpt-5-codex')"

    binary_path = config.get("binary_path")
    if binary_path is not None and not isinstance(binary_path, str):
        return "runtime_config.binary_path must be a string"

    if not os.environ.get(ENV_API_KEY):
        return f"{ENV_API_KEY} env var not set (Codex CLI reads it directly)"

    return None


def _normalise_extra_args(value: Any) -> tuple[list[str], str | None]:
    """Coerce extra_args into a list of strings, or return a descriptive error."""
    if value is None:
        return ([], None)
    if not isinstance(value, list):
        return ([], "runtime_config.extra_args must be a list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return (
                [],
                "runtime_config.extra_args must be a list of strings; "
                f"got element of type {type(item).__name__}",
            )
        out.append(item)
    return (out, None)


def _first_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the first key in `keys` whose value is a non-empty string."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int:
    """Return the first key in `keys` whose value coerces to int; default 0.

    Raises (TypeError, ValueError) only when a present key has a value that
    can't coerce — propagated by the caller to a descriptive _failed().
    """
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value is None:
            continue
        return int(value)
    return 0


async def _read_cli_version(binary: str) -> str | None:
    """Best-effort ``codex --version`` for the healthcheck display string.

    Kills + reaps the subprocess on timeout so a hanging CLI doesn't leak
    a child process every time the operator runs a healthcheck.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except OSError:
            logger.exception("failed to kill timed-out version check pid=%s", process.pid)
        try:
            await asyncio.shield(process.wait())
        except (asyncio.CancelledError, Exception):
            logger.exception("error waiting for timed-out version check pid=%s", process.pid)
        return None

    line = stdout.decode("utf-8", errors="replace").strip().splitlines()
    return line[0] if line else None


async def _kill_process_tree(
    process: asyncio.subprocess.Process,
    new_session: bool,
) -> None:
    """SIGKILL the process group (POSIX) or single process (Windows). Reap zombies."""
    if process.returncode is not None:
        return  # already exited

    try:
        if new_session and hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError):
        # Race: process exited between check and signal — fine.
        pass
    except OSError:
        logger.exception("failed to signal subprocess pid=%s", process.pid)
        process.kill()  # best-effort fallback

    try:
        await asyncio.shield(process.wait())
    except (asyncio.CancelledError, Exception):
        logger.exception("error waiting for killed subprocess")


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * MS_PER_SECOND)


def _failed(
    message: str,
    *,
    duration_ms: int,
    model_id: str | None = None,
    exit_code: int | None = None,
    stderr: str = "",
    timed_out: bool = False,
) -> RuntimeResult:
    structured: dict[str, Any] = {
        "provider": "codex",
        "timed_out": timed_out,
    }
    if model_id is not None:
        structured["model"] = model_id
    if exit_code is not None:
        structured["exit_code"] = exit_code
    if stderr:
        structured["stderr"] = stderr
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=duration_ms,
        errors=[message],
        structured=structured,
    )

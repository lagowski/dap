"""Shell command runtime adapter — runs a single command in a working directory.

Use cases: running tests (`pytest`), git operations, file mutations driven by
deterministic logic rather than an LLM. Agents using this runtime supply the
command via `runtime_config.command` (preferred) or by emitting a `<command>`
block in their prompt template — the latter lets a templated agent compute
the command from PipelineState fields.

Security model (v0.1): single-user, local-trust. The command runs with the
engine's privileges in the configured working directory; there is NO
sandbox, network restriction, or filesystem confinement. Multi-user setups
must wait for #38 (auth) plus a sandboxing layer before exposing this
runtime to untrusted agent definitions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from typing import Any, Final

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.bash")

DEFAULT_SHELL: Final = "/bin/bash"
DEFAULT_TIMEOUT_MS: Final = 60_000
MS_PER_SECOND: Final = 1000
COMMAND_TAG_PATTERN: Final = re.compile(
    r"<command>\s*(.*?)\s*</command>",
    re.DOTALL,
)


class BashAdapter(BaseAdapter):
    """Runs a shell command via the configured shell, capturing stdout/stderr/exit code."""

    id = "bash"
    display_name = "Bash (shell)"
    kind: RuntimeKind = "shell"

    async def healthcheck(self) -> HealthStatus:
        shell = os.environ.get("DAP_BASH_SHELL", DEFAULT_SHELL)
        if shutil.which(shell) is None:
            return HealthStatus(available=False, missing=[f"shell binary: {shell}"])
        return HealthStatus(available=True, version=shell)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:
        config = task.runtime_config

        command = _resolve_command(config, task.prompt_xml)
        if command is None:
            return _failed(
                "No command supplied. Set runtime_config.command or include "
                "a <command>...</command> block in the agent's prompt_template.",
                duration_ms=0,
            )

        shell = config.get("shell", DEFAULT_SHELL)
        if not isinstance(shell, str):
            return _failed("runtime_config.shell must be a string", duration_ms=0)

        cwd = task.working_directory or os.getcwd()
        timeout_seconds = max(task.timeout_ms, 1) / MS_PER_SECOND

        env = os.environ.copy()
        extra_env = config.get("env", {})
        if isinstance(extra_env, dict):
            for key, value in extra_env.items():
                if isinstance(key, str) and isinstance(value, str):
                    env[key] = value

        start = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                shell,
                "-c",
                command,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return _failed(
                f"Shell not found: {shell} ({exc})",
                duration_ms=_elapsed_ms(start),
                command=command,
                shell=shell,
            )
        except OSError as exc:
            return _failed(
                f"Failed to start subprocess: {exc}",
                duration_ms=_elapsed_ms(start),
                command=command,
                shell=shell,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            # Reap so we don't leak zombies; ignore errors during cleanup.
            try:
                await process.wait()
            except Exception:
                logger.exception("error waiting for killed subprocess")
            return _failed(
                f"Command timed out after {task.timeout_ms}ms",
                duration_ms=_elapsed_ms(start),
                command=command,
                shell=shell,
                exit_code=None,
                stderr="",
                timed_out=True,
            )

        duration_ms = _elapsed_ms(start)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = process.returncode if process.returncode is not None else -1

        success = exit_code == 0
        errors: list[str] = []
        if not success:
            preview = stderr.strip().splitlines()[-5:]
            errors.append(
                f"Command exited with code {exit_code}"
                + (": " + " | ".join(preview) if preview else "")
            )

        return RuntimeResult(
            success=success,
            output=stdout,
            duration_ms=duration_ms,
            errors=errors,
            structured={
                "exit_code": exit_code,
                "stderr": stderr,
                "command": command,
                "shell": shell,
                "timed_out": False,
            },
        )


def _resolve_command(config: dict[str, Any], prompt_xml: str) -> str | None:
    """Pick the command from runtime_config first, else parse from prompt XML."""
    explicit = config.get("command")
    if isinstance(explicit, str) and explicit.strip():
        return explicit

    match = COMMAND_TAG_PATTERN.search(prompt_xml)
    if match is not None:
        extracted = match.group(1).strip()
        if extracted:
            return extracted

    return None


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * MS_PER_SECOND)


def _failed(
    message: str,
    *,
    duration_ms: int,
    command: str | None = None,
    shell: str | None = None,
    exit_code: int | None = None,
    stderr: str = "",
    timed_out: bool = False,
) -> RuntimeResult:
    structured: dict[str, Any] = {"timed_out": timed_out}
    if command is not None:
        structured["command"] = command
    if shell is not None:
        structured["shell"] = shell
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

"""Shell command runtime adapter — runs a single command in a working directory.

Use cases: running tests (`pytest`), git operations, file mutations driven by
deterministic logic rather than an LLM. Agents using this runtime supply the
command via `runtime_config.command` (preferred) or by emitting a `<command>`
block in their prompt template — the latter lets a templated agent compute
the command from PipelineState fields.

Shell precedence: `runtime_config.shell` → `DAP_BASH_SHELL` env var → `/bin/bash`.

Process lifecycle: subprocesses run in a new POSIX session/process group so a
timeout or engine cancellation kills the entire group (background jobs and
nested children) rather than only the shell. Falls back to single-process
kill on platforms without `os.setsid`.

Security model (v0.1): single-user, local-trust. The command runs with the
engine's privileges in the configured working directory; there is NO
sandbox, network restriction, or filesystem confinement. Multi-user setups
must wait for #38 (auth) plus a sandboxing layer before exposing this
runtime to untrusted agent definitions.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import shutil
import signal
import time
from typing import Any, Final

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters._subprocess_env import merge_subprocess_env
from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.bash")

DEFAULT_SHELL: Final = "/bin/bash"
SHELL_ENV_VAR: Final = "DAP_BASH_SHELL"
MS_PER_SECOND: Final = 1000
COMMAND_TAG_PATTERN: Final = re.compile(
    r"<command>\s*(.*?)\s*</command>",
    re.DOTALL,
)


def _resolve_shell(config: dict[str, Any]) -> str | None:
    """Pick the shell with documented precedence. Returns None if config invalid."""
    explicit = config.get("shell")
    if explicit is not None:
        return explicit if isinstance(explicit, str) else None
    return os.environ.get(SHELL_ENV_VAR, DEFAULT_SHELL)


class BashAdapter(BaseAdapter):
    """Runs a shell command via the configured shell, capturing stdout/stderr/exit code."""

    id = "bash"
    display_name = "Bash (shell)"
    kind: RuntimeKind = "shell"

    async def healthcheck(self) -> HealthStatus:
        shell = _resolve_shell({}) or DEFAULT_SHELL
        if shutil.which(shell) is None:
            return HealthStatus(available=False, missing=[f"shell binary: {shell}"])
        return HealthStatus(available=True, version=shell)

    async def execute(self, task: RuntimeTask) -> RuntimeResult:  # noqa: PLR0911
        # Many returns: each guard maps to a distinct precondition failure
        # with its own error message; collapsing into a dispatch dict obscures
        # the mapping (same rationale as ApiCallAdapter.execute).
        config = task.runtime_config

        command = _resolve_command(config, task.prompt_xml)
        if command is None:
            return _failed(
                "No command supplied. Set runtime_config.command or include "
                "a <command>...</command> block in the agent's prompt_template.",
                duration_ms=0,
                command=None,
                shell=None,
            )

        shell = _resolve_shell(config)
        if shell is None:
            return _failed(
                "runtime_config.shell must be a string",
                duration_ms=0,
                command=command,
                shell=None,
            )

        env, env_error = merge_subprocess_env(task.project_env_vars, config)
        if env_error is not None:
            return _failed(env_error, duration_ms=0, command=command, shell=shell)

        cwd = task.working_directory or os.getcwd()
        timeout_seconds = max(task.timeout_ms or 60_000, 1) / MS_PER_SECOND

        start = time.monotonic()
        # start_new_session=True puts the subprocess in its own POSIX
        # session / process group so we can kill the entire tree (including
        # background jobs the shell may have spawned) on timeout or cancel.
        # Windows lacks setsid; we accept the single-process limitation there.
        new_session = hasattr(os, "setsid")

        try:
            process = await asyncio.create_subprocess_exec(
                shell,
                "-c",
                command,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=new_session,
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
            await _kill_process_tree(process, new_session)
            return _failed(
                f"Command timed out after {task.timeout_ms}ms",
                duration_ms=_elapsed_ms(start),
                command=command,
                shell=shell,
                exit_code=None,
                stderr="",
                timed_out=True,
            )
        except asyncio.CancelledError:
            # Engine-level abort/pause cancels the run task. Tear down the
            # subprocess tree so we don't leak background processes, then
            # re-raise so the caller's cancellation logic still runs.
            await _kill_process_tree(process, new_session)
            raise

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
            structured=_make_structured(
                command=command,
                shell=shell,
                exit_code=exit_code,
                stderr=stderr,
                timed_out=False,
            ),
        )


def _resolve_command(config: dict[str, Any], prompt_xml: str) -> str | None:
    """Pick the command from runtime_config first, else parse from prompt XML."""
    explicit = config.get("command")
    if isinstance(explicit, str) and explicit.strip():
        return explicit

    match = COMMAND_TAG_PATTERN.search(prompt_xml)
    if match is not None:
        extracted = html.unescape(match.group(1).strip())
        if extracted:
            return extracted

    return None


async def _kill_process_tree(
    process: asyncio.subprocess.Process,
    new_session: bool,
) -> None:
    """SIGKILL the process group (POSIX) or the single process (Windows). Reap zombies."""
    if process.returncode is not None:
        return  # already exited

    try:
        if new_session and hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError):
        # Race: process exited between the check and signal — fine, just reap.
        pass
    except OSError:
        logger.exception("failed to signal subprocess pid=%s", process.pid)
        process.kill()  # best-effort fallback

    # Shield the wait so cancellation propagating from above doesn't leave a zombie.
    try:
        await asyncio.shield(process.wait())
    except (asyncio.CancelledError, Exception):
        logger.exception("error waiting for killed subprocess")


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * MS_PER_SECOND)


def _make_structured(
    *,
    command: str | None,
    shell: str | None,
    exit_code: int | None,
    stderr: str,
    timed_out: bool,
) -> dict[str, Any]:
    """Always-consistent shape so downstream nodes can branch reliably."""
    return {
        "command": command,
        "shell": shell,
        "exit_code": exit_code,
        "stderr": stderr,
        "timed_out": timed_out,
    }


def _failed(
    message: str,
    *,
    duration_ms: int,
    command: str | None,
    shell: str | None,
    exit_code: int | None = None,
    stderr: str = "",
    timed_out: bool = False,
) -> RuntimeResult:
    return RuntimeResult(
        success=False,
        output="",
        duration_ms=duration_ms,
        errors=[message],
        structured=_make_structured(
            command=command,
            shell=shell,
            exit_code=exit_code,
            stderr=stderr,
            timed_out=timed_out,
        ),
    )

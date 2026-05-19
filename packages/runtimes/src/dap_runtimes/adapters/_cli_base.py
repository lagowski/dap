"""Shared base class for CLI-tool runtime adapters (#255).

The ``claude-code``, ``codex``, and ``gemini-cli`` adapters all spawn a
provider's binary, pipe the rendered XML through stdin, and parse a
JSON response. The subprocess lifecycle, env layering, kill/cancel
semantics, JSON decode, and most of the failure-reporting logic are
identical across the three; before this base class each adapter
re-implemented the lot, multiplying drift surface.

What's shared (lives here):

- ``_normalise_extra_args`` — coerce ``runtime_config.extra_args`` to
  ``list[str]`` with a precise error message.
- ``_first_string`` / ``_first_int`` — payload-key fallbacks used by
  codex / gemini-cli where the JSON shape has shifted across releases.
- ``_kill_process_tree`` — POSIX session-group SIGKILL with zombie reap.
- ``_BaseCliAdapter`` — the orchestration of a CLI invocation:
  validate config → resolve binary → build argv → merge env → spawn
  subprocess with timeout → parse JSON. Subclasses customise via
  three hooks (``_validate_config``, ``_build_argv``, ``_parse_payload``)
  and a handful of class-level constants.

What stays per-adapter:

- argv assembly (different flags per CLI).
- JSON-payload parsing (different shapes per provider; codex / gemini
  also defend against shape drift across CLI releases).
- ``_validate_config`` rules (codex requires ``OPENAI_API_KEY``;
  gemini accepts ``thinking_budget``).

Tests patch ``asyncio.create_subprocess_exec`` and ``shutil.which`` at
this module's path (``dap_runtimes.adapters._cli_base``) since those
calls live here after the extraction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import time
from dataclasses import dataclass
from typing import Any, Final

from dap_types import HealthStatus, RuntimeKind, RuntimeResult, RuntimeTask

from dap_runtimes.adapters._subprocess_env import merge_subprocess_env
from dap_runtimes.adapters.base import BaseAdapter

logger = logging.getLogger("dap.runtimes.cli_base")

MS_PER_SECOND: Final = 1000


@dataclass(frozen=True)
class SubprocessOutcome:
    """The result of running a CLI subprocess to completion.

    Three terminal cases, distinguished by flags:

    - ``start_error`` set: the subprocess could not be spawned
      (``FileNotFoundError`` / ``OSError``); other fields are zero/empty.
    - ``timed_out=True``: the subprocess exceeded the configured
      timeout and was killed; ``stdout`` / ``stderr`` are empty.
    - Otherwise: the subprocess ran to completion; the fields hold
      the captured exit code, decoded stdout / stderr, and elapsed time.
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    start_error: str | None = None


def _normalise_extra_args(value: Any) -> tuple[list[str], str | None]:
    """Coerce ``runtime_config.extra_args`` into a list of strings (#213)."""
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
    """Return the first key in ``keys`` whose value is a non-empty string."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int:
    """Return the first key in ``keys`` whose value coerces to int; default 0.

    Tolerates stringified floats (e.g. ``"12.0"``) and skips on parse failure
    rather than raising — CLI usage stats vary by provider/version, and one
    odd value should not abort the run. Booleans are explicitly skipped:
    ``isinstance(True, int)`` is True in Python, but treating ``True``/``False``
    as token counts is almost certainly wrong shape. (#214)
    """
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            try:
                return int(float(value))
            except (TypeError, ValueError, OverflowError):
                # OverflowError: int(float("inf")) / int(float("1e309")) —
                # float() succeeds but int() can't represent infinity.
                continue
    return 0


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * MS_PER_SECOND)


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


async def _read_cli_version(binary: str) -> str | None:
    """Best-effort ``<binary> --version`` for the healthcheck display string.

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


class _BaseCliAdapter(BaseAdapter):
    """Common implementation for CLI-tool runtime adapters.

    Subclasses MUST set the four class-level constants and override the
    three hook methods (``_validate_config``, ``_build_argv``,
    ``_parse_payload``). The healthcheck is overridable when the
    provider needs additional probes (e.g. codex's ``OPENAI_API_KEY``
    presence check).
    """

    kind: RuntimeKind = "cli"

    # Subclasses set these:
    default_binary: str = ""
    provider_name: str = ""  # used in RuntimeResult.structured.provider
    display_label: str = ""  # used in error messages, e.g. "Claude" / "Codex" / "Gemini"
    install_hint: str = ""  # appended to "binary missing" healthcheck error

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _validate_config(self, config: dict[str, Any]) -> str | None:
        """Per-call validation of ``runtime_config``. Return error string or None.

        Default no-op; subclasses override.
        """
        del config
        return None

    def _build_argv(
        self,
        binary: str,
        config: dict[str, Any],
        extra_args: list[str],
    ) -> list[str]:
        """Assemble the CLI argv (``[binary, *flags, *extra_args]``)."""
        raise NotImplementedError

    def _decode_stdout(self, stdout: str) -> dict[str, Any]:
        """Parse subprocess stdout into a payload dict.

        Default: treat the entire stdout as a single JSON object.
        Override for streaming formats (e.g. NDJSON / stream-json).
        """
        return json.loads(stdout)  # type: ignore[no-any-return]

    def _parse_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
        outcome: SubprocessOutcome,
    ) -> RuntimeResult:
        """Convert decoded JSON into a successful ``RuntimeResult``.

        Subclasses may also return a failure ``RuntimeResult`` (via
        ``self._failed(...)``) when the payload is malformed in a
        provider-specific way (missing fields, ``is_error`` flag set,
        etc.).
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Default healthcheck — override in subclasses that need more probes
    # ------------------------------------------------------------------

    async def healthcheck(self) -> HealthStatus:
        binary = self._resolve_binary({})
        if shutil.which(binary) is None:
            return HealthStatus(
                available=False,
                missing=[f"{binary} binary on PATH ({self.install_hint})"],
            )
        version = await _read_cli_version(binary)
        return HealthStatus(available=True, version=version)

    # ------------------------------------------------------------------
    # Shared execute() pipeline
    # ------------------------------------------------------------------

    async def execute(self, task: RuntimeTask) -> RuntimeResult:  # noqa: PLR0911
        # Many returns: each guard maps to a distinct precondition failure
        # with its own error message; collapsing into a dispatch obscures
        # the mapping (same rationale as ApiCallAdapter / BashAdapter).
        config = task.runtime_config

        validation_error = self._validate_config(config)
        if validation_error is not None:
            return self._failed(validation_error, duration_ms=0)

        binary = self._resolve_binary(config)
        if shutil.which(binary) is None:
            return self._failed(
                f"{self.display_label} binary not found: {binary}",
                duration_ms=0,
                model_id=config.get("model_id"),
            )

        extra_args, extra_args_error = _normalise_extra_args(config.get("extra_args"))
        if extra_args_error is not None:
            return self._failed(extra_args_error, duration_ms=0)

        argv = self._build_argv(binary, config, extra_args)

        # Four-layer env (#65 + #388): engine env → instance env_vars
        # → project env_vars → per-agent runtime_config.env (highest).
        # The CLI reads its provider-specific auth env var
        # (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY) itself;
        # we just propagate the documented overlays.
        env, env_error = merge_subprocess_env(
            task.project_env_vars,
            config,
            instance_env_vars=task.instance_env_vars,
        )
        if env_error is not None:
            return self._failed(
                env_error,
                duration_ms=0,
                model_id=config.get("model_id"),
            )

        # Resolve effective timeout once and reuse in both the wait_for budget
        # and any "timed out after Xms" message — RuntimeTask.timeout_ms is
        # nullable so we fall back to the documented 60s default.
        effective_timeout_ms = task.timeout_ms or 60_000
        cwd = task.working_directory or os.getcwd()
        timeout_seconds = max(effective_timeout_ms, 1) / MS_PER_SECOND

        outcome = await self._run_subprocess(
            argv=argv,
            stdin=task.prompt_xml.encode("utf-8"),
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            binary=binary,
        )

        # Use ``.get`` for ``model_id`` in failure paths so a misbehaving
        # subclass that skipped its own validation produces a clear error
        # message instead of a confusing KeyError.
        model_id = config.get("model_id")

        if outcome.start_error is not None:
            return self._failed(
                outcome.start_error,
                duration_ms=outcome.duration_ms,
                model_id=model_id,
            )

        if outcome.timed_out:
            return self._failed(
                f"Command timed out after {effective_timeout_ms}ms",
                duration_ms=outcome.duration_ms,
                model_id=model_id,
                timed_out=True,
            )

        if outcome.exit_code != 0:
            preview = outcome.stderr.strip().splitlines()[-5:]
            message = f"{self.display_label} CLI exited with code {outcome.exit_code}" + (
                ": " + " | ".join(preview) if preview else ""
            )
            return self._failed(
                message,
                duration_ms=outcome.duration_ms,
                model_id=model_id,
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )

        try:
            payload = self._decode_stdout(outcome.stdout)
        except json.JSONDecodeError as exc:
            return self._failed(
                f"Could not parse {self.display_label} CLI output as JSON: {exc.msg}",
                duration_ms=outcome.duration_ms,
                model_id=model_id,
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )

        if not isinstance(payload, dict):
            return self._failed(
                f"{self.display_label} CLI returned unexpected JSON shape: "
                f"expected object, got {type(payload).__name__}",
                duration_ms=outcome.duration_ms,
                model_id=model_id,
                exit_code=outcome.exit_code,
                stderr=outcome.stderr,
            )

        return self._parse_payload(payload, config, outcome)

    # ------------------------------------------------------------------
    # Internals — overridable when needed but rarely
    # ------------------------------------------------------------------

    def _resolve_binary(self, config: dict[str, Any]) -> str:
        explicit = config.get("binary_path")
        if isinstance(explicit, str) and explicit.strip():
            return explicit
        return self.default_binary

    async def _run_subprocess(
        self,
        *,
        argv: list[str],
        stdin: bytes,
        cwd: str,
        env: dict[str, str],
        timeout_seconds: float,
        binary: str,
    ) -> SubprocessOutcome:
        """Spawn ``argv``, pipe ``stdin``, run with timeout. Always returns an outcome."""
        new_session = hasattr(os, "setsid")
        start = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=new_session,
            )
        except FileNotFoundError as exc:
            return SubprocessOutcome(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_ms=_elapsed_ms(start),
                start_error=f"{self.display_label} binary not found: {binary} ({exc})",
            )
        except OSError as exc:
            return SubprocessOutcome(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_ms=_elapsed_ms(start),
                start_error=f"Failed to start subprocess: {exc}",
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=stdin),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            await _kill_process_tree(process, new_session)
            return SubprocessOutcome(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_ms=_elapsed_ms(start),
                timed_out=True,
            )
        except asyncio.CancelledError:
            await _kill_process_tree(process, new_session)
            raise

        return SubprocessOutcome(
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=_elapsed_ms(start),
        )

    def _failed(
        self,
        message: str,
        *,
        duration_ms: int,
        model_id: str | None = None,
        exit_code: int | None = None,
        stderr: str = "",
        timed_out: bool = False,
    ) -> RuntimeResult:
        structured: dict[str, Any] = {
            "provider": self.provider_name,
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


# Re-export name unchanged so external callers (RuntimeRegistry) keep working.
__all__ = [
    "MS_PER_SECOND",
    "SubprocessOutcome",
    "_BaseCliAdapter",
    "_first_int",
    "_first_string",
    "_normalise_extra_args",
]

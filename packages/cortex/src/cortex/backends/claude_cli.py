"""Claude Code CLI backend — subscription bridge via subprocess.

Spawns `claude` CLI, sends prompt via stdin, parses stream-json stdout.
Works with Claude subscription — no API key needed.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

from cortex.backends.base import (
    Backend,
    BackendTimeoutError,
    BackendUnavailableError,
    LLMRequest,
    LLMResponse,
)

logger = logging.getLogger(__name__)

_NESTING_GUARDS = [
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION",
    "CLAUDE_CODE_PARENT_SESSION",
]


def _parse_stream_json(stdout: str) -> dict:
    """Parse Claude Code's stream-json output format."""
    session_id = ""
    model = ""
    assistant_texts: list[str] = []
    cost_usd = 0.0
    input_tokens = 0
    output_tokens = 0
    result_event: dict = {}

    for raw_line in stdout.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "system" and event.get("subtype") == "init":
            session_id = event.get("session_id", session_id)
            model = event.get("model", model)

        elif event_type == "assistant":
            session_id = event.get("session_id", session_id)
            message = event.get("message", {})
            for block in message.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        assistant_texts.append(text)

        elif event_type == "result":
            result_event = event
            session_id = event.get("session_id", session_id)
            cost_usd = event.get("total_cost_usd", 0.0) or 0.0
            usage = event.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)

    return {
        "content": "\n\n".join(assistant_texts).strip(),
        "session_id": session_id,
        "model": model,
        "cost_usd": cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "result": result_event,
    }


class ClaudeCLIBackend(Backend):
    """Claude Code CLI subprocess backend."""

    backend_type = "claude_cli"  # type: ignore[assignment]

    def __init__(
        self,
        command: str = "claude",
        model: str = "",
        cwd: str = "",
        max_turns: int = 0,
        timeout_sec: int = 300,
        instructions_file: str = "",
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        allowed_tools: list[str] | None = None,
    ):
        self.command = command
        self.model = model
        self.cwd = cwd or os.getcwd()
        self.max_turns = max_turns
        self.timeout_sec = timeout_sec
        self.instructions_file = instructions_file
        self.extra_args = extra_args or []
        self.custom_env = env or {}
        self.allowed_tools = allowed_tools or []
        self._session_id = ""

    def _build_args(self, resume_session: str = "") -> list[str]:
        args = [
            "--print",
            "-",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        # --dangerously-skip-permissions bypasses all permission checks in
        # non-interactive mode. More reliable than --permission-mode
        # bypassPermissions when --allowedTools is also in play — those two
        # flags conflict in claude 2.1+, causing silent Edit/Write failures.
        # --allowedTools additionally restricts available tool surface per-agent
        # (#198); both flags compose correctly with --dangerously-skip-permissions.
        args.append("--dangerously-skip-permissions")
        if self.allowed_tools:
            args.extend(["--allowedTools", " ".join(self.allowed_tools)])
        if resume_session:
            args.extend(["--resume", resume_session])
        if self.model:
            args.extend(["--model", self.model])
        if self.max_turns > 0:
            args.extend(["--max-turns", str(self.max_turns)])
        if self.instructions_file and not resume_session:
            args.extend(["--append-system-prompt-file", self.instructions_file])
        if self.extra_args:
            args.extend(self.extra_args)
        return args

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in _NESTING_GUARDS:
            env.pop(key, None)
        for key, value in self.custom_env.items():
            env[key] = value
        return env

    def invoke(self, request: LLMRequest) -> LLMResponse:
        """Spawn claude CLI, send prompt via stdin, parse stream-json output."""
        prompt_parts = []
        if request.system_prompt:
            prompt_parts.append(request.system_prompt)
            prompt_parts.append("---")
        prompt_parts.append(request.user_prompt)
        prompt = "\n\n".join(prompt_parts)

        resume_id = self._session_id
        args = self._build_args(resume_session=resume_id)
        env = self._build_env()

        logger.info("Spawning: %s %s (cwd=%s)", self.command, " ".join(args), self.cwd)

        try:
            result = subprocess.run(
                [self.command, *args],
                input=prompt,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                env=env,
                timeout=self.timeout_sec,
            )
        except subprocess.TimeoutExpired as e:
            # #128: hard failure, not a fake-success LLMResponse. The execution
            # wrapper used to treat the sentinel string as success and route
            # past the human gate without any commits.
            raise BackendTimeoutError(
                backend="claude_cli",
                timeout_sec=self.timeout_sec,
            ) from e
        except FileNotFoundError as e:
            raise BackendUnavailableError(
                f"'{self.command}' not found in PATH",
                backend="claude_cli",
            ) from e

        if result.returncode != 0:
            logger.warning("Claude CLI exited %d: %s", result.returncode, result.stderr[:500])

        parsed = _parse_stream_json(result.stdout)

        if parsed["session_id"]:
            self._session_id = parsed["session_id"]

        return LLMResponse(
            content=parsed["content"],
            model=parsed["model"],
            session_id=parsed["session_id"],
            input_tokens=parsed["input_tokens"],
            output_tokens=parsed["output_tokens"],
            cost_usd=parsed["cost_usd"],
            backend="claude_cli",
            raw={"stderr": result.stderr[:1000], "exit_code": result.returncode},
        )

    def health_check(self) -> bool:
        try:
            result = subprocess.run(
                [self.command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning("Claude CLI health check failed: %s", e)
            return False

    @property
    def session_id(self) -> str:
        return self._session_id

    def set_session(self, session_id: str) -> None:
        self._session_id = session_id

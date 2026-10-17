"""aider CLI runtime adapter (#929).

Spawns the ``aider`` binary for a single non-interactive message and captures
its plain-text output. Built on ``_BaseCliAdapter`` like ``codex`` and
``gemini-cli``, so the git workspace config and guards (#922, #923) apply with
no extra code.

Invocation: the rendered XML is piped through stdin as for the other CLI
runtimes, and aider reads it with ``--message-file /dev/stdin``. Passing it as
``--message <text>`` instead would hit Linux's 128 KiB per-argument limit on a
large prompt. ``/dev/stdin`` makes this runtime POSIX-only, like the process
group handling in the base.

Auto-commits stay **on** by default. aider commits each edit it applies, which
is what ``require_nonempty_diff`` counts and ``push`` sends; ``append_only``
holds because aider only appends commits. ``auto_commits: false`` passes
``--no-auto-commits`` for a run whose edits a later node commits, and is refused
together with ``push``: the edits would stay uncommitted and nothing would be
pushed. ``--no-dirty-commits`` keeps aider from committing changes that were in
the workspace before the run under its own name, and ``--no-gitignore`` keeps
it from committing a ``.gitignore`` edit that would make an otherwise empty run
look like it changed something.

Output: aider prints text, not JSON. The whole stdout is the result's output,
and the ``Tokens: … sent, … received. Cost: $… message, $… session.`` lines it
prints after each model reply give the token and cost figures.

Auth: aider (via litellm) reads the provider key for the model from the
environment itself. The key is checked up front for the providers whose model
names are recognisable; other models (``ollama_chat/…``, custom routes) are
passed through and fail in aider if misconfigured.
"""

from __future__ import annotations

import os
import re
from typing import Any, Final

from dap_types import HealthStatus, RuntimeResult

from dap_runtimes.adapters._cli_base import SubprocessOutcome, _BaseCliAdapter

ANTHROPIC_KEY: Final = "ANTHROPIC_API_KEY"
OPENAI_KEY: Final = "OPENAI_API_KEY"
GEMINI_KEY: Final = "GEMINI_API_KEY"
DEEPSEEK_KEY: Final = "DEEPSEEK_API_KEY"
OPENROUTER_KEY: Final = "OPENROUTER_API_KEY"

# Checked in order; the first prefix that matches the lower-cased model id wins,
# so a routed model (``openrouter/anthropic/...``) resolves to its router's key.
_KEY_BY_PREFIX: Final = (
    ("openrouter/", OPENROUTER_KEY),
    ("anthropic/", ANTHROPIC_KEY),
    ("claude", ANTHROPIC_KEY),
    ("openai/", OPENAI_KEY),
    ("gpt-", OPENAI_KEY),
    ("chatgpt", OPENAI_KEY),
    ("o1", OPENAI_KEY),
    ("o3", OPENAI_KEY),
    ("o4", OPENAI_KEY),
    ("gemini", GEMINI_KEY),
    ("deepseek", DEEPSEEK_KEY),
)

# aider's own short model aliases (``aider --model sonnet``).
_KEY_BY_ALIAS: Final = {
    "sonnet": ANTHROPIC_KEY,
    "opus": ANTHROPIC_KEY,
    "haiku": ANTHROPIC_KEY,
    "3": OPENAI_KEY,
    "4": OPENAI_KEY,
    "4o": OPENAI_KEY,
    "4-turbo": OPENAI_KEY,
    "35turbo": OPENAI_KEY,
    "35-turbo": OPENAI_KEY,
    "flash": GEMINI_KEY,
    "r1": DEEPSEEK_KEY,
}

_PROVIDER_KEYS: Final = (ANTHROPIC_KEY, OPENAI_KEY, GEMINI_KEY, DEEPSEEK_KEY, OPENROUTER_KEY)

_COUNT: Final = r"[\d.]+[kKmM]?"
_TOKENS_RE: Final = re.compile(
    rf"Tokens:\s*(?P<sent>{_COUNT}) sent\b.*?(?P<received>{_COUNT}) received"
)
_SESSION_COST_RE: Final = re.compile(r"\$(?P<cost>[\d.]+) session")
_SUFFIX: Final = {"k": 1_000, "m": 1_000_000}


def provider_key_for(model_id: str) -> str | None:
    """The env var aider needs for ``model_id``, or None when it can't be told."""
    model = model_id.strip().lower()
    if model in _KEY_BY_ALIAS:
        return _KEY_BY_ALIAS[model]
    for prefix, key in _KEY_BY_PREFIX:
        if model.startswith(prefix):
            return key
    return None


def _count(text: str) -> int:
    multiplier = _SUFFIX.get(text[-1].lower(), 1)
    number = text[:-1] if multiplier != 1 else text
    try:
        return round(float(number) * multiplier)
    except ValueError:
        return 0


def parse_aider_usage(text: str) -> dict[str, Any]:
    """Token and cost figures from aider's output.

    Tokens are summed over every ``Tokens:`` line (one per model reply, so a run
    with reflections prints several); the cost is the last ``$… session`` figure,
    which already covers the whole run. ``cost_usd`` is None when aider printed
    none (models without pricing data).
    """
    input_tokens = output_tokens = 0
    cost: float | None = None
    for line in text.splitlines():
        tokens = _TOKENS_RE.search(line)
        if tokens is None:
            continue
        input_tokens += _count(tokens["sent"])
        output_tokens += _count(tokens["received"])
        session = _SESSION_COST_RE.search(line)
        if session is not None:
            try:
                cost = float(session["cost"])
            except ValueError:
                pass
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}


class AiderAdapter(_BaseCliAdapter):
    """Runs aider for one message with auto-commits, captures its text output."""

    id = "aider"
    display_name = "Aider"

    default_binary = "aider"
    provider_name = "aider"
    display_label = "Aider"
    install_hint = "install: pip install aider-install && aider-install"

    async def healthcheck(self) -> HealthStatus:
        # Which key matters depends on each agent's model, so the healthcheck only
        # asks for one of the known ones; execute() checks the model's own key.
        base = await super().healthcheck()
        if not base.available:
            return base
        if not any(os.environ.get(key) for key in _PROVIDER_KEYS):
            return HealthStatus(
                available=False,
                missing=[f"a provider key env var for aider: one of {', '.join(_PROVIDER_KEYS)}"],
            )
        return base

    def _validate_config(self, config: dict[str, Any]) -> str | None:
        model_id = config.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            return "runtime_config.model_id is required (e.g. 'claude-sonnet-4-5')"

        binary_path = config.get("binary_path")
        if binary_path is not None and not isinstance(binary_path, str):
            return "runtime_config.binary_path must be a string"

        auto_commits = config.get("auto_commits", True)
        if not isinstance(auto_commits, bool):
            return "runtime_config.auto_commits must be a boolean"
        if not auto_commits and config.get("push") is True:
            return (
                "runtime_config.auto_commits=false can't be combined with push: "
                "aider's edits would stay uncommitted and nothing would be pushed"
            )

        key = provider_key_for(model_id)
        if key is not None and not self._key_is_set(key, config):
            return f"{key} env var not set (aider needs it for model '{model_id}')"
        return None

    @staticmethod
    def _key_is_set(key: str, config: dict[str, Any]) -> bool:
        if os.environ.get(key):
            return True
        env = config.get("env")
        return isinstance(env, dict) and bool(env.get(key))

    def _build_argv(
        self,
        binary: str,
        config: dict[str, Any],
        extra_args: list[str],
    ) -> list[str]:
        argv = [
            binary,
            "--message-file",
            "/dev/stdin",
            "--model",
            config["model_id"],
            "--yes-always",
            "--no-pretty",
            "--no-stream",
            "--no-fancy-input",
            "--no-check-update",
            "--no-show-model-warnings",
            "--no-gitignore",
            "--no-dirty-commits",
        ]
        if config.get("auto_commits", True) is False:
            argv.append("--no-auto-commits")
        return [*argv, *extra_args]

    def _decode_stdout(self, stdout: str) -> dict[str, Any]:
        # Plain text: nothing to decode, so never a JSON error.
        return {"text": stdout}

    def _parse_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
        outcome: SubprocessOutcome,
    ) -> RuntimeResult:
        text = str(payload.get("text", ""))
        usage = parse_aider_usage(text)
        input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
        return RuntimeResult(
            success=True,
            output=text.strip(),
            tokens_used=input_tokens + output_tokens,
            cost_usd=usage["cost_usd"],
            duration_ms=outcome.duration_ms,
            errors=[],
            structured={
                "provider": self.provider_name,
                "model": config["model_id"],
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                "exit_code": outcome.exit_code,
                "timed_out": False,
            },
        )


__all__: Final = ["AiderAdapter", "parse_aider_usage", "provider_key_for"]

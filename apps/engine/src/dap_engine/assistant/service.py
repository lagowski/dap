"""Config-assistant inference (#689 slice 2).

Backs the assistant chat with a real LLM by reusing the tested
:class:`ApiCallAdapter` — it already normalises providers, resolves the
provider key from the env overlay, validates config, and handles errors/cost.
We pick whichever provider has a key available (os.environ + the decrypted
instance env vars), stuff the curated docs into the system prompt for
grounding, render the transcript, and run one api-call.

No secrets leave this boundary: the docs + the user's transcript go into the
prompt; provider key VALUES never do (the adapter reads them from the env
overlay, they're not interpolated into any string we build).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from dap_runtimes import ApiCallAdapter
from dap_runtimes.adapters._providers import PROVIDER_REGISTRY
from dap_types import RuntimeTask

from dap_engine.assistant.docs_corpus import DOCS_CORPUS

logger = logging.getLogger("dap.engine.assistant")

# Provider preference + a sensible cheap default model for each. Overridable
# per-instance via DAP_ASSISTANT_PROVIDER / DAP_ASSISTANT_MODEL env vars.
_PROVIDER_PRIORITY: tuple[str, ...] = ("anthropic", "openai", "gemini", "glm", "openrouter")
_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "glm": "glm-4.5",
    "openrouter": "anthropic/claude-3.5-haiku",
}

_SYSTEM_INSTRUCTIONS = """\
You are the DAP configuration assistant. Help the user map an intent (e.g. "an
agent that reviews PRs cheaply") onto a concrete DAP configuration: runtime,
model/provider, role, key prompt/contract notes, and the provider/env
requirements. Ground every recommendation in the reference below — do NOT
invent config fields that aren't in it. Be concise and concrete. These are
suggestions the user applies manually; never claim anything was saved, and
never ask for or include secret values (only env-var NAMES).

# Reference
"""

_NO_PROVIDER_MESSAGE = (
    "I can't answer yet — no LLM provider is configured on this instance. Add a "
    "provider API key in Settings → environment variables (e.g. ANTHROPIC_API_KEY, "
    "OPENAI_API_KEY, or GEMINI_API_KEY) and I'll start giving grounded config advice."
)


@dataclass
class AssistantReply:
    text: str
    grounded: bool = False
    errors: list[str] = field(default_factory=list)


def select_provider(env: Mapping[str, str]) -> tuple[str, str] | None:
    """Pick (provider_id, model_id) whose key is present in ``env``.

    Honours DAP_ASSISTANT_PROVIDER / DAP_ASSISTANT_MODEL overrides, else walks
    the priority order. Returns ``None`` when no provider key is available.
    """
    override = env.get("DAP_ASSISTANT_PROVIDER")
    model_override = env.get("DAP_ASSISTANT_MODEL")
    candidates = (override, *_PROVIDER_PRIORITY) if override else _PROVIDER_PRIORITY
    for pid in candidates:
        info = PROVIDER_REGISTRY.get(pid)
        if info is None or info.default_env_var is None:
            continue
        if env.get(info.default_env_var):
            model = model_override or _DEFAULT_MODELS.get(pid)
            if model:
                return pid, model
    return None


def build_transcript(messages: list[dict[str, str]]) -> str:
    """Render the chat history into a plain transcript for the prompt."""
    lines = []
    for m in messages:
        who = "User" if m.get("role") == "user" else "Assistant"
        lines.append(f"{who}: {m.get('content', '')}")
    lines.append("Assistant:")
    return "\n".join(lines)


async def generate_reply(
    messages: list[dict[str, str]],
    *,
    env: Mapping[str, str],
    adapter: ApiCallAdapter | None = None,
) -> AssistantReply:
    """Produce one grounded assistant reply, or a graceful no-provider message."""
    choice = select_provider(env)
    if choice is None:
        return AssistantReply(text=_NO_PROVIDER_MESSAGE, grounded=False)
    provider_id, model_id = choice

    # Instance env vars the adapter should overlay so the provider can read its
    # key. We pass only what's not already in os.environ (the adapter merges).
    instance_overlay = {k: v for k, v in env.items() if k not in os.environ}

    task = RuntimeTask(
        execution_id="assistant",
        prompt_xml=build_transcript(messages),
        working_directory=".",
        timeout_ms=60_000,
        runtime_config={
            "provider": provider_id,
            "model_id": model_id,
            "system_prompt": _SYSTEM_INSTRUCTIONS + DOCS_CORPUS,
            "max_tokens": 1024,
        },
        instance_env_vars=instance_overlay,
    )

    result = await (adapter or ApiCallAdapter()).execute(task)
    if not result.success:
        # Raw provider errors can carry endpoint URLs / key fragments / stack
        # traces — log them server-side, surface only a generic message.
        logger.warning(
            "assistant: provider %s call failed: %s",
            provider_id,
            "; ".join(result.errors) or "unknown error",
        )
        return AssistantReply(
            text=f"The model call failed — check the provider key/quota for {provider_id}.",
            grounded=False,
            errors=list(result.errors),
        )
    return AssistantReply(text=result.output.strip(), grounded=True)
